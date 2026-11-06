"""Recherche automatique du logo d'une marque (La Roche-Posay, Avène, Gallia, Guigoz…).

L'outil propose plusieurs logos ; c'est la pharmacie qui choisit celui à garder (voir marques.py pour le rangement).
Aucun logo n'est enregistré sans ce choix : un nom de marque peut désigner autre chose (une revue, une commune…).

Sources, par ordre de confiance :
  0. le site officiel de la marque (trouvé par les moteurs de recherche, ou à une adresse devinée comme nuxe.com) :
     logo déclaré par le site, image ou dessin vectoriel (SVG, converti en PNG) de l'en-tête de sa page d'accueil ;
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
from urllib.parse import unquote, urljoin, urlparse

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


# --------------------------------------------------------------------------- Site officiel de la marque
# Le logo le plus sûr et le plus à jour est celui que la marque affiche sur son propre site : on trouve le site
# (moteurs de recherche, ou adresses devinées comme nuxe.com, pileje.fr, sans dépendre des moteurs), puis on lit sa
# page d'accueil : logo déclaré pour les moteurs (données « schema.org »), images et dessins vectoriels (SVG) de
# l'en-tête dont le nom, la classe ou le texte de remplacement parlent de logo ou de la marque.
COTE_RENDU_SVG = 1200          # pixels : les logos vectoriels du site sont convertis en PNG de cette taille (grand côté)
MAX_SITES = 3                  # sites officiels possibles examinés au plus
MOTS_A_EVITER = ("footer", "pied", "partenaire", "partner", "paiement", "payment", "social", "facebook", "instagram",
                 "youtube", "twitter", "linkedin", "tiktok", "pinterest", "label", "certif", "award", "prix-", "trustpilot",
                 "avis-verifies", "app-store", "google-play", "appstore", "playstore", "flag", "drapeau", "icon-",
                 "picto", "loader", "spinner", "cart", "panier", "search", "recherche", "menu", "burger")
DOMAINE_A_VENDRE = re.compile(r"for sale|à vendre|a vendre|domain (is )?parked|parking|buy this domain|"
                              r"domaine (est )?en vente|coming soon|bient[oô]t disponible", re.I)
_LOGO_JSONLD = re.compile(r'"logo"\s*:\s*(?:\{[^{}]*?"(?:url|contentUrl)"\s*:\s*)?"([^"]+)"')


def _domaine(url: str) -> str:
    try:
        hote = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return hote[4:] if hote.startswith("www.") else hote


def _domaine_de_marque(url: str, jetons) -> bool:
    """Le nom de domaine contient tous les mots de la marque (nuxe.com, eau-thermale-avene.fr, pileje.fr…)."""
    dom = _domaine(url)
    if not dom or any(d in dom for d in ip.DOMAINES_IGNORES):
        return False
    etiquette = _compact(dom.rsplit(".", 1)[0])
    return all(j in etiquette for j in jetons)


def _adresses_devinees(marque: str, jetons) -> list[str]:
    """Adresses probables du site de la marque : nom collé ou avec des tirets, en .fr et en .com."""
    mots = [m for m in _mots(marque) if m not in GENERIQUES] or list(jetons)
    bases = []
    for b in ("".join(jetons), "-".join(jetons), "".join(mots), "-".join(mots)):
        if b and b not in bases:
            bases.append(b)
    return [f"https://www.{b}.{tld}/" for b in bases[:3] for tld in ("fr", "com")]


def _sites_officiels(marque: str, jetons, incidents) -> list[str]:
    """Pages d'accueil possibles du site de la marque : celles des résultats de recherche d'abord, puis les adresses
    devinées (essayées même si les moteurs de recherche ne répondent pas)."""
    trouves = []
    try:
        lots, erreurs = ip._recherches("text", [f"{marque} site officiel", f"{marque} laboratoire"],
                                       region="fr-fr", max_results=10)
        if erreurs and not any(lots):
            incidents.append(f"Recherche du site officiel — {erreurs[0]}")
        for lot in lots:
            for r in lot:
                u = (r.get("href") or "").strip()
                if u.startswith("http") and _domaine_de_marque(u, jetons):
                    trouves.append(f"{urlparse(u).scheme}://{urlparse(u).netloc}/")
    except Exception as e:
        incidents.append(f"Recherche du site officiel — {ip._detail_erreur(e)}")
    # domaine le plus court d'abord (nuxe.com avant nuxe-boutique-exemple.fr)
    trouves = sorted(dict.fromkeys(trouves), key=lambda u: (len(_domaine(u)), not _domaine(u).endswith(".fr")))
    devinees = [u for u in _adresses_devinees(marque, jetons) if _domaine(u) not in {_domaine(t) for t in trouves}]
    return trouves[:MAX_SITES] + devinees


def _contexte(el, niveaux=3) -> str:
    """Classe, identifiant, texte de remplacement, titre et lien d'un élément et de ses parents proches."""
    morceaux, n = [], 0
    while el is not None and n <= niveaux:
        for att in ("class", "id", "alt", "title", "aria-label", "href", "data-testid"):
            v = el.get(att) if hasattr(el, "get") else None
            if v:
                morceaux.append(str(v))
        tag = getattr(el, "tag", "")
        if isinstance(tag, str) and tag.lower() in ("header", "footer", "nav"):
            morceaux.append(tag.lower())
        el, n = el.getparent(), n + 1
    return " ".join(morceaux).lower()


def _dans(el, balise) -> bool:
    while el is not None:
        if isinstance(getattr(el, "tag", None), str) and el.tag.lower() == balise:
            return True
        el = el.getparent()
    return False


def _source_image(img) -> str:
    """Adresse d'une balise <img> : src, ou les variantes paresseuses (data-src…), ou la plus grande du srcset."""
    for att in ("src", "data-src", "data-lazy-src", "data-original"):
        v = (img.get(att) or "").strip()
        if v and not v.startswith("data:"):
            return v
    srcset = img.get("srcset") or img.get("data-srcset") or ""
    meilleur, taille = "", -1
    for part in srcset.split(","):
        bouts = part.strip().split()
        if bouts:
            try:
                t = float(bouts[1].rstrip("wx")) if len(bouts) > 1 else 1.0
            except ValueError:
                t = 1.0
            if t > taille:
                meilleur, taille = bouts[0], t
    return meilleur


def candidats_logo_page(html: str, base_url: str, jetons) -> list[dict]:
    """Logos possibles d'une page d'accueil, du plus probable au moins probable. Chaque candidat : {url} ou {svg}
    (dessin vectoriel écrit dans la page), avec un score."""
    try:
        import lxml.html
        doc = lxml.html.fromstring(html)
    except Exception:
        return []
    marque_compacte = "".join(jetons)
    cands = []

    def a_eviter(ctx):
        return any(m in ctx for m in MOTS_A_EVITER)

    # 1. logo déclaré pour les moteurs de recherche (schema.org Organization / Brand)
    for script in doc.xpath('//script[contains(translate(@type,"LDJSON","ldjson"),"ld+json")]/text()'):
        for m in _LOGO_JSONLD.finditer(script or ""):
            u = m.group(1).replace("\\/", "/").strip()
            if u and not u.startswith("data:"):
                cands.append({"url": urljoin(base_url, u), "score": 9.0, "origine": "logo déclaré par le site"})
    # 2. images de l'en-tête ou dont le contexte parle de logo
    for img in doc.xpath("//img"):
        src = _source_image(img)
        if not src:
            continue
        chemin = urlparse(urljoin(base_url, src)).path.lower()  # sans le domaine (qui contient toujours la marque)
        ctx = _contexte(img) + " " + chemin
        parle_logo = "logo" in ctx or "brand" in ctx
        de_la_marque = marque_compacte in _compact(ctx)
        if not (parle_logo or (de_la_marque and _dans(img, "header"))) or a_eviter(ctx.replace("logo", "")):
            continue
        ext = _extension(unquote(src).split("?")[0])
        score = 5.0 + 2 * (ext == "svg") + 1 * (ext == "png") + 2 * _dans(img, "header") + 1.5 * de_la_marque \
            - 2.5 * (not de_la_marque and not _dans(img, "header")) - 4 * _dans(img, "footer") \
            - 3 * (_dans(img, "nav") and not de_la_marque)  # logo d'une rubrique du menu (revue, programme…)
        cands.append({"url": urljoin(base_url, src), "score": score, "origine": "image de l'en-tête du site"})
    # 3. dessins vectoriels écrits dans la page (<svg> du logo)
    for svg in doc.xpath('//*[local-name()="svg"]'):
        titre = " ".join(svg.xpath('.//*[local-name()="title"]/text()'))
        ctx = _contexte(svg) + " " + titre.lower()
        if not ("logo" in ctx or marque_compacte in _compact(ctx)) or a_eviter(ctx.replace("logo", "")):
            continue
        if svg.xpath('.//*[local-name()="use"]') and not svg.xpath('.//*[local-name()="path"]'):
            continue  # simple renvoi vers un dessin rangé ailleurs dans la page : illisible seul
        if len(svg.xpath('.//*[local-name()="path" or local-name()="polygon" or local-name()="rect"]')) < 2:
            continue  # trop simple pour être un logo (icône)
        try:
            texte = lxml.html.tostring(svg, encoding="unicode")
        except Exception:
            continue
        if "xmlns=" not in texte[:300]:
            texte = texte.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
        score = 6.5 + 2 * _dans(svg, "header") - 4 * _dans(svg, "footer")
        cands.append({"svg": texte.encode("utf-8"), "score": score, "origine": "dessin vectoriel de l'en-tête du site"})
    cands.sort(key=lambda c: -c["score"])
    if any(c["score"] >= 6.5 for c in cands):  # des logos sûrs : les candidats douteux (logo d'une revue…) sont écartés
        cands = [c for c in cands if c["score"] >= 6.5]
    vus, sortie = set(), []
    for c in cands:
        cle = c.get("url") or hashlib.md5(c["svg"]).hexdigest()
        if cle not in vus:
            vus.add(cle)
            sortie.append(c)
    return sortie[:5]


_BALISE_SVG = re.compile(rb"<svg\b[^>]*>", re.I | re.S)


def _svg_dimensionne(octets: bytes) -> bytes:
    """Un dessin vectoriel sans largeur ni hauteur en pixels (seulement une « viewBox », cas fréquent sur les sites)
    serait dessiné sur une page par défaut, trop grande : on lui donne les dimensions de sa viewBox."""
    m = _BALISE_SVG.search(octets)
    if not m:
        return octets
    balise = m.group(0)
    vb = re.search(rb"viewBox\s*=\s*[\"']\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)", balise, re.I)
    if not vb:
        return octets

    def en_pixels(att):
        v = re.search(rb"\s" + att + rb"\s*=\s*[\"']\s*([\d.]+)\s*(px)?\s*[\"']", balise)
        return v is not None

    if en_pixels(b"width") and en_pixels(b"height"):
        return octets
    nouvelle = re.sub(rb"\s(width|height)\s*=\s*[\"'][^\"']*[\"']", b"", balise)
    nouvelle = nouvelle[:4] + b' width="' + vb.group(1) + b'" height="' + vb.group(2) + b'"' + nouvelle[4:]
    return octets[:m.start()] + nouvelle + octets[m.end():]


def svg_en_png(octets: bytes, cote: int = COTE_RENDU_SVG):
    """Dessin vectoriel (SVG) converti en image PNG à fond transparent (PyMuPDF). Retourne (image PIL, octets PNG)."""
    import fitz
    octets = _svg_dimensionne(octets)
    doc = fitz.open(stream=octets, filetype="svg")
    page = doc[0]
    w, h = page.rect.width, page.rect.height
    if w <= 0 or h <= 0:
        raise ValueError("dessin vide")
    zoom = cote / max(w, h)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=True)
    png = pix.tobytes("png")
    img = Image.open(io.BytesIO(png))
    img.load()
    return img, png


def _est_svg(octets: bytes, url: str = "") -> bool:
    debut = octets[:400].lstrip().lower()
    return _extension(unquote(url).split("?")[0]) == "svg" or debut.startswith(b"<svg") or (
        debut.startswith(b"<?xml") and b"<svg" in octets[:1500].lower())


def _logo_du_candidat(cand: dict, page: str):
    """(image PIL, octets prêts à enregistrer) d'un candidat, ou None."""
    try:
        if "svg" in cand:
            return svg_en_png(cand["svg"])
        octets = ip.telecharger_octets(cand["url"], referer=page, max_octets=MAX_LOGO_OCTETS, delai=(4, 12))
        if _est_svg(octets, cand["url"]):
            return svg_en_png(octets)
        img = Image.open(io.BytesIO(octets))
        img.load()
        return img, octets
    except Exception:
        return None


def _rogner_transparent(img: Image.Image) -> Image.Image:
    """Retire les marges vides (transparentes) autour d'un logo."""
    rgba = img.convert("RGBA")
    boite = rgba.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    return rgba.crop(boite) if boite else rgba


def propositions_site_officiel(marque: str, nombre: int = 3, budget: float = 25.0):
    """Logos trouvés sur le site officiel de la marque. Retourne (propositions avec l'image, incidents).
    Chaque proposition : {nom, url, page, site, largeur, hauteur, licence, source, rang, octets, miniature}."""
    jetons = jetons_marque(marque)
    incidents = []
    if not jetons:
        return [], incidents
    debut = time.time()
    sites = _sites_officiels(marque, jetons, incidents)

    def lire(url):
        try:
            html, finale = ip._telecharger_page(url)
        except Exception:
            return None
        if not _domaine_de_marque(finale, jetons):
            return None  # renvoi vers un autre site (domaine à vendre, revendeur…)
        titre = re.search(r"<title[^>]*>(.*?)</title>", html[:100_000], re.I | re.S)
        if titre and DOMAINE_A_VENDRE.search(titre.group(1)):
            return None
        if "".join(jetons) not in _compact(html[:200_000]):
            return None  # la page ne parle pas de la marque (page d'attente…)
        return html, finale

    pages = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(lire, sites):
            if res and _domaine(res[1]) not in {_domaine(p[1]) for p in pages}:
                pages.append(res)
    if not pages:
        incidents.append("Site officiel — introuvable ou inaccessible")
        return [], incidents
    sortie = []
    for html, finale in pages[:MAX_SITES]:
        if time.time() - debut > budget or len(sortie) >= nombre:
            break
        for cand in candidats_logo_page(html, finale, jetons):
            res = _logo_du_candidat(cand, finale)
            if not res:
                continue
            img, octets = res
            if img.mode in ("RGBA", "LA", "P"):
                img = _rogner_transparent(img)
                if "svg" in cand or _est_svg(octets, cand.get("url", "")):
                    tampon = io.BytesIO()
                    img.save(tampon, "PNG")
                    octets = tampon.getvalue()
            if max(img.size) < COTE_MIN_LOGO or not _logo_visible(img):
                continue
            if img.width < img.height * 0.6 and cand["score"] < 9:
                continue  # très étroit et haut : plutôt une icône ou un visuel qu'un logo de marque
            site = _domaine(finale)
            sortie.append({"nom": f"Logo du site {site}", "url": cand.get("url") or finale, "page": finale,
                           "site": site, "largeur": img.width, "hauteur": img.height, "licence": "",
                           "source": f"Site officiel ({site})", "rang": 0, "octets": octets, "miniature": img.copy()})
            if len(sortie) >= nombre:
                break
    return sortie, incidents


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
    """Télécharge et vérifie l'image d'une proposition. Retourne la proposition complétée, ou None.
    Une proposition du site officiel arrive déjà téléchargée et vérifiée (clés « octets » et « miniature »)."""
    if prop.get("octets") and prop.get("miniature") is not None:
        return prop
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
    with ThreadPoolExecutor(max_workers=2) as ex:  # site officiel et Wikimedia en même temps
        f_site = ex.submit(propositions_site_officiel, marque, 3, budget * 0.7)
        f_wiki = ex.submit(propositions_wikimedia, marque)
        props_site, inc_site = f_site.result()
        props_wiki, inc = f_wiki.result()
    incidents += inc + inc_site
    props_web = []
    if web is True or (web == "si_besoin" and len(props_wiki) + len(props_site) < 3):
        if time.time() - debut < budget:
            props_web, inc = propositions_web(marque)
            incidents += inc
    candidats = props_site + props_wiki + props_web
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
