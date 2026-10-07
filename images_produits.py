"""Recherche du visuel et du nom d'un produit à partir de son code CIP13 / EAN13.

Ordre de recherche :
  1. dossier local  images/<code>.(png|jpg|jpeg|webp)   (cache et dépôts manuels)
  2. Open Beauty Facts, Open Food Facts, Open Products Facts (bases collaboratives)
  3. Recherche web VÉRIFIÉE (rechercher_visuels) : des moteurs de recherche trouvent des pages qui citent le code ;
     chaque page est ouverte et ne compte que si le code y figure réellement. Aucune liste de sites n'est figée :
     un site qui change d'adresse est retrouvé par le moteur, une page qui ne répond plus est ignorée.
     À défaut : pages dont le nom du produit correspond (« probable »), puis images web non vérifiées.
  4. Import ou collage manuel d'une image (collage depuis le presse-papiers, glisser-déposer, fichier).
Toute image retenue est mémorisée dans images/<code>.png.
"""
import base64
import importlib
import io
import ipaddress
import json
import re
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from PIL import Image, ImageOps

from chemins import DONNEES

DOSSIER_IMAGES = DONNEES / "images"
DOSSIER_IMAGES.mkdir(parents=True, exist_ok=True)

SOURCES = [
    ("Open Beauty Facts", "https://world.openbeautyfacts.org/api/v2/product/{code}.json"),
    ("Open Food Facts", "https://world.openfoodfacts.org/api/v2/product/{code}.json"),
    ("Open Products Facts", "https://world.openproductsfacts.org/api/v2/product/{code}.json"),
]
CHAMPS = "product_name,product_name_fr,brands,image_front_url,image_url,selected_images"
HEADERS = {"User-Agent": "AffichesPharmacieBouton/1.0 (usage interne pharmacie)"}
# Les sites marchands refusent souvent les programmes : la recherche web se présente comme un navigateur ordinaire.
NAVIGATEUR = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.6",
}
EXTENSIONS = ("png", "jpg", "jpeg", "webp")
MAX_IMAGE_OCTETS = 8_000_000      # une image plus lourde est refusée
MAX_PAGE_OCTETS = 2_500_000       # une page plus lourde est lue jusqu'à cette taille seulement
MAX_PAGES = 18                    # pages ouvertes par étape de la recherche vérifiée
DELAI_ETAPE = 22                  # secondes accordées à l'ouverture des pages d'une étape
MIN_COTE_VERIFIE = 150            # une image de page plus petite (en pixels) n'est pas retenue
DOMAINES_IGNORES = ("google.", "facebook.", "instagram.", "pinterest.", "youtube.", "youtu.be", "tiktok.",
                    "twitter.", "x.com", "linkedin.", "reddit.", "amazon.", "ebay.", "aliexpress.", "leboncoin.",
                    "wikipedia.", "duckduckgo.", "bing.com")
RANG_NIVEAU = {"nom": 0, "moyen": 1, "fort": 2}
IMAGE_A_IGNORER = re.compile(r"logo|sprite|favicon|placeholder|no-?image|spacer|\.svg|\.gif(\?|$)", re.I)


def nettoyer_code(brut: str) -> str:
    return re.sub(r"\D", "", brut or "")


def vers_rgb_blanc(img: Image.Image) -> Image.Image:
    """Aplatit la transparence sur fond blanc."""
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        fond = Image.new("RGBA", img.size, (255, 255, 255, 255))
        fond.alpha_composite(img)
        return fond.convert("RGB")
    return img.convert("RGB")


def rogner_marges_blanches(img: Image.Image, tolerance: int = 245) -> Image.Image:
    gris = img.convert("L").point(lambda p: 255 if p < tolerance else 0)
    boite = gris.getbbox()
    if not boite:
        return img
    marge = int(max(img.size) * 0.01)
    x0, y0, x1, y1 = boite
    return img.crop((max(0, x0 - marge), max(0, y0 - marge),
                     min(img.width, x1 + marge), min(img.height, y1 + marge)))


def image_locale(code: str):
    for ext in EXTENSIONS:
        p = DOSSIER_IMAGES / f"{code}.{ext}"
        if p.exists():
            img = Image.open(p)
            img.load()
            return vers_rgb_blanc(img)
    return None


def enregistrer_image(code: str, img: Image.Image) -> Path:
    p = DOSSIER_IMAGES / f"{code}.png"
    vers_rgb_blanc(img).save(p)
    return p


# --------------------------------------------------------------------------- Téléchargements
def _url_publique(url: str) -> bool:
    """Adresse web ordinaire (http/https vers un site public) : refuse les adresses locales ou internes."""
    try:
        p = urlparse(url)
        hote = (p.hostname or "").lower()
    except ValueError:
        return False
    if p.scheme not in ("http", "https") or not hote:
        return False
    if hote == "localhost" or hote.endswith((".local", ".localhost", ".internal", ".lan")):
        return False
    try:
        return ipaddress.ip_address(hote).is_global
    except ValueError:
        return True  # nom de domaine


def telecharger_octets(url: str, referer: str = "", max_octets: int = MAX_IMAGE_OCTETS, delai=(5, 20)) -> bytes:
    """Télécharge un fichier image (taille limitée). Lève une exception en cas d'échec."""
    if not _url_publique(url):
        raise ValueError("adresse non autorisée")
    entetes = dict(NAVIGATEUR)
    entetes["Accept"] = "image/png,image/jpeg,image/webp,image/*;q=0.8,*/*;q=0.5"
    if referer:
        entetes["Referer"] = referer
    with requests.get(url, headers=entetes, timeout=delai, stream=True) as r:
        r.raise_for_status()
        octets = bytearray()
        for bloc in r.iter_content(65536):
            octets += bloc
            if len(octets) > max_octets:
                raise ValueError("image trop volumineuse")
    return bytes(octets)


def image_depuis_octets(octets: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(octets))
    img.load()
    try:
        img = ImageOps.exif_transpose(img)  # photo prise « couchée » : remise à l'endroit
    except Exception:
        pass
    return vers_rgb_blanc(img)


def telecharger_image(url: str, referer: str = "") -> Image.Image:
    return image_depuis_octets(telecharger_octets(url, referer=referer))


def obtenir_image(candidat: dict) -> Image.Image:
    """Image complète d'une proposition : celle déjà reçue pendant la recherche, sinon téléchargée."""
    if candidat.get("octets"):
        return image_depuis_octets(candidat["octets"])
    return telecharger_image(candidat["image"], referer=candidat.get("page", ""))


def image_depuis_collage(valeur: dict) -> Image.Image:
    """Image reçue du composant « coller une image » : fichier (données base64) ou adresse web."""
    if not isinstance(valeur, dict):
        raise ValueError("rien reçu")
    donnees = valeur.get("donnees")
    if donnees:
        if donnees.startswith("data:") and "," in donnees[:200]:
            donnees = donnees.split(",", 1)[1]
        octets = base64.b64decode(donnees)
        if len(octets) > MAX_IMAGE_OCTETS:
            raise ValueError("image trop volumineuse")
        return image_depuis_octets(octets)
    if valeur.get("url"):
        return telecharger_image(str(valeur["url"]).strip())
    raise ValueError("aucune image dans ce qui a été collé")


def _url_image(prod: dict) -> str:
    url = prod.get("image_front_url") or prod.get("image_url")
    if url:
        return url
    # repli : n'importe quelle image « front » sélectionnée
    front = (prod.get("selected_images") or {}).get("front", {}).get("display", {})
    return next(iter(front.values()), "") if front else ""


def rechercher(code: str):
    """Retourne (image PIL ou None, marque, détail, journal : liste de lignes lisibles)."""
    code = nettoyer_code(code)
    journal = []
    if len(code) not in (8, 12, 13):
        return None, "", "", [f"Code invalide ({len(code)} chiffres) : saisir un CIP13 ou un EAN de 13 chiffres."]

    img = image_locale(code)
    if img is not None:
        journal.append("Visuel déjà présent dans le dossier images.")
    marque, detail = "", ""
    for source, modele in SOURCES:
        try:
            r = requests.get(modele.format(code=code), params={"fields": CHAMPS},
                             headers=HEADERS, timeout=20)
        except Exception as e:
            journal.append(f"{source} : connexion impossible ({type(e).__name__} : {str(e)[:120]}).")
            continue
        if r.status_code == 404:
            journal.append(f"{source} : produit absent de la base.")
            continue
        if r.status_code != 200:
            journal.append(f"{source} : réponse inattendue (HTTP {r.status_code}).")
            continue
        try:
            data = r.json()
        except ValueError:
            journal.append(f"{source} : réponse illisible.")
            continue
        if data.get("status") != 1:
            journal.append(f"{source} : produit absent de la base.")
            continue
        prod = data.get("product", {})
        if not marque and not detail:
            marque = (prod.get("brands") or "").split(",")[0].strip()
            libelle = (prod.get("product_name_fr") or prod.get("product_name") or "").strip()
            mots_m = marque.split()
            mots_l = libelle.split()
            if mots_m and [x.lower() for x in mots_l[:len(mots_m)]] == [x.lower() for x in mots_m]:
                libelle = " ".join(mots_l[len(mots_m):]).lstrip("-–—:, ")  # la marque n'est pas répétée dans le détail
            detail = libelle
        if img is not None:
            journal.append(f"{source} : fiche trouvée (nom récupéré).")
            continue
        url = _url_image(prod)
        if not url:
            journal.append(f"{source} : fiche trouvée, mais sans photo.")
            continue
        try:
            img = rogner_marges_blanches(telecharger_image(url))
            enregistrer_image(code, img)
            journal.append(f"{source} : visuel obtenu.")
        except Exception as e:
            journal.append(f"{source} : téléchargement du visuel impossible ({type(e).__name__}).")

    if img is None:
        journal.append("Aucun visuel dans les bases publiques : recherche sur les sites marchands, collage ou import manuel.")
    return img, marque, detail, journal


def analyser_image(img: Image.Image) -> dict:
    """Évalue si une image ressemble à un visuel « studio » (fond blanc uni, comme sur les sites marchands).
    Retourne {'blanc': part de bordure blanche (0-1), 'studio': bool, 'cote_min': pixels}."""
    rgb = img.convert("RGB")
    cote_min = min(rgb.size)
    petit = rgb.copy()
    petit.thumbnail((200, 200))
    w, h = petit.size
    m = max(2, int(min(w, h) * 0.05))
    px = petit.load()
    total = blancs = 0
    for y in range(h):
        for x in range(w):
            if x < m or x >= w - m or y < m or y >= h - m:
                total += 1
                r, g, b = px[x, y]
                if r >= 240 and g >= 240 and b >= 240:
                    blancs += 1
    part = blancs / max(1, total)
    return {"blanc": part, "studio": part >= 0.90, "cote_min": cote_min}


def _mots_utiles(texte: str):
    t = unicodedata.normalize("NFD", (texte or "").lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return [m for m in re.split(r"[^a-z0-9]+", t) if len(m) >= 4 and not m.isdigit()]


def _site(url: str) -> str:
    h = urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


# --------------------------------------------------------------------------- Moteurs de recherche
def _ddgs(methode, requete, **options):
    """Une recherche (« text » ou « images ») par les moteurs du module ddgs ; un 2e essai en cas d'échec."""
    from ddgs import DDGS
    erreur = None
    for essai in range(2):
        try:
            return getattr(DDGS(), methode)(requete, **options) or []
        except Exception as e:
            erreur = e
            time.sleep(0.6 + essai)
    raise erreur


def _recherches(methode, requetes, **options):
    """Plusieurs recherches, 3 à la fois. Retourne (liste de résultats par requête, erreurs lisibles)."""
    try:
        importlib.import_module("ddgs")
    except Exception:
        return [], ["module de recherche web non installé (ddgs)"]
    erreurs = []

    def une(req):
        try:
            return _ddgs(methode, req, **options)
        except Exception as e:
            erreurs.append(f"« {req[:40]} » : {type(e).__name__}")
            return []

    with ThreadPoolExecutor(max_workers=3) as ex:
        lots = list(ex.map(une, list(dict.fromkeys(r for r in requetes if r.strip()))))
    return lots, erreurs


def images_web(requetes):
    """Résultats bruts d'une recherche d'images (sans doublon). Retourne (résultats, erreurs)."""
    lots, erreurs = _recherches("images", requetes, region="fr-fr", max_results=25)
    vus, bruts = set(), []
    for lot in lots:
        for r in lot:
            u = r.get("image")
            if u and u not in vus:
                vus.add(u)
                bruts.append(r)
    return bruts, erreurs


# --------------------------------------------------------------------------- Images du web (non vérifiées)
def propositions_web(requetes, nom: str = "", nombre: int = 8, taille_min: int = 350, bruts=None):
    """Propositions d'images issues d'une recherche d'images web, classées par ressemblance à un visuel
    de site marchand : fond blanc uni, haute résolution, titre proche du nom du produit.
    Ces images ne sont PAS vérifiées (rien ne prouve qu'elles montrent le bon produit).
    bruts : résultats d'une recherche d'images déjà faite (sinon la recherche est lancée avec requetes).
    Retourne (liste de dicts, message). À valider visuellement par l'utilisateur."""
    erreur = ""
    if bruts is None:
        if isinstance(requetes, str):
            requetes = [requetes]
        bruts, erreurs = images_web(requetes)
        if erreurs and not bruts:
            erreur = f"Recherche web indisponible ({erreurs[0]})."
    vus, candidats = set(), []
    for r in bruts:
        url = r.get("image")
        if not url or url in vus:
            continue
        vus.add(url)
        w, h = int(r.get("width") or 0), int(r.get("height") or 0)
        if w and h and min(w, h) < taille_min:
            continue
        candidats.append({"miniature": r.get("thumbnail") or url, "image": url, "titre": r.get("title", ""),
                          "site": _site(r.get("url") or url), "page": r.get("url") or "",
                          "largeur": w, "hauteur": h, "verifie": False, "niveau": "non vérifié"})
    candidats = candidats[:24]

    def evaluer(c):
        try:
            r = requests.get(c["miniature"], headers=HEADERS, timeout=8)
            r.raise_for_status()
            a = analyser_image(Image.open(io.BytesIO(r.content)))
        except Exception:
            a = {"blanc": 0.0, "studio": False, "cote_min": 0}
        c["blanc"], c["studio"] = a["blanc"], a["studio"]
        return c

    with ThreadPoolExecutor(max_workers=8) as ex:
        candidats = list(ex.map(evaluer, candidats))

    mots = _mots_utiles(nom)
    for c in candidats:
        titre = " ".join(_mots_utiles(c["titre"]))
        pertinence = (sum(1 for m in mots if m in titre) / len(mots)) if mots else 0.5
        resolution = min(1.0, min(c["largeur"] or 400, c["hauteur"] or 400) / 800)
        c["score"] = 100 * c["blanc"] + 40 * resolution + 30 * pertinence
    candidats.sort(key=lambda c: -c["score"])
    sortie = candidats[:nombre]
    return sortie, ("" if sortie else (erreur or "Aucune proposition suffisamment grande trouvée."))


# --------------------------------------------------------------------------- Recherche vérifiée par le code
class _LecteurPage(HTMLParser):
    """Lit les informations utiles d'une page produit : balises meta, données structurées (JSON-LD), images."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.metas = []            # (nom ou propriété en minuscules, contenu)
        self.images_liees = []     # <link rel="image_src"> et éléments itemprop="image"
        self.blocs_ld = []         # textes JSON-LD
        self.titre = ""
        self._ld = None
        self._titre = None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            cle = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if cle and "content" in a:
                self.metas.append((cle, a["content"].strip()))
        elif tag == "link":
            if "image_src" in a.get("rel", "").lower() and a.get("href"):
                self.images_liees.append(a["href"])
        elif tag == "script":
            if "ld+json" in a.get("type", "").lower():
                self._ld = []
        elif tag == "title" and not self.titre:
            self._titre = []
        if a.get("itemprop", "").lower() == "image" and tag in ("img", "link", "a"):
            v = a.get("content") or a.get("src") or a.get("href") or a.get("data-src")
            if v:
                self.images_liees.append(v)

    def handle_data(self, data):
        if self._ld is not None:
            self._ld.append(data)
        elif self._titre is not None:
            self._titre.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._ld is not None:
            self.blocs_ld.append("".join(self._ld))
            self._ld = None
        elif tag == "title" and self._titre is not None:
            self.titre = " ".join("".join(self._titre).split())
            self._titre = None


def _aplatir_ld(x, sortie):
    if isinstance(x, list):
        for y in x:
            _aplatir_ld(y, sortie)
    elif isinstance(x, dict):
        sortie.append(x)
        for y in x.values():
            if isinstance(y, (dict, list)):
                _aplatir_ld(y, sortie)


def _est_produit(noeud: dict) -> bool:
    t = noeud.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(isinstance(x, str) and x.lower() in ("product", "productgroup", "individualproduct") for x in types)


def _images_ld(v):
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [u for x in v for u in _images_ld(x)]
    if isinstance(v, dict):
        return _images_ld(v.get("url") or v.get("contentUrl") or v.get("@id") or "")
    return []


def _texte_ld(v) -> str:
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        return _texte_ld(v.get("name") or "")
    if isinstance(v, list) and v:
        return _texte_ld(v[0])
    return ""


def _motif_code(code: str):
    # le code, éventuellement précédé d'un seul zéro (EAN-13 écrit sur 14 chiffres), sans autre chiffre accolé
    return re.compile(r"(?<!\d)0?" + re.escape(code) + r"(?!\d)")


def analyser_page(html: str, url: str, code: str, nom: str = ""):
    """Vérifie qu'une page parle bien du produit et en tire nom, marque et images.
    Retourne None si la page ne convient pas, sinon un dict :
      niveau : « fort » (le code figure dans l'adresse, le titre ou les données du produit),
               « moyen » (le code figure dans le texte de la page),
               « nom » (code absent, mais le nom du produit correspond : à vérifier) ;
      nom, marque, images (adresses complètes), occurrences."""
    motif = _motif_code(code)
    occurrences = len(motif.findall(html))
    code_dans_url = bool(motif.search(unquote(url)))
    lecteur = _LecteurPage()
    try:
        lecteur.feed(html)
    except Exception:
        pass
    noeuds = []
    for bloc in lecteur.blocs_ld:
        try:
            _aplatir_ld(json.loads(bloc, strict=False), noeuds)
        except ValueError:
            continue
    produits = [n for n in noeuds if _est_produit(n)]
    cibles = [n for n in produits if motif.search(json.dumps(n, ensure_ascii=False))]
    meta = {}
    for cle, contenu in lecteur.metas:
        meta.setdefault(cle, contenu)
    titre_page = meta.get("og:title") or lecteur.titre

    if occurrences or code_dans_url:
        fort = (code_dans_url or bool(cibles) or bool(motif.search(lecteur.titre))
                or any(motif.search(c) for _, c in lecteur.metas))
        if not fort and occurrences > 8:
            return None  # page de liste ou de catégorie : le code n'y est qu'un parmi d'autres
        niveau = "fort" if fort else "moyen"
        sources = cibles or (produits if len(produits) == 1 else [])
    else:
        mots = sorted(set(_mots_utiles(nom)))
        if not nom or len(mots) < 2:
            return None
        texte = " ".join(_mots_utiles(" ".join([titre_page, lecteur.titre] + [_texte_ld(n.get("name")) for n in produits])))
        accord = sum(1 for m in mots if m in texte.split()) / len(mots)
        if accord < 0.75 or not (len(produits) == 1 or meta.get("og:type", "").lower().startswith("product")):
            return None
        niveau, sources = "nom", produits if len(produits) == 1 else []

    images = []
    for n in sources:
        images += _images_ld(n.get("image"))
    for cle in ("og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src"):
        images += [c for k, c in lecteur.metas if k == cle]
    images += lecteur.images_liees
    propres, vus = [], set()
    for u in images:
        u = (u or "").strip()
        if not u or u.startswith("data:"):
            continue
        u = urljoin(url, u)
        if urlparse(u).scheme not in ("http", "https") or IMAGE_A_IGNORER.search(u) or u in vus:
            continue
        vus.add(u)
        propres.append(u)

    nom_page = next((_texte_ld(n.get("name")) for n in sources if _texte_ld(n.get("name"))), "")
    if not nom_page:
        nom_page = meta.get("og:title", "")
    marque = next((_texte_ld(n.get("brand")) for n in sources if _texte_ld(n.get("brand"))), "")
    nom_page = " ".join(motif.sub("", nom_page).split()).strip(" -–—:|")
    return {"niveau": niveau, "nom": nom_page if 0 < len(nom_page) <= 140 else "",
            "marque": marque if len(marque) <= 40 else "", "images": propres, "occurrences": occurrences}


def _telecharger_page(url: str):
    """Télécharge une page web (taille limitée). Retourne (texte, adresse finale)."""
    if not _url_publique(url):
        raise ValueError("adresse non autorisée")
    entetes = dict(NAVIGATEUR)
    entetes["Accept"] = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"
    with requests.get(url, headers=entetes, timeout=(4, 8), stream=True) as r:
        r.raise_for_status()
        if not _url_publique(r.url):
            raise ValueError("adresse non autorisée")
        type_ = r.headers.get("content-type", "").lower()
        if type_ and "html" not in type_ and "xml" not in type_:
            raise ValueError("ce n'est pas une page web")
        octets = bytearray()
        for bloc in r.iter_content(65536):
            octets += bloc
            if len(octets) > MAX_PAGE_OCTETS:
                break
        try:
            texte = bytes(octets).decode("utf-8")
        except UnicodeDecodeError:
            texte = bytes(octets).decode(r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1"
                                         else "latin-1", errors="replace")
        return texte, r.url


def _motif_erreur(e: Exception) -> str:
    reponse = getattr(e, "response", None)
    if reponse is not None:
        return f"HTTP {reponse.status_code}"
    return type(e).__name__


def _verifier_pages(urls, code, nom="", delai=DELAI_ETAPE):
    """Ouvre les pages en parallèle et garde celles qui parlent du produit.
    Retourne (pages confirmées : liste de dicts, bilan : {'refus': [...], 'sans_code': n, 'lentes': n})."""
    bilan = {"refus": [], "sans_code": 0, "lentes": 0}
    if not urls:
        return [], bilan

    def lire(url):
        try:
            html, finale = _telecharger_page(url)
        except Exception as e:
            return ("refus", url, _motif_erreur(e))
        info = analyser_page(html, finale, code, nom)
        if info is None:
            return ("sans_code", url, "")
        info["url"], info["site"] = finale, _site(finale)
        return ("ok", url, info)

    ex = ThreadPoolExecutor(max_workers=8)
    futures = [ex.submit(lire, u) for u in urls]
    faits, _ = wait(futures, timeout=delai)
    ex.shutdown(wait=False, cancel_futures=True)
    bilan["lentes"] = len(futures) - len(faits)
    pages = []
    for f in futures:
        if f not in faits:
            continue
        try:
            etat, url, valeur = f.result()
        except Exception:
            continue
        if etat == "ok":
            pages.append(valeur)
        elif etat == "refus":
            bilan["refus"].append(f"{_site(url)} ({valeur})")
        else:
            bilan["sans_code"] += 1
    return pages, bilan


def _cle_url(u: str):
    p = urlparse(u)
    return (p.netloc.lower(), p.path.rstrip("/"))


def _url_examinable(u: str) -> bool:
    """Page web ordinaire, hors réseaux sociaux, moteurs de recherche et fichiers."""
    p = urlparse(u or "")
    if p.scheme not in ("http", "https") or not p.netloc:
        return False
    if any(d in p.netloc.lower() for d in DOMAINES_IGNORES):
        return False
    return not re.search(r"\.(pdf|jpe?g|png|webp|gif)$", p.path, re.I)


def _urls_de_recherche(requetes, code):
    """Pages trouvées par les moteurs de recherche (texte), sans doublon.
    Retourne (adresses dont le résultat cite déjà le code, autres adresses, erreurs lisibles)."""
    lots, erreurs = _recherches("text", requetes, region="fr-fr", max_results=15)
    motif = _motif_code(code)
    vus, avec, sans = set(), [], []
    for lot in lots:
        for r in lot:
            u = (r.get("href") or "").strip()
            if not _url_examinable(u) or _cle_url(u) in vus:
                continue
            vus.add(_cle_url(u))
            texte = " ".join([unquote(u), r.get("title") or "", r.get("body") or ""])
            (avec if motif.search(texte) else sans).append(u)
    return avec, sans, erreurs


def _pages_des_images(bruts, code, mots_nom=()):
    """Pages d'origine des résultats d'images ; celles dont le titre cite le code, puis le nom, en premier."""
    motif = _motif_code(code)
    vus, notees = set(), []
    for r in bruts:
        u = (r.get("url") or "").strip()
        if not _url_examinable(u) or _cle_url(u) in vus:
            continue
        vus.add(_cle_url(u))
        titre = " ".join([unquote(u), r.get("title") or ""])
        mots_titre = _mots_utiles(titre)
        note = 2 if motif.search(titre) else (1 if mots_nom and any(m in mots_titre for m in mots_nom) else 0)
        notees.append((note, len(notees), u))
    return [u for _, _, u in sorted(notees, key=lambda x: (-x[0], x[1]))]


def _empreinte(img: Image.Image):
    """Empreinte visuelle (24×24 niveaux de gris sur fond blanc carré) pour reconnaître une même photo
    trouvée sur plusieurs sites, quelle que soit sa taille."""
    cote = max(img.size)
    carre = Image.new("L", (cote, cote), 255)
    carre.paste(img.convert("L"), ((cote - img.width) // 2, (cote - img.height) // 2))
    return list(carre.resize((24, 24), Image.BILINEAR).getdata())


def _meme_image(a, b, seuil: float = 14.0) -> bool:
    """Deux empreintes proches (écart moyen sur 255 niveaux) : même photo."""
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a) <= seuil


def _evaluer_image(url, page):
    """Télécharge une image de page produit. Retourne un dict de candidat, ou None si elle ne convient pas."""
    try:
        octets = telecharger_octets(url, referer=page["url"], delai=(4, 12))
        img = Image.open(io.BytesIO(octets))
        img.load()
    except Exception:
        return None
    rgb = vers_rgb_blanc(img)
    if min(rgb.size) < MIN_COTE_VERIFIE or max(rgb.size) > 3 * min(rgb.size):
        return None  # trop petite, ou bandeau
    a = analyser_image(rgb)
    mini = rgb.copy()
    mini.thumbnail((320, 320))
    return {"miniature": mini, "image": url, "octets": octets if len(octets) <= 1_500_000 else None,
            "titre": page.get("nom", ""), "site": page["site"], "page": page["url"],
            "largeur": rgb.width, "hauteur": rgb.height, "blanc": a["blanc"], "studio": a["studio"],
            "verifie": page["niveau"] != "nom", "niveau": page["niveau"], "sites": {page["site"]},
            "_h": _empreinte(rogner_marges_blanches(rgb))}


def _candidats_de_pages(pages, maxi=30):
    """Images des pages confirmées : téléchargées, dédoublonnées (même photo sur plusieurs sites), classées."""
    demandes, vus = [], set()
    for p in sorted(pages, key=lambda p: -RANG_NIVEAU[p["niveau"]]):
        for u in p["images"][:4]:
            if (u, p["url"]) not in vus:
                vus.add((u, p["url"]))
                demandes.append((u, p))
    demandes = demandes[:maxi]
    if not demandes:
        return []
    ex = ThreadPoolExecutor(max_workers=8)
    futures = [ex.submit(_evaluer_image, u, p) for u, p in demandes]
    faits, _ = wait(futures, timeout=DELAI_ETAPE)
    ex.shutdown(wait=False, cancel_futures=True)
    candidats = []
    for f in futures:
        if f in faits:
            try:
                c = f.result()
            except Exception:
                c = None
            if c:
                candidats.append(c)
    candidats.sort(key=lambda c: -min(c["largeur"], c["hauteur"]))
    uniques = []
    for c in candidats:
        for u in uniques:
            if _meme_image(u["_h"], c["_h"]):
                u["sites"] |= c["sites"]
                if RANG_NIVEAU[c["niveau"]] > RANG_NIVEAU[u["niveau"]]:
                    u["niveau"] = c["niveau"]
                break
        else:
            uniques.append(c)
    for c in uniques:
        c["verifie"] = c["niveau"] != "nom"
        c["nb_sites"] = len(c["sites"])
        c["sites"] = sorted(c["sites"])
        resolution = min(1.0, min(c["largeur"], c["hauteur"]) / 800)
        confiance = {"fort": 25, "moyen": 15, "nom": 0}.get(c["niveau"], 0)
        c["score"] = (100 * c["blanc"] + 40 * resolution + confiance + 25 * min(1.0, (c["nb_sites"] - 1) / 2)
                      - (60 if c["niveau"] == "nom" else 0))
        del c["_h"]
    uniques.sort(key=lambda c: -c["score"])
    return uniques


def _marque_devinee(nom: str) -> str:
    """Marque en tête d'un nom de la forme « Marque - produit - détail » (1 à 3 mots, sans chiffre)."""
    m = re.match(r"^\s*([^-–—|:]{2,30}?)\s+[-–—|:]\s+\S", nom or "")
    if m and 1 <= len(m.group(1).split()) <= 3 and not re.search(r"\d", m.group(1)):
        return m.group(1).strip()
    return ""


def _nom_consensus(pages):
    """Nom et marque du produit les plus cohérents entre les pages (le nom le plus proche des autres)."""
    noms = [p["nom"] for p in pages if p.get("nom") and p["niveau"] != "nom"]
    if not noms:
        return "", ""
    ensembles = [set(_mots_utiles(n)) for n in noms]

    def proximite(i):
        return sum(len(ensembles[i] & ensembles[k]) / max(1, len(ensembles[i] | ensembles[k]))
                   for k in range(len(noms)) if k != i)

    meilleur = noms[max(range(len(noms)), key=lambda i: (proximite(i), -len(noms[i])))]
    marques = Counter(p["marque"] for p in pages if p.get("marque") and p["niveau"] != "nom")
    marque = marques.most_common(1)[0][0] if marques else _marque_devinee(meilleur)
    return meilleur, marque


MOTS_VIDES = {"de", "du", "des", "en", "et", "la", "le", "les", "au", "aux", "pour", "avec", "unite", "unité",
              "unités", "unites", "boite", "boîte", "x1"}


def _nom_pour_requete(nom: str, marque: str = "") -> str:
    """Nom raccourci pour une recherche : 6 mots significatifs, sans quantités ni séparateurs."""
    texte = re.sub(r"\b\d+(?:[.,]\d+)?\s?(?:ml|cl|l|mg|g|gr|kg|x)\b", " ", nom or "", flags=re.I)  # quantités
    mots = [m.strip(",;:()") for m in re.split(r"\s+[-–—|/]\s+|\s+", texte)]
    mots = [m for i, m in enumerate(mots)
            if m and not re.fullmatch(r"[\d.,]+", m) and (i == 0 or m.lower() not in MOTS_VIDES)]
    texte = " ".join(mots[:6])
    if marque and marque.lower() not in texte.lower():
        texte = f"{marque} {texte}"
    return texte


def rechercher_visuels(code: str, nom: str = "", requetes_extra=(), nombre: int = 12, progression=None):
    """Recherche web du visuel (voir _rechercher_visuels). En cas d'incident imprévu, l'ancienne recherche d'images
    (non vérifiée) prend le relais : l'écran n'est jamais bloqué."""
    try:
        return _rechercher_visuels(code, nom, requetes_extra, nombre, progression)
    except Exception as e:
        code = nettoyer_code(code)
        try:
            autres, msg = propositions_web([code] + ([f"{nom} {code}"] if nom else []), nom=nom, nombre=nombre)
        except Exception:
            autres, msg = [], ""
        return {"verifies": [], "autres": autres, "nom": "", "marque": "",
                "journal": [f"Recherche vérifiée interrompue ({type(e).__name__}) : images web non vérifiées."]
                + ([msg] if msg else [])}


BUDGET_RECHERCHE = 45  # secondes : passé ce délai, la recherche élargie par le nom n'est pas lancée


def _rechercher_visuels(code, nom="", requetes_extra=(), nombre=12, progression=None):
    """Cascade :
      1. moteurs de recherche (pages et images) interrogés avec le code, sous plusieurs formulations ;
         chaque page trouvée (y compris la page d'origine de chaque image) est ouverte et ne compte que si
         le code y figure ;
      2. si moins de 4 pages confirmées : recherche élargie par le nom (connu, saisi, ou relevé sur une
         page confirmée), les pages trouvées étant toujours vérifiées par le code ;
      3. à défaut de code retrouvé : pages au nom correspondant (« probables »), puis images non vérifiées."""
    debut = time.time()
    code = nettoyer_code(code)
    sortie = {"verifies": [], "autres": [], "nom": "", "marque": "", "journal": []}
    j = sortie["journal"]

    def etape(texte):
        if progression:
            try:
                progression(texte)
            except Exception:
                pass

    if len(code) not in (8, 12, 13):
        j.append(f"Code invalide ({len(code)} chiffres) : recherche web impossible.")
        return sortie
    extras = [x.strip() for x in requetes_extra if x and x.strip()]
    nom = (nom or "").strip()
    nom_ref = nom or (extras[0] if extras else "")
    deja, pages, tous_bruts = set(), [], []
    compte = {"pages": 0}

    def resume(pages_, bilan_, titre):
        avec_code_ = [p for p in pages_ if p["niveau"] != "nom"]
        par_nom_ = [p for p in pages_ if p["niveau"] == "nom"]
        if avec_code_:
            sites = ", ".join(sorted({p["site"] for p in avec_code_}))
            j.append(f"{titre} : {len(avec_code_)} pages contiennent le code ({sites}).")
        else:
            j.append(f"{titre} : aucune page ne contient le code.")
        if par_nom_:
            j.append(f"{titre} : {len(par_nom_)} pages sans le code mais au nom correspondant (à vérifier).")
        if bilan_["refus"]:
            j.append("Pages inaccessibles : " + ", ".join(bilan_["refus"][:6])
                     + (" …" if len(bilan_["refus"]) > 6 else "") + ".")
        if bilan_["sans_code"] or bilan_["lentes"]:
            j.append(f"Pages écartées : {bilan_['sans_code']} sans le code, {bilan_['lentes']} trop lentes.")

    def chercher(req_texte, req_images, titre):
        etape(f"{titre} : interrogation des moteurs de recherche…")
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_texte = ex.submit(_urls_de_recherche, req_texte, code)
            f_images = ex.submit(images_web, req_images)
            avec, sans, err_t = f_texte.result()
            bruts, err_i = f_images.result()
        tous_bruts.extend(bruts)
        erreurs = err_t + err_i
        j.append(f"{titre} : {len(set(req_texte)) + len(set(req_images))} recherches · {len(avec)} pages citant le "
                 f"code, {len(sans)} autres pages, {len(bruts)} images"
                 + (f" · incidents : {' ; '.join(erreurs[:3])}" if erreurs else "") + ".")
        return avec, sans, bruts

    def examiner(urls, titre, nom_verif):
        nouvelles = []
        for u in urls:
            if _cle_url(u) not in deja and len(nouvelles) < MAX_PAGES:
                deja.add(_cle_url(u))
                nouvelles.append(u)
        if not nouvelles:
            j.append(f"{titre} : aucune nouvelle page à examiner.")
            return []
        etape(f"{titre} : vérification de {len(nouvelles)} pages…")
        trouvees, bilan = _verifier_pages(nouvelles, code, nom_verif)
        compte["pages"] += len(nouvelles)
        resume(trouvees, bilan, titre)
        return trouvees

    # --- 1. le code
    req_texte = [code, f'"{code}"', f"code EAN {code}"]
    req_images = [code, f"{code} pharmacie"]
    if nom:
        req_texte.append(f"{_nom_pour_requete(nom)} {code}")
        req_images.append(f"{_nom_pour_requete(nom)} {code}")
    for x in extras:
        req_texte.append(f"{x} {code}")
        req_images += [f"{x} {code}", x]
    avec, sans, bruts = chercher(req_texte, req_images, "Étape 1 (code)")
    pages += examiner(avec + _pages_des_images(bruts, code, _mots_utiles(nom_ref)) + sans[:6],
                      "Étape 1 (code)", nom_ref)

    # --- 2. recherche élargie par le nom, pages toujours vérifiées par le code
    nom_trouve, marque_trouvee = _nom_consensus(pages)
    nom_rebond = nom_ref or nom_trouve
    if len([p for p in pages if p["niveau"] != "nom"]) < 4 and len(_mots_utiles(nom_rebond)) >= 2:
        if time.time() - debut > BUDGET_RECHERCHE:
            j.append("Étape 2 non lancée (recherche déjà longue) : « Affiner la recherche » permet de la relancer.")
        else:
            court = _nom_pour_requete(nom_rebond, marque_trouvee)
            avec2, sans2, bruts2 = chercher([court, f"{court} pharmacie"], [court, f"{court} parapharmacie"],
                                            f"Étape 2 (nom « {court[:45]} »)")
            pages += examiner(avec2 + _pages_des_images(bruts2, code, _mots_utiles(court)) + sans2,
                              "Étape 2 (nom)", nom_rebond)

    # --- 3. photos, nom, et à défaut images non vérifiées
    etape("Téléchargement des photos…")
    candidats = _candidats_de_pages(pages)
    confirmes = [c for c in candidats if c["niveau"] != "nom"]
    probables = [c for c in candidats if c["niveau"] == "nom"]
    sortie["verifies"] = (confirmes or probables)[:nombre]  # les « probables » ne servent qu'à défaut
    sortie["nom"], sortie["marque"] = _nom_consensus(pages)
    if not confirmes and tous_bruts:
        etape("Préparation des images non vérifiées…")
        sortie["autres"], _ = propositions_web([], nom=nom_ref or nom_trouve, nombre=nombre, bruts=tous_bruts)
    j.append(f"Résultat : {len(confirmes)} photo(s) confirmée(s) par le code, {len(probables)} probable(s)"
             + (" (non retenues, des photos confirmées existent)" if confirmes and probables else "")
             + f", {compte['pages']} pages examinées, {time.time() - debut:.0f} s.")
    return sortie
