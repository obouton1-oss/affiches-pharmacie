"""Génération du PDF d'affiche promo (fond blanc, prêt à imprimer).

Ordre de haut en bas : visuel, marque (grande), détail du produit (plus petit), prix barré, prix, texte sous le prix,
dates, logo. Pour un autre type de promotion (voir promos.py), le prix devient l'offre elle-même (« 3 pour 2 »…),
avec un petit texte au-dessus dans le bandeau et une pastille facultative (« –25 % ») sur le visuel.
Chaque élément (visuel, marque, détail, prix barré, prix, texte sous le prix, pastille, dates, logo) peut être
déplacé et redimensionné
via `reglages = {element: {"dx": ..., "dy": ..., "s": ...}}` :
  dx, dy : déplacement en fraction de la largeur / hauteur de la page (dy positif = vers le haut)
  s      : facteur de taille (1 = taille automatique)
Orientation : une page plus large que haute (voir est_paysage) est mise en page « en paysage » : le visuel (ou les
visuels) occupe la colonne de gauche, les textes, le prix et le logo la colonne de droite, centrés en hauteur.
Une page en portrait garde la mise en page d'origine (de haut en bas, comme ci-dessus).
"""
import io
import math
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4, A5, A6
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

VERT = HexColor("#175848")
VERT_CLAIR = HexColor("#9ABB1F")
GRIS = HexColor("#6B6B6B")
LOGO = Path(__file__).parent / "logo.png"

FORMATS = {"A4": A4, "A5": A5, "A6": A6}
SEUIL_PAYSAGE = 1.05  # rapport largeur / hauteur à partir duquel l'affiche est mise en page en paysage


def est_paysage(taille_page) -> bool:
    """Vrai si la page est nettement plus large que haute (mise en page en paysage)."""
    return taille_page[0] >= taille_page[1] * SEUIL_PAYSAGE


def orienter(taille_page, paysage: bool):
    """Page (largeur, hauteur) en portrait ou en paysage : le petit côté en largeur ou en hauteur."""
    petit, grand = sorted(taille_page)
    return (grand, petit) if paysage else (petit, grand)


ELEMENTS = {"image": "Visuel", "marque": "Marque", "detail": "Détail", "prix_barre": "Prix barré",
            "prix": "Prix", "ligne": "Texte sous le prix", "pastille": "Pastille", "dates": "Dates", "logo": "Logo"}
# Ordre des textes sous le visuel, de haut en bas (réglage « ordre » du style ; le texte sous le prix suit toujours le prix)
ORDRE_DEFAUT = ("marque", "detail", "prix_barre", "prix", "dates")
LIBELLES_ORDRE = {"marque": "Marque", "detail": "Produit (détail)", "prix_barre": "Prix barré", "prix": "Prix promo",
                  "dates": "Dates"}


def ordre_valide(ordre) -> tuple:
    """L'ordre demandé s'il contient exactement les cinq éléments (dans un ordre quelconque), sinon l'ordre habituel."""
    try:
        ordre = tuple(ordre)
    except TypeError:
        return ORDRE_DEFAUT
    return ordre if sorted(ordre) == sorted(ORDRE_DEFAUT) else ORDRE_DEFAUT


MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]
GRAS, NORMAL = "Helvetica-Bold", "Helvetica"

DOSSIER_POLICES = Path(__file__).parent / "polices"
POLICES = ["Helvetica", "Montserrat", "Poppins", "Lato", "Open Sans", "Nunito", "Oswald", "Playfair Display"]
_fichiers_police = {"Open Sans": "OpenSans", "Playfair Display": "PlayfairDisplay"}
STYLE_DEFAUT = {"police": "Helvetica", "couleur_nom": "#175848", "couleur_prix": "#000000",
                "couleur_accent": "#9ABB1F", "couleur_secondaire": "#6B6B6B",
                "fond_prix": True, "couleur_fond_prix": "#FFD500", "cadre": "aucun", "couleur_cadre": "#175848",
                # couleur du détail du produit : vide = la même que la marque (cas d'origine, et des affiches déjà faites)
                "couleur_detail": None,
                # police, gras, italique et souligné propres à chaque texte (voir TEXTES) ; vide = tout comme avant
                "textes": {}}


def couleur_detail(st) -> str:
    """Couleur (hexadécimale) du détail du produit : celle de la marque tant qu'aucune autre n'est choisie."""
    return st.get("couleur_detail") or st["couleur_nom"]


# Textes de l'affiche dont la police, le gras, l'italique et le souligné se choisissent un par un : (libellé, gras d'origine).
# style["textes"] = {texte: {"police": famille, "gras": bool, "italique": True, "souligne": True}} ; seuls les choix qui
# diffèrent de l'origine y figurent (police absente = celle de l'affiche).
TEXTES = {"marque": ("Marque", True), "detail": ("Détail du produit", False), "prix": ("Prix", True),
          "prix_barre": ("Prix barré", False), "ligne": ("Texte sous le prix", False), "dates": ("Dates", False)}
ANGLE_ITALIQUE = 12  # degrés : l'inclinaison de l'Helvetica penchée, appliquée à toutes les polices
_TAN_ITALIQUE = math.tan(math.radians(ANGLE_ITALIQUE))


def textes_valides(brut) -> dict:
    """Les choix de police et de style de texte reconnus, ramenés à leur forme minimale (ce qui diffère de l'origine)."""
    res = {}
    if not isinstance(brut, dict):
        return res
    for texte, (_, gras_origine) in TEXTES.items():
        r = brut.get(texte)
        if not isinstance(r, dict):
            continue
        v = {}
        if isinstance(r.get("police"), str) and r["police"] in POLICES:
            v["police"] = r["police"]
        if "gras" in r and bool(r["gras"]) != gras_origine:
            v["gras"] = bool(r["gras"])
        if r.get("italique"):
            v["italique"] = True
        if r.get("souligne"):
            v["souligne"] = True
        if v:
            res[texte] = v
    return res


def police_texte(st, texte):
    """(nom de police reportlab, italique, souligné) du texte `texte` de l'affiche, d'après le style."""
    r = textes_valides(st.get("textes")).get(texte, {})
    reg, bold = polices_famille(r.get("police") or st["police"])
    return (bold if r.get("gras", TEXTES[texte][1]) else reg), bool(r.get("italique")), bool(r.get("souligne"))


# Cadre autour de l'affiche : en option seulement (aucun par défaut)
CADRES = {"aucun": "Aucun cadre", "fin": "Trait fin", "epais": "Trait épais", "double": "Double trait",
          "arrondi": "Coins arrondis", "pointille": "Pointillés", "coins": "Coins seulement"}
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
    mots, lignes, cour = [m for m in re.split(r"[ \t\r\n]+", texte) if m], [], ""  # l'espace insécable ne coupe pas
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


def _equilibrer(texte, police, taille, largeur, nb_lignes):
    """Mêmes mots, même nombre de lignes, mais lignes de longueurs voisines (au lieu d'un dernier mot isolé)."""
    mots = [m for m in re.split(r"[ \t\r\n]+", texte) if m]
    bas = max((stringWidth(m, police, taille) for m in mots), default=0.0)
    haut = largeur
    for _ in range(24):
        milieu = (bas + haut) / 2
        if len(_lignes_auto(texte, police, taille, milieu)) <= nb_lignes:
            haut = milieu
        else:
            bas = milieu
    return _lignes_auto(texte, police, taille, haut)


def _ajuster_titre(texte, police, largeur, hauteur, taille_max, max_lignes=3, equilibre=False):
    """Si le texte contient des retours à la ligne, ils sont respectés tels quels.
    equilibre : répartit les mots de façon régulière sur les lignes (affiches en paysage)."""
    forcees = [l.strip() for l in texte.split("\n") if l.strip()]
    manuel = len(forcees) > 1
    taille = taille_max
    while taille > 6:
        if manuel and equilibre:  # en paysage (colonne étroite), une ligne imposée trop longue est elle-même coupée
            lignes = [p for l in forcees for p in _lignes_auto(l, police, taille, largeur)]
        else:
            lignes = forcees if manuel else _lignes_auto(texte.replace("\n", " "), police, taille, largeur)
        if ((manuel or len(lignes) <= max_lignes) and len(lignes) * taille * 1.15 <= hauteur
                and all(stringWidth(l, police, taille) <= largeur for l in lignes)):
            if equilibre and not manuel and len(lignes) > 1:
                lignes = _equilibrer(texte.replace("\n", " "), police, taille, largeur, len(lignes))
            return lignes, taille
        taille -= 1
    if manuel:
        return ([p for l in forcees for p in _lignes_auto(l, police, 6, largeur)] if equilibre else forcees), 6
    return _lignes_auto(texte, police, 6, largeur), 6


def _prix_parts(p: Decimal):
    ent, cts = divmod(int(p * 100), 100)
    return str(ent), (f"{cts:02d}" if cts else "")


def _largeur_prix(p, taille, gras=GRAS):
    ent, cts = _prix_parts(p)
    return (stringWidth(ent, gras, taille)
            + taille * 0.08
            + max(stringWidth(cts, gras, taille * 0.42) if cts else 0,
                  stringWidth("€", gras, taille * 0.42)))


def _incliner(c, cx, base, italique):
    """Texte en italique : ouvre un état graphique penché autour du point (cx, base) et retourne le nouveau point
    (0, 0), à fermer avec _fin_inclinaison. Sans italique, ne fait rien et retourne le point tel quel."""
    if not italique:
        return cx, base, False
    c.saveState()
    c.translate(cx, base)
    c.transform(1, 0, _TAN_ITALIQUE, 1, 0, 0)
    return 0.0, 0.0, True


def _fin_inclinaison(c, ouvert):
    if ouvert:
        c.restoreState()


def _souligner(c, x0, x1, base, taille, couleur):
    """Trait sous le texte posé sur la ligne de base `base`, de la couleur du texte."""
    c.saveState()
    c.setStrokeColor(couleur)
    c.setLineWidth(max(0.5, taille * 0.06))
    c.setLineCap(0)
    c.line(x0, base - taille * 0.13, x1, base - taille * 0.13)
    c.restoreState()


def _ecrire_centre(c, texte, cx, base, police, taille, couleur, italique=False, souligne=False):
    """Une ligne de texte centrée en cx, sur la ligne de base `base`."""
    c.setFillColor(couleur)
    c.setFont(police, taille)
    x, b, ouvert = _incliner(c, cx, base, italique)
    c.drawCentredString(x, b, texte)
    if souligne:
        moitie = stringWidth(texte, police, taille) / 2
        _souligner(c, x - moitie, x + moitie, b, taille, couleur)
    _fin_inclinaison(c, ouvert)


def _dessiner_prix(c, p, cx, base_y, taille, gras=GRAS, couleur=VERT, hc=0.72, italique=False, souligne=False):
    ent, cts = _prix_parts(p)
    w = _largeur_prix(p, taille, gras)
    c.setFillColor(couleur)
    cx, base_y, ouvert = _incliner(c, cx, base_y, italique)
    x = cx - w / 2
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
    if souligne:
        _souligner(c, x, x + w, base_y, taille, couleur)
    _fin_inclinaison(c, ouvert)


# ----------------------------------------------------------------------------
# Autres types de promotion : texte principal, petit texte au-dessus, pastille
# ----------------------------------------------------------------------------
K_TAILLE = 0.30   # corps du petit texte au-dessus du prix, en fraction du corps du prix
K_ECART = 0.16    # espace entre le prix et le petit texte, en fraction du corps du prix
SUP_TAILLE = 0.62  # corps d'un exposant (« e » de 2e), en fraction du corps du texte
_EXPOSANT = re.compile(r"\^\{([^}]*)\}")


def _segments(texte):
    """'2^{e} à' -> [('2', False), ('e', True), (' à', False)] (True = exposant)."""
    res, pos = [], 0
    for m in _EXPOSANT.finditer(texte):
        if m.start() > pos:
            res.append((texte[pos:m.start()], False))
        res.append((m.group(1), True))
        pos = m.end()
    if pos < len(texte):
        res.append((texte[pos:], False))
    return res


def _largeur_texte(texte, police, taille):
    return sum(stringWidth(t, police, taille * (SUP_TAILLE if sup else 1)) for t, sup in _segments(texte))


def _dessiner_texte(c, texte, cx, base, taille, police, couleur, hc=0.72, italique=False, souligne=False):
    """Texte centré en cx, sur la ligne de base `base` ; ^{e} = exposant."""
    largeur = _largeur_texte(texte, police, taille)
    c.setFillColor(couleur)
    cx, base, ouvert = _incliner(c, cx, base, italique)
    x = cx - largeur / 2
    for t, sup in _segments(texte):
        corps = taille * (SUP_TAILLE if sup else 1)
        c.setFont(police, corps)
        c.drawString(x, base + (taille * hc * 0.36 if sup else 0), t)
        x += stringWidth(t, police, corps)
    if souligne:
        _souligner(c, cx - largeur / 2, cx + largeur / 2, base, taille, couleur)
    _fin_inclinaison(c, ouvert)


def _largeur_grand(prix, grand, taille, gras):
    """Largeur du contenu principal du bandeau : le texte `grand` s'il y en a un, sinon le prix."""
    return _largeur_texte(grand, gras, taille) if grand else _largeur_prix(prix, taille, gras)


def _contenu_bandeau(prix, grand, kicker, taille, gras, hc):
    """(largeur du contenu, hauteur ajoutée par le petit texte) du bandeau pour un corps `taille`."""
    w = _largeur_grand(prix, grand, taille, gras)
    if kicker:
        w = max(w, _largeur_texte(kicker, gras, taille * K_TAILLE))
    return w, ((K_ECART + hc * K_TAILLE) * taille if kicker else 0.0)


def _facteur_bandeau(grand, kicker):
    """Réduction du corps de départ du prix quand le bandeau contient aussi un petit texte ou un texte principal."""
    if kicker:
        return 0.80
    return 0.88 if grand else 1.0


def _dessiner_bandeau(c, prix, grand, kicker, cx, base, taille, gras, couleur, hc, italique=False, souligne=False):
    """Contenu du bandeau : prix (ou texte principal) sur la ligne de base `base`, petit texte au-dessus."""
    if grand:
        _dessiner_texte(c, grand, cx, base, taille, gras, couleur, hc, italique, souligne)
    else:
        _dessiner_prix(c, prix, cx, base, taille, gras, couleur, hc, italique, souligne)
    if kicker:
        _dessiner_texte(c, kicker, cx, base + hc * taille + K_ECART * taille, taille * K_TAILLE, gras, couleur, hc,
                        italique, souligne)


def _pastille(c, texte, cx, cy, rayon, gras, couleur_fond, couleur_texte):
    """Disque coloré avec le texte (« –25 % ») au centre. Retourne le rayon réellement utilisé."""
    c.setFillColor(couleur_fond)
    c.circle(cx, cy, rayon, stroke=0, fill=1)
    unite = max(0.1, _largeur_texte(texte, gras, 1.0))
    corps = min(rayon * 0.80, rayon * 1.55 / unite)
    c.setFillColor(couleur_texte)
    c.setFont(gras, corps)
    c.drawCentredString(cx, cy - corps * 0.36, texte)
    return rayon


def rayon_pastille(texte, largeur_page, gras):
    """Rayon de la pastille : plus grand quand le texte est long (« –12,50 € »)."""
    unite = max(0.1, _largeur_texte(texte, gras, 1.0))
    return largeur_page * 0.10 * min(1.35, max(1.0, unite / 3.2))


MAX_VISUELS = 4  # nombre maximal de visuels sur une affiche (par exemple une gamme de produits)


def _disposition_rangee(images, largeur_max, hauteur_max, ecart_rel=0.03, coin=None):
    """Plusieurs visuels sur une seule rangée : même hauteur, côte à côte, espacés d'une fraction de la largeur.
    coin : (« droite » ou « gauche », largeur, hauteur) d'un objet posé dans un coin haut de la zone (pastille) :
    si la rangée le recouvrirait, elle est réduite juste assez pour passer à côté ou en dessous.
    Retourne ([(x, largeur)] depuis le bord gauche du groupe, largeur du groupe, hauteur commune)."""
    n = len(images)
    ecart = largeur_max * ecart_rel
    somme = sum(im.size[0] / im.size[1] for im in images)
    h = min(hauteur_max, (largeur_max - (n - 1) * ecart) / somme)
    gw = h * somme + (n - 1) * ecart
    facteur = 1.0
    if coin:
        cote, cw, ch = coin
        deborde = ((largeur_max + gw) / 2 > largeur_max - cw) if cote == "droite" else ((largeur_max - gw) / 2 < cw)
        if deborde and h > hauteur_max - ch:  # la rangée toucherait le coin : réduction minimale
            facteur = min(1.0, max((largeur_max - 2 * cw) / gw, (hauteur_max - ch) / h, 0.4))
    h, ecart = h * facteur, ecart * facteur
    x, positions = 0.0, []
    for im in images:
        w = h * im.size[0] / im.size[1]
        positions.append((x, w))
        x += w + ecart
    return positions, x - ecart, h


def _disposition_auto(images, largeur_max, hauteur_max, ecart_rel=0.03):
    """Plusieurs visuels dans une colonne (affiche en paysage) : une seule rangée, ou une grille de 1 à n-1 colonnes
    (2 visuels l'un sous l'autre, 4 visuels en 2 x 2…), selon ce qui donne les plus grands visuels.
    Retourne ([(x, y, largeur, hauteur)] depuis le coin bas-gauche du groupe, largeur du groupe, hauteur du groupe)."""
    n = len(images)
    ecart = min(largeur_max, hauteur_max) * ecart_rel
    pos_r, gw_r, h_r = _disposition_rangee(images, largeur_max, hauteur_max, ecart_rel)
    rangee = [(x, 0.0, w, h_r) for x, w in pos_r]
    meilleur, aire_meilleure = (rangee, gw_r, h_r), sum(w * h_r for _, w in pos_r)
    aire_rangee = aire_meilleure
    for cols in range(1, n):
        lignes = -(-n // cols)
        cell_w = (largeur_max - (cols - 1) * ecart) / cols
        cell_h = (hauteur_max - (lignes - 1) * ecart) / lignes
        taille = []  # taille de chaque visuel, à l'échelle de sa cellule
        for im in images:
            ech = min(cell_w / im.size[0], cell_h / im.size[1])
            taille.append((im.size[0] * ech, im.size[1] * ech))
        rangs = [list(range(r * cols, min(n, (r + 1) * cols))) for r in range(lignes)]
        larg_rang = [sum(taille[i][0] for i in rg) + (len(rg) - 1) * ecart for rg in rangs]
        haut_rang = [max(taille[i][1] for i in rg) for rg in rangs]
        gw, gh = max(larg_rang), sum(haut_rang) + (lignes - 1) * ecart
        rects, y_haut_rang = [None] * n, gh
        for rg, lr, hr in zip(rangs, larg_rang, haut_rang):
            y_haut_rang -= hr
            x = (gw - lr) / 2  # ligne centrée ; visuels alignés sur la base de la ligne
            for i in rg:
                rects[i] = (x, y_haut_rang, taille[i][0], taille[i][1])
                x += taille[i][0] + ecart
            y_haut_rang -= ecart
        aire = sum(w * h for _, _, w, h in rects)
        if aire > aire_meilleure:
            meilleur, aire_meilleure = (rects, gw, gh), aire
    if aire_meilleure < aire_rangee * 1.12:  # à peu près équivalent : la rangée, comme sur l'affiche en portrait
        return rangee, gw_r, h_r
    return meilleur


def _dessiner_cadre(c, W, H, S, cadre, couleur):
    """Cadre autour de l'affiche, en retrait du bord (hors de la marge que les imprimantes n'impriment pas)."""
    if cadre not in CADRES or cadre == "aucun":
        return
    retrait = S * 0.024
    x0, y0, w, h = retrait, retrait, W - 2 * retrait, H - 2 * retrait
    c.saveState()
    c.setStrokeColor(couleur)
    c.setLineJoin(0)
    if cadre == "fin":
        c.setLineWidth(max(0.8, S * 0.003))
        c.rect(x0, y0, w, h, stroke=1, fill=0)
    elif cadre == "epais":
        c.setLineWidth(S * 0.011)
        c.rect(x0, y0, w, h, stroke=1, fill=0)
    elif cadre == "double":
        c.setLineWidth(S * 0.006)
        c.rect(x0, y0, w, h, stroke=1, fill=0)
        e = S * 0.011
        c.setLineWidth(max(0.6, S * 0.002))
        c.rect(x0 + e, y0 + e, w - 2 * e, h - 2 * e, stroke=1, fill=0)
    elif cadre == "arrondi":
        c.setLineWidth(S * 0.007)
        c.roundRect(x0, y0, w, h, S * 0.045, stroke=1, fill=0)
    elif cadre == "pointille":
        ep = S * 0.006
        c.setLineWidth(ep)
        c.setLineCap(1)
        c.setDash(0.01, ep * 2.6)  # points ronds
        c.rect(x0, y0, w, h, stroke=1, fill=0)
    elif cadre == "coins":
        c.setLineWidth(S * 0.009)
        c.setLineCap(0)
        b = S * 0.12  # longueur de chaque branche
        for (x, y, sx, sy) in ((x0, y0, 1, 1), (x0 + w, y0, -1, 1), (x0, y0 + h, 1, -1), (x0 + w, y0 + h, -1, -1)):
            chemin = c.beginPath()
            chemin.moveTo(x, y + sy * b)
            chemin.lineTo(x, y)
            chemin.lineTo(x + sx * b, y)
            c.drawPath(chemin, stroke=1, fill=0)
    c.restoreState()


def _libres():
    """Module des éléments libres (importé à la demande : il importe lui-même ce module)."""
    import elements_libres
    return elements_libres


def _chevauche(zone, autres) -> bool:
    """La zone (x0, y0, x1, y1) en recouvre-t-elle une des autres, ne serait-ce qu'un peu ?"""
    return any(min(zone[2], a[2]) > max(zone[0], a[0]) and min(zone[3], a[3]) > max(zone[1], a[1]) for a in autres)


def _dessiner_visuel(c, image, x, y, w, h, transparent=False):
    """Dessine un visuel. transparent : son fond blanc devient transparent (un élément est derrière) ; sinon, comme
    toujours, en JPEG opaque."""
    buf = io.BytesIO()
    if transparent:
        try:
            _libres().visuel_transparent(image).save(buf, format="PNG")
            buf.seek(0)
            c.drawImage(ImageReader(buf), x, y, w, h, mask="auto")
            return
        except Exception:  # visuel inhabituel : on le dessine opaque plutôt que de perdre l'affiche
            buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=92)
    buf.seek(0)
    c.drawImage(ImageReader(buf), x, y, w, h)


def _reg(reglages, el):
    g = (reglages or {}).get(el) or {}
    return float(g.get("dx", 0.0)), float(g.get("dy", 0.0)), max(0.1, float(g.get("s", 1.0)))


def construire_pdf(sortie, taille_page, marque, detail, prix, prix_barre=None, texte_dates="",
                   image=None, afficher_logo=True, reglages=None, majuscules=True, style=None, promo=None,
                   identite=None, logo_marque=None, elements=None):
    """sortie : chemin ou objet binaire. taille_page : (largeur, hauteur) en points.
    Si la marque est vide, le détail devient la ligne principale.
    image : un visuel (image PIL) ou une liste de visuels (MAX_VISUELS au maximum), placés côte à côte sur une rangée.
    promo : autre type de promotion (voir promos.composer, clé « rendu ») : kicker (petit texte au-dessus),
    grand (texte principal à la place du prix), prix, prix_barre, ligne (texte sous le prix), pastille.
    Sans promo, c'est l'affiche « prix promo » d'origine (prix et prix_barre).
    Une page plus large que haute (est_paysage) est mise en page en paysage : visuel(s) à gauche, textes à droite.
    logo_marque : logo de la marque (image PIL, de préférence RVBA), imprimé à la place du nom de la marque (le détail du
    produit reste écrit) ; ignoré si la marque est vide.
    elements : éléments ajoutés à la main (voir elements_libres.py) : textes, prix, formes, derrière (par défaut, avant
    tout le reste) ou devant le visuel. Leurs cadres portent la clé « libre_<identifiant> ».
    Retourne les cadres des éléments, normalisés (x0, y0, x1, y1) depuis le coin haut-gauche."""
    W, H = taille_page
    paysage = est_paysage(taille_page)
    S, L = (H, W) if paysage else (W, H)  # petit côté, grand côté : base des tailles de texte et des marges
    promo = promo or {}
    if promo:
        prix, prix_barre = promo.get("prix"), promo.get("prix_barre")
    kicker, grand = (promo.get("kicker") or "").strip(), (promo.get("grand") or "").strip()
    texte_ligne, texte_pastille = (promo.get("ligne") or "").strip(), (promo.get("pastille") or "").strip()
    st = dict(STYLE_DEFAUT)
    st.update(style or {})
    st["textes"] = textes_valides(st.get("textes"))
    reg, bold = polices_famille(st["police"])
    # police, italique et souligné de chaque texte (par défaut : la police de l'affiche, en gras ou non comme toujours)
    f_marque, i_marque, u_marque = police_texte(st, "marque")
    f_detail, i_detail, u_detail = police_texte(st, "detail")
    f_prix, i_prix, u_prix = police_texte(st, "prix")
    f_barre, i_barre, u_barre = police_texte(st, "prix_barre")
    f_ligne, i_ligne, u_ligne = police_texte(st, "ligne")
    f_dates, i_dates, u_dates = police_texte(st, "dates")
    hc = _hauteur_chiffre(f_prix)
    col_nom, col_prix = HexColor(st["couleur_nom"]), HexColor(st["couleur_prix"])
    col_det = HexColor(couleur_detail(st))
    col_accent, col_sec = HexColor(st["couleur_accent"]), HexColor(st["couleur_secondaire"])
    marque, detail = (marque or "").strip(), (detail or "").strip()
    if not marque:
        logo_marque = None  # pas de marque : rien à remplacer par un logo
    if not marque and detail:
        marque, detail = detail, ""
    images = [im for im in (image if isinstance(image, (list, tuple)) else [image]) if im is not None][:MAX_VISUELS]
    c = canvas.Canvas(sortie, pagesize=(W, H))
    c.setTitle(f"Affiche promo - {marque} {detail}".replace("\n", " ").strip())
    m = S * 0.07
    zone_w = W - 2 * m
    y_haut = H - S * 0.05
    cx_t, cx_img, larg_img = W / 2, W / 2, zone_w  # centre des textes, centre et largeur de la zone du visuel
    cadres = {}  # coordonnées PDF (origine en bas à gauche)

    # ---- Éléments ajoutés à la main : ceux de derrière sont dessinés en premier, sous tout le reste
    libres = []
    if elements:
        mod_libres = _libres()
        libres = [mod_libres.mise_en_page(e, W, H, S, st) for e in mod_libres.elements_valides(elements)]
        for g in libres:
            if not g["e"]["devant"]:
                mod_libres.dessiner(c, g, st)
    derriere = [g["bbox"] for g in libres if not g["e"]["devant"] and not g["vide"]]

    # ---- Paysage : colonne de gauche = visuel(s), colonne de droite = textes, prix et logo
    disposition = None  # plusieurs visuels : (positions, largeur, hauteur) du groupe
    y_bas_img, y_haut_img = S * 0.05, y_haut
    if paysage and texte_pastille:  # la pastille occupe le coin haut gauche : le visuel se place en dessous
        y_haut_img = y_haut - 1.95 * rayon_pastille(texte_pastille, S, f_prix) - S * 0.01
    if paysage and images:
        boite_h0 = y_haut_img - y_bas_img
        zone_img = (W - 2 * m) * min(0.58, 0.47 + 0.03 * len(images))  # largeur maximale de la colonne du visuel
        if len(images) == 1:
            largeur_groupe = min(zone_img, boite_h0 * images[0].size[0] / images[0].size[1])
        else:
            disposition = _disposition_auto(images, zone_img, boite_h0)
            largeur_groupe = disposition[1]
        # la colonne s'adapte au visuel : un tube étroit laisse plus de place au texte, un pot carré en prend davantage
        larg_img = min(zone_img, max(largeur_groupe + (W - 2 * m) * 0.04, (W - 2 * m) * 0.34))
        cx_img = m + larg_img / 2
        x_texte = m + larg_img + m
        zone_w = W - m - x_texte
        cx_t = x_texte + zone_w / 2

    texte_marque = marque or " "
    if majuscules:
        texte_marque = texte_marque.upper()
    # identité de la pharmacie : None = pharmacie d'origine (logo.png et « Pharmacie Bouton »)
    nom_pharmacie, chemin_logo = ("Pharmacie Bouton", LOGO) if identite is None else (
        str(identite.get("nom") or "").strip(), identite.get("logo"))
    logo_img = ImageReader(str(chemin_logo)) if (afficher_logo and chemin_logo and Path(chemin_logo).exists()) else None
    nom_seul = bool(afficher_logo and logo_img is None and nom_pharmacie)  # sans logo importé : le nom seul en pied de page
    fond = bool(st.get("fond_prix"))
    ordre = ordre_valide(st.get("ordre"))
    pad_x, pad_b, pad_h = 0.20, 0.12, 0.16  # marges du bandeau, en fraction du corps du prix

    k = 1.0  # facteur de réduction des textes : en paysage, ils sont réduits jusqu'à tenir dans la hauteur
    while True:
        g = L * k  # base des tailles de texte et des espaces (en portrait : la hauteur de la page)

        # ---- Textes : marque (grande) puis détail (plus petit)
        if detail:
            l_marque, t_marque = _ajuster_titre(texte_marque, f_marque, zone_w, g * 0.12, g * 0.058, max_lignes=2,
                                                equilibre=paysage)
            l_detail, t_detail = _ajuster_titre(detail, f_detail, zone_w, g * 0.12, g * 0.038, max_lignes=3,
                                                equilibre=paysage)
        else:
            l_marque, t_marque = _ajuster_titre(texte_marque, f_marque, zone_w, g * 0.15, g * 0.058, max_lignes=3,
                                                equilibre=paysage)
            l_detail, t_detail = [], 0.0
        bloc_m = len(l_marque) * t_marque * 1.15
        bloc_d = len(l_detail) * t_detail * 1.15
        if logo_marque is not None:  # le logo de la marque tient la place du nom : même zone, hauteur plus mesurée
            l_marque, t_marque = [], 0.0
            h_logo_marque = min(g * (0.09 if detail else 0.12), zone_w * 0.85 * logo_marque.height / logo_marque.width)
            bloc_m = h_logo_marque

        # ---- Tailles des éléments (indépendantes de leur position)
        pied_h = g * 0.05
        y_pied = y_bas_img if paysage else g * 0.03  # en portrait, le logo est collé plus bas que les marges
        cy_logo = y_pied + pied_h / 2

        taille_d = 0.0
        if texte_dates:
            taille_d = g * 0.022
            while stringWidth(texte_dates, f_dates, taille_d) > zone_w and taille_d > 6:
                taille_d -= 0.5

        # texte sous le prix (calcul de la promotion, précision) : juste sous le prix
        bloc_l, l_ligne, t_ligne = 0.0, [], 0.0
        if texte_ligne:
            l_ligne, t_ligne = _ajuster_titre(texte_ligne, f_ligne, zone_w, g * 0.10, g * 0.028, max_lignes=3,
                                              equilibre=paysage)
            bloc_l = len(l_ligne) * t_ligne * 1.15

        taille_p = g * (0.22 if fond else 0.14) * _facteur_bandeau(grand, kicker)
        while True:
            w_contenu, h_kicker = _contenu_bandeau(prix, grand, kicker, taille_p, f_prix, hc)
            if (w_contenu + (2 * pad_x * taille_p if fond else 0)) <= zone_w or taille_p <= 10:
                break
            taille_p -= 1
        taille_b = taille_p * (0.25 if fond else 0.30) if prix_barre is not None else 0.0

        # ---- Positions automatiques, de bas en haut : logo, puis les éléments dans l'ordre choisi (par défaut : dates,
        # prix, prix barré, détail, marque), puis le visuel dans la place restante
        y = y_pied + pied_h + g * 0.015
        y_dates = y_ligne = y_prix = y_barre = y_detail = y_marque = y
        sommet = y  # haut de l'élément le plus haut
        for nom in reversed(ordre):
            if nom == "dates":
                y_dates = y
                if texte_dates:
                    y += taille_d + g * 0.022
                    sommet = y_dates + taille_d
            elif nom == "prix":
                y_ligne = y
                if texte_ligne:
                    y += bloc_l + g * (0.012 if st.get("fond_prix") else 0.024)  # sans bandeau : place pour le filet sous le prix
                y_prix = y + (taille_p * pad_b if fond else 0)
                sommet = y_prix + taille_p * hc + h_kicker + (taille_p * pad_h if fond else 0)  # haut du bandeau
                y = sommet + g * 0.02
            elif nom == "prix_barre":
                y_barre = y
                if prix_barre is not None:
                    y_barre = y + taille_b * 0.3  # place sous le texte pour le bout du trait diagonal
                    y = y_barre + taille_b * 0.95 + g * 0.015
                    sommet = y_barre + taille_b * 0.95
            elif nom == "detail":
                y_detail = y + g * 0.005
                if detail:
                    y = y_detail + bloc_d + g * 0.012
                    sommet = y_detail + bloc_d
            elif nom == "marque":
                y_marque = y
                y = y_marque + bloc_m + g * 0.018
                sommet = y_marque + bloc_m
        if not paysage or sommet <= y_haut or k <= 0.3:
            break
        k *= 0.96  # en paysage : le bloc de textes dépasse en hauteur, on le réduit un peu et on recommence

    if paysage:  # bloc de textes centré en hauteur entre le logo et le haut de la page
        decalage = max(0.0, y_haut - sommet) / 2
        y_dates, y_ligne, y_prix, y_barre, y_detail, y_marque = (
            v + decalage for v in (y_dates, y_ligne, y_prix, y_barre, y_detail, y_marque))
        y_image_bas, y_image_haut = y_bas_img, y_haut_img
    else:
        y_image_bas, y_image_haut = y, y_haut

    # ---- Visuel (en haut, ou à gauche en paysage) : un seul, ou plusieurs (rangée ; grille en paysage)
    if images and y_image_haut - y_image_bas > 0:
        dx, dy, s = _reg(reglages, "image")
        boite_h = y_image_haut - y_image_bas
        cx = cx_img + dx * W
        cy = y_image_bas + boite_h / 2 + dy * H
        if len(images) == 1:
            image = images[0]
            iw, ih = image.size
            ech = min(larg_img / iw, boite_h / ih)
            dw, dh = iw * ech * s, ih * ech * s
            zone_visuel = (cx - dw / 2, cy - dh / 2, cx + dw / 2, cy + dh / 2)
            _dessiner_visuel(c, image, cx - dw / 2, cy - dh / 2, dw, dh, _chevauche(zone_visuel, derriere))
            cadres["image"] = zone_visuel
        elif paysage:  # visuels en une rangée ou en grille, au mieux, centrés dans leur colonne
            rects, gw, gh = disposition
            gauche, bas = cx - gw * s / 2, cy - gh * s / 2
            zone_visuel = (gauche, bas, gauche + gw * s, bas + gh * s)
            transparent = _chevauche(zone_visuel, derriere)
            for img, (x_rel, y_rel, w_rel, h_rel) in zip(images, rects):
                _dessiner_visuel(c, img, gauche + x_rel * s, bas + y_rel * s, w_rel * s, h_rel * s, transparent)
            cadres["image"] = zone_visuel
        else:
            r_pastille = rayon_pastille(texte_pastille, S, f_prix) if texte_pastille else 0.0
            coin = ("droite", 1.95 * r_pastille, 1.95 * r_pastille) if texte_pastille else None
            positions, gw, gh = _disposition_rangee(images, zone_w, boite_h, coin=coin)
            cy = y_image_bas + gh / 2 + dy * H  # la rangée repose sur le bas de la zone, juste au-dessus de la marque
            gauche, bas = cx - gw * s / 2, cy - gh * s / 2
            zone_visuel = (gauche, bas, gauche + gw * s, bas + gh * s)
            transparent = _chevauche(zone_visuel, derriere)
            for img, (x_rel, w_rel) in zip(images, positions):
                _dessiner_visuel(c, img, gauche + x_rel * s, bas, w_rel * s, gh * s, transparent)
            cadres["image"] = zone_visuel

    # ---- Logo de la marque (à la place du nom)
    if logo_marque is not None:
        dx, dy, sc = _reg(reglages, "marque")
        hl = h_logo_marque * sc
        wl = hl * logo_marque.width / logo_marque.height
        cx = cx_t + dx * W
        cy = (y_marque + bloc_m / 2) + dy * H
        c.drawImage(ImageReader(logo_marque), cx - wl / 2, cy - hl / 2, wl, hl, mask="auto")
        cadres["marque"] = (cx - wl / 2, cy - hl / 2, cx + wl / 2, cy + hl / 2)

    # ---- Marque puis détail
    for el, lignes, taille, bloc, y_bloc, police, ital, soul in (
            ("marque", l_marque, t_marque, bloc_m, y_marque, f_marque, i_marque, u_marque),
            ("detail", l_detail, t_detail, bloc_d, y_detail, f_detail, i_detail, u_detail)):
        if not lignes:
            continue
        dx, dy, s = _reg(reglages, el)
        t = taille * s
        hb = len(lignes) * t * 1.15
        cx = cx_t + dx * W
        cy = (y_bloc + bloc / 2) + dy * H
        haut = cy + hb / 2
        for i, l in enumerate(lignes):
            _ecrire_centre(c, l, cx, haut - t * 0.85 - i * t * 1.15, police, t, col_det if el == "detail" else col_nom,
                           ital, soul)
        lmax = max(stringWidth(l, police, t) for l in lignes)
        cadres[el] = (cx - lmax / 2, haut - hb, cx + lmax / 2, haut)

    # ---- Prix barré
    if prix_barre is not None:
        dx, dy, s = _reg(reglages, "prix_barre")
        ent, cts = _prix_parts(prix_barre)
        txt = f"{ent},{cts or '00'} €" if cts else f"{ent} €"
        t = taille_b * s
        cx = cx_t + dx * W
        base = (y_barre + taille_b * 0.3) + dy * H - t * 0.3
        wt = stringWidth(txt, f_barre, t)
        _ecrire_centre(c, txt, cx, base, f_barre, t, col_sec, i_barre, u_barre)
        c.setStrokeColor(col_sec)
        c.setLineWidth(max(1.5, t * 0.08))
        c.setLineCap(1)  # extrémités arrondies
        c.line(cx - wt / 2 - t * 0.1, base - t * 0.25, cx + wt / 2 + t * 0.1, base + t * 0.95)  # diagonale ↗
        cadres["prix_barre"] = (cx - wt / 2, base - t * 0.25, cx + wt / 2, base + t * 0.95)

    # ---- Prix (ou texte principal de l'offre), avec son petit texte au-dessus
    dx, dy, s = _reg(reglages, "prix")
    p = taille_p * s
    cx = cx_t + dx * W
    wp, h_kicker_p = _contenu_bandeau(prix, grand, kicker, p, f_prix, hc)
    base = (y_prix + (taille_p * hc + h_kicker) / 2) + dy * H - (p * hc + h_kicker_p) / 2
    if fond:  # bandeau coloré derrière le prix
        bx0, bx1 = cx - wp / 2 - pad_x * p, cx + wp / 2 + pad_x * p
        by0, by1 = base - pad_b * p, base + p * hc + h_kicker_p + pad_h * p
        c.setFillColor(HexColor(st["couleur_fond_prix"]))
        c.roundRect(bx0, by0, bx1 - bx0, by1 - by0, 0.14 * p, stroke=0, fill=1)
        cadres["prix"] = (bx0, by0, bx1, by1)
    else:
        c.setStrokeColor(col_accent)
        c.setLineWidth(max(1.5, g * 0.004 * s))
        c.line(cx - wp / 2, base - p * 0.07, cx + wp / 2, base - p * 0.07)
        cadres["prix"] = (cx - wp / 2, base - p * 0.09, cx + wp / 2, base + p * hc + h_kicker_p)
    _dessiner_bandeau(c, prix, grand, kicker, cx, base, p, f_prix, col_prix, hc, i_prix, u_prix)

    # ---- Texte sous le prix
    if l_ligne:
        dx, dy, s = _reg(reglages, "ligne")
        t = t_ligne * s
        hb = len(l_ligne) * t * 1.15
        cx = cx_t + dx * W
        haut = (y_ligne + bloc_l / 2) + dy * H + hb / 2
        for i, l in enumerate(l_ligne):
            _ecrire_centre(c, l, cx, haut - t * 0.85 - i * t * 1.15, f_ligne, t, col_nom, i_ligne, u_ligne)
        lmax = max(stringWidth(l, f_ligne, t) for l in l_ligne)
        cadres["ligne"] = (cx - lmax / 2, haut - hb, cx + lmax / 2, haut)

    # ---- Pastille (« –25 % »), en haut à droite du visuel (en paysage : en haut à gauche, la colonne de droite
    # porte les textes)
    if texte_pastille:
        dx, dy, s = _reg(reglages, "pastille")
        r0 = rayon_pastille(texte_pastille, S, f_prix)
        r = r0 * s
        cx_p = (m + r0 * 0.95) if paysage else (W - m - r0 * 0.95)
        cx, cy = cx_p + dx * W, y_haut - r0 * 0.95 + dy * H
        _pastille(c, texte_pastille, cx, cy, r, f_prix,
                  HexColor(st["couleur_fond_prix"]) if fond else col_prix, col_prix if fond else white)
        cadres["pastille"] = (cx - r, cy - r, cx + r, cy + r)

    # ---- Dates
    if texte_dates:
        dx, dy, s = _reg(reglages, "dates")
        t = taille_d * s
        cx = cx_t + dx * W
        base = (y_dates + taille_d * 0.3) + dy * H - t * 0.3
        _ecrire_centre(c, texte_dates, cx, base, f_dates, t, col_sec, i_dates, u_dates)
        wt = stringWidth(texte_dates, f_dates, t)
        cadres["dates"] = (cx - wt / 2, base - t * 0.2, cx + wt / 2, base + t * 0.8)

    # ---- Logo + nom de la pharmacie
    if logo_img is not None or nom_seul:
        dx, dy, s = _reg(reglages, "logo")
        lw, lh = logo_img.getSize() if logo_img is not None else (1, 1)
        h_logo = pied_h * s
        w_logo = h_logo * lw / lh if logo_img is not None else 0.0
        ts = g * 0.017 * s
        ecart = S * 0.015 * s if logo_img is not None else 0.0
        tw = stringWidth(nom_pharmacie, bold, ts)
        gw = w_logo + ecart + tw
        if (paysage or identite is not None) and gw > zone_w:  # colonne étroite ou nom long : le logo et le nom se réduisent pour y tenir
            f = zone_w / gw
            h_logo, w_logo, ts, ecart, tw, gw = h_logo * f, w_logo * f, ts * f, ecart * f, tw * f, gw * f
        cx = cx_t + dx * W
        cy = cy_logo + dy * H
        x_g = cx - gw / 2
        if logo_img is not None:
            c.drawImage(logo_img, x_g, cy - h_logo / 2, w_logo, h_logo, mask="auto")
        c.setFillColor(VERT)
        c.setFont(bold, ts)
        c.drawString(x_g + w_logo + ecart, cy - ts * 0.3, nom_pharmacie)
        h_cadre = h_logo if logo_img is not None else max(ts * 1.4, 1.0)
        cadres["logo"] = (x_g, cy - h_cadre / 2, x_g + gw, cy + h_cadre / 2)

    for g in libres:  # éléments de devant : au-dessus de tout, le cadre de l'affiche mis à part
        if g["e"]["devant"]:
            _libres().dessiner(c, g, st)
    try:
        couleur_cadre = HexColor(st.get("couleur_cadre") or STYLE_DEFAUT["couleur_cadre"])
    except Exception:
        couleur_cadre = HexColor(STYLE_DEFAUT["couleur_cadre"])
    _dessiner_cadre(c, W, H, S, st.get("cadre"), couleur_cadre)
    c.showPage()
    c.save()
    # Ordre des cadres = ordre des couches dans l'éditeur (le dernier est au-dessus) : éléments de derrière, éléments
    # de l'affiche, éléments de devant
    tous = {f"libre_{g['e']['id']}": g["bbox"] for g in libres if not g["e"]["devant"]}
    tous.update(cadres)
    tous.update({f"libre_{g['e']['id']}": g["bbox"] for g in libres if g["e"]["devant"]})
    return {k: (x0 / W, 1 - y1 / H, x1 / W, 1 - y0 / H) for k, (x0, y0, x1, y1) in tous.items()}


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
