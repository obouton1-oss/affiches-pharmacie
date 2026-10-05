"""Génération du PDF d'affiche promo (fond blanc, prêt à imprimer).

Ordre de haut en bas : visuel, marque (grande), détail du produit (plus petit), prix barré, prix, dates, logo.
Chaque élément (visuel, marque, détail, prix barré, prix, dates, logo) peut être déplacé et redimensionné
via `reglages = {element: {"dx": ..., "dy": ..., "s": ...}}` :
  dx, dy : déplacement en fraction de la largeur / hauteur de la page (dy positif = vers le haut)
  s      : facteur de taille (1 = taille automatique)
"""
import io
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, A5, A6
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

VERT = HexColor("#175848")
VERT_CLAIR = HexColor("#9ABB1F")
GRIS = HexColor("#6B6B6B")
LOGO = Path(__file__).parent / "logo.png"

FORMATS = {"A4": A4, "A5": A5, "A6": A6}
ELEMENTS = {"image": "Visuel", "marque": "Marque", "detail": "Détail", "prix_barre": "Prix barré",
            "prix": "Prix", "dates": "Dates", "logo": "Logo"}
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]
GRAS, NORMAL = "Helvetica-Bold", "Helvetica"

DOSSIER_POLICES = Path(__file__).parent / "polices"
POLICES = ["Helvetica", "Montserrat", "Poppins", "Lato", "Open Sans", "Nunito", "Oswald", "Playfair Display"]
_fichiers_police = {"Open Sans": "OpenSans", "Playfair Display": "PlayfairDisplay"}
STYLE_DEFAUT = {"police": "Helvetica", "couleur_nom": "#175848", "couleur_prix": "#000000",
                "couleur_accent": "#9ABB1F", "couleur_secondaire": "#6B6B6B",
                "fond_prix": True, "couleur_fond_prix": "#FFD500"}
THEMES = {
    "Impact (prix noir sur jaune)": {"couleur_nom": "#175848", "couleur_prix": "#000000",
                                     "couleur_accent": "#9ABB1F", "couleur_secondaire": "#6B6B6B",
                                     "fond_prix": True, "couleur_fond_prix": "#FFD500"},
    "Charte pharmacie (vert)": {"couleur_nom": "#175848", "couleur_prix": "#175848",
                                "couleur_accent": "#9ABB1F", "couleur_secondaire": "#6B6B6B",
                                "fond_prix": False},
    "Promo (prix rouge)": {"couleur_nom": "#222222", "couleur_prix": "#D62828",
                           "couleur_accent": "#D62828", "couleur_secondaire": "#6B6B6B",
                           "fond_prix": False},
    "Bleu": {"couleur_nom": "#12467A", "couleur_prix": "#12467A",
             "couleur_accent": "#3C9BD6", "couleur_secondaire": "#6B6B6B", "fond_prix": False},
    "Noir et blanc": {"couleur_nom": "#111111", "couleur_prix": "#111111",
                      "couleur_accent": "#111111", "couleur_secondaire": "#555555", "fond_prix": False},
}
_polices_enregistrees = {}


def polices_famille(famille: str):
    """Retourne (nom normal, nom gras) utilisables dans reportlab ; Helvetica si la police est absente."""
    if famille in _polices_enregistrees:
        return _polices_enregistrees[famille]
    paire = (NORMAL, GRAS)
    if famille != "Helvetica":
        base = _fichiers_police.get(famille, famille)
        f_reg, f_bold = DOSSIER_POLICES / f"{base}-Regular.ttf", DOSSIER_POLICES / f"{base}-Bold.ttf"
        if f_reg.exists() and f_bold.exists():
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            try:
                pdfmetrics.registerFont(TTFont(f"{base}-R", str(f_reg)))
                pdfmetrics.registerFont(TTFont(f"{base}-B", str(f_bold)))
                paire = (f"{base}-R", f"{base}-B")
            except Exception:
                paire = (NORMAL, GRAS)
    _polices_enregistrees[famille] = paire
    return paire


def _hauteur_chiffre(nom_police: str) -> float:
    """Hauteur des chiffres/capitales, en fraction du corps (0,72 pour Helvetica)."""
    from reportlab.pdfbase import pdfmetrics
    try:
        face = pdfmetrics.getFont(nom_police).face
        cap = getattr(face, "capHeight", None)
        if cap:
            return max(0.55, min(0.85, cap / 1000.0))
    except Exception:
        pass
    return 0.72


def reglages_defaut():
    return {e: {"dx": 0.0, "dy": 0.0, "s": 1.0} for e in ELEMENTS}


def parse_prix(texte):
    """'12,9' -> Decimal('12.90'). Retourne None si vide ou invalide."""
    if texte is None or not str(texte).strip():
        return None
    try:
        v = Decimal(str(texte).replace("€", "").replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return None
    return v.quantize(Decimal("0.01")) if v >= 0 else None


def libelle_dates(debut: date | None, fin: date | None) -> str:
    if debut and fin:
        if debut.year == fin.year:
            d = f"{debut.day}" + ("er" if debut.day == 1 else "")
            if debut.month != fin.month:
                d += f" {MOIS[debut.month - 1]}"
            return f"Du {d} au {fin.day}{'er' if fin.day == 1 else ''} {MOIS[fin.month - 1]} {fin.year}"
        return (f"Du {debut.day} {MOIS[debut.month - 1]} {debut.year} "
                f"au {fin.day} {MOIS[fin.month - 1]} {fin.year}")
    if fin:
        return f"Jusqu'au {fin.day}{'er' if fin.day == 1 else ''} {MOIS[fin.month - 1]} {fin.year}"
    if debut:
        return f"À partir du {debut.day}{'er' if debut.day == 1 else ''} {MOIS[debut.month - 1]} {debut.year}"
    return ""


def _lignes_auto(texte, police, taille, largeur_max):
    mots, lignes, cour = texte.split(), [], ""
    for m in mots:
        test = f"{cour} {m}".strip()
        if stringWidth(test, police, taille) <= largeur_max or not cour:
            cour = test
        else:
            lignes.append(cour)
            cour = m
    if cour:
        lignes.append(cour)
    return lignes


def _ajuster_titre(texte, police, largeur, hauteur, taille_max, max_lignes=3):
    """Si le texte contient des retours à la ligne, ils sont respectés tels quels."""
    forcees = [l.strip() for l in texte.split("\n") if l.strip()]
    manuel = len(forcees) > 1
    taille = taille_max
    while taille > 6:
        lignes = forcees if manuel else _lignes_auto(texte.replace("\n", " "), police, taille, largeur)
        if ((manuel or len(lignes) <= max_lignes) and len(lignes) * taille * 1.15 <= hauteur
                and all(stringWidth(l, police, taille) <= largeur for l in lignes)):
            return lignes, taille
        taille -= 1
    return (forcees if manuel else _lignes_auto(texte, police, 6, largeur)), 6


def _prix_parts(p: Decimal):
    ent, cts = divmod(int(p * 100), 100)
    return str(ent), (f"{cts:02d}" if cts else "")


def _largeur_prix(p, taille, gras=GRAS):
    ent, cts = _prix_parts(p)
    return (stringWidth(ent, gras, taille)
            + taille * 0.08
            + max(stringWidth(cts, gras, taille * 0.42) if cts else 0,
                  stringWidth("€", gras, taille * 0.42)))


def _dessiner_prix(c, p, cx, base_y, taille, gras=GRAS, couleur=VERT, hc=0.72):
    ent, cts = _prix_parts(p)
    w = _largeur_prix(p, taille, gras)
    x = cx - w / 2
    c.setFillColor(couleur)
    c.setFont(gras, taille)
    c.drawString(x, base_y, ent)
    x2 = x + stringWidth(ent, gras, taille) + taille * 0.08
    petit = taille * 0.42
    cap = taille * hc  # hauteur des chiffres
    c.setFont(gras, petit)
    if cts:
        c.drawString(x2, base_y + cap - petit * hc, "€")  # € en haut, centimes dessous
        c.drawString(x2, base_y, cts)
    else:
        c.drawString(x2, base_y + cap - petit * hc, "€")


def _reg(reglages, el):
    g = (reglages or {}).get(el) or {}
    return float(g.get("dx", 0.0)), float(g.get("dy", 0.0)), max(0.1, float(g.get("s", 1.0)))


def construire_pdf(sortie, taille_page, marque, detail, prix, prix_barre=None, texte_dates="",
                   image=None, afficher_logo=True, reglages=None, majuscules=True, style=None):
    """sortie : chemin ou objet binaire. taille_page : (largeur, hauteur) en points.
    Si la marque est vide, le détail devient la ligne principale.
    Retourne les cadres des éléments, normalisés (x0, y0, x1, y1) depuis le coin haut-gauche."""
    W, H = taille_page
    st = dict(STYLE_DEFAUT)
    st.update(style or {})
    reg, bold = polices_famille(st["police"])
    hc = _hauteur_chiffre(bold)
    col_nom, col_prix = HexColor(st["couleur_nom"]), HexColor(st["couleur_prix"])
    col_accent, col_sec = HexColor(st["couleur_accent"]), HexColor(st["couleur_secondaire"])
    marque, detail = (marque or "").strip(), (detail or "").strip()
    if not marque and detail:
        marque, detail = detail, ""
    c = canvas.Canvas(sortie, pagesize=(W, H))
    c.setTitle(f"Affiche promo - {marque} {detail}".replace("\n", " ").strip())
    m = W * 0.07
    zone_w = W - 2 * m
    y_haut = H - W * 0.05
    cadres = {}  # coordonnées PDF (origine en bas à gauche)

    # ---- Textes : marque (grande) puis détail (plus petit)
    texte_marque = marque or " "
    if majuscules:
        texte_marque = texte_marque.upper()
    if detail:
        l_marque, t_marque = _ajuster_titre(texte_marque, bold, zone_w, H * 0.12, H * 0.058, max_lignes=2)
        l_detail, t_detail = _ajuster_titre(detail, reg, zone_w, H * 0.12, H * 0.038, max_lignes=3)
    else:
        l_marque, t_marque = _ajuster_titre(texte_marque, bold, zone_w, H * 0.15, H * 0.058, max_lignes=3)
        l_detail, t_detail = [], 0.0
    bloc_m = len(l_marque) * t_marque * 1.15
    bloc_d = len(l_detail) * t_detail * 1.15

    # ---- Positions automatiques (de bas en haut) : logo, dates, prix, prix barré, détail, marque, visuel
    pied_h = H * 0.05
    y_pied = H * 0.03  # le logo est collé plus bas que les marges de la page
    logo_img = ImageReader(str(LOGO)) if (afficher_logo and LOGO.exists()) else None
    cy_logo = y_pied + pied_h / 2
    y = y_pied + pied_h + H * 0.015

    y_dates = y
    taille_d = 0.0
    if texte_dates:
        taille_d = H * 0.022
        while stringWidth(texte_dates, reg, taille_d) > zone_w and taille_d > 6:
            taille_d -= 0.5
        y += taille_d + H * 0.022

    fond = bool(st.get("fond_prix"))
    pad_x, pad_b, pad_h = 0.20, 0.12, 0.16  # marges du bandeau, en fraction du corps du prix
    taille_p = H * (0.22 if fond else 0.14)
    while (_largeur_prix(prix, taille_p, bold) + (2 * pad_x * taille_p if fond else 0)) > zone_w and taille_p > 10:
        taille_p -= 1
    y_prix = y + (taille_p * pad_b if fond else 0)
    y = y_prix + taille_p * hc + (taille_p * pad_h if fond else 0) + H * 0.02

    taille_b = 0.0
    y_barre = y
    if prix_barre is not None:
        taille_b = taille_p * (0.25 if fond else 0.30)
        y_barre = y + taille_b * 0.3  # place sous le texte pour le bout du trait diagonal
        y = y_barre + taille_b * 0.95 + H * 0.015

    y_detail = y + H * 0.005
    if detail:
        y = y_detail + bloc_d + H * 0.012
    y_marque = y
    y = y_marque + bloc_m + H * 0.018
    y_image_bas = y
    y_image_haut = y_haut

    # ---- Visuel (en haut)
    if image is not None and y_image_haut - y_image_bas > 0:
        dx, dy, s = _reg(reglages, "image")
        boite_h = y_image_haut - y_image_bas
        iw, ih = image.size
        ech = min(zone_w / iw, boite_h / ih)
        dw, dh = iw * ech * s, ih * ech * s
        cx = W / 2 + dx * W
        cy = y_image_bas + boite_h / 2 + dy * H
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="JPEG", quality=92)
        buf.seek(0)
        c.drawImage(ImageReader(buf), cx - dw / 2, cy - dh / 2, dw, dh)
        cadres["image"] = (cx - dw / 2, cy - dh / 2, cx + dw / 2, cy + dh / 2)

    # ---- Marque puis détail
    for el, lignes, taille, bloc, y_bloc, police in (
            ("marque", l_marque, t_marque, bloc_m, y_marque, bold),
            ("detail", l_detail, t_detail, bloc_d, y_detail, reg)):
        if not lignes:
            continue
        dx, dy, s = _reg(reglages, el)
        t = taille * s
        hb = len(lignes) * t * 1.15
        cx = W / 2 + dx * W
        cy = (y_bloc + bloc / 2) + dy * H
        haut = cy + hb / 2
        c.setFillColor(col_nom)
        c.setFont(police, t)
        for i, l in enumerate(lignes):
            c.drawCentredString(cx, haut - t * 0.85 - i * t * 1.15, l)
        lmax = max(stringWidth(l, police, t) for l in lignes)
        cadres[el] = (cx - lmax / 2, haut - hb, cx + lmax / 2, haut)

    # ---- Prix barré
    if prix_barre is not None:
        dx, dy, s = _reg(reglages, "prix_barre")
        ent, cts = _prix_parts(prix_barre)
        txt = f"{ent},{cts or '00'} €" if cts else f"{ent} €"
        t = taille_b * s
        cx = W / 2 + dx * W
        base = (y_barre + taille_b * 0.3) + dy * H - t * 0.3
        wt = stringWidth(txt, reg, t)
        c.setFillColor(col_sec)
        c.setFont(reg, t)
        c.drawCentredString(cx, base, txt)
        c.setStrokeColor(col_sec)
        c.setLineWidth(max(1.5, t * 0.08))
        c.setLineCap(1)  # extrémités arrondies
        c.line(cx - wt / 2 - t * 0.1, base - t * 0.25, cx + wt / 2 + t * 0.1, base + t * 0.95)  # diagonale ↗
        cadres["prix_barre"] = (cx - wt / 2, base - t * 0.25, cx + wt / 2, base + t * 0.95)

    # ---- Prix
    dx, dy, s = _reg(reglages, "prix")
    p = taille_p * s
    cx = W / 2 + dx * W
    base = (y_prix + taille_p * hc / 2) + dy * H - p * hc / 2
    wp = _largeur_prix(prix, p, bold)
    if fond:  # bandeau coloré derrière le prix
        bx0, bx1 = cx - wp / 2 - pad_x * p, cx + wp / 2 + pad_x * p
        by0, by1 = base - pad_b * p, base + p * hc + pad_h * p
        c.setFillColor(HexColor(st["couleur_fond_prix"]))
        c.roundRect(bx0, by0, bx1 - bx0, by1 - by0, 0.14 * p, stroke=0, fill=1)
        cadres["prix"] = (bx0, by0, bx1, by1)
    else:
        c.setStrokeColor(col_accent)
        c.setLineWidth(max(1.5, H * 0.004 * s))
        c.line(cx - wp / 2, base - p * 0.07, cx + wp / 2, base - p * 0.07)
        cadres["prix"] = (cx - wp / 2, base - p * 0.09, cx + wp / 2, base + p * hc)
    _dessiner_prix(c, prix, cx, base, p, bold, col_prix, hc)

    # ---- Dates
    if texte_dates:
        dx, dy, s = _reg(reglages, "dates")
        t = taille_d * s
        cx = W / 2 + dx * W
        base = (y_dates + taille_d * 0.3) + dy * H - t * 0.3
        c.setFillColor(col_sec)
        c.setFont(reg, t)
        c.drawCentredString(cx, base, texte_dates)
        wt = stringWidth(texte_dates, reg, t)
        cadres["dates"] = (cx - wt / 2, base - t * 0.2, cx + wt / 2, base + t * 0.8)

    # ---- Logo + nom de la pharmacie
    if logo_img is not None:
        dx, dy, s = _reg(reglages, "logo")
        lw, lh = logo_img.getSize()
        h_logo = pied_h * s
        w_logo = h_logo * lw / lh
        ts = H * 0.017 * s
        ecart = W * 0.015 * s
        tw = stringWidth("Pharmacie Bouton", bold, ts)
        gw = w_logo + ecart + tw
        cx = W / 2 + dx * W
        cy = cy_logo + dy * H
        x_g = cx - gw / 2
        c.drawImage(logo_img, x_g, cy - h_logo / 2, w_logo, h_logo, mask="auto")
        c.setFillColor(VERT)
        c.setFont(bold, ts)
        c.drawString(x_g + w_logo + ecart, cy - ts * 0.3, "Pharmacie Bouton")
        cadres["logo"] = (x_g, cy - h_logo / 2, x_g + gw, cy + h_logo / 2)

    c.showPage()
    c.save()
    return {k: (x0 / W, 1 - y1 / H, x1 / W, 1 - y0 / H) for k, (x0, y0, x1, y1) in cadres.items()}


def rendu(*args, **kwargs):
    """Retourne (octets du PDF, cadres normalisés des éléments)."""
    buf = io.BytesIO()
    cadres = construire_pdf(buf, *args, **kwargs)
    return buf.getvalue(), cadres


def pdf_en_octets(*args, **kwargs) -> bytes:
    return rendu(*args, **kwargs)[0]


def apercu_png(pdf_octets: bytes, dpi=90) -> bytes:
    import pymupdf as fitz
    doc = fitz.open(stream=pdf_octets, filetype="pdf")
    return doc[0].get_pixmap(dpi=int(round(dpi))).tobytes("png")


# ----------------------------------------------------------------------------
# Mise en page pour l'impression : exemplaires et planches A4
# ----------------------------------------------------------------------------
def disposition_a4(taille_page):
    """Meilleure grille d'affiches sur une feuille A4 (portrait ou paysage).
    Retourne (nb_par_feuille, (largeur_feuille, hauteur_feuille), colonnes, lignes)."""
    pw, ph = taille_page
    meilleur = (1, A4, 1, 1)
    for feuille in (A4, (A4[1], A4[0])):
        cols = int((feuille[0] + 0.5) // pw)
        lignes = int((feuille[1] + 0.5) // ph)
        if cols * lignes > meilleur[0]:
            meilleur = (cols * lignes, feuille, cols, lignes)
    return meilleur


def pdf_impression(pdf_unitaire: bytes, taille_page, exemplaires: int = 1, planche: bool = False):
    """Assemble le PDF à imprimer.
    - planche=False : `exemplaires` pages au format de l'affiche.
    - planche=True  : plusieurs affiches par feuille A4 (ex. 2 x A5, 4 x A6), traits de coupe discrets.
    Retourne (octets du PDF, nombre de feuilles, affiches par feuille)."""
    import pymupdf
    exemplaires = max(1, int(exemplaires))
    src = pymupdf.open(stream=pdf_unitaire, filetype="pdf")
    sortie = pymupdf.open()
    par_feuille, feuille, cols, lignes = disposition_a4(taille_page)
    if not planche or par_feuille == 1:
        for _ in range(exemplaires):
            sortie.insert_pdf(src)
        return sortie.tobytes(), exemplaires, 1
    pw, ph = taille_page
    SW, SH = feuille
    x0 = (SW - cols * pw) / 2
    y0 = (SH - lignes * ph) / 2
    feuilles = -(-exemplaires // par_feuille)
    restant = exemplaires
    for _ in range(feuilles):
        page = sortie.new_page(width=SW, height=SH)
        nb = min(par_feuille, restant)
        restant -= nb
        for k in range(nb):
            r, c = divmod(k, cols)
            page.show_pdf_page(pymupdf.Rect(x0 + c * pw, y0 + r * ph, x0 + (c + 1) * pw, y0 + (r + 1) * ph), src, 0)
        gris = (0.6, 0.6, 0.6)
        for c in range(1, cols):  # traits de coupe entre les affiches
            x = x0 + c * pw
            page.draw_line((x, y0 - 6), (x, y0 + lignes * ph + 6), color=gris, width=0.4, dashes="[3 3] 0")
        for r in range(1, lignes):
            y = y0 + r * ph
            page.draw_line((x0 - 6, y), (x0 + cols * pw + 6, y), color=gris, width=0.4, dashes="[3 3] 0")
    return sortie.tobytes(), feuilles, par_feuille
