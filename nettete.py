"""Amélioration de la netteté d'un visuel de faible résolution (agrandissement x4 par réseau de neurones).

- Mode « rapide »  : modèle ESPCN (très léger, bien adapté aux visuels à contours nets).
- Mode « qualité » : modèle EDSR (plus précis, plus lent : de quelques secondes à environ une minute).
Les modèles sont téléchargés une seule fois dans le dossier modeles/. Si le module ou le modèle est
indisponible, un agrandissement classique avec accentuation est appliqué à la place.
Un agrandissement ne peut pas recréer des détails absents de l'image d'origine (petits textes illisibles).
Les derniers résultats sont gardés en mémoire (nombre limité) ; aucune image n'est écrite sur le disque.
"""
import hashlib
import shutil
from collections import OrderedDict
from pathlib import Path

import requests
from PIL import Image, ImageFilter

from chemins import CODE, DONNEES

DOSSIER_MODELES = CODE / "modeles"
ANCIEN_DOSSIER = DONNEES / "images" / "nettete"  # ancien cache sur disque, supprimé au démarrage
MAX_MEMO = 8  # résultats gardés en mémoire (les plus récents)
MODELES = {
    "rapide": ("espcn", "ESPCN_x4.pb",
               "https://raw.githubusercontent.com/fannymonori/TF-ESPCN/master/export/ESPCN_x4.pb", 90_000),
    "qualite": ("edsr", "EDSR_x4.pb",
                "https://raw.githubusercontent.com/Saafke/EDSR_Tensorflow/master/models/EDSR_x4.pb", 30_000_000),
}
COTE_MAX = 1800   # taille maximale du résultat (pixels)
_memo = OrderedDict()


def vider_ancien_cache() -> None:
    """Supprime l'ancien cache d'images agrandies (versions précédentes de l'outil)."""
    shutil.rmtree(ANCIEN_DOSSIER, ignore_errors=True)


def _modele(mode: str) -> Path:
    nom, fichier, url, taille_min = MODELES[mode]
    chemin = DOSSIER_MODELES / fichier
    if chemin.exists() and chemin.stat().st_size >= taille_min:
        return chemin
    DOSSIER_MODELES.mkdir(exist_ok=True)
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    if len(r.content) < taille_min:
        raise RuntimeError("modèle incomplet")
    chemin.write_bytes(r.content)
    return chemin


def _agrandir_ia(img: Image.Image, mode: str) -> Image.Image:
    import cv2
    import numpy as np
    nom = MODELES[mode][0]
    cote = 900 if mode == "qualite" else 1400  # borne la mémoire utilisée par l'agrandissement x4
    if max(img.size) > cote:
        img = img.copy()
        img.thumbnail((cote, cote), Image.LANCZOS)
    sr = cv2.dnn_superres.DnnSuperResImpl_create()
    sr.readModel(str(_modele(mode)))
    sr.setModel(nom, 4)
    bgr = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    sortie = sr.upsample(bgr)
    return Image.fromarray(cv2.cvtColor(sortie, cv2.COLOR_BGR2RGB))


def _agrandir_classique(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    grand = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
    return grand.filter(ImageFilter.UnsharpMask(radius=1.6, percent=130, threshold=2))


def ameliorer(img: Image.Image, mode: str = "rapide"):
    """Retourne (image agrandie et affinée, description de la méthode)."""
    img = img.convert("RGB")
    cle = hashlib.sha1(img.resize((96, 96)).tobytes() + str(img.size).encode() + mode.encode()).hexdigest()[:16]
    if cle in _memo:
        _memo.move_to_end(cle)
        return _memo[cle]
    try:
        res = _agrandir_ia(img, mode)
        methode = "Netteté améliorée (IA, " + ("rapide" if mode == "rapide" else "haute qualité") + ")"
    except Exception:
        res = _agrandir_classique(img)
        methode = "Netteté améliorée (agrandissement classique : modèle IA indisponible)"
    if max(res.size) > COTE_MAX:
        res.thumbnail((COTE_MAX, COTE_MAX), Image.LANCZOS)
    _memo[cle] = (res, methode)
    while len(_memo) > MAX_MEMO:
        _memo.popitem(last=False)
    return res, methode
