"""Recherche du visuel et du nom d'un produit à partir de son code CIP13 / EAN13.

Ordre de recherche :
  1. dossier local  images/<code>.(png|jpg|jpeg|webp)   (cache et dépôts manuels)
  2. Open Beauty Facts, Open Food Facts, Open Products Facts (bases collaboratives)
  3. Recherche d'images sur le web (propositions à valider par l'utilisateur)
Aucune base ne couvre tous les produits : le dernier recours est l'import manuel.
Toute image retenue est mémorisée dans images/<code>.png.
"""
import io
import re
from pathlib import Path

import requests
from PIL import Image

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
EXTENSIONS = ("png", "jpg", "jpeg", "webp")


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


def telecharger_image(url: str) -> Image.Image:
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content))
    img.load()
    return vers_rgb_blanc(img)


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
        journal.append("Aucun visuel dans les bases publiques : utiliser les propositions web ou l'import manuel.")
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
    import unicodedata
    t = unicodedata.normalize("NFD", texte.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return [m for m in re.split(r"[^a-z0-9]+", t) if len(m) >= 4 and not m.isdigit()]


def propositions_web(requetes, nom: str = "", nombre: int = 8, taille_min: int = 350):
    """Propositions d'images issues d'une recherche web, classées par ressemblance à un visuel
    de site marchand : fond blanc uni, haute résolution, titre proche du nom du produit.
    Retourne (liste de dicts, message). À valider visuellement par l'utilisateur."""
    from concurrent.futures import ThreadPoolExecutor
    from urllib.parse import urlparse
    if isinstance(requetes, str):
        requetes = [requetes]
    try:
        from ddgs import DDGS
    except Exception:
        return [], "Module de recherche web non installé (ddgs)."
    bruts, erreur = [], ""
    for req in requetes:
        try:
            try:
                res = DDGS().images(req, max_results=20, size="Large")
            except TypeError:
                res = DDGS().images(req, max_results=20)
            bruts.extend(res or [])
        except Exception as e:
            erreur = f"Recherche web indisponible ({type(e).__name__})."
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
                          "site": urlparse(r.get("url") or url).netloc.replace("www.", ""),
                          "largeur": w, "hauteur": h})
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
