"""Page A4 regroupant plusieurs affiches (6 au maximum par feuille), en portrait ou en paysage.

Chaque feuille comporte un titre personnalisable en haut, une grille de vignettes (visuel, marque, détail,
prix barré éventuel, prix ou offre, pastille éventuelle) et le logo de la pharmacie en pied de page.
Au-delà de 6 affiches, plusieurs feuilles sont produites, avec des vignettes réparties de façon équilibrée.
"""
import io
from decimal import Decimal
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

import affiche as af
import promos

MAX_PAR_PAGE = 6
TITRE_DEFAUT = "Promos du mois"
PORTRAIT, PAYSAGE = "Portrait", "Paysage"
ORIENTATIONS = (PORTRAIT, PAYSAGE)
# nombre de vignettes -> (colonnes, lignes)
GRILLES = {PORTRAIT: {1: (1, 1), 2: (1, 2), 3: (1, 3), 4: (2, 2), 5: (2, 3), 6: (2, 3)},
           PAYSAGE: {1: (1, 1), 2: (2, 1), 3: (3, 1), 4: (2, 2), 5: (3, 2), 6: (3, 2)}}
MARGE = 12 * mm
ECART = 5 * mm
PAD_X, PAD_B, PAD_H = 0.20, 0.12, 0.16  # marges du bandeau de prix, en fraction du corps du prix (comme l'affiche)


def repartir(n: int, maxi: int = MAX_PAR_PAGE) -> list[int]:
    """Répartit n affiches sur le moins de feuilles possible, de façon équilibrée (7 -> 4 + 3, 13 -> 5 + 4 + 4)."""
    if n <= 0:
        return []
    pages = -(-n // maxi)
    base, reste = divmod(n, pages)
    return [base + 1] * reste + [base] * (pages - reste)


def _prix_depuis_texte(valeur):
    try:
        return Decimal(str(valeur)) if valeur not in (None, "") else None
    except Exception:
        return None


def _bloc_prix(c, prix, grand, kicker, cx, y_bas, taille, largeur_max, st, bold, hc):
    """Dessine le prix (ou le texte de l'offre, avec son petit texte au-dessus), avec bandeau ou filet, à partir
    de y_bas. Retourne le haut du bloc et le corps utilisé."""
    fond = bool(st.get("fond_prix"))
    taille *= af._facteur_bandeau(grand, kicker)
    while True:
        largeur, h_kicker = af._contenu_bandeau(prix, grand, kicker, taille, bold, hc)
        if largeur + (2 * PAD_X * taille if fond else 0) <= largeur_max or taille <= 8:
            break
        taille -= 1
    base = y_bas + (taille * PAD_B if fond else taille * 0.09)
    if fond:
        x0, x1 = cx - largeur / 2 - PAD_X * taille, cx + largeur / 2 + PAD_X * taille
        y0, y1 = base - PAD_B * taille, base + taille * hc + h_kicker + PAD_H * taille
        c.setFillColor(HexColor(st["couleur_fond_prix"]))
        c.roundRect(x0, y0, x1 - x0, y1 - y0, 0.14 * taille, stroke=0, fill=1)
        haut = y1
    else:
        c.setStrokeColor(HexColor(st["couleur_accent"]))
        c.setLineWidth(max(1.2, taille * 0.04))
        c.line(cx - largeur / 2, base - taille * 0.07, cx + largeur / 2, base - taille * 0.07)
        haut = base + taille * hc + h_kicker
    af._dessiner_bandeau(c, prix, grand, kicker, cx, base, taille, bold, HexColor(st["couleur_prix"]), hc)
    return haut, taille


def _prix_barre(c, prix_barre, cx, y_bas, taille, reg, couleur):
    """Dessine le prix barré (trait en diagonale) à partir de y_bas. Retourne le haut du bloc."""
    ent, cts = af._prix_parts(prix_barre)
    texte = f"{ent},{cts or '00'} €" if cts else f"{ent} €"
    base = y_bas + taille * 0.3
    largeur = stringWidth(texte, reg, taille)
    c.setFillColor(couleur)
    c.setFont(reg, taille)
    c.drawCentredString(cx, base, texte)
    c.setStrokeColor(couleur)
    c.setLineWidth(max(1.0, taille * 0.08))
    c.setLineCap(1)
    c.line(cx - largeur / 2 - taille * 0.1, base - taille * 0.25, cx + largeur / 2 + taille * 0.1, base + taille * 0.95)
    return base + taille * 0.95


def _lignes_centrees(c, lignes, taille, police, couleur, cx, haut):
    c.setFillColor(couleur)
    c.setFont(police, taille)
    for i, ligne in enumerate(lignes):
        c.drawCentredString(cx, haut - taille * 0.85 - i * taille * 1.15, ligne)


def _image(c, img, x, y, w, h):
    """Dessine l'image dans la boîte (x, y, w, h), sans la déformer, centrée."""
    if img is None or w <= 0 or h <= 0:
        return
    iw, ih = img.size
    ech = min(w / iw, h / ih)
    dw, dh = iw * ech, ih * ech
    tampon = io.BytesIO()
    img.convert("RGB").save(tampon, format="JPEG", quality=92)
    tampon.seek(0)
    c.drawImage(ImageReader(tampon), x + (w - dw) / 2, y + (h - dh) / 2, dw, dh)


def _images(c, images, x, y, w, h, sur_le_bas=False, coin=None):
    """Dessine un ou plusieurs visuels dans la boîte (x, y, w, h) : un seul, centré ; plusieurs, côte à côte sur
    une rangée (même hauteur), comme sur l'affiche. sur_le_bas : la rangée repose sur le bas de la boîte.
    coin : objet posé dans un coin haut de la boîte (pastille), voir affiche._disposition_rangee."""
    images = [im for im in (images or []) if im is not None][:af.MAX_VISUELS]
    if len(images) <= 1:
        _image(c, images[0] if images else None, x, y, w, h)
        return
    if w <= 0 or h <= 0:
        return
    positions, largeur_groupe, hauteur = af._disposition_rangee(images, w, h, coin=coin)
    gauche, bas = x + (w - largeur_groupe) / 2, (y if (sur_le_bas or coin) else y + (h - hauteur) / 2)
    for img, (x_rel, w_rel) in zip(images, positions):
        tampon = io.BytesIO()
        img.convert("RGB").save(tampon, format="JPEG", quality=92)
        tampon.seek(0)
        c.drawImage(ImageReader(tampon), gauche + x_rel, bas, w_rel, hauteur)


def _texte_sous_prix(c, texte, cx, y_bas, largeur, h, reg, couleur):
    """Texte sous le prix (calcul de la promotion, précision), de bas en haut à partir de y_bas. Retourne le haut."""
    if not texte:
        return y_bas
    lignes, t = af._ajuster_titre(texte, reg, largeur, h * 0.14, h * 0.045, max_lignes=3)
    bloc = len(lignes) * t * 1.15
    _lignes_centrees(c, lignes, t, reg, couleur, cx, y_bas + bloc)
    return y_bas + bloc + h * 0.012


def _logo_marque(c, img, cx, y_bas, hauteur):
    """Logo de marque (image PIL) centré en cx, de la hauteur donnée, posé sur y_bas."""
    largeur = hauteur * img.width / img.height
    c.drawImage(ImageReader(img), cx - largeur / 2, y_bas, largeur, hauteur, mask="auto")


def _vignette(c, x, y, w, h, e, st):
    """Vignette d'une affiche. (x, y) : coin inférieur gauche. e : entrée de l'historique, avec « _images » (liste
    d'images PIL : le visuel principal puis les autres)."""
    images = e.get("_images") or ([e["_image"]] if e.get("_image") is not None else [])
    reg, bold = af.polices_famille(st["police"])
    hc = af._hauteur_chiffre(bold)
    col_nom, col_sec = HexColor(st["couleur_nom"]), HexColor(st["couleur_secondaire"])

    c.setFillColor(white)
    c.setStrokeColor(HexColor("#D9D9D9"))
    c.setLineWidth(0.8)
    c.roundRect(x, y, w, h, 3 * mm, stroke=1, fill=1)

    marque, detail = (e.get("marque") or "").strip(), (e.get("detail") or "").strip()
    logo_m = e.get("_logo_marque") if marque else None  # logo de la marque, imprimé à la place de son nom
    if not marque and detail:
        marque, detail = detail, ""
    if e.get("majuscules", True):
        marque = marque.upper()
    rendu = promos.rendu_entree(e)  # autre type de promotion : texte de l'offre, pastille…
    if rendu:
        prix, prix_barre = rendu["prix"], rendu["prix_barre"]
        grand, kicker, ligne, pastille = rendu["grand"], rendu["kicker"], rendu["ligne"], rendu["pastille"]
    else:
        prix, prix_barre = _prix_depuis_texte(e.get("prix")), _prix_depuis_texte(e.get("prix_barre"))
        grand = kicker = ligne = pastille = ""
    if prix is None and not grand:
        return
    fond = bool(st.get("fond_prix"))
    horizontal = w / h >= 1.05
    pad = min(w, h) * 0.06

    if not horizontal:  # vignette en hauteur : visuel, marque, détail, prix barré, prix (de haut en bas)
        zone_w, cx = w - 2 * pad, x + w / 2
        y_cur = _texte_sous_prix(c, ligne, cx, y + pad, zone_w, h, reg, col_nom)
        haut, tp = _bloc_prix(c, prix, grand, kicker, cx, y_cur, h * (0.15 if fond else 0.11), zone_w, st, bold, hc)
        y_cur = haut + h * 0.015
        if prix_barre is not None:
            tb = tp * (0.25 if fond else 0.30)
            y_cur = _prix_barre(c, prix_barre, cx, y_cur, tb, reg, col_sec) + h * 0.012
        if detail:
            lignes, t = af._ajuster_titre(detail, reg, zone_w, h * 0.12, h * 0.040, max_lignes=3)
            bloc = len(lignes) * t * 1.15
            _lignes_centrees(c, lignes, t, reg, col_nom, cx, y_cur + bloc)
            y_cur += bloc + h * 0.012
        if logo_m is not None:
            hl = min(h * 0.075, zone_w * 0.85 * logo_m.height / logo_m.width)
            _logo_marque(c, logo_m, cx, y_cur, hl)
            y_cur += hl + h * 0.015
        else:
            lignes, t = af._ajuster_titre(marque or " ", bold, zone_w, h * 0.10, h * 0.070,
                                          max_lignes=2 if detail else 3)
            bloc = len(lignes) * t * 1.15
            _lignes_centrees(c, lignes, t, bold, col_nom, cx, y_cur + bloc)
            y_cur += bloc + h * 0.015
        r = af.rayon_pastille(pastille, min(w, h * 1.1), bold) if pastille else 0.0
        _images(c, images, x + pad, y_cur, zone_w, y + h - pad - y_cur, sur_le_bas=True,
                coin=("droite", 1.95 * r, 1.95 * r) if pastille else None)
        if pastille:  # en haut à droite du visuel
            _pastille(c, pastille, x + w - pad - r * 0.95, y + h - pad - r * 0.95, r, st, bold, fond)
        return

    # vignette en largeur : visuel à gauche ; marque, détail, prix barré et prix à droite
    part = {0: 0.40, 1: 0.40, 2: 0.48, 3: 0.54}.get(len(images), 0.58)  # plusieurs visuels : plus de place à gauche
    larg_image = min(w * part, (h - 2 * pad) * 0.95 * max(1, len(images)))
    r_p = af.rayon_pastille(pastille, min(w, h * 1.1), bold) if pastille else 0.0
    _images(c, images, x + pad, y + pad, larg_image, h - 2 * pad,
            coin=("gauche", 1.95 * r_p, 1.95 * r_p) if pastille else None)
    tx0 = x + pad + larg_image + pad
    zone_w = x + w - pad - tx0
    cx = tx0 + zone_w / 2
    y_cur = _texte_sous_prix(c, ligne, cx, y + pad, zone_w, h, reg, col_nom)
    haut, tp = _bloc_prix(c, prix, grand, kicker, cx, y_cur, h * (0.26 if fond else 0.20), zone_w, st, bold, hc)
    y_cur = haut + h * 0.02
    if prix_barre is not None:
        tb = tp * (0.25 if fond else 0.30)
        y_cur = _prix_barre(c, prix_barre, cx, y_cur, tb, reg, col_sec) + h * 0.015
    libre_haut, libre_bas = y + h - pad, y_cur
    dispo = libre_haut - libre_bas
    hl = 0.0
    if logo_m is not None:
        hl = min(h * 0.13, zone_w * 0.9 * logo_m.height / logo_m.width)
    if logo_m is not None and detail:
        l_marque, t_marque = [], 0.0
        l_detail, t_detail = af._ajuster_titre(detail, reg, zone_w, dispo * 0.50, h * 0.09, max_lignes=3)
    elif logo_m is not None:
        l_marque, t_marque, l_detail, t_detail = [], 0.0, [], 0.0
    elif detail:
        l_marque, t_marque = af._ajuster_titre(marque or " ", bold, zone_w, dispo * 0.45, h * 0.16, max_lignes=2)
        l_detail, t_detail = af._ajuster_titre(detail, reg, zone_w, dispo * 0.50, h * 0.09, max_lignes=3)
    else:
        l_marque, t_marque = af._ajuster_titre(marque or " ", bold, zone_w, dispo * 0.90, h * 0.16, max_lignes=3)
        l_detail, t_detail = [], 0.0
    bloc_m, bloc_d = (hl if logo_m is not None else len(l_marque) * t_marque * 1.15), len(l_detail) * t_detail * 1.15
    ecart = h * 0.02 if l_detail else 0.0
    haut_groupe = libre_bas + (dispo + bloc_m + ecart + bloc_d) / 2  # groupe marque + détail centré dans l'espace libre
    if logo_m is not None:
        _logo_marque(c, logo_m, cx, haut_groupe - hl, hl)
    else:
        _lignes_centrees(c, l_marque, t_marque, bold, col_nom, cx, haut_groupe)
    if l_detail:
        _lignes_centrees(c, l_detail, t_detail, reg, col_nom, cx, haut_groupe - bloc_m - ecart)
    if pastille:  # en haut à gauche du visuel (la colonne de droite porte les textes)
        r = af.rayon_pastille(pastille, min(w, h * 1.1), bold)
        _pastille(c, pastille, x + pad + r * 0.95, y + h - pad - r * 0.95, r, st, bold, fond)


def _pastille(c, texte, cx, cy, rayon, st, bold, fond):
    """Pastille aux couleurs du bandeau de prix (noir sur jaune) ou, sans bandeau, de la couleur du prix."""
    couleur_prix = HexColor(st["couleur_prix"])
    af._pastille(c, texte, cx, cy, rayon, bold, HexColor(st["couleur_fond_prix"]) if fond else couleur_prix,
                 couleur_prix if fond else white)


def _identite(identite):
    """(nom, chemin du logo ou None) à imprimer en pied de page ; identite None = pharmacie d'origine."""
    if identite is None:
        return "Pharmacie Bouton", (af.LOGO if af.LOGO.exists() else None)
    chemin = identite.get("logo")
    return str(identite.get("nom") or "").strip(), (chemin if chemin and Path(chemin).exists() else None)


def _pied_de_page(c, largeur_page, y, hauteur, st, bold, identite=None):
    nom, chemin = _identite(identite)
    logo = ImageReader(str(chemin)) if chemin else None
    if logo is None and not nom:
        return
    lw, lh = logo.getSize() if logo else (1, 1)
    w_logo = hauteur * lw / lh if logo else 0.0
    ts = hauteur * 0.36
    ecart = largeur_page * 0.012 if logo else 0.0
    tw = stringWidth(nom, bold, ts)
    largeur_max = largeur_page * 0.9
    if w_logo + ecart + tw > largeur_max:  # nom très long : le tout se réduit pour tenir dans la page
        f = largeur_max / (w_logo + ecart + tw)
        w_logo, ts, ecart, tw, hauteur = w_logo * f, ts * f, ecart * f, tw * f, hauteur * f
    x_g = (largeur_page - (w_logo + ecart + tw)) / 2
    if logo:
        c.drawImage(logo, x_g, y, w_logo, hauteur, mask="auto")
    c.setFillColor(af.VERT)
    c.setFont(bold, ts)
    c.drawString(x_g + w_logo + ecart, y + hauteur / 2 - ts * 0.3, nom)


def construire_planche(sortie, titre, groupes, style=None, afficher_logo=True, orientation=PORTRAIT, identite=None):
    """sortie : chemin ou objet binaire. groupes : une liste d'entrées de l'historique par feuille A4
    (6 au maximum chacune, avec la clé « _images » : liste des visuels de l'affiche). Le titre est repris sur chaque feuille.
    orientation : « Portrait » ou « Paysage »."""
    paysage = orientation == PAYSAGE
    W, H = (A4[1], A4[0]) if paysage else A4
    marge = 10 * mm if paysage else MARGE
    grilles = GRILLES[PAYSAGE if paysage else PORTRAIT]
    st = dict(af.STYLE_DEFAUT)
    st.update(style or {})
    reg, bold = af.polices_famille(st["police"])
    titre = (titre or "").strip()
    c = canvas.Canvas(sortie, pagesize=(W, H))
    c.setTitle(f"Promos – {titre}" if titre else "Promos")
    for entrees in groupes:
        entrees = entrees[:MAX_PAR_PAGE]
        if not entrees:
            continue
        haut_contenu = H - marge
        if titre:  # titre centré, souligné d'un filet de la couleur d'accentuation
            lignes, t = af._ajuster_titre(titre, bold, W - 2 * marge, (17 if paysage else 26) * mm,
                                          32 if paysage else 40, max_lignes=2)
            bloc = len(lignes) * t * 1.15
            _lignes_centrees(c, lignes, t, bold, HexColor(st["couleur_nom"]), W / 2, H - marge)
            y_filet = H - marge - bloc - 3 * mm
            c.setStrokeColor(HexColor(st["couleur_accent"]))
            c.setLineWidth(2.5)
            c.setLineCap(1)
            c.line(W / 2 - 30 * mm, y_filet, W / 2 + 30 * mm, y_filet)
            haut_contenu = y_filet - (5 if paysage else 7) * mm
        bas_contenu = 8 * mm
        if afficher_logo and (identite is not None or af.LOGO.exists()):
            h_logo = (8 if paysage else 9) * mm
            _pied_de_page(c, W, 7 * mm, h_logo, st, bold, identite)
            bas_contenu = 7 * mm + h_logo + 4 * mm

        n = len(entrees)
        cols, lignes_grille = grilles[n]
        larg_zone = W - 2 * marge
        w_v = (larg_zone - (cols - 1) * ECART) / cols
        h_v = (haut_contenu - bas_contenu - (lignes_grille - 1) * ECART) / lignes_grille
        for k, e in enumerate(entrees):
            r, col = divmod(k, cols)
            dans_ligne = min(cols, n - r * cols)  # dernière ligne incomplète : vignettes centrées
            decalage = (cols - dans_ligne) * (w_v + ECART) / 2
            x = marge + decalage + col * (w_v + ECART)
            y = haut_contenu - (r + 1) * h_v - r * ECART
            _vignette(c, x, y, w_v, h_v, e, st)
        c.showPage()
    c.save()


def pdf_planche(titre, groupes, style=None, afficher_logo=True, orientation=PORTRAIT, identite=None) -> bytes:
    tampon = io.BytesIO()
    construire_planche(tampon, titre, groupes, style, afficher_logo, orientation, identite)
    return tampon.getvalue()


def apercus_png(pdf_octets: bytes, dpi=80) -> list[bytes]:
    """Un aperçu PNG par feuille."""
    import pymupdf
    doc = pymupdf.open(stream=pdf_octets, filetype="pdf")
    return [page.get_pixmap(dpi=int(dpi)).tobytes("png") for page in doc]
