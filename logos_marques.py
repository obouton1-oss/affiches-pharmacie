"""Recherche automatique du logo d'une marque (La Roche-Posay, Avène, Gallia, Guigoz…).

L'outil propose plusieurs logos ; c'est la pharmacie qui choisit celui à garder (voir marques.py pour le rangement).
Aucun logo n'est enregistré sans ce choix : un nom de marque peut désigner autre chose (une revue, une commune…).

Sources, par ordre de confiance :
  1. Wikidata : le logo officiel déclaré pour l'entreprise ou la marque (nom identique et description de marque exigés) ;
  2. Wikipédia et Wikimedia Commons : fichiers dont le titre est le nom de la marque suivi de « logo » (ou le nom seul) ;
     les logos en SVG sont fournis en PNG à fond transparent ;
  3. recherche d'images sur le web (« <marque> logo ») : seules sont gardées les images dont le titre ou l'adresse
     citent la marque et le mot « logo ».
Chaque proposition dit d'où elle vient. Ce module ne dépend pas de Streamlit.
"""
import hashlib
import io
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote

import requests
from PIL import Image

import images_produits as ip

# Wikimedia demande de s'identifier ; l'adresse du projet sert de contact (aucune donnée personnelle).
ENTETES_WIKI = {"User-Agent": "AffichesPharmacie/1.0 (https://github.com/obouton1-oss/affiches-pharmacie)",
                "Accept-Language": "fr"}
API_FR = "https://fr.wikipedia.org/w/api.php"
API_COMMONS = "https://commons.wikimedia.org/w/api.php"
API_WIKIDATA = "https://www.wikidata.org/w/api.php"
LARGEUR_WIKI = 1000            # pixels : les logos vectoriels (SVG) sont fournis en PNG de cette largeur
FORMATS = ("svg", "png", "jpg", "jpeg", "gif", "webp")
MAX_LOGO_OCTETS = 3_000_000
COTE_MIN_LOGO = 60             # pixels (grand côté) : en dessous, une image n'est pas un logo exploitable
MAX_EXTRAS = 3                 # mots du titre d'un fichier étrangers à la marque, au plus (avec le mot « logo »)

ARTICLES = {"le", "la", "les", "l", "de", "du", "des", "d", "et", "the", "a", "en", "of"}
GENERIQUES = {"laboratoire", "laboratoires", "lab", "labs", "laboratories", "laboratory"}
MOTS_LOGO = {"logo", "logos", "logotype", "logotipo", "wordmark", "brand", "marque", "identite", "identity", "emblem",
             "embleme", "signature", "nouveau", "new", "officiel", "official", "ancien", "old", "icon", "icone"}
MOTS_NEUTRES = {"svg", "png", "jpg", "jpeg", "gif", "webp", "noir", "blanc", "black", "white", "color", "couleur",
                "colour", "orange", "bleu", "blue", "vert", "green", "rouge", "red", "light", "dark", "vivid", "srgb",
                "rgb", "cmyk", "version", "small", "big", "square", "carre", "fond", "transparent", "transparente",
                "background", "sans", "avec", "horizontal", "vertical", "eq"}
DESCRIPTION_DE_MARQUE = re.compile(r"marque|brand|entreprise|societe|company|laborato|cosmet|pharmac|alimentaire|"
                                   r"fabricant|manufactur|groupe|dermo|produit|firm|business|enseigne|soin", re.I)


# --------------------------------------------------------------------------- Noms
def _sans_accents(texte) -> str:
    t = unicodedata.normalize("NFD", str(texte or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _mots(texte) -> list[str]:
    return [m for m in re.split(r"[^a-z0-9]+", _sans_accents(texte)) if m]


def jetons_marque(marque) -> list[str]:
    """Mots qui identifient la marque (sans articles ni « laboratoire ») : « La Roche-Posay » -> roche, posay."""
    mots = [m for m in _mots(marque) if m not in ARTICLES]
    utiles = [m for m in mots if m not in GENERIQUES]
    return utiles or mots


def _compact(texte) -> str:
    return "".join(_mots(texte))


def _nom_de_fichier(titre: str) -> str:
    """Titre d'un fichier Wikimedia sans préfixe « Fichier: » / « File: »."""
    return re.sub(r"^(fichier|file)\s*:\s*", "", titre.strip(), flags=re.I)


def _extension(nom: str) -> str:
    return nom.rsplit(".", 1)[-1].lower() if "." in nom else ""


def mots_etrangers(titre_fichier: str, jetons) -> int:
    """Nombre de mots du titre qui ne sont ni la marque, ni « logo », ni un détail de forme (année, couleur, format)."""
    nom = _nom_de_fichier(titre_fichier)
    stem = nom.rsplit(".", 1)[0] if "." in nom else nom
    n = 0
    for m in _mots(stem):
        if m.isdigit() or m in ARTICLES or m in MOTS_LOGO or m in MOTS_NEUTRES or m in GENERIQUES:
            continue
        if any(m == j or (len(m) >= 4 and (m in j or j in m)) for j in jetons):
            continue
        n += 1
    return n


def titre_convient(titre_fichier: str, jetons) -> bool:
    """Le titre du fichier contient la marque et ressemble à un logo : mot « logo » (ou équivalent), ou le nom seul."""
    nom = _nom_de_fichier(titre_fichier)
    if _extension(nom) not in FORMATS or not jetons:
        return False
    compact = _compact(nom.rsplit(".", 1)[0])
    if not all(j in compact for j in jetons):
        return False
    extras = mots_etrangers(nom, jetons)
    a_mot_logo = any(m in MOTS_LOGO for m in _mots(nom)) or "logo" in compact
    return extras <= MAX_EXTRAS if a_mot_logo else extras == 0


# --------------------------------------------------------------------------- Wikimedia
def _api(url: str, **params) -> dict:
    params = dict(params, format="json", origin="*")
    r = requests.get(url, params=params, headers=ENTETES_WIKI, timeout=8)
    r.raise_for_status()
    return r.json()


def _pages(reponse: dict) -> list[dict]:
    pages = (reponse.get("query") or {}).get("pages") or {}
    return list(pages.values()) if isinstance(pages, dict) else list(pages)


def _titres_de_recherche(api: str, marque: str, jetons) -> list[str]:
    """Fichiers de l'espace « Fichier » dont le titre correspond à la marque."""
    r = _api(api, action="query", list="search", srnamespace="6", srlimit="20", srsearch=f"{marque} logo")
    titres = [_nom_de_fichier(x.get("title", "")) for x in (r.get("query") or {}).get("search", [])]
    return [t for t in titres if titre_convient(t, jetons)]


def _logo_wikidata(marque: str, jetons) -> str:
    """Nom du fichier de logo déclaré sur Wikidata pour l'entreprise ou la marque, ou ''."""
    r = _api(API_WIKIDATA, action="wbsearchentities", language="fr", uselang="fr", limit="8", type="item", search=marque)
    trouves = [x for x in r.get("search", []) if x.get("id")]
    ok = []
    for x in trouves:
        mots_label = set(_mots(x.get("label", "")))
        if not all(j in mots_label or any(j in m for m in mots_label) for j in jetons):
            continue
        if not DESCRIPTION_DE_MARQUE.search(_sans_accents(x.get("description", ""))):
            continue
        ok.append(x["id"])
    if not ok:
        return ""
    e = _api(API_WIKIDATA, action="wbgetentities", props="claims", ids="|".join(ok[:5]))
    for ident in ok[:5]:
        affirmations = ((e.get("entities") or {}).get(ident) or {}).get("claims", {}).get("P154") or []
        valeurs = []
        for a in affirmations:
            if a.get("rank") == "deprecated" or "P582" in (a.get("qualifiers") or {}):  # logo dont l'usage a pris fin
                continue
            v = ((a.get("mainsnak") or {}).get("datavalue") or {}).get("value")
            if isinstance(v, str) and _extension(v) in FORMATS:
                valeurs.append((a.get("rank") == "preferred", v))
        if valeurs:
            valeurs.sort(key=lambda t: t[0])
            return valeurs[-1][1]
    return ""


def _infos_fichiers(noms: list[str]) -> dict:
    """{nom: {url, largeur, hauteur, licence}} pour des fichiers Wikipédia / Commons ; l'url est celle d'un PNG pour les SVG."""
    if not noms:
        return {}
    r = _api(API_FR, action="query", prop="imageinfo", iiprop="url|size|mime|extmetadata", iiurlwidth=str(LARGEUR_WIKI),
             iiextmetadatafilter="LicenseShortName", titles="|".join("File:" + n for n in noms))
    sortie = {}
    for p in _pages(r):
        ii = (p.get("imageinfo") or [{}])[0]
        url = ii.get("thumburl") or ii.get("url")
        if not url:
            continue
        sortie[_nom_de_fichier(p.get("title", ""))] = {
            "url": url, "largeur": int(ii.get("width") or 0), "hauteur": int(ii.get("height") or 0),
            "licence": ((ii.get("extmetadata") or {}).get("LicenseShortName") or {}).get("value", ""),
            "page": ii.get("descriptionurl", "")}
    return sortie


def _telecharger_wiki(url: str) -> bytes:
    r = requests.get(url, headers=ENTETES_WIKI, timeout=(5, 20))
    r.raise_for_status()
    if len(r.content) > MAX_LOGO_OCTETS:
        raise ValueError("fichier trop volumineux")
    return r.content


def _normalise_nom(nom: str) -> str:
    return _nom_de_fichier(nom).replace("_", " ").strip().lower()


def propositions_wikimedia(marque: str, nombre: int = 5):
    """Logos de Wikidata, de Wikipédia et de Wikimedia Commons. Retourne (propositions sans image, incidents).
    Chaque proposition : {nom, url, largeur, hauteur, licence, page, source, rang}."""
    jetons = jetons_marque(marque)
    incidents = []
    if not jetons:
        return [], incidents

    def avec_incident(fonction, libelle, *args):
        try:
            return fonction(*args)
        except Exception as e:
            incidents.append(f"{libelle} — {ip._detail_erreur(e)}")
            return None

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_wd = ex.submit(avec_incident, _logo_wikidata, "Wikidata", marque, jetons)
        f_fr = ex.submit(avec_incident, _titres_de_recherche, "Wikipédia", API_FR, marque, jetons)
        f_co = ex.submit(avec_incident, _titres_de_recherche, "Wikimedia Commons", API_COMMONS, marque, jetons)
        officiel, titres_fr, titres_co = f_wd.result() or "", f_fr.result() or [], f_co.result() or []

    # fichiers de la recherche, sans doublon, du titre le plus proche de la marque au plus éloigné
    vus, titres = set(), []
    for t in titres_fr + titres_co:
        if _normalise_nom(t) not in vus:
            vus.add(_normalise_nom(t))
            titres.append(t)
    titres.sort(key=lambda t: (mots_etrangers(t, jetons), _extension(t) not in ("svg", "png"), t.lower()))
    voulus = ([officiel] if officiel else []) + [t for t in titres if _normalise_nom(t) != _normalise_nom(officiel)]
    voulus = voulus[:nombre + 2]
    infos = avec_incident(_infos_fichiers, "Wikipédia (détail des fichiers)", voulus) or {}
    par_nom = {_normalise_nom(n): v for n, v in infos.items()}
    sortie = []
    for t in voulus:
        v = par_nom.get(_normalise_nom(t))
        if not v:
            continue
        est_officiel = bool(officiel) and _normalise_nom(t) == _normalise_nom(officiel)
        sortie.append({"nom": _nom_de_fichier(t), "source": "Wikidata (logo officiel)" if est_officiel
                       else "Wikipédia / Wikimedia Commons", "rang": 0 if est_officiel else 1, **v})
    return sortie[:nombre], incidents


# --------------------------------------------------------------------------- Web
def propositions_web(marque: str, nombre: int = 6, bruts=None):
    """Résultats de la recherche d'images web « <marque> logo », sans l'image elle-même.
    bruts : résultats déjà obtenus (sinon la recherche est faite). Retourne (propositions, incidents)."""
    jetons = jetons_marque(marque)
    if not jetons:
        return [], []
    incidents = []
    if bruts is None:
        bruts, incidents = ip.images_web([f"{marque} logo", f"{marque} logo officiel png"])
    vus, gardes = set(), []
    for r in bruts:
        url = r.get("image")
        if not url or url in vus:
            continue
        vus.add(url)
        site = ip._site(r.get("url") or url)
        if any(d in site for d in ip.DOMAINES_IGNORES):
            continue
        texte = _compact(ip._texte_resultat(r))
        mots = set(_mots(ip._texte_resultat(r)))
        if not all(j in texte for j in jetons) or not ("logo" in mots or "logotype" in mots or "logo" in texte):
            continue
        if _extension(unquote(url).split("?")[0]) == "svg":
            continue  # non lisible directement : les SVG viennent de Wikimedia, déjà converti
        try:
            w, h = int(r.get("width") or 0), int(r.get("height") or 0)
        except (TypeError, ValueError):
            w = h = 0
        if w and h and (max(w, h) < 250 or min(w, h) < COTE_MIN_LOGO):
            continue
        officiel = any(j in _compact(site) for j in jetons)
        png = _extension(unquote(url).split("?")[0]) == "png"
        score = 3 * png + 2 * officiel + min(max(w, h, 400), 1500) / 1500 - 0.3 * mots_etrangers(r.get("title", ""), jetons) / 3
        gardes.append({"nom": r.get("title", "") or url, "url": url, "page": r.get("url") or "", "site": site,
                       "largeur": w, "hauteur": h, "licence": "", "source": f"Recherche web ({site})", "rang": 2,
                       "score": score})
    gardes.sort(key=lambda c: -c["score"])
    return gardes[:nombre], incidents


# --------------------------------------------------------------------------- Ensemble
def _logo_visible(img: Image.Image) -> bool:
    """Faux pour une image vide ou un logo entièrement blanc (invisible sur une affiche à fond blanc)."""
    rgba = img.convert("RGBA")
    petit = rgba.copy()
    petit.thumbnail((120, 120))
    pixels = [p for p in petit.getdata() if p[3] > 40]
    if len(pixels) < 20:
        return False
    sombres = sum(1 for p in pixels if (p[0] + p[1] + p[2]) / 3 < 235)
    return sombres / len(pixels) >= 0.02


def _recuperer(prop: dict):
    """Télécharge et vérifie l'image d'une proposition. Retourne la proposition complétée, ou None."""
    try:
        if prop["rang"] <= 1:
            octets = _telecharger_wiki(prop["url"])
        else:
            octets = ip.telecharger_octets(prop["url"], referer=prop.get("page", ""), max_octets=MAX_LOGO_OCTETS,
                                           delai=(4, 12))
        img = Image.open(io.BytesIO(octets))
        img.load()
    except Exception:
        return None
    if max(img.size) < COTE_MIN_LOGO or not _logo_visible(img):
        return None
    prop = dict(prop)
    prop["octets"] = octets
    prop["largeur"], prop["hauteur"] = img.size  # dimensions réelles de l'image reçue
    prop["miniature"] = img.copy()
    return prop


def chercher(marque: str, nombre: int = 8, web=True, budget: float = 40.0):
    """Propose des logos pour la marque. Retourne (propositions, message, incidents).
    web : True = toujours chercher sur le web aussi ; « si_besoin » = seulement si Wikimedia propose moins de 3 logos.
    Chaque proposition contient « octets » (fichier image prêt à enregistrer), « miniature » (image PIL), « source »,
    « nom », « largeur », « hauteur »."""
    marque = " ".join(str(marque or "").split())
    if not jetons_marque(marque):
        return [], "Saisir d'abord la marque.", []
    debut = time.time()
    incidents = []
    props_wiki, inc = propositions_wikimedia(marque)
    incidents += inc
    props_web = []
    if web is True or (web == "si_besoin" and len(props_wiki) < 3):
        if time.time() - debut < budget:
            props_web, inc = propositions_web(marque)
            incidents += inc
    candidats = props_wiki + props_web
    with ThreadPoolExecutor(max_workers=6) as ex:
        recues = [p for p in ex.map(_recuperer, candidats) if p]
    recues.sort(key=lambda p: p["rang"])  # tri stable : l'ordre de chaque source est conservé
    # doublons : même fichier, ou même dessin trouvé par plusieurs sources (la source la plus fiable est gardée)
    sortie, empreintes, hashes = [], [], set()
    for p in recues:
        h = hashlib.md5(p["octets"]).hexdigest()
        if h in hashes:
            continue
        e = ip._empreinte(ip.vers_rgb_blanc(p["miniature"]))
        if any(ip._meme_image(e, autre, seuil=6.0) for autre in empreintes):
            continue
        hashes.add(h)
        empreintes.append(e)
        sortie.append(p)
    sortie = sortie[:nombre]
    if sortie:
        return sortie, "", incidents
    if incidents:
        return [], f"Recherche impossible pour le moment ({incidents[0]}).", incidents
    return [], f"Aucun logo trouvé pour « {marque} ». Importer ou coller l'image du logo.", incidents
