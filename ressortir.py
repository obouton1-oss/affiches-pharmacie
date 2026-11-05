"""« Faire ressortir » un produit sur l'affiche : ombre douce autour et sous le produit.

Utile pour un produit blanc ou très clair (tube, flacon, boîte) qui se perd sur le fond blanc de l'affiche.
Le contour du produit est retrouvé à partir du fond blanc relié aux bords de l'image (les visuels nettoyés sont sur
fond blanc pur). Deux ombres sont posées hors du produit, jamais dessus :
- un halo très léger tout autour, qui dessine les bords (même le haut du produit) ;
- une ombre portée, un peu plus marquée, décalée vers le bas, qui « pose » le produit.
L'image est agrandie d'une marge (plus grande en bas) pour que l'ombre ne soit pas coupée.

Les derniers résultats sont gardés en mémoire (nombre limité) : l'aperçu est redessiné à chaque changement.
"""
import hashlib
import threading
from collections import OrderedDict

import numpy as np
from PIL import Image, ImageChops, ImageFilter

COTE_MAX = 1500        # côté maximal (pixels) du visuel traité (l'impression n'en demande pas plus)
SEUIL_BLANC = 244      # un pixel dont le canal le plus sombre atteint ce seuil peut être du fond
SEUIL_BORD = 6.0       # variation de clarté (Sobel) qui marque un bord, même pâle
COTE_MASQUE = 900      # côté maximal (pixels) de l'image sur laquelle on cherche le contour
# marges ajoutées autour du visuel, en fraction de son plus grand côté (gauche, haut, droite, bas)
MARGES = (0.045, 0.035, 0.045, 0.075)
TEINTE = (52, 60, 72)  # gris bleuté, plus naturel qu'un noir pur
HALO = {"flou": 0.022, "opacite": 0.30}                              # halo tout autour
PORTEE = {"flou": 0.030, "dx": 0.010, "dy": 0.028, "opacite": 0.42}  # ombre portée vers le bas
MAX_MEMO = 8
_memo = OrderedDict()
_verrou = threading.Lock()


def _plus_sombre(img):
    r, g, b = img.split()
    return ImageChops.darker(ImageChops.darker(r, g), b)


def silhouette(rgb: Image.Image):
    """Masque (mode L, 255 = produit, à la taille de l'image), ou None si l'image n'a pas de fond blanc (photo
    d'ambiance) ou si elle est presque toute blanche.
    Le fond est le blanc relié aux bords de l'image ; les bords du produit, même très pâles (produit blanc dont le
    reflet touche presque le blanc du fond), arrêtent ce remplissage. Les blancs à l'intérieur du produit en font partie."""
    import cv2
    echelle = min(1.0, COTE_MASQUE / max(rgb.size))
    petit = rgb if echelle == 1.0 else rgb.resize((max(1, int(rgb.width * echelle)), max(1, int(rgb.height * echelle))),
                                                 Image.BOX)
    w, h = petit.size
    gris = np.asarray(_plus_sombre(petit), dtype=np.uint8)
    lisse = cv2.GaussianBlur(gris, (3, 3), 0)
    # bords : variation de clarté, même faible (le fond blanc des visuels nettoyés est parfaitement uni)
    gx, gy = cv2.Sobel(lisse, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(lisse, cv2.CV_32F, 0, 1, ksize=3)
    bord = (cv2.magnitude(gx, gy) > SEUIL_BORD).astype(np.uint8)
    bord = cv2.dilate(bord, np.ones((3, 3), np.uint8))
    libre = ((lisse >= SEUIL_BLANC) & (bord == 0)).astype(np.uint8)
    cadre = np.pad(libre, 1, constant_values=1)  # le tour de l'image relie tout le fond qui touche un bord
    remplir_cadre = np.zeros((h + 4, w + 4), np.uint8)
    cv2.floodFill(cadre, remplir_cadre, (0, 0), 2)
    fond = cadre[1:-1, 1:-1] == 2
    part_fond = fond.mean()
    if part_fond < 0.02 or part_fond > 0.985:
        return None
    produit = (~fond).astype(np.uint8) * 255
    # le liseré de bord (dilaté) a mordu sur le fond : on le retire, puis on referme les petites brèches
    produit = cv2.erode(produit, np.ones((3, 3), np.uint8))
    k = max(3, int(round(max(w, h) * 0.012)) | 1)
    produit = cv2.morphologyEx(produit, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    contours, _ = cv2.findContours(produit, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    surface_min = 0.002 * w * h  # poussières et grain de l'image ignorés
    plein = np.zeros_like(produit)
    cv2.drawContours(plein, [c for c in contours if cv2.contourArea(c) >= surface_min], -1, 255, thickness=-1)
    if plein.mean() < 255 * 0.01:
        return None
    masque = Image.fromarray(plein, "L")
    if masque.size != rgb.size:
        masque = masque.resize(rgb.size, Image.BILINEAR).point(lambda v: 255 if v >= 128 else 0)
    return masque


def _cle(img: Image.Image, transparent: bool) -> str:
    return hashlib.sha1(img.convert("RGB").resize((48, 48)).tobytes()
                        + f"{img.size}|{transparent}".encode()).hexdigest()


def appliquer(img: Image.Image, transparent: bool = False) -> Image.Image:
    """Le visuel avec son ombre douce, sur une image agrandie des MARGES.
    transparent=False : image RVB sur fond blanc (rendu habituel).
    transparent=True : image RVBA dont le fond est transparent (un élément ajouté passe derrière le visuel).
    Si le contour du produit n'est pas trouvé (pas de fond blanc), le visuel est rendu inchangé."""
    cle = _cle(img, transparent)
    with _verrou:
        if cle in _memo:
            _memo.move_to_end(cle)
            return _memo[cle]
    rgb = img.convert("RGB")
    if max(rgb.size) > COTE_MAX:
        rgb = rgb.copy()
        rgb.thumbnail((COTE_MAX, COTE_MAX), Image.LANCZOS)
    masque = silhouette(rgb)
    if masque is None:
        resultat = img
    else:
        resultat = _composer(rgb, masque, transparent)
        resultat.info["ressortir_source"] = img  # pour retrouver l'original (version transparente, voir affiche.py)
    with _verrou:
        _memo[cle] = resultat
        _memo.move_to_end(cle)
        while len(_memo) > MAX_MEMO:
            _memo.popitem(last=False)
    return resultat


def _composer(rgb, masque, transparent):
    w, h = rgb.size
    cote = max(w, h)
    g, t, d, b = (int(round(m * cote)) for m in MARGES)
    W, H = w + g + d, h + t + b
    m_grand = Image.new("L", (W, H), 0)
    m_grand.paste(masque, (g, t))

    def ombre(flou, dx, dy, opacite):
        decale = Image.new("L", (W, H), 0)
        decale.paste(masque, (g + int(round(dx * cote)), t + int(round(dy * cote))))
        return np.asarray(decale.filter(ImageFilter.GaussianBlur(flou * cote)), dtype=np.float32) / 255 * opacite

    a = ombre(HALO["flou"], 0, 0, HALO["opacite"])
    p = ombre(PORTEE["flou"], PORTEE["dx"], PORTEE["dy"], PORTEE["opacite"])
    alpha = 1 - (1 - a) * (1 - p)                      # les deux ombres se cumulent
    dedans = np.asarray(m_grand, dtype=np.float32) / 255
    alpha *= (1 - dedans)                              # jamais d'ombre sur le produit lui-même
    alpha8 = Image.fromarray(np.clip(alpha * 255, 0, 255).astype(np.uint8), "L")

    if transparent:
        sortie = Image.new("RGBA", (W, H), TEINTE + (0,))
        sortie.putalpha(alpha8)
        produit = Image.new("RGBA", (W, H), (255, 255, 255, 0))
        produit.paste(rgb.convert("RGBA"), (g, t))
        # bord du produit adouci d'un pixel, pour un détourage sans escalier
        produit.putalpha(m_grand.filter(ImageFilter.GaussianBlur(0.6)))
        sortie.alpha_composite(produit)
        return sortie
    fond = Image.new("RGB", (W, H), (255, 255, 255))
    fond.paste(Image.new("RGB", (W, H), TEINTE), (0, 0), alpha8)
    grand = Image.new("RGB", (W, H), (255, 255, 255))
    grand.paste(rgb, (g, t))
    # « produit » multiplié par l'ombre : le fond blanc du visuel laisse voir l'ombre, le produit reste intact
    return ImageChops.multiply(fond, grand)
