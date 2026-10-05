"""Nettoyage d'un visuel produit : suppression du fond, recadrage serré, fond blanc.

Méthode 1 (si le module « rembg » est installé) : détourage par intelligence artificielle,
  efficace sur les photos prises « sur le vif ».
Méthode 2 (toujours disponible) : suppression d'un fond uni ou quasi uni, à partir des bords.
Le résultat est mémorisé dans images/propres/ pour ne pas être recalculé.
"""
import hashlib
from pathlib import Path

from PIL import Image, ImageDraw

from chemins import DONNEES

DOSSIER = DONNEES / "images" / "propres"
COTE_MAX_IA = 1800  # côté maximal (pixels) de l'image soumise au détourage par IA
_session = {"obj": None, "essai": 0}
_memo = {}  # résultats déjà calculés dans ce processus (évite de recalculer à chaque rafraîchissement)


def _rembg_session():
    if _session["obj"] is not None or _session["essai"] >= 3:
        return _session["obj"]
    _session["essai"] += 1  # une panne passagère (téléchargement du modèle…) ne bloque pas définitivement
    try:
        from rembg import new_session
        try:  # options sobres en mémoire (utile sur les hébergements modestes)
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.enable_cpu_mem_arena = False
            options.enable_mem_pattern = False
            options.intra_op_num_threads = 2
            _session["obj"] = new_session("u2net", sess_opts=options)
        except TypeError:
            _session["obj"] = new_session("u2net")
    except Exception:
        _session["obj"] = None
    return _session["obj"]


def rembg_disponible() -> bool:
    return _rembg_session() is not None


def _sur_blanc_et_rogner(rgba: Image.Image, marge_rel: float = 0.02) -> Image.Image:
    alpha = rgba.getchannel("A")
    boite = alpha.point(lambda a: 255 if a > 20 else 0).getbbox()
    if boite:
        m = int(max(rgba.size) * marge_rel)
        x0, y0, x1, y1 = boite
        rgba = rgba.crop((max(0, x0 - m), max(0, y0 - m), min(rgba.width, x1 + m), min(rgba.height, y1 + m)))
    fond = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    fond.alpha_composite(rgba)
    return fond.convert("RGB")


def _couverture(alpha: Image.Image) -> float:
    h = alpha.point(lambda a: 255 if a > 20 else 0).histogram()
    return h[255] / max(1, sum(h))


def _detourage_ia(img: Image.Image):
    sess = _rembg_session()
    if sess is None:
        return None
    try:
        from rembg import remove
        source = img.convert("RGB")
        if max(source.size) > COTE_MAX_IA:  # limite la mémoire utilisée sur les hébergements modestes
            source.thumbnail((COTE_MAX_IA, COTE_MAX_IA), Image.LANCZOS)
        rgba = remove(source, session=sess, post_process_mask=True).convert("RGBA")
    except Exception:
        return None
    if not 0.04 < _couverture(rgba.getchannel("A")) < 0.97:
        return None  # détourage incohérent (rien ou tout détecté)
    return rgba


def _detourage_fond_uni(img: Image.Image, tolerance: int = 28):
    """Remplissage depuis les bords : ne retire que ce qui ressemble au fond de la photo."""
    rgb = img.convert("RGB")
    echelle = min(1.0, 700 / max(rgb.size))
    petit = rgb.resize((max(1, int(rgb.width * echelle)), max(1, int(rgb.height * echelle))))
    w, h = petit.size
    marqueur = (255, 0, 255)
    travail = petit.copy()
    germes = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
              (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]
    for g in germes:
        if travail.getpixel(g) != marqueur:
            ImageDraw.floodfill(travail, g, marqueur, thresh=tolerance)
    masque = Image.new("L", (w, h), 255)
    px_t, px_m = travail.load(), masque.load()
    for y in range(h):
        for x in range(w):
            if px_t[x, y] == marqueur:
                px_m[x, y] = 0
    masque = masque.resize(rgb.size, Image.BILINEAR)
    if not 0.04 < _couverture(masque) < 0.97:
        return None
    rgba = rgb.convert("RGBA")
    rgba.putalpha(masque)
    return rgba


def nettoyer(img: Image.Image):
    """Retourne (image propre sur fond blanc, description de la méthode employée)."""
    img = img.convert("RGB")
    cle = hashlib.sha1(img.resize((128, 128)).tobytes() + str(img.size).encode()).hexdigest()[:16]
    DOSSIER.mkdir(parents=True, exist_ok=True)
    fichier_img = DOSSIER / f"{cle}.png"
    fichier_txt = DOSSIER / f"{cle}.txt"
    if cle in _memo:
        return _memo[cle]
    if fichier_img.exists() and fichier_txt.exists():
        _memo[cle] = (Image.open(fichier_img).convert("RGB"), fichier_txt.read_text(encoding="utf-8"))
        return _memo[cle]

    rgba = _detourage_ia(img)
    methode = "Détourage automatique (IA)"
    if rgba is None:
        rgba = _detourage_fond_uni(img)
        methode = "Suppression du fond uni"
    if rgba is None:
        propre = _sur_blanc_et_rogner(img.convert("RGBA"), 0.0)
        methode = "Fond non détecté : visuel simplement recadré"
    else:
        propre = _sur_blanc_et_rogner(rgba)
    _memo[cle] = (propre, methode)
    if methode.startswith("Détourage automatique"):  # seuls les résultats de l'IA sont conservés sur disque
        propre.save(fichier_img)
        fichier_txt.write_text(methode, encoding="utf-8")
    return propre, methode
