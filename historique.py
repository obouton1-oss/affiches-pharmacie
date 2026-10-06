"""Historique des affiches créées, conservées 3 mois.

Chaque affiche enregistrée correspond à deux fichiers dans le dossier « historique » :
  <identifiant>.json : réglages de l'affiche (produit, prix ou type de promotion, dates, format, couleurs,
                       positions) et miniature ;
  <identifiant>.jpg  : visuel du produit tel qu'il est imprimé (déjà nettoyé).

L'identifiant se déduit du produit, du prix, des dates et du format : enregistrer à nouveau la même affiche
(par exemple après un changement de police ou de position) remplace l'entrée existante au lieu d'en créer une autre.
Les affiches de plus de DUREE_JOURS jours sont supprimées automatiquement.
Sur l'hébergement en ligne, les fichiers sont recopiés dans la sauvegarde privée (voir sauvegarde.py).
"""
import base64
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone

from PIL import Image

import sauvegarde
from chemins import DONNEES

DOSSIER = DONNEES / "historique"
DUREE_JOURS = 90  # environ 3 mois
COTE_MAX_VISUEL = 2000  # pixels
LARGEUR_MINIATURE = 360  # pixels
_cache = {"signature": None, "entrees": []}


def _fichier_json(identifiant: str):
    return DOSSIER / f"{identifiant}.json"


def _fichier_visuel(identifiant: str):
    return DOSSIER / f"{identifiant}.jpg"


def identifiant(params: dict) -> str:
    """Identifiant stable : même produit, même prix, mêmes dates, même format = même affiche."""
    cles = {k: params.get(k) for k in ("code", "marque", "detail", "prix", "prix_barre", "debut", "fin", "format",
                                       "largeur_mm", "hauteur_mm")}
    if (params.get("promo") or {}).get("type") not in (None, "standard"):
        cles["promo"] = params["promo"]  # un autre type de promotion = une autre affiche (les anciennes ne changent pas)
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


def enregistrer(params: dict, visuel, apercu_png: bytes) -> str:
    """Enregistre (ou met à jour) une affiche. `params` : produit, prix, dates, format, style, positions…
    `visuel` : image PIL (ou None). `apercu_png` : aperçu de l'affiche. Retourne l'identifiant."""
    DOSSIER.mkdir(parents=True, exist_ok=True)
    ident = identifiant(params)
    entree = dict(params)
    entree.update(version=1, id=ident, cree=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  a_visuel=visuel is not None, apercu=_miniature(apercu_png))
    if visuel is not None:
        image = visuel.convert("RGB")
        if max(image.size) > COTE_MAX_VISUEL:
            image.thumbnail((COTE_MAX_VISUEL, COTE_MAX_VISUEL), Image.LANCZOS)
        tampon = io.BytesIO()
        image.save(tampon, format="JPEG", quality=92)
        _ecrire_atomique(_fichier_visuel(ident), tampon.getvalue())
        sauvegarde.planifier(f"{sauvegarde.PREFIXE_HISTORIQUE}{ident}.jpg")
    else:
        _fichier_visuel(ident).unlink(missing_ok=True)
    _ecrire_atomique(_fichier_json(ident), json.dumps(entree, ensure_ascii=False).encode("utf-8"))
    sauvegarde.planifier(f"{sauvegarde.PREFIXE_HISTORIQUE}{ident}.json")
    return ident


def lister() -> list[dict]:
    """Toutes les affiches enregistrées, les plus récentes d'abord."""
    if not DOSSIER.exists():
        return []
    fichiers = sorted(DOSSIER.glob("*.json"))
    signature = tuple((f.name, f.stat().st_mtime_ns) for f in fichiers)
    if signature == _cache["signature"]:
        return _cache["entrees"]
    entrees = []
    for f in fichiers:
        try:
            e = json.loads(f.read_text(encoding="utf-8"))
            if e.get("id") and e.get("cree") and (e.get("prix") or e.get("promo")):
                entrees.append(e)
        except Exception:
            continue  # fichier incomplet ou illisible : ignoré
    entrees.sort(key=lambda e: e["cree"], reverse=True)
    _cache.update(signature=signature, entrees=entrees)
    return entrees


def charger(ident: str):
    return next((e for e in lister() if e["id"] == ident), None)


def visuel(ident: str):
    """Visuel de l'affiche (image PIL RVB), ou None s'il n'y en a pas."""
    fichier = _fichier_visuel(ident)
    if not fichier.exists():
        return None
    try:
        img = Image.open(fichier)
        img.load()
        return img.convert("RGB")
    except Exception:
        return None


def apercu_octets(entree: dict) -> bytes:
    return base64.b64decode(entree["apercu"])


def supprimer(ident: str) -> None:
    _fichier_json(ident).unlink(missing_ok=True)
    _fichier_visuel(ident).unlink(missing_ok=True)
    sauvegarde.supprimer(f"{sauvegarde.PREFIXE_HISTORIQUE}{ident}.json")
    sauvegarde.supprimer(f"{sauvegarde.PREFIXE_HISTORIQUE}{ident}.jpg")


def purger(jours: int = DUREE_JOURS) -> int:
    """Supprime les affiches plus anciennes que `jours` jours. Retourne le nombre d'affiches supprimées."""
    limite = datetime.now(timezone.utc) - timedelta(days=jours)
    supprimees = 0
    for e in lister():
        try:
            if datetime.fromisoformat(e["cree"]) < limite:
                supprimer(e["id"])
                supprimees += 1
        except Exception:
            continue
    return supprimees


def jours_restants(entree: dict, jours: int = DUREE_JOURS) -> int:
    cree = datetime.fromisoformat(entree["cree"])
    return max(0, jours - (datetime.now(timezone.utc) - cree).days)


def date_creation(entree: dict) -> str:
    """Date de création au format JJ/MM/AAAA (heure de Paris)."""
    cree = datetime.fromisoformat(entree["cree"])
    try:
        from zoneinfo import ZoneInfo
        cree = cree.astimezone(ZoneInfo("Europe/Paris"))
    except Exception:
        pass  # base des fuseaux horaires absente : date en heure universelle
    return cree.strftime("%d/%m/%Y")
