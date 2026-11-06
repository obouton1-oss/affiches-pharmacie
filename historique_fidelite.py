"""Historique des affiches « carte de fidélité » (onglet « Cartes de fidélité »), séparé de celui des affiches promo.

Dossier « fidelite » de la pharmacie (à côté de « historique », jamais mélangé avec lui) :
  <identifiant>.json : réglages de l'affiche (laboratoire, offre, textes, format, orientation, options, style) et miniature ;
  <identifiant>.png  : copie du logo du laboratoire tel qu'imprimé (s'il y en a un), pour que l'affiche reste
                       réimprimable même si le logo de la marque est remplacé ou supprimé ensuite.

Contrairement aux affiches promo (3 mois), ces affiches sont conservées jusqu'à ce qu'on les supprime : un programme de
fidélité dure souvent toute l'année. MAX_AFFICHES au plus (au-delà, les plus anciennes sont retirées).
L'identifiant se déduit du laboratoire, de l'offre, des textes, du format et de l'orientation : enregistrer à nouveau la
même affiche (autre police, autre titre…) remplace l'entrée existante. Sauvegarde en ligne : voir sauvegarde.py.
"""
import base64
import hashlib
import io
import json
from datetime import datetime, timezone

from PIL import Image

import marques
import pharmacie
import sauvegarde

DOSSIER = "fidelite"
MAX_AFFICHES = 200
LARGEUR_MINIATURE = 360  # pixels
_caches = {}  # dossier -> (signature des fichiers, entrées)

# réglages enregistrés avec chaque affiche (mêmes noms que les champs de l'onglet, sans le préfixe « w_fid_ »)
CHAMPS = ("labo", "achetes", "offerts", "libre", "haut", "bas", "gamme", "precision", "titre", "logo_labo",
          "logo_pharmacie", "majuscules", "format", "paysage")


def _dossier(base=None):
    return (pharmacie.contexte().dossier if base is None else base) / DOSSIER


def identifiant(params: dict) -> str:
    """Même laboratoire, même offre, mêmes textes, même format et orientation = même affiche."""
    cles = {"labo": marques.cle(params.get("labo"))}
    cles.update({k: params.get(k) for k in ("haut", "bas", "gamme", "format", "paysage")})
    texte = json.dumps(cles, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(texte.encode("utf-8")).hexdigest()[:12]


def _miniature(apercu_png: bytes) -> str:
    img = Image.open(io.BytesIO(apercu_png)).convert("RGB")
    img.thumbnail((LARGEUR_MINIATURE, LARGEUR_MINIATURE * 3), Image.LANCZOS)
    tampon = io.BytesIO()
    img.save(tampon, format="JPEG", quality=82)
    return base64.b64encode(tampon.getvalue()).decode("ascii")


def _ecrire_atomique(fichier, octets: bytes) -> None:
    temporaire = fichier.with_name(fichier.name + ".tmp")
    temporaire.write_bytes(octets)
    temporaire.replace(fichier)


def enregistrer(params: dict, style: dict, logo_labo, apercu_png: bytes) -> str:
    """Enregistre (ou met à jour) une affiche. logo_labo : image PIL du logo imprimé, ou None. Retourne l'identifiant."""
    dossier = _dossier()
    dossier.mkdir(parents=True, exist_ok=True)
    ident = identifiant(params)
    entree = {k: params.get(k) for k in CHAMPS}
    entree.update(version=1, id=ident, style=dict(style or {}), a_logo=logo_labo is not None,
                  cree=datetime.now(timezone.utc).isoformat(timespec="seconds"), apercu=_miniature(apercu_png))
    fichier_logo = dossier / f"{ident}.png"
    if logo_labo is not None:
        tampon = io.BytesIO()
        logo_labo.convert("RGBA").save(tampon, format="PNG", optimize=True)
        _ecrire_atomique(fichier_logo, tampon.getvalue())
        sauvegarde.planifier(f"{sauvegarde.PREFIXE_FIDELITE}{ident}.png")
    elif fichier_logo.exists():
        fichier_logo.unlink(missing_ok=True)
        sauvegarde.supprimer(f"{sauvegarde.PREFIXE_FIDELITE}{ident}.png")
    _ecrire_atomique(dossier / f"{ident}.json", json.dumps(entree, ensure_ascii=False).encode("utf-8"))
    sauvegarde.planifier(f"{sauvegarde.PREFIXE_FIDELITE}{ident}.json")
    for e in lister()[MAX_AFFICHES:]:  # au-delà du maximum : les plus anciennes sont retirées
        supprimer(e["id"])
    return ident


def lister(base=None) -> list[dict]:
    """Toutes les affiches fidélité enregistrées, les plus récentes d'abord."""
    dossier = _dossier(base)
    if not dossier.exists():
        return []
    fichiers = sorted(dossier.glob("*.json"))
    signature = tuple((f.name, f.stat().st_mtime_ns) for f in fichiers)
    memo = _caches.get(str(dossier))
    if memo and memo[0] == signature:
        return memo[1]
    entrees = []
    for f in fichiers:
        try:
            e = json.loads(f.read_text(encoding="utf-8"))
            if e.get("id") and e.get("cree") and (e.get("labo") or "").strip():
                entrees.append(e)
        except Exception:
            continue  # fichier incomplet ou illisible : ignoré
    entrees.sort(key=lambda e: e["cree"], reverse=True)
    _caches[str(dossier)] = (signature, entrees)
    return entrees


def charger(ident: str):
    return next((e for e in lister() if e["id"] == ident), None)


def logo(ident: str, base=None):
    """Logo du laboratoire enregistré avec l'affiche (image PIL RGBA), ou None."""
    fichier = _dossier(base) / f"{ident}.png"
    if not fichier.exists():
        return None
    try:
        img = Image.open(fichier)
        img.load()
        return img.convert("RGBA")
    except Exception:
        return None


def apercu_octets(entree: dict) -> bytes:
    return base64.b64decode(entree["apercu"])


def supprimer(ident: str) -> None:
    dossier = _dossier()
    for nom in (f"{ident}.json", f"{ident}.png"):
        (dossier / nom).unlink(missing_ok=True)
        sauvegarde.supprimer(f"{sauvegarde.PREFIXE_FIDELITE}{nom}")


def date_creation(entree: dict) -> str:
    """Date d'enregistrement au format JJ/MM/AAAA (heure de Paris)."""
    cree = datetime.fromisoformat(entree["cree"])
    try:
        from zoneinfo import ZoneInfo
        cree = cree.astimezone(ZoneInfo("Europe/Paris"))
    except Exception:
        pass
    return cree.strftime("%d/%m/%Y")
