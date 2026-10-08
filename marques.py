"""Logos de marques d'une pharmacie (SVR, Avène…), imprimés sur l'affiche à la place du nom de la marque.

L'outil ne peut pas reproduire la typographie d'une marque à partir de son nom : la pharmacie importe (ou colle) une fois
le logo de la marque, et l'outil le retrouve à chaque affiche de cette marque (le nom saisi, sans tenir compte des accents,
des majuscules ni de la ponctuation, désigne le même logo : « Avène », « AVENE » et « avene »).
Les logos sont rangés dans le dossier de la pharmacie (voir pharmacie.py) et sauvegardés en ligne avec ses autres données :
  marques/<clé>.png  : le logo (PNG, transparence conservée, marges retirées, 1000 pixels au plus) ;
  marques/index.json : {clé: nom affiché}.
Ce module ne dépend pas de Streamlit ; les fonctions reçoivent le dossier de la pharmacie.
"""
import io
import json
import unicodedata
from pathlib import Path

from PIL import Image, ImageOps

import sauvegarde

DOSSIER = "marques"
INDEX = "index.json"
MAX_LOGOS = 60
COTE_MAX = 1000          # pixels
COTE_MIN = 40            # pixels : en dessous, l'image est trop petite pour un logo
TAILLE_MAX = 5_000_000   # octets


def cle(nom) -> str:
    """Identifiant d'une marque : lettres et chiffres seulement, sans accents, en minuscules (« Avène » -> « avene »)."""
    sans = "".join(c for c in unicodedata.normalize("NFD", str(nom or "")) if unicodedata.category(c) != "Mn")
    return "".join(c for c in sans.lower() if c.isalnum())[:40]


def _dossier(base) -> Path:
    return Path(base) / DOSSIER


def _lire_index(base) -> dict:
    try:
        lu = json.loads((_dossier(base) / INDEX).read_text(encoding="utf-8"))
        return {k: str(v) for k, v in lu.items() if isinstance(lu, dict) and (_dossier(base) / f"{k}.png").exists()}
    except Exception:
        return {}


def lister(base) -> list[dict]:
    """[{« cle », « nom »}, ...] par ordre alphabétique."""
    return sorted(({"cle": k, "nom": v} for k, v in _lire_index(base).items()), key=lambda m: m["nom"].lower())


def trouver(base, marque):
    """Clé du logo enregistré pour cette marque (le nom saisi sur l'affiche), ou None."""
    k = cle(marque)
    return k if k and (_dossier(base) / f"{k}.png").exists() and k in _lire_index(base) else None


def charger(base, k):
    """Logo (image PIL RGBA), ou None s'il n'existe plus."""
    if not k:
        return None
    fichier = _dossier(base) / f"{cle(k)}.png"
    try:
        img = Image.open(fichier)
        img.load()
        return img.convert("RGBA")
    except Exception:
        return None


def _rogner(img: Image.Image) -> Image.Image:
    """Retire les marges vides : transparentes, ou blanches si l'image n'a pas de transparence."""
    alpha = img.getchannel("A")
    if alpha.getextrema()[0] < 250:  # il y a de la transparence
        boite = alpha.point(lambda p: 255 if p > 8 else 0).getbbox()
    else:
        boite = img.convert("L").point(lambda p: 255 if p < 245 else 0).getbbox()
    if not boite:
        return img
    marge = max(1, int(max(img.size) * 0.01))
    x0, y0, x1, y1 = boite
    return img.crop((max(0, x0 - marge), max(0, y0 - marge), min(img.width, x1 + marge), min(img.height, y1 + marge)))


def _ecrire_atomique(fichier: Path, octets: bytes) -> None:
    temporaire = fichier.with_name(fichier.name + ".tmp")
    temporaire.write_bytes(octets)
    temporaire.replace(fichier)


def enregistrer(base, nom, image):
    """Enregistre (ou remplace) le logo de la marque `nom`. `image` : image PIL ou octets d'un fichier PNG/JPG.
    Retourne (clé, message) ; la clé est None si l'enregistrement a échoué (le message dit pourquoi)."""
    nom = " ".join(str(nom or "").split())[:60]
    k = cle(nom)
    if not k:
        return None, "Saisir d'abord la marque : le logo est rangé sous son nom."
    try:
        if isinstance(image, (bytes, bytearray)):
            if len(image) > TAILLE_MAX:
                return None, "Fichier trop volumineux (5 Mo au plus)."
            image = Image.open(io.BytesIO(image))
        image.load()
        img = ImageOps.exif_transpose(image).convert("RGBA")
    except Exception:
        return None, "Image illisible : importer un fichier PNG ou JPG."
    index = _lire_index(base)
    if k not in index and len(index) >= MAX_LOGOS:
        return None, f"{MAX_LOGOS} logos au plus : en supprimer un avant d'en ajouter un autre."
    img = _rogner(img)
    if max(img.size) < COTE_MIN:
        return None, "Image trop petite pour servir de logo."
    img.thumbnail((COTE_MAX, COTE_MAX), Image.LANCZOS)
    dossier = _dossier(base)
    dossier.mkdir(parents=True, exist_ok=True)
    tampon = io.BytesIO()
    img.save(tampon, format="PNG", optimize=True)
    _ecrire_atomique(dossier / f"{k}.png", tampon.getvalue())
    index[k] = index.get(k) or nom  # le nom affiché reste celui de la première saisie
    _ecrire_atomique(dossier / INDEX, json.dumps(index, ensure_ascii=False, indent=1).encode("utf-8"))
    sauvegarde.planifier(f"{sauvegarde.PREFIXE_MARQUES}{k}.png")
    sauvegarde.planifier(f"{sauvegarde.PREFIXE_MARQUES}{INDEX}")
    return k, f"Logo de {index[k]} enregistré."


def supprimer(base, k) -> None:
    k = cle(k)
    if not k:
        return
    index = _lire_index(base)
    (_dossier(base) / f"{k}.png").unlink(missing_ok=True)
    sauvegarde.supprimer(f"{sauvegarde.PREFIXE_MARQUES}{k}.png")
    if k in index:
        del index[k]
        _ecrire_atomique(_dossier(base) / INDEX, json.dumps(index, ensure_ascii=False, indent=1).encode("utf-8"))
        sauvegarde.planifier(f"{sauvegarde.PREFIXE_MARQUES}{INDEX}")
