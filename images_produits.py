"""Recherche du visuel et du nom d'un produit à partir de son code CIP13 / EAN13.

Ordre de recherche :
  1. dossier local  images/<code>.(png|jpg|jpeg|webp)   (images déposées à la main ; l'outil n'y écrit rien)
  2. visuel d'une affiche déjà enregistrée pour ce code (historique)
  3. Open Beauty Facts, Open Food Facts, Open Products Facts (bases collaboratives)
  4. Recherche web VÉRIFIÉE (rechercher_visuels) : des moteurs de recherche trouvent des pages qui citent le code ;
     chaque page est ouverte et ne compte que si le code y figure réellement. Aucune liste de sites n'est figée :
     un site qui change d'adresse est retrouvé par le moteur, une page qui ne répond plus est ignorée.
     À défaut : pages dont le nom du produit correspond (« probable »), puis images web dont le titre cite le code
     ou correspond au nom du produit (jamais d'images sans rapport avec lui).
  5. Import ou collage manuel d'une image (collage depuis le presse-papiers, glisser-déposer, fichier).
Stockage : aucune image trouvée, choisie ou collée n'est conservée sur le disque. Seuls les visuels des affiches
enregistrées le sont, dans l'historique (voir historique.py) ; supprimer l'affiche supprime ses visuels.
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
from urllib.parse import unquote, urljoin, urlparse

import requests
from PIL import Image, ImageOps

from chemins import DONNEES

DOSSIER_IMAGES = DONNEES / "images"  # lu seulement (images déposées à la main)

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
MIN_COTE_VERIFIE = 300            # produit plus petit (grand côté, en pixels, marges retirées) : non proposé
DELAI_AGRANDIR = 10               # secondes accordées à la recherche des versions en grand des meilleures photos
DOMAINES_IGNORES = ("google.", "facebook.", "instagram.", "pinterest.", "youtube.", "youtu.be", "tiktok.",
                    "twitter.", "x.com", "linkedin.", "reddit.", "amazon.", "ebay.", "aliexpress.", "leboncoin.",
                    "wikipedia.", "duckduckgo.", "bing.com")
RANG_NIVEAU = {"nom": 0, "moyen": 1, "fort": 2}
IMAGE_A_IGNORER = re.compile(r"logo|sprite|favicon|placeholder|no-?image|spacer|\.svg|\.gif(\?|$)", re.I)
CODES_REFUS = (401, 403, 406, 429, 451, 503)  # réponses d'un site qui refuse les programmes : 2e essai « en navigateur »


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


def visuel_historique(code: str):
    """Visuel principal de l'affiche enregistrée la plus récente pour ce code (déjà traité : nettoyé, net).
    Retourne (image ou None, date de l'affiche JJ/MM/AAAA). L'image porte img.info["origine"] = "historique"."""
    try:
        import historique
        for e in historique.lister():  # les plus récentes d'abord
            if nettoyer_code(e.get("code") or "") == code and e.get("a_visuel"):
                img = historique.visuel(e["id"])
                if img is not None:
                    img.info["origine"] = "historique"
                    return img, historique.date_creation(e)
    except Exception:
        pass
    return None, ""


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


class _ErreurHTTP(Exception):
    """Réponse d'erreur d'un site (code HTTP), pour le journal de la recherche."""

    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code_http = code


def _refus_du_site(e: Exception) -> bool:
    """Le site a refusé la requête (ou coupé la connexion) : un 2e essai avec l'empreinte d'un vrai navigateur peut passer."""
    if isinstance(e, requests.exceptions.HTTPError):
        return getattr(e.response, "status_code", 0) in CODES_REFUS
    return isinstance(e, (requests.exceptions.ConnectionError, requests.exceptions.SSLError))


def _get_navigateur(url: str, entetes: dict, delai):
    """GET avec l'empreinte réseau d'un navigateur (module primp, installé avec ddgs) : beaucoup de sites refusent
    (HTTP 403) les programmes ordinaires mais acceptent un navigateur. Retourne la réponse primp (code < 400)."""
    import primp
    r = primp.Client(timeout=max(delai) if isinstance(delai, tuple) else delai, impersonate="random",
                     impersonate_os="random").get(url, headers={k: v for k, v in entetes.items()
                                                                if k.lower() != "user-agent"})
    if r.status_code >= 400:
        raise _ErreurHTTP(r.status_code)
    return r


def telecharger_octets(url: str, referer: str = "", max_octets: int = MAX_IMAGE_OCTETS, delai=(5, 20)) -> bytes:
    """Télécharge un fichier image (taille limitée). Lève une exception en cas d'échec."""
    if not _url_publique(url):
        raise ValueError("adresse non autorisée")
    entetes = dict(NAVIGATEUR)
    entetes["Accept"] = "image/png,image/jpeg,image/webp,image/*;q=0.8,*/*;q=0.5"
    if referer:
        entetes["Referer"] = referer
    try:
        with requests.get(url, headers=entetes, timeout=delai, stream=True) as r:
            r.raise_for_status()
            octets = bytearray()
            for bloc in r.iter_content(65536):
                octets += bloc
                if len(octets) > max_octets:
                    raise ValueError("image trop volumineuse")
        return bytes(octets)
    except Exception as e:
        if not _refus_du_site(e):
            raise
        octets = _get_navigateur(url, entetes, delai).content  # le site refusait le programme : 2e essai en navigateur
        if len(octets) > max_octets:
            raise ValueError("image trop volumineuse")
        return bytes(octets)


COTE_UTILE = 2600  # au-delà (pixels), l'image est réduite dès sa lecture : assez pour imprimer net, sans surcharger la mémoire


def _ouvrir(octets: bytes) -> Image.Image:
    """Image lue depuis des octets ; une très grande photo (3000 px et plus) est décodée directement en plus petit."""
    img = Image.open(io.BytesIO(octets))
    if max(img.size) > COTE_UTILE:
        try:
            img.draft("RGB", (COTE_UTILE, COTE_UTILE))  # JPEG : décodage réduit, bien plus léger en mémoire
        except Exception:
            pass
    img.load()
    if max(img.size) > COTE_UTILE:
        img.thumbnail((COTE_UTILE, COTE_UTILE), Image.LANCZOS)
    return img


def image_depuis_octets(octets: bytes) -> Image.Image:
    img = _ouvrir(octets)
    try:
        img = ImageOps.exif_transpose(img)  # photo prise « couchée » : remise à l'endroit
    except Exception:
        pass
    return vers_rgb_blanc(img)


def telecharger_image(url: str, referer: str = "") -> Image.Image:
    return image_depuis_octets(telecharger_octets(url, referer=referer))


def obtenir_image(candidat: dict) -> Image.Image:
    """Image complète d'une proposition : celle déjà reçue pendant la recherche, sinon téléchargée (dans sa plus grande
    version disponible : voir version_grande)."""
    if candidat.get("octets"):
        return image_depuis_octets(candidat["octets"])
    img = telecharger_image(candidat["image"], referer=candidat.get("page", ""))
    try:
        mieux = version_grande(candidat["image"], img, referer=candidat.get("page", ""))
        if mieux:
            img = image_depuis_octets(mieux["octets"])
    except Exception:
        pass
    return img


# --------------------------------------------------------------------------- Photo en grand et netteté réelle
# Les sites marchands montrent souvent une miniature (250 à 400 px) alors que la photo d'origine (1 000 à 3 000 px)
# est à la même adresse, sans les paramètres de taille, ou sous un autre nom de format. Relevé sur de vrais sites
# (10/10/2026) : site de marque 400 → 3 000 px en retirant « ?…&height=400 » ; Magento 265 → 1 000 px en retirant
# « ?width=265… » ; PrestaShop « home_default » 250 px / « product_main_2x » 1 440 px ; « /img/product/400/ » →
# « /800/ ». Attention : certains sites agrandissent sur demande (« /resize/2000x2000/ » d'une photo de 800 px) :
# une version n'est gardée que si elle montre la même photo ET qu'elle est réellement plus détaillée.
_PARAMS_TAILLE = {"w", "width", "h", "height", "sw", "sh", "wid", "hei", "size", "resize", "fit", "canvas", "t", "dpr",
                  "quality", "q", "qlt", "optimize", "bg-color", "bg", "crop", "fm", "fmt", "auto", "mode", "scale",
                  "maxwidth", "maxheight", "max-w", "max-h", "imwidth", "imheight", "resmode", "op_sharpen", "rect",
                  "im", "impolicy", "format", "trim", "pad", "dw", "dh", "ws", "hs", "s"}
_EXT = r"(?=\.(?:jpe?g|png|webp)$)"
_FORMATS_PRESTASHOP = ("product_main_2x", "large_default", "thickbox_default")
MAX_VARIANTES = 5
GAIN_MINI = 1.6  # une version n'est gardée que si ses détails réels sont au moins 1,6 fois plus fins (voir plus bas)


def variantes_grandes(url: str) -> list:
    """Adresses où la même photo existe peut-être en plus grand (de la plus probable à la moins probable)."""
    try:
        p = urlparse(url)
    except ValueError:
        return []
    chemin, requete = p.path, p.query
    base = f"{p.scheme}://{p.netloc}"
    res = []

    def ajouter(ch, req=""):
        u = base + ch + (("?" + req) if req else "")
        if u != url and u not in res:
            res.append(u)

    # 1. paramètres de taille dans l'adresse (?width=265&height=265…) : sans eux, la photo d'origine
    if requete:
        from urllib.parse import parse_qsl, urlencode
        params = parse_qsl(requete, keep_blank_values=True)
        utiles = [(k, v) for k, v in params if k.lower() not in _PARAMS_TAILLE]
        if len(utiles) < len(params):
            if utiles:
                ajouter(chemin, urlencode(utiles))
            ajouter(chemin)
    # 2. formats nommés dans le chemin
    hote = p.netloc.lower()
    if re.search(r"open(beauty|food|products|petfood)facts", hote):  # Open Facts : « .400.jpg » → « .full.jpg »
        if re.search(r"\.\d{2,3}\.jpg$", chemin):
            ajouter(re.sub(r"\.\d{2,3}\.jpg$", ".full.jpg", chemin))
    if re.search(r"-\d{2,4}x\d{2,4}" + _EXT, chemin, re.I):  # WordPress : « -300x300.jpg »
        ajouter(re.sub(r"-\d{2,4}x\d{2,4}" + _EXT, "", chemin, flags=re.I))
    if "/cdn/shop/" in chemin or "shopify" in hote:  # Shopify : « _600x.jpg », « _grande.jpg »
        nouveau = re.sub(r"_(?:\d{2,4}x\d{0,4}|x\d{2,4}|pico|icon|thumb|small|compact|medium|large|grande)(?:@\dx)?"
                         + _EXT, "", chemin, flags=re.I)
        ajouter(nouveau)
    if "/media/catalog/product/cache/" in chemin:  # Magento : copie réduite en cache → photo d'origine
        ajouter(re.sub(r"/media/catalog/product/cache/(?:.*?/)?[0-9a-f]{32}/", "/media/catalog/product/", chemin))
    if re.search(r"/resize/\d+x\d+/", chemin):  # « /resize/800x800/media/… » → « /media/… »
        ajouter(re.sub(r"/resize/\d+x\d+/", "/", chemin, count=1))
    if "/upload/" in chemin and re.search(r"/upload/(?:[a-z]{1,2}_[^/]+)/", chemin):  # Cloudinary : transformations
        ajouter(re.sub(r"/upload/(?:[a-z]{1,2}_[^/]+/)+", "/upload/", chemin, count=1))
    m = re.search(r"/(\d+)-([a-z][a-z0-9_]*)/([^/]+)$", chemin)  # PrestaShop : « /34110-home_default/nom.jpg »
    if m and re.search(r"default|main|home|small|medium|cart|thumb|large", m.group(2)):
        for fmt in _FORMATS_PRESTASHOP:
            if fmt != m.group(2):
                ajouter(chemin[:m.start()] + f"/{m.group(1)}-{fmt}/{m.group(3)}")
    m = re.search(r"/(?:products?|img|images?|thumbs?|photos?|visuels?)/(\d{2,3})/", chemin, re.I)  # « /img/product/400/ »
    if m and 60 <= int(m.group(1)) <= 700 and "facts" not in hote:  # (Open Facts : chiffres du code, pas une taille)
        ajouter(chemin[:m.start(1)] + str(int(m.group(1)) * 2) + chemin[m.end(1):], requete)
    return res[:MAX_VARIANTES]


def resolution_effective(img: Image.Image) -> int:
    """Taille réelle des détails d'une image (grand côté, en pixels). Une miniature agrandie (photo de 300 px servie
    en 1 200 px) est floue : sa taille affichée est grande, mais sa résolution effective reste d'environ 300 px.
    Méthode : on réduit l'image (facteur f) puis on la ré-agrandit, et on compare ce qui se perd à ce qui se perd
    avec une réduction deux fois plus forte. Dans une image nette, une petite réduction fait déjà perdre une part
    notable des détails (rapport de 0,27 à 0,31 relevé sur des images nettes) ; dans une image agrandie, presque rien
    (0,11 à 0,15) tant que f reste au-dessus de sa vraie taille. Étalonné le 10/10/2026 sur des images de synthèse
    nettes, floues et agrandies ×2 à ×4 : images nettes reconnues comme telles ; agrandies, vraie taille retrouvée à
    environ 25 % près (jusqu'à 2,5 fois plus pour un dessin très simple agrandi ×7) : assez pour distinguer une
    photo d'origine d'une miniature agrandie."""
    from PIL import ImageChops, ImageStat
    g = img.convert("L")
    grand = max(g.size)
    if grand < 32:
        return grand
    if grand > 1000:
        g = g.resize((max(1, round(g.width * 1000 / grand)), max(1, round(g.height * 1000 / grand))), Image.BILINEAR)
    w, h = g.size

    def perte(f):
        petit = g.resize((max(2, round(w * f)), max(2, round(h * f))), Image.LANCZOS)
        return ImageStat.Stat(ImageChops.difference(g, petit.resize((w, h), Image.BICUBIC))).mean[0]

    if perte(0.1) < 0.5:
        return round(grand * 0.1)  # image presque unie : aucun détail à mesurer
    for f in (0.9, 0.8, 0.7, 0.6, 0.5, 0.42, 0.35, 0.3, 0.25, 0.2):
        if perte(f) >= SEUIL_DETAIL * perte(f / 2):
            return grand if f >= 0.9 else round(grand * f)  # premier niveau où la réduction se voit : vraie taille
    return round(grand * 0.2)


SEUIL_DETAIL = 0.25  # voir resolution_effective


def version_grande(url: str, img: Image.Image, referer: str = "", delai=(3, 8)):
    """Cherche une version plus grande et réellement plus détaillée de la même photo (voir variantes_grandes).
    Retourne {'url', 'octets', 'img'} (img : produit, marges blanches retirées), ou None."""
    actuel = rogner_marges_blanches(vers_rgb_blanc(img))
    if max(actuel.size) >= 1800:
        return None  # déjà grande
    eff_ref = resolution_effective(actuel)
    emp_ref, coul_ref = _empreinte(actuel), _empreinte_couleur(actuel)
    meilleur = None
    for v in variantes_grandes(url):
        try:
            octets = telecharger_octets(v, referer=referer, delai=delai)
            produit = rogner_marges_blanches(vers_rgb_blanc(_ouvrir(octets)))
        except Exception:
            continue
        if (max(produit.size) < max(actuel.size) * 1.2 or not _meme_image(emp_ref, _empreinte(produit))
                or not _meme_image(coul_ref, _empreinte_couleur(produit), 10.0)):
            continue  # pas la même photo (autre produit, autre vue, autre teinte)
        eff = resolution_effective(produit)
        if eff >= eff_ref * GAIN_MINI and (meilleur is None or eff > meilleur["eff"]):
            meilleur = {"url": v, "octets": octets, "img": produit, "eff": eff}
            if eff >= 1500:
                break
    return meilleur


def _url_de_collage(url: str) -> str:
    """Adresse collée : un lien « Google Images » (…/imgres?imgurl=…) donne l'adresse de la photo elle-même."""
    try:
        p = urlparse(url)
        if "google." in p.netloc and p.path.startswith("/imgres"):
            from urllib.parse import parse_qs
            vraie = (parse_qs(p.query).get("imgurl") or [""])[0]
            if vraie:
                return vraie
    except ValueError:
        pass
    return url


def image_depuis_adresse(url: str) -> Image.Image:
    """Image depuis une adresse collée : adresse d'une image, lien Google Images, ou adresse d'une page produit
    (la photo principale de la page est prise). La plus grande version disponible est gardée."""
    url = _url_de_collage(url.strip())
    if not _url_publique(url):
        raise ValueError("adresse non autorisée")
    try:
        octets = telecharger_octets(url)
        img = image_depuis_octets(octets)
    except Exception as e:
        # pas une image : peut-être la page d'un produit (on prend sa photo principale)
        try:
            html, url = _telecharger_page(url)
        except Exception:
            raise e
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
        adresses = []
        for n in noeuds:
            if _est_produit(n):
                adresses += _images_ld(n.get("image"))
        adresses += [c for k, c in lecteur.metas if k in ("og:image", "og:image:secure_url", "twitter:image")]
        adresses += lecteur.images_liees
        for a in adresses:
            a = urljoin(url, (a or "").strip())
            if not a or IMAGE_A_IGNORER.search(a):
                continue
            try:
                img = telecharger_image(a, referer=url)
                url = a
                break
            except Exception:
                continue
        else:
            raise ValueError("aucune photo trouvée à cette adresse")
    try:
        mieux = version_grande(url, img)
        if mieux:
            img = image_depuis_octets(mieux["octets"])
    except Exception:
        pass
    return img


def image_depuis_collage(valeur: dict) -> Image.Image:
    """Image reçue du composant « coller une image » : fichier (données base64), adresse d'une image, lien Google Images
    ou adresse d'une page produit."""
    if not isinstance(valeur, dict):
        raise ValueError("rien reçu")
    donnees = valeur.get("donnees")
    if donnees:
        if donnees.startswith("data:") and "," in donnees[:200]:
            donnees = donnees.split(",", 1)[1]
        octets = base64.b64decode(donnees)
        if len(octets) > MAX_IMAGE_OCTETS:
            raise ValueError("image trop volumineuse")
        img = image_depuis_octets(octets)
        source = str(valeur.get("source") or "").strip()  # adresse d'origine de l'image glissée, si le navigateur la donne
        if source and _url_publique(source):
            try:
                mieux = version_grande(source, img)
                if mieux:
                    img = image_depuis_octets(mieux["octets"])
            except Exception:
                pass
        return img
    if valeur.get("url"):
        return image_depuis_adresse(str(valeur["url"]))
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
        journal.append("Visuel déposé dans le dossier images.")
    else:
        img, date_affiche = visuel_historique(code)
        if img is not None:
            journal.append(f"Visuel repris de l'affiche enregistrée le {date_affiche} (historique).")
    marque, detail = "", ""

    def interroger(modele):  # les trois bases sont interrogées en même temps (avant : l'une après l'autre)
        try:
            return requests.get(modele.format(code=code), params={"fields": CHAMPS}, headers=HEADERS, timeout=(4, 10))
        except Exception as e:
            return e

    with ThreadPoolExecutor(max_workers=len(SOURCES)) as ex:
        reponses = list(ex.map(interroger, [modele for _, modele in SOURCES]))
    for (source, _modele), r in zip(SOURCES, reponses):
        if isinstance(r, Exception):
            e = r
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
            img = telecharger_image(url)
            try:  # la base donne une version réduite (400 px) : la photo d'origine est demandée
                mieux = version_grande(url, img)
                if mieux:
                    img = image_depuis_octets(mieux["octets"])
            except Exception:
                pass
            img = rogner_marges_blanches(img)
            journal.append(f"{source} : visuel obtenu ({max(img.size)} px).")
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
def _detail_erreur(e: Exception) -> str:
    """Nature d'un incident, lisible dans le journal : type d'erreur et début du message."""
    texte = " ".join(str(e).split())
    return type(e).__name__ + (f" ({texte[:90]})" if texte else "")


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


def _texte_bing(requete):
    """Recherche de pages par Bing, directement (moteur fourni avec ddgs mais désactivé par défaut) : repli quand
    les moteurs de texte de ddgs échouent tous (adresses de l'hébergeur bloquées, par exemple)."""
    from ddgs.engines.bing import Bing
    res = Bing(timeout=8).search(requete, region="fr-fr")
    if not res:
        raise ValueError("aucun résultat ou réponse refusée")
    return [{"title": r.title, "href": r.href, "body": r.body} for r in res]


def _recherches(methode, requetes, **options):
    """Plusieurs recherches, 3 à la fois. Retourne (liste de résultats par requête, incidents lisibles).
    Recherche de pages (« text ») : si les moteurs de ddgs échouent, Bing est interrogé directement ; dès qu'ils
    échouent pour une autre raison qu'une absence de résultat, ils ne sont plus sollicités pour les requêtes suivantes."""
    try:
        importlib.import_module("ddgs")
    except Exception:
        return [], ["module de recherche web non installé (ddgs)"]
    erreurs = []
    etat = {"ddgs_ko": False, "bing": 0}

    def une(req):
        cause = ""
        if not (methode == "text" and etat["ddgs_ko"]):
            try:
                return _ddgs(methode, req, **options)
            except Exception as e:
                cause = "ddgs " + _detail_erreur(e)
                if methode == "text" and "No results" not in str(e):
                    etat["ddgs_ko"] = True
        if methode == "text":
            try:
                res = _texte_bing(req)
                etat["bing"] += 1
                return res
            except Exception as e:
                cause = (cause + " ; " if cause else "") + "Bing " + _detail_erreur(e)
        erreurs.append(f"« {req[:30]} » — {cause}")
        return []

    with ThreadPoolExecutor(max_workers=3) as ex:
        lots = list(ex.map(une, list(dict.fromkeys(r for r in requetes if r.strip()))))
    if etat["bing"]:
        erreurs.append(f"moteurs ddgs indisponibles, {etat['bing']} recherche(s) de pages faite(s) par Bing")
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


# --------------------------------------------------------------------------- Images du web (code non vérifié sur la page)
ACCORD_TITRE_MIN = 0.66  # part des mots du nom du produit à retrouver dans le titre d'une image (2 mots au moins)


def _texte_resultat(r: dict) -> str:
    """Ce qu'un résultat d'images dit de lui-même : titre, adresse de la page, adresse de l'image."""
    return " ".join([r.get("title") or "", unquote(r.get("url") or ""), unquote(r.get("image") or "")])


def propositions_web(requetes, nom: str = "", nombre: int = 8, taille_min: int = 350, bruts=None, code: str = ""):
    """Propositions d'images issues d'une recherche d'images web. Une image n'est gardée que si elle a un rapport
    avec le produit :
      niveau « indice » : le code figure dans son titre ou dans l'adresse de son image ou de sa page ;
      niveau « titre »  : son titre contient les mots du nom du produit (2 mots au moins, 2/3 d'entre eux).
    Toutes les autres, sans rapport avec le produit (une recherche sur un simple numéro en renvoie beaucoup),
    sont écartées. Le code n'est PAS vérifié sur la page : à valider visuellement par l'utilisateur.
    Classement : indice d'abord, puis ressemblance à un visuel de site marchand (fond blanc uni, haute résolution,
    titre proche du nom).
    bruts : résultats d'une recherche d'images déjà faite (sinon la recherche est lancée avec requetes).
    Retourne (liste de dicts, message si la liste est vide)."""
    erreur = ""
    if bruts is None:
        if isinstance(requetes, str):
            requetes = [requetes]
        bruts, erreurs = images_web(requetes)
        if erreurs and not bruts:
            erreur = f"Recherche web indisponible ({erreurs[0]})."
    motif = _motif_code(nettoyer_code(code)) if nettoyer_code(code) else None
    mots = sorted(set(_mots_utiles(nom)))
    vus, candidats, ecartees = set(), [], 0
    for r in bruts:
        url = r.get("image")
        if not url or url in vus:
            continue
        vus.add(url)
        texte = _texte_resultat(r)
        cite_code = bool(motif and motif.search(texte))
        accord = 0.0
        if len(mots) >= 2:
            presents = set(_mots_utiles(texte))
            accord = sum(1 for m in mots if m in presents) / len(mots)
        if not cite_code and accord < ACCORD_TITRE_MIN:
            ecartees += 1
            continue
        try:
            w, h = int(r.get("width") or 0), int(r.get("height") or 0)
        except (TypeError, ValueError):
            w = h = 0
        if w and h and min(w, h) < taille_min:
            continue
        candidats.append({"miniature": r.get("thumbnail") or url, "image": url, "titre": r.get("title", ""),
                          "site": _site(r.get("url") or url), "page": r.get("url") or "",
                          "largeur": w, "hauteur": h, "verifie": False,
                          "niveau": "indice" if cite_code else "titre", "accord": accord})
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

    if candidats:
        with ThreadPoolExecutor(max_workers=8) as ex:
            candidats = list(ex.map(evaluer, candidats))
    for c in candidats:
        resolution = min(1.0, min(c["largeur"] or 400, c["hauteur"] or 400) / 800)
        c["score"] = 100 * c["blanc"] + 40 * resolution + 30 * c["accord"] + (60 if c["niveau"] == "indice" else 0)
    candidats.sort(key=lambda c: -c["score"])
    sortie = candidats[:nombre]
    if sortie:
        return sortie, ""
    if erreur:
        return [], erreur
    if ecartees:
        return [], (f"{ecartees} images trouvées, toutes sans rapport avec le produit (ni le code ni le nom dans le "
                    "titre) : écartées.")
    return [], "Aucune proposition suffisamment grande trouvée."


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


def _page_requests(url: str):
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


def _page_navigateur(url: str):
    """2e essai d'une page refusée : requête avec l'empreinte d'un navigateur (voir _get_navigateur)."""
    entetes = dict(NAVIGATEUR)
    entetes["Accept"] = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"
    r = _get_navigateur(url, entetes, 8)
    finale = str(getattr(r, "url", "") or url)
    if not _url_publique(finale):
        raise ValueError("adresse non autorisée")
    type_ = str((getattr(r, "headers", None) or {}).get("content-type", "")).lower()
    if type_ and "html" not in type_ and "xml" not in type_:
        raise ValueError("ce n'est pas une page web")
    return r.text[:MAX_PAGE_OCTETS], finale


def _telecharger_page(url: str, trace=None):
    """Télécharge une page web (taille limitée). Retourne (texte, adresse finale).
    Un site qui refuse le programme (HTTP 403, connexion coupée…) est réessayé « en navigateur » ; trace (liste)
    reçoit « navigateur » quand ce 2e essai a réussi."""
    if not _url_publique(url):
        raise ValueError("adresse non autorisée")
    try:
        return _page_requests(url)
    except Exception as e:
        if not _refus_du_site(e):
            raise
        resultat = _page_navigateur(url)
        if trace is not None:
            trace.append("navigateur")
        return resultat


def _motif_erreur(e: Exception) -> str:
    code = getattr(e, "code_http", None)
    if code:
        return f"HTTP {code} même en navigateur"
    reponse = getattr(e, "response", None)
    if reponse is not None:
        return f"HTTP {reponse.status_code}"
    return type(e).__name__


def _verifier_pages(urls, code, nom="", delai=DELAI_ETAPE):
    """Ouvre les pages en parallèle et garde celles qui parlent du produit.
    Retourne (pages confirmées : liste de dicts, bilan : {'refus': [...], 'sans_code': n, 'lentes': n,
    'navigateur': pages obtenues seulement au 2e essai « en navigateur »})."""
    bilan = {"refus": [], "sans_code": 0, "lentes": 0, "navigateur": 0}
    if not urls:
        return [], bilan

    def lire(url):
        trace = []
        try:
            html, finale = _telecharger_page(url, trace)
        except Exception as e:
            return ("refus", url, _motif_erreur(e))
        if trace:
            bilan["navigateur"] += 1
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
    Retourne (adresses dont le résultat cite déjà le code, autres adresses, incidents lisibles,
    titres des résultats qui citent le code : le nom du produit s'y lit même si la page ne s'ouvre pas)."""
    lots, erreurs = _recherches("text", requetes, region="fr-fr", max_results=15)
    motif = _motif_code(code)
    vus, avec, sans, titres = set(), [], [], []
    for lot in lots:
        for r in lot:
            u = (r.get("href") or "").strip()
            if not _url_examinable(u) or _cle_url(u) in vus:
                continue
            vus.add(_cle_url(u))
            texte = " ".join([unquote(u), r.get("title") or "", r.get("body") or ""])
            if motif.search(texte):
                avec.append(u)
                if r.get("title"):
                    titres.append(r["title"])
            else:
                sans.append(u)
    return avec, sans, erreurs, titres


SEPARATEURS_TITRE = re.compile(r"\s+[|–—:•»]\s+|\s+-\s+")


def _nom_des_titres(titres, code):
    """Nom et marque du produit lus dans les titres de résultats de recherche qui citent le code
    (« Avène Cleanance Gel nettoyant 400 ml - Pharmacie X » : on garde la partie qui ressemble le plus aux autres)."""
    motif = _motif_code(code)
    noms = []
    for t in titres:
        for segment in SEPARATEURS_TITRE.split(t or ""):
            segment = " ".join(motif.sub("", segment).split()).strip(" -–—:|,;")
            if len(_mots_utiles(segment)) >= 2 and 8 <= len(segment) <= 100:
                noms.append(segment)
                break
    return _nom_consensus([{"nom": n, "marque": "", "niveau": "moyen"} for n in noms])


def _pages_des_images(bruts, code, mots_nom=(), hors_sujet=6):
    """Pages d'origine des résultats d'images ; celles dont le titre cite le code, puis le nom, en premier.
    Celles dont ni le titre ni l'adresse ne citent le code ou le nom (résultats sans rapport, fréquents quand la
    recherche porte sur un simple numéro) ne sont gardées qu'au nombre de hors_sujet."""
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
    classees = sorted(notees, key=lambda x: (-x[0], x[1]))
    pertinentes = [u for n, _, u in classees if n > 0]
    return pertinentes + [u for n, _, u in classees if n == 0][:hors_sujet]


def _empreinte(img: Image.Image):
    """Empreinte visuelle (24×24 niveaux de gris sur fond blanc carré) pour reconnaître une même photo
    trouvée sur plusieurs sites, quelle que soit sa taille."""
    cote = max(img.size)
    carre = Image.new("L", (cote, cote), 255)
    carre.paste(img.convert("L"), ((cote - img.width) // 2, (cote - img.height) // 2))
    return list(carre.resize((24, 24), Image.BILINEAR).getdata())


def _empreinte_couleur(img: Image.Image):
    """Empreinte en couleur (12×12, rouge, vert, bleu) : distingue deux produits de même forme mais de teinte
    différente (même flacon, autre gamme), que l'empreinte en niveaux de gris peut confondre."""
    cote = max(img.size)
    carre = Image.new("RGB", (cote, cote), (255, 255, 255))
    carre.paste(img.convert("RGB"), ((cote - img.width) // 2, (cote - img.height) // 2))
    return [v for px in carre.resize((12, 12), Image.BILINEAR).getdata() for v in px]


def _meme_image(a, b, seuil: float = 14.0) -> bool:
    """Deux empreintes proches (écart moyen sur 255 niveaux) : même photo."""
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a) <= seuil


def _evaluer_image(url, page, rang=0):
    """Télécharge une image de page produit. Retourne un dict de candidat, ou None si elle ne convient pas.
    rang : place de l'image dans la page (0 = photo principale du produit).
    Les dimensions retenues sont celles du produit lui-même, marges blanches retirées (comme à l'impression)."""
    try:
        octets = telecharger_octets(url, referer=page["url"], delai=(4, 12))
        img = _ouvrir(octets)
    except Exception:
        return None
    rgb = vers_rgb_blanc(img)
    if max(rgb.size) > 3 * min(rgb.size):
        return None  # bandeau
    produit = rogner_marges_blanches(rgb)  # un petit flacon au milieu d'un grand fond blanc reste petit
    if min(produit.size) < 40:
        return None  # image vide ou presque
    return _candidat(url, octets, rgb, produit, page, rang)


def _candidat(url, octets, rgb, produit, page, rang=0):
    a = analyser_image(rgb)
    mini = rgb.copy()
    mini.thumbnail((320, 320))
    return {"miniature": mini, "image": url, "octets": octets if len(octets) <= 2_500_000 else None,
            "titre": page.get("nom", ""), "site": page["site"], "page": page["url"],
            "largeur": produit.width, "hauteur": produit.height, "nette": resolution_effective(produit),
            "blanc": a["blanc"], "studio": a["studio"],
            "verifie": page["niveau"] != "nom", "niveau": page["niveau"], "sites": {page["site"]},
            "principale": rang == 0, "_h": _empreinte(produit), "_sources": [(url, page)]}


_CHAMPS_PHOTO = ("miniature", "image", "octets", "page", "largeur", "hauteur", "nette", "blanc", "studio")


def _agrandir_candidat(c):
    """Plus grande version, réellement plus nette, de la photo d'un candidat, cherchée à partir de chacun des sites
    où elle a été trouvée. Retourne les nouvelles valeurs (dict) ou None ; ne modifie pas le candidat (calcul fait
    en parallèle, appliqué seulement s'il finit à temps)."""
    nette, nouveau = c["nette"], None
    for url, page in list(c["_sources"])[:3]:
        try:
            img = (_ouvrir(c["octets"]) if (c.get("octets") and url == c["image"])
                   else telecharger_image(url, referer=page["url"]))
            mieux = version_grande(url, img, referer=page["url"])
        except Exception:
            continue
        if mieux and mieux["eff"] > nette * GAIN_MINI:
            rgb = vers_rgb_blanc(_ouvrir(mieux["octets"]))
            cand = _candidat(mieux["url"], mieux["octets"], rgb, mieux["img"], page)
            nouveau = {k: cand[k] for k in _CHAMPS_PHOTO}
            nouveau["agrandie"] = True
            nette = cand["nette"]
            if nette >= 1500:
                break
    return nouveau


def _candidats_de_pages(pages, maxi=30):
    """Images des pages confirmées : téléchargées, dédoublonnées (même photo sur plusieurs sites), classées."""
    demandes, vus = [], set()
    for p in sorted(pages, key=lambda p: -RANG_NIVEAU[p["niveau"]]):
        for rang, u in enumerate(p["images"][:4]):
            if (u, p["url"]) not in vus:
                vus.add((u, p["url"]))
                demandes.append((u, p, rang))
    demandes = demandes[:maxi]
    if not demandes:
        return []
    ex = ThreadPoolExecutor(max_workers=8)
    futures = [ex.submit(_evaluer_image, u, p, rang) for u, p, rang in demandes]
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
    # même photo sur plusieurs sites : on garde la version la plus nette (résolution réelle, fond blanc d'abord) ;
    # les versions plus petites comptent quand même comme sites où la photo figure, et servent à chercher plus grand
    candidats.sort(key=lambda c: (-c["nette"] - 400 * c["studio"]))
    uniques = []
    for c in candidats:
        for u in uniques:
            if _meme_image(u["_h"], c["_h"]):
                u["sites"] |= c["sites"]
                u["_sources"] += c["_sources"]
                u["principale"] = u["principale"] or c["principale"]
                if RANG_NIVEAU[c["niveau"]] > RANG_NIVEAU[u["niveau"]]:
                    u["niveau"] = c["niveau"]
                break
        else:
            uniques.append(c)

    def noter(c):
        resolution = min(1.0, c["nette"] / 1000)  # grand côté réellement net : un flacon fin reste net
        confiance = {"fort": 25, "moyen": 15, "nom": 0}.get(c["niveau"], 0)
        c["score"] = (100 * c["blanc"] + 45 * resolution + confiance + 25 * min(1.0, (len(c["sites"]) - 1) / 2)
                      + (20 if c["principale"] else 0)  # photo principale d'une page, plutôt qu'une photo de galerie
                      - (60 if c["niveau"] == "nom" else 0))

    for c in uniques:
        noter(c)
    uniques.sort(key=lambda c: -c["score"])
    # les meilleures photos encore modestes (moins de 1 500 px réels) : on cherche leur version d'origine, en grand
    a_agrandir = [c for c in uniques[:8] if c["nette"] < 1500]
    if a_agrandir:
        ex = ThreadPoolExecutor(max_workers=8)
        futures = {ex.submit(_agrandir_candidat, c): c for c in a_agrandir}
        faits, _ = wait(list(futures), timeout=DELAI_AGRANDIR)
        ex.shutdown(wait=False, cancel_futures=True)
        for f in faits:
            try:
                nouveau = f.result()
            except Exception:
                nouveau = None
            if nouveau:
                futures[f].update(nouveau)
                noter(futures[f])
    uniques = [c for c in uniques if max(c["largeur"], c["hauteur"]) >= MIN_COTE_VERIFIE]
    for c in uniques:
        c["verifie"] = c["niveau"] != "nom"
        c["nb_sites"] = len(c["sites"])
        c["sites"] = sorted(c["sites"])
        c.pop("_h", None)
        c.pop("_sources", None)
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
    """Recherche web du visuel (voir _rechercher_visuels). En cas d'incident imprévu, une recherche d'images plus
    simple (filtrée : titre citant le code ou le nom) prend le relais : l'écran n'est jamais bloqué."""
    try:
        return _rechercher_visuels(code, nom, requetes_extra, nombre, progression)
    except Exception as e:
        code = nettoyer_code(code)
        try:
            autres, msg = propositions_web([code] + ([f"{nom} {code}"] if nom else []), nom=nom, nombre=nombre,
                                           code=code)
        except Exception:
            autres, msg = [], ""
        return {"verifies": [], "autres": autres, "nom": "", "marque": "",
                "journal": [f"Recherche vérifiée interrompue ({_detail_erreur(e)}) : images web filtrées seulement."]
                + ([msg] if msg else [])}


BUDGET_RECHERCHE = 45  # secondes : passé ce délai, la recherche élargie par le nom n'est pas lancée


def _incidents(erreurs) -> str:
    """Incidents des moteurs de recherche pour le journal : causes distinctes, avec leur nombre."""
    if not erreurs:
        return ""
    groupes = Counter(e.partition(" — ")[2] or e for e in erreurs)
    return " · incidents : " + " | ".join(f"{n} × {c}" if n > 1 else c for c, n in groupes.most_common(3))


def _rechercher_visuels(code, nom="", requetes_extra=(), nombre=12, progression=None):
    """Cascade :
      1. moteurs de recherche (pages et images) interrogés avec le code, sous plusieurs formulations ;
         chaque page trouvée (y compris la page d'origine de chaque image) est ouverte et ne compte que si
         le code y figure ;
      2. si moins de 4 pages confirmées : recherche élargie par le nom (connu, saisi, ou relevé sur une page
         confirmée ou dans les titres des résultats), les pages trouvées étant toujours vérifiées par le code ;
      3. à défaut de code retrouvé : pages au nom correspondant (« probables »), puis images web dont le titre cite
         le code ou correspond au nom (code non vérifié) ; les images sans rapport avec le produit sont écartées."""
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
    deja, pages, tous_bruts, titres_code = set(), [], [], []
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
        if bilan_.get("navigateur"):
            j.append(f"{bilan_['navigateur']} pages n'ont répondu qu'au 2e essai « en navigateur ».")
        if bilan_["sans_code"] or bilan_["lentes"]:
            j.append(f"Pages écartées : {bilan_['sans_code']} sans le code, {bilan_['lentes']} trop lentes.")

    def chercher(req_texte, req_images, titre):
        etape(f"{titre} : interrogation des moteurs de recherche…")
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_texte = ex.submit(_urls_de_recherche, req_texte, code)
            f_images = ex.submit(images_web, req_images)
            avec, sans, err_t, titres = f_texte.result()
            bruts, err_i = f_images.result()
        tous_bruts.extend(bruts)
        titres_code.extend(titres)
        nb_img_code = sum(1 for r in bruts if _motif_code(code).search(_texte_resultat(r)))
        j.append(f"{titre} : {len(set(req_texte)) + len(set(req_images))} recherches · {len(avec)} pages citant le "
                 f"code, {len(sans)} autres pages, {len(bruts)} images dont {nb_img_code} citant le code"
                 + _incidents(err_t + err_i) + ".")
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
    req_images = [code, f'"{code}"', f"{code} pharmacie"]
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
    if not nom_trouve and titres_code:  # aucune page ouverte, mais les résultats de recherche donnent le nom
        nom_trouve, marque_trouvee = _nom_des_titres(titres_code, code)
        if nom_trouve:
            j.append(f"Nom lu dans les titres des résultats de recherche : « {nom_trouve[:70]} » (à vérifier).")
    nom_rebond = nom_ref or nom_trouve
    if (nom_ref and nom_trouve and len(nom_trouve) > len(nom_ref)
            and set(_mots_utiles(nom_ref)) <= set(_mots_utiles(nom_trouve))):
        nom_rebond = nom_trouve  # le nom relevé complète celui qu'on avait (« Avene Gel nettoyant » → « … Cleanance … »)
    if len([p for p in pages if p["niveau"] != "nom"]) < 4 and len(_mots_utiles(nom_rebond)) >= 2:
        if time.time() - debut > BUDGET_RECHERCHE:
            j.append("Étape 2 non lancée (recherche déjà longue) : « Affiner la recherche » permet de la relancer.")
        else:
            court = _nom_pour_requete(nom_rebond, marque_trouvee)
            avec2, sans2, bruts2 = chercher([court, f"{court} pharmacie"], [court, f"{court} parapharmacie"],
                                            f"Étape 2 (nom « {court[:45]} »)")
            pages += examiner(avec2 + _pages_des_images(bruts2, code, _mots_utiles(court)) + sans2,
                              "Étape 2 (nom)", nom_rebond)
            nom_trouve2, marque2 = _nom_consensus(pages)
            if nom_trouve2:
                nom_trouve, marque_trouvee = nom_trouve2, marque2

    # --- 3. photos, nom, et à défaut images dont le titre est cohérent
    etape("Téléchargement des photos…")
    candidats = _candidats_de_pages(pages)
    confirmes = [c for c in candidats if c["niveau"] != "nom"]
    probables = [c for c in candidats if c["niveau"] == "nom"]
    sortie["verifies"] = (confirmes or probables)[:nombre]  # les « probables » ne servent qu'à défaut
    sortie["nom"], sortie["marque"] = _nom_consensus(pages)
    if not sortie["nom"]:
        sortie["nom"], sortie["marque"] = nom_trouve, marque_trouvee
    if not confirmes and tous_bruts:
        etape("Tri des images du web…")
        sortie["autres"], msg = propositions_web([], nom=nom_rebond, nombre=nombre, bruts=tous_bruts, code=code)
        n_indice = sum(1 for c in sortie["autres"] if c["niveau"] == "indice")
        j.append(f"Images du web : {n_indice} citant le code, {len(sortie['autres']) - n_indice} au titre cohérent avec "
                 f"le nom" + (f" ({msg})" if msg else "") + ".")
    j.append(f"Résultat : {len(confirmes)} photo(s) confirmée(s) par le code, {len(probables)} probable(s)"
             + (" (non retenues, des photos confirmées existent)" if confirmes and probables else "")
             + f", {compte['pages']} pages examinées, {time.time() - debut:.0f} s.")
    return sortie
