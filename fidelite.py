"""Affiches et affichettes « carte de fidélité » par laboratoire (onglet « Cartes de fidélité »).

Exemple : « Santé Verte : 6 produits achetés + 1 offert ». De haut en bas : logo et nom de la pharmacie, titre
(« CARTE DE FIDÉLITÉ »), logo du laboratoire (ou son nom), gamme concernée (facultatif), offre dans le bandeau
du prix, précision (facultatif). En paysage : laboratoire à gauche, titre et offre à droite.
Affiche destinée au rayon ou à l'espace de vente : le suivi des achats se fait par le logiciel de la pharmacie
(pas de carte papier ni de cases à tamponner).

Les couleurs et la police sont celles du style en cours (onglet « Créer une affiche »). Le logo du laboratoire est
rangé avec les logos de marques de la pharmacie (voir marques.py). Ce module ne dépend pas de Streamlit.
"""
import io
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

import affiche as af
import planche

TITRE_DEFAUT = "Carte de fidélité"
ACHETES_DEFAUT, OFFERTS_DEFAUT = 6, 1
MAX_ACHETES, MAX_OFFERTS = 30, 5
PRECISION_EXEMPLE = "Avantage enregistré en caisse sur votre compte fidélité"  # exemple affiché dans le champ vide


def textes_offre(achetes, offerts):
    """(petit texte, texte principal) de l'offre : (« 6 produits achetés », « + 1 offert »)."""
    achetes, offerts = max(1, int(achetes)), max(1, int(offerts))
    haut = f"{achetes} produit{'s' if achetes > 1 else ''} acheté{'s' if achetes > 1 else ''}"
    bas = f"+ {offerts} offert{'s' if offerts > 1 else ''}"
    return haut, bas


def resume(labo, haut, bas):
    """Une ligne pour les messages et le nom du fichier : « Santé Verte : 6 produits achetés + 1 offert »."""
    offre = " ".join(t for t in (haut, bas) if t)
    return f"{labo} : {offre}" if labo else offre


# ----------------------------------------------------------------------------
# Dessin
# ----------------------------------------------------------------------------
def _texte_tient(texte, police, largeur, hauteur, taille_max, max_lignes=2):
    """(lignes, corps) du texte ajusté à la boîte ; voir affiche._ajuster_titre."""
    return af._ajuster_titre(texte, police, largeur, hauteur, taille_max, max_lignes=max_lignes)


def _lignes(c, lignes, taille, police, couleur, cx, cy):
    """Lignes centrées horizontalement en cx et verticalement autour de cy."""
    bloc = len(lignes) * taille * 1.15
    haut = cy + bloc / 2
    c.setFillColor(couleur)
    c.setFont(police, taille)
    for i, ligne in enumerate(lignes):
        c.drawCentredString(cx, haut - taille * 0.88 - i * taille * 1.15, ligne)


def _titre(c, texte, x, y, w, h, st, bold):
    """Titre dans un ruban arrondi de la couleur de la marque, texte blanc, centré dans la boîte."""
    texte = texte.upper()
    corps = min(h * 0.62, w * 0.9 / max(0.1, stringWidth(texte, bold, 1.0)))
    corps = max(6.0, corps)
    larg = stringWidth(texte, bold, corps) + corps * 1.6
    haut = corps * 1.55
    cx, cy = x + w / 2, y + h / 2
    c.setFillColor(HexColor(st["couleur_nom"]))
    c.roundRect(cx - larg / 2, cy - haut / 2, larg, haut, haut / 2, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont(bold, corps)
    c.drawCentredString(cx, cy - corps * 0.36, texte)


def _labo(c, nom, logo, x, y, w, h, st, bold, majuscules):
    """Logo du laboratoire (image PIL) dans la boîte, ou à défaut son nom en grand."""
    if logo is not None:
        lw, lh = logo.size
        ech = min(w / lw, h / lh)
        dw, dh = lw * ech, lh * ech
        c.drawImage(ImageReader(logo), x + (w - dw) / 2, y + (h - dh) / 2, dw, dh, mask="auto")
        return
    if not nom:
        return
    texte = nom.upper() if majuscules else nom
    lignes, t = _texte_tient(texte, bold, w, h, h * 0.55, max_lignes=3)
    _lignes(c, lignes, t, bold, HexColor(st["couleur_nom"]), x + w / 2, y + h / 2)


def _offre(c, haut, bas, x, y, w, h, st, bold, hc):
    """Offre : petit texte (« 6 produits achetés ») au-dessus du texte principal (« + 1 offert »), dans le bandeau
    du prix (ou soulignée d'un filet sans bandeau), centrée dans la boîte."""
    fond = bool(st.get("fond_prix"))
    ratio = 0.52  # corps du petit texte / corps du texte principal
    pad_x, pad_b, pad_h, ecart = 0.30, 0.20, 0.22, 0.20
    t = h  # corps du texte principal, réduit jusqu'à tenir dans la boîte
    while t > 6:
        larg = max(af._largeur_texte(bas, bold, t) if bas else 0.0,
                   af._largeur_texte(haut, bold, t * ratio) if haut else 0.0)
        cont_h = (hc * t if bas else 0.0) + ((hc * t * ratio) if haut else 0.0) + (ecart * t if haut and bas else 0.0)
        tot_w = larg + (2 * pad_x * t if fond else 0.0)
        tot_h = cont_h + ((pad_b + pad_h) * t if fond else 0.12 * t)
        if tot_w <= w and tot_h <= h:
            break
        t -= 0.5
    cx = x + w / 2
    bas_bloc = y + (h - tot_h) / 2
    base = bas_bloc + (pad_b * t if fond else 0.12 * t)
    couleur = HexColor(st["couleur_prix"])
    if fond:
        c.setFillColor(HexColor(st["couleur_fond_prix"]))
        c.roundRect(cx - tot_w / 2, bas_bloc, tot_w, tot_h, 0.16 * t, stroke=0, fill=1)
    else:
        c.setStrokeColor(HexColor(st["couleur_accent"]))
        c.setLineWidth(max(1.2, t * 0.045))
        c.line(cx - larg / 2, bas_bloc + 0.03 * t, cx + larg / 2, bas_bloc + 0.03 * t)
    if bas:
        af._dessiner_texte(c, bas, cx, base, t, bold, couleur, hc)
        base += hc * t + ecart * t
    if haut:
        af._dessiner_texte(c, haut, cx, base, t * ratio, bold, couleur, hc)


def _empiler(blocs, x, y, w, h, ecart):
    """Répartit la hauteur (x, y, w, h) entre les blocs [(poids, fonction(x, y, w, h)), ...], de haut en bas."""
    blocs = [b for b in blocs if b]
    if not blocs:
        return
    utile = h - ecart * (len(blocs) - 1)
    total = sum(p for p, _ in blocs)
    haut = y + h
    for poids, dessin in blocs:
        hb = utile * poids / total
        dessin(x, haut - hb, w, hb)
        haut -= hb + ecart


def construire(sortie, taille_page, labo="", logo_labo=None, haut="", bas="", achetes=ACHETES_DEFAUT,
               offerts=OFFERTS_DEFAUT, titre=TITRE_DEFAUT, gamme="", precision="", style=None, identite=None,
               logo_pharmacie=True, majuscules=True):
    """Dessine l'affiche dans `sortie` (chemin ou objet binaire). taille_page : (largeur, hauteur) en points.
    haut / bas : petit texte et texte principal de l'offre (voir textes_offre). identite : voir pharmacie.identite."""
    W, H = taille_page
    S = min(W, H)
    st = dict(af.STYLE_DEFAUT)
    st.update(style or {})
    reg, bold = af.polices_famille(st["police"])
    hc = af._hauteur_chiffre(bold)
    labo, gamme, precision, titre = (" ".join(str(v or "").split()) for v in (labo, gamme, precision, titre))
    c = canvas.Canvas(sortie, pagesize=(W, H))
    c.setTitle("Carte de fidélité" + (f" – {labo}" if labo else ""))
    af._dessiner_cadre(c, W, H, S, st.get("cadre", "aucun"), HexColor(st.get("couleur_cadre") or st["couleur_nom"]))
    marge = S * (0.085 if st.get("cadre", "aucun") != "aucun" else 0.06)
    x0, y0, x1, y1 = marge, marge, W - marge, H - marge
    ecart = S * 0.025

    if logo_pharmacie:  # logo et nom de la pharmacie, en haut, séparés du reste par un filet
        h_ph = S * 0.085
        planche._pied_de_page(c, W, y1 - h_ph, h_ph, st, bold, identite)
        y_filet = y1 - h_ph - S * 0.025
        c.setStrokeColor(HexColor(st["couleur_accent"]))
        c.setLineWidth(max(1.0, S * 0.004))
        c.setLineCap(1)
        c.line(W / 2 - S * 0.18, y_filet, W / 2 + S * 0.18, y_filet)
        y1 = y_filet - S * 0.035

    b_titre = (0.10, lambda x, y, w, h: _titre(c, titre, x, y, w, h, st, bold)) if titre else None
    b_labo = (0.38, lambda x, y, w, h: _labo(c, labo, logo_labo, x, y, w, h, st, bold, majuscules)) \
        if (labo or logo_labo is not None) else None
    b_gamme = None
    if gamme:
        def _dessin_gamme(x, y, w, h):
            lignes, t = _texte_tient(gamme, reg, w, h, h * 0.75)
            _lignes(c, lignes, t, reg, HexColor(af.couleur_detail(st)), x + w / 2, y + h / 2)
        b_gamme = (0.07, _dessin_gamme)
    b_offre = (0.30, lambda x, y, w, h: _offre(c, haut, bas, x, y, w, h, st, bold, hc)) if (haut or bas) else None
    b_prec = None
    if precision:
        def _dessin_precision(x, y, w, h):
            lignes, t = _texte_tient(precision, reg, w, h, h * 0.7)
            _lignes(c, lignes, t, reg, HexColor(st["couleur_secondaire"]), x + w / 2, y + h / 2)
        b_prec = (0.07, _dessin_precision)

    if W / H >= af.SEUIL_PAYSAGE:  # paysage : laboratoire (et gamme) à gauche, titre et offre à droite
        part = 0.40 if (b_labo or b_gamme) else 0.0
        if part:
            _empiler([b_labo, b_gamme], x0, y0, (x1 - x0) * part - ecart, y1 - y0, ecart)
        xd = x0 + (x1 - x0) * part
        _empiler([b_titre, b_offre, b_prec], xd, y0, x1 - xd, y1 - y0, ecart)
    else:
        _empiler([b_titre, b_labo, b_gamme, b_offre, b_prec], x0, y0, x1 - x0, y1 - y0, ecart)
    c.showPage()
    c.save()


def pdf(*args, **kwargs) -> bytes:
    tampon = io.BytesIO()
    construire(tampon, *args, **kwargs)
    return tampon.getvalue()
