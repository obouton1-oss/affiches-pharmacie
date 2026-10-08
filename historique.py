"""Historique des affiches créées, conservées 3 mois.

Chaque affiche enregistrée correspond à plusieurs fichiers dans le dossier « historique » :
  <identifiant>.json : réglages de l'affiche (produit, prix ou type de promotion, dates, format et orientation
                       (« orientation » : « Paysage », absent pour le portrait), couleurs, positions) et miniature ;
  <identifiant>.jpg  : visuel du produit tel qu'il est imprimé (déjà nettoyé) ;
  <identifiant>_2.jpg, _3.jpg, _4.jpg : autres visuels de l'affiche, s'il y en a (gamme de produits).

L'identifiant se déduit du produit, du prix, des dates, du format et de l'orientation : enregistrer à nouveau la même affiche
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
import pharmacie

DUREE_JOURS = 90  # environ 3 mois
MAX_VISUELS = 4  # visuels par affiche (même valeur que affiche.MAX_VISUELS)
COTE_MAX_VISUEL = 2000  # pixels
LARGEUR_MINIATURE = 360  # pixels
_caches = {}  # dossier de la pharmacie -> (signature des fichiers, entrées) : une entrée par pharmacie, remplacée d'un bloc


def _dossier():
    """Dossier de l'historique de la pharmacie connectée."""
    return pharmacie.contexte().dossier / "historique"


def _fichier_json(identifiant: str):
    return _dossier() / f"{identifiant}.json"


def _nom_visuel(identifiant: str, rang: int = 1) -> str:
    """Nom du fichier du visuel n° `rang` (1 = visuel principal)."""
    return f"{identifiant}.jpg" if rang == 1 else f"{identifiant}_{rang}.jpg"


def _fichier_visuel(identifiant: str, rang: int = 1):
    return _dossier() / _nom_visuel(identifiant, rang)


def identifiant(params: dict) -> str:
    """Identifiant stable : même produit, même prix, mêmes dates, même format = même affiche."""
    cles = {k: params.get(k) for k in ("code", "marque", "detail", "prix", "prix_barre", "debut", "fin", "format",
                                       "largeur_mm", "hauteur_mm")}
    if (params.get("promo") or {}).get("type") not in (None, "standard"):
        cles["promo"] = params["promo"]  # un autre type de promotion = une autre affiche (les anciennes ne changent pas)
    if params.get("orientation"):
        cles["orientation"] = params["orientation"]  # paysage = une autre affiche que le portrait du même produit
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


def enregistrer(params: dict, visuel, apercu_png: bytes, supplementaires=()) -> str:
    """Enregistre (ou met à jour) une affiche. `params` : produit, prix, dates, format, style, positions…
    `visuel` : image PIL (ou None). `supplementaires` : autres visuels de l'affiche (gamme), dans l'ordre.
    `apercu_png` : aperçu de l'affiche. Retourne l'identifiant."""
    _dossier().mkdir(parents=True, exist_ok=True)
    ident = identifiant(params)
    images = [v for v in (visuel, *supplementaires) if v is not None][:MAX_VISUELS]
    entree = dict(params)
    entree.update(version=1, id=ident, cree=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  a_visuel=bool(images), nb_visuels=len(images), apercu=_miniature(apercu_png))
    for rang in range(1, MAX_VISUELS + 1):
        fichier = _fichier_visuel(ident, rang)
        if rang <= len(images):
            image = images[rang - 1].convert("RGB")
            if max(image.size) > COTE_MAX_VISUEL:
                image.thumbnail((COTE_MAX_VISUEL, COTE_MAX_VISUEL), Image.LANCZOS)
            tampon = io.BytesIO()
            image.save(tampon, format="JPEG", quality=92)
            _ecrire_atomique(fichier, tampon.getvalue())
            sauvegarde.planifier(f"{sauvegarde.PREFIXE_HISTORIQUE}{_nom_visuel(ident, rang)}")
        elif fichier.exists():  # visuel retiré depuis le précédent enregistrement de cette affiche
            fichier.unlink(missing_ok=True)
            sauvegarde.supprimer(f"{sauvegarde.PREFIXE_HISTORIQUE}{_nom_visuel(ident, rang)}")
    _ecrire_atomique(_fichier_json(ident), json.dumps(entree, ensure_ascii=False).encode("utf-8"))
    sauvegarde.planifier(f"{sauvegarde.PREFIXE_HISTORIQUE}{ident}.json")
    return ident


def lister() -> list[dict]:
    """Toutes les affiches enregistrées, les plus récentes d'abord."""
    dossier = _dossier()
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
            if e.get("id") and e.get("cree") and (e.get("prix") or e.get("promo")):
                entrees.append(e)
        except Exception:
            continue  # fichier incomplet ou illisible : ignoré
    entrees.sort(key=lambda e: e["cree"], reverse=True)
    _caches[str(dossier)] = (signature, entrees)
    return entrees


def charger(ident: str):
    return next((e for e in lister() if e["id"] == ident), None)


def _lire_visuel(fichier):
    if not fichier.exists():
        return None
    try:
        img = Image.open(fichier)
        img.load()
        return img.convert("RGB")
    except Exception:
        return None


def visuel(ident: str):
    """Visuel principal de l'affiche (image PIL RVB), ou None s'il n'y en a pas."""
    return _lire_visuel(_fichier_visuel(ident))


def visuels(ident: str) -> list:
    """Tous les visuels de l'affiche, dans l'ordre (liste vide s'il n'y en a pas)."""
    images = [_lire_visuel(_fichier_visuel(ident, rang)) for rang in range(1, MAX_VISUELS + 1)]
    return [img for img in images if img is not None]


def apercu_octets(entree: dict) -> bytes:
    return base64.b64decode(entree["apercu"])


def supprimer(ident: str) -> None:
    _fichier_json(ident).unlink(missing_ok=True)
    sauvegarde.supprimer(f"{sauvegarde.PREFIXE_HISTORIQUE}{ident}.json")
    for rang in range(1, MAX_VISUELS + 1):
        _fichier_visuel(ident, rang).unlink(missing_ok=True)
        sauvegarde.supprimer(f"{sauvegarde.PREFIXE_HISTORIQUE}{_nom_visuel(ident, rang)}")


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
