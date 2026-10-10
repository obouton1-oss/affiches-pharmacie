"""Éléments ajoutés à la main sur une affiche : un texte, un prix ou une forme (rond, étoile, flèche…), de la couleur
voulue, placé derrière ou devant le visuel (derrière par défaut).

Un élément est un dictionnaire simple, enregistré tel quel avec l'affiche dans l'historique :
  id        identifiant (8 caractères)
  type      « texte », « prix » ou « forme »
  devant    False (par défaut) : derrière tout le reste, visuel compris ; True : devant tout le reste
  cx, cy    centre, en fraction de la largeur et de la hauteur de la page (depuis le coin haut gauche)
  rot       rotation en degrés, dans le sens des aiguilles d'une montre
  couleur   couleur du texte, du prix ou de la forme (#RRGGBB)
  texte     texte (type texte), prix (type prix, « 9,90 »), texte facultatif dans la forme (type forme)
  police    famille de police (voir affiche.POLICES) ; vide = la police de l'affiche
  gras, italique, souligne   style du texte, du prix ou du texte écrit dans la forme (gras par défaut)
  t         corps du texte ou du prix, en fraction du petit côté de la page (types texte et prix)
  fond, couleur_fond   prix sur fond coloré (type prix)
  forme     rectangle, arrondi, carre, rond, ovale, triangle, losange, fleche, etoile, explosion, coeur, croix
  l, h      largeur et hauteur, en fraction du petit côté de la page (type forme ; h = l pour une forme à proportions fixes)

Quand un élément placé derrière le visuel le recouvre en partie, le fond blanc du visuel est rendu transparent à
l'impression (sans quoi le visuel, opaque, le cacherait). Sans élément derrière, le visuel est dessiné comme avant.

Ce module ne dépend pas de Streamlit.
"""
import hashlib
import math
import re
import threading
import uuid
from collections import OrderedDict
from decimal import Decimal

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps
from reportlab.lib.colors import HexColor

import affiche as af

MAX_ELEMENTS = 12
TYPES = {"texte": "Texte", "prix": "Prix", "forme": "Forme"}
# forme : (libellé, proportions libres ?) ; une forme à proportions fixes garde une largeur égale à sa hauteur
FORMES = {"rectangle": ("Rectangle", True), "arrondi": ("Rectangle arrondi", True), "carre": ("Carré", False),
          "rond": ("Rond", False), "ovale": ("Ovale", True), "triangle": ("Triangle", True),
          "losange": ("Losange", True), "fleche": ("Flèche", True), "etoile": ("Étoile", False),
          "explosion": ("Explosion", False), "coeur": ("Cœur", False), "croix": ("Croix", False)}
FORME_DEFAUT = "rectangle"
PRIX_MAX = Decimal("99999")
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


# ----------------------------------------------------------------------------
# Validation, création, modification
# ----------------------------------------------------------------------------
def _couleur(valeur, defaut):
    return valeur.upper() if isinstance(valeur, str) and _HEX.match(valeur) else defaut


def _nombre(valeur, defaut, mini, maxi):
    try:
        x = float(valeur)
    except (TypeError, ValueError):
        return defaut
    if x != x or x in (float("inf"), float("-inf")):
        return defaut
    return max(mini, min(maxi, x))


def element_valide(e):
    """L'élément complet et valide (valeurs ramenées dans leurs limites), ou None s'il n'est pas reconnu."""
    if not isinstance(e, dict) or e.get("type") not in TYPES:
        return None
    type_ = e["type"]
    texte = str(e.get("texte") or "").replace("\r", "")
    r = {"id": re.sub(r"[^0-9A-Za-z]", "", str(e.get("id") or ""))[:12] or uuid.uuid4().hex[:8], "type": type_,
         "devant": bool(e.get("devant", False)),
         "cx": _nombre(e.get("cx"), 0.5, -0.5, 1.5), "cy": _nombre(e.get("cy"), 0.5, -0.5, 1.5),
         "rot": _nombre(e.get("rot"), 0.0, -180.0, 180.0),
         "couleur": _couleur(e.get("couleur"), "#222222"),
         "police": e["police"] if isinstance(e.get("police"), str) and e["police"] in af.POLICES else None,
         "gras": bool(e.get("gras", True)), "italique": bool(e.get("italique", False)),
         "souligne": bool(e.get("souligne", False))}
    if type_ == "texte":
        r.update(texte=texte[:200], t=_nombre(e.get("t"), 0.07, 0.02, 0.5))
    elif type_ == "prix":
        r.update(texte=texte.replace("\n", " ")[:12], fond=bool(e.get("fond", False)),
                 couleur_fond=_couleur(e.get("couleur_fond"), "#FFD500"), t=_nombre(e.get("t"), 0.16, 0.03, 0.6))
    else:
        forme = e.get("forme") if e.get("forme") in FORMES else FORME_DEFAUT
        largeur = _nombre(e.get("l"), 0.34, 0.03, 1.6)
        hauteur = _nombre(e.get("h"), 0.22, 0.03, 1.6)
        if not FORMES[forme][1]:
            hauteur = largeur
        r.update(forme=forme, texte=texte[:60], l=largeur, h=hauteur)
    return r


def elements_valides(liste) -> list:
    """Les éléments reconnus de la liste (identifiants uniques, MAX_ELEMENTS au plus), dans l'ordre."""
    res, vus = [], set()
    for brut in liste if isinstance(liste, (list, tuple)) else []:
        e = element_valide(brut)
        if e is None:
            continue
        while e["id"] in vus:
            e["id"] = uuid.uuid4().hex[:8]
        vus.add(e["id"])
        res.append(e)
    return res[:MAX_ELEMENTS]


def nouveau(type_, style=None) -> dict:
    """Un nouvel élément, au centre de la page, derrière le visuel, aux couleurs du style de l'affiche."""
    st = dict(af.STYLE_DEFAUT)
    st.update(style or {})
    e = {"id": uuid.uuid4().hex[:8], "type": type_, "devant": False, "cx": 0.5, "cy": 0.5, "rot": 0.0}
    if type_ == "texte":
        e.update(texte="Offre limitée", couleur=st["couleur_nom"], t=0.07)
    elif type_ == "prix":
        e.update(texte="9,90", couleur=st["couleur_prix"], fond=False, couleur_fond=st["couleur_fond_prix"], t=0.16)
    else:
        e.update(forme=FORME_DEFAUT, texte="", couleur=st["couleur_accent"], l=0.34, h=0.22)
    return element_valide(e)


def dupliquer(e) -> dict:
    """Une copie de l'élément, légèrement décalée (même couche)."""
    copie = dict(e)
    copie.update(id=uuid.uuid4().hex[:8], cx=e["cx"] + 0.04, cy=e["cy"] + 0.04)
    return element_valide(copie)


def deplacer(e, ddx, ddy, ds=1.0) -> dict:
    """Déplace l'élément (ddx, ddy : fractions de la largeur et de la hauteur de la page, ddy vers le bas) et le
    redimensionne (ds : facteur) autour de son centre."""
    ds = _nombre(ds, 1.0, 0.1, 10.0)
    r = dict(e)
    r["cx"] = e["cx"] + _nombre(ddx, 0.0, -2.0, 2.0)
    r["cy"] = e["cy"] + _nombre(ddy, 0.0, -2.0, 2.0)
    if e["type"] in ("texte", "prix"):
        r["t"] = e["t"] * ds
    else:
        r["l"], r["h"] = e["l"] * ds, e["h"] * ds
    return element_valide(r)


def nom(e) -> str:
    """Libellé court de l'élément, pour les listes et l'aperçu."""
    def court(texte, n):
        texte = " ".join(str(texte).split())
        return texte if len(texte) <= n else texte[:n - 1].rstrip() + "…"
    if e["type"] == "texte":
        return "Texte : " + (court(e["texte"], 22) or "(vide)")
    if e["type"] == "prix":
        return "Prix : " + (court(e["texte"], 12) or "(vide)")
    return "Forme : " + FORMES[e["forme"]][0] + (f" « {court(e['texte'], 14)} »" if e["texte"].strip() else "")


# ----------------------------------------------------------------------------
# Mise en page et dessin
# ----------------------------------------------------------------------------
def _englobant(cx, cy, largeur, hauteur, rot):
    """Rectangle englobant (x0, y0, x1, y1) d'un rectangle largeur × hauteur centré en (cx, cy) et tourné de rot degrés."""
    a = math.radians(rot)
    ca, sa = abs(math.cos(a)), abs(math.sin(a))
    bw, bh = largeur * ca + hauteur * sa, largeur * sa + hauteur * ca
    return (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)


def _prix(texte):
    try:
        p = af.parse_prix(texte)
    except Exception:  # valeur non finie (« NaN »…)
        return None
    return p if p is not None and p <= PRIX_MAX else None


def prix_valide(texte) -> bool:
    """Le texte est-il un prix lisible (« 9,90 », « 12 », « 3.5 € ») ?"""
    return _prix(texte) is not None


def mise_en_page(e, largeur_page, hauteur_page, petit_cote, style) -> dict:
    """Géométrie de l'élément sur la page, en points (origine en bas à gauche) : centre, taille, rectangle englobant
    (« bbox ») et contenu à dessiner. « vide » : rien à dessiner (texte vide, prix invalide)."""
    st = dict(af.STYLE_DEFAUT)
    st.update(style or {})
    reg, bold = af.polices_famille(e.get("police") or st["police"])
    police = bold if e["gras"] else reg
    cx, cy = e["cx"] * largeur_page, hauteur_page - e["cy"] * hauteur_page
    g = {"e": e, "cx": cx, "cy": cy, "vide": False}
    if e["type"] == "texte":
        taille = e["t"] * petit_cote
        lignes = e["texte"].strip("\n").split("\n")
        if not any(ligne.strip() for ligne in lignes):
            g.update(vide=True, w=taille * 2.0, h=taille * 1.15)
        else:
            g.update(w=max(af._largeur_texte(ligne, police, taille) for ligne in lignes),
                     h=len(lignes) * taille * 1.15)
        g.update(lignes=lignes, police=police, taille=taille, hc=af._hauteur_chiffre(police))
    elif e["type"] == "prix":
        taille, hc, p = e["t"] * petit_cote, af._hauteur_chiffre(police), _prix(e["texte"])
        if p is None:
            g.update(vide=True, w=taille * 1.5, h=taille * hc)
        else:
            w, h = af._largeur_prix(p, taille, police), taille * hc
            if e["fond"]:
                w, h = w + 0.60 * taille, h + 0.44 * taille
            g.update(w=w, h=h)
        g.update(p=p, police=police, taille=taille, hc=hc)
    else:
        largeur = e["l"] * petit_cote
        g.update(w=largeur, h=largeur if not FORMES[e["forme"]][1] else e["h"] * petit_cote, police=police,
                 hc=af._hauteur_chiffre(police))
    g["bbox"] = _englobant(cx, cy, g["w"], g["h"], e["rot"])
    return g


def _polygone(c, points):
    chemin = c.beginPath()
    chemin.moveTo(*points[0])
    for point in points[1:]:
        chemin.lineTo(*point)
    chemin.close()
    c.drawPath(chemin, stroke=0, fill=1)


def _points_etoile(branches, r_ext, r_int):
    points = []
    for i in range(2 * branches):
        r = r_ext if i % 2 == 0 else r_int
        a = math.pi / 2 + i * math.pi / branches
        points.append((r * math.cos(a), r * math.sin(a)))
    return points


def _points_coeur(w, h, n=120):
    brut = []
    for i in range(n):
        t = 2 * math.pi * i / n
        brut.append((16 * math.sin(t) ** 3,
                     13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)))
    x0, x1 = min(p[0] for p in brut), max(p[0] for p in brut)
    y0, y1 = min(p[1] for p in brut), max(p[1] for p in brut)
    return [((x - (x0 + x1) / 2) / (x1 - x0) * w, (y - (y0 + y1) / 2) / (y1 - y0) * h) for x, y in brut]


def _forme(c, forme, w, h):
    """Dessine la forme pleine, centrée en (0, 0), de la couleur de remplissage courante."""
    if forme == "rectangle" or forme == "carre":
        c.rect(-w / 2, -h / 2, w, h, stroke=0, fill=1)
    elif forme == "arrondi":
        c.roundRect(-w / 2, -h / 2, w, h, min(w, h) * 0.22, stroke=0, fill=1)
    elif forme == "rond":
        c.circle(0, 0, w / 2, stroke=0, fill=1)
    elif forme == "ovale":
        c.ellipse(-w / 2, -h / 2, w / 2, h / 2, stroke=0, fill=1)
    elif forme == "triangle":
        _polygone(c, [(-w / 2, -h / 2), (w / 2, -h / 2), (0, h / 2)])
    elif forme == "losange":
        _polygone(c, [(0, h / 2), (w / 2, 0), (0, -h / 2), (-w / 2, 0)])
    elif forme == "fleche":  # vers la droite (la rotation change le sens)
        tete = min(w * 0.5, h * 0.85)
        tige = h * 0.5
        x = w / 2 - tete
        _polygone(c, [(-w / 2, -tige / 2), (x, -tige / 2), (x, -h / 2), (w / 2, 0), (x, h / 2), (x, tige / 2),
                      (-w / 2, tige / 2)])
    elif forme == "etoile":
        r = w / 2
        _polygone(c, [(x, y - 0.0955 * r) for x, y in _points_etoile(5, r, r * 0.45)])  # centrée dans son carré
    elif forme == "explosion":
        _polygone(c, _points_etoile(16, w / 2, w / 2 * 0.80))
    elif forme == "coeur":
        _polygone(c, _points_coeur(w, h))
    elif forme == "croix":
        a, m = w * 0.18, w / 2
        _polygone(c, [(-a, -m), (a, -m), (a, -a), (m, -a), (m, a), (a, a), (a, m), (-a, m), (-a, a), (-m, a),
                      (-m, -a), (-a, -a)])


# Zone utile à l'intérieur de chaque forme, pour y écrire un texte : (largeur, hauteur, décalage x, décalage y), en
# fractions de la largeur et de la hauteur de la forme
_INTERIEUR = {"rectangle": (0.88, 0.74, 0.0, 0.0), "arrondi": (0.84, 0.70, 0.0, 0.0), "carre": (0.84, 0.70, 0.0, 0.0),
              "rond": (0.70, 0.46, 0.0, 0.0), "ovale": (0.70, 0.46, 0.0, 0.0), "triangle": (0.50, 0.34, 0.0, -0.14),
              "losange": (0.50, 0.36, 0.0, 0.0), "fleche": (0.58, 0.38, -0.10, 0.0), "etoile": (0.46, 0.26, 0.0, -0.06),
              "explosion": (0.66, 0.42, 0.0, 0.0), "coeur": (0.56, 0.32, 0.0, 0.05), "croix": (0.30, 0.30, 0.0, 0.0)}


def couleur_sur(fond_hex: str):
    """Blanc sur un fond foncé, presque noir sur un fond clair."""
    r, g, b = (int(fond_hex[i:i + 2], 16) for i in (1, 3, 5))
    return HexColor("#FFFFFF") if 0.299 * r + 0.587 * g + 0.114 * b < 150 else HexColor("#222222")


def _texte_dans_forme(c, g):
    e = g["e"]
    lignes = e["texte"].strip("\n").split("\n")
    if not any(ligne.strip() for ligne in lignes):
        return
    fw, fh, dx, dy = _INTERIEUR[e["forme"]]
    zone_w, zone_h = g["w"] * fw, g["h"] * fh
    unite = max(af._largeur_texte(ligne, g["police"], 1.0) for ligne in lignes)
    taille = max(1.0, min(zone_w / max(unite, 0.1), zone_h / (len(lignes) * 1.15)))
    haut = dy * g["h"] + len(lignes) * taille * 1.15 / 2
    c.setFillColor(couleur_sur(e["couleur"]))
    for i, ligne in enumerate(lignes):
        af._dessiner_texte(c, ligne, dx * g["w"], haut - taille * 0.85 - i * taille * 1.15, taille, g["police"],
                           couleur_sur(e["couleur"]), g["hc"], e["italique"], e["souligne"])


def dessiner(c, g, style=None) -> None:
    """Dessine l'élément (mis en page par mise_en_page) sur le canevas reportlab."""
    e = g["e"]
    if g["vide"]:
        return
    c.saveState()
    c.translate(g["cx"], g["cy"])
    if e["rot"]:
        c.rotate(-e["rot"])  # sens des aiguilles d'une montre
    couleur = HexColor(e["couleur"])
    if e["type"] == "texte":
        n = len(g["lignes"])
        haut = n * g["taille"] * 1.15 / 2
        for i, ligne in enumerate(g["lignes"]):
            af._dessiner_texte(c, ligne, 0, haut - g["taille"] * 0.85 - i * g["taille"] * 1.15, g["taille"],
                               g["police"], couleur, g["hc"], e["italique"], e["souligne"])
    elif e["type"] == "prix":
        if e["fond"]:
            c.setFillColor(HexColor(e["couleur_fond"]))
            c.roundRect(-g["w"] / 2, -g["h"] / 2, g["w"], g["h"], 0.14 * g["taille"], stroke=0, fill=1)
        af._dessiner_prix(c, g["p"], 0, -g["taille"] * g["hc"] / 2, g["taille"], g["police"], couleur, g["hc"],
                          e["italique"], e["souligne"])
    else:
        c.setFillColor(couleur)
        _forme(c, e["forme"], g["w"], g["h"])
        _texte_dans_forme(c, g)
    c.restoreState()


# ----------------------------------------------------------------------------
# Visuel à fond transparent (quand un élément est derrière)
# ----------------------------------------------------------------------------
COTE_REGION = 400            # côté maximal (pixels) de l'image sur laquelle on cherche le fond
COTE_MAX_TRANSPARENT = 1500  # côté maximal (pixels) du visuel transparent dessiné (l'impression n'en demande pas plus)
SEUIL_FOND = 236             # sur l'image réduite, un pixel dont le canal le plus sombre atteint ce seuil est « blanc »
# transparence selon le canal le plus sombre d'un pixel : blanc (244 et plus) = transparent, 220 et moins = opaque
_RAMPE = [0 if v >= 244 else 255 if v <= 220 else int((244 - v) * 255 / 24) for v in range(256)]
_MAX_REGIONS = 16
_regions = OrderedDict()  # empreinte de l'image -> masque des zones de fond (petit : quelques centaines de Ko)
_verrou = threading.Lock()


def _plus_sombre(img):
    r, g, b = img.split()
    return ImageChops.darker(ImageChops.darker(r, g), b)


def _region_fond(img):
    """Masque (mode L, 255 = fond) des zones blanches reliées au bord de l'image, sur une version réduite."""
    rgb = img.convert("RGB")
    cle = hashlib.sha1(rgb.resize((64, 64)).tobytes() + str(rgb.size).encode()).hexdigest()
    with _verrou:
        if cle in _regions:
            _regions.move_to_end(cle)
            return _regions[cle]
    echelle = min(1.0, COTE_REGION / max(rgb.size))
    petit = rgb.resize((max(1, int(rgb.width * echelle)), max(1, int(rgb.height * echelle))), Image.BOX)
    blanc = _plus_sombre(petit).point(lambda v: 255 if v >= SEUIL_FOND else 0)
    w, h = blanc.size
    cadre = ImageOps.expand(blanc, border=1, fill=255)  # le tour de l'image relie tout le blanc qui touche un bord
    ImageDraw.floodfill(cadre, (0, 0), 128)
    region = cadre.crop((1, 1, w + 1, h + 1)).point(lambda v: 255 if v == 128 else 0)
    with _verrou:
        _regions[cle] = region
        while len(_regions) > _MAX_REGIONS:
            _regions.popitem(last=False)
    return region


def visuel_transparent(img):
    """Le visuel (RVBA) dont le fond blanc, relié au bord de l'image, est transparent. Les blancs à l'intérieur du produit
    (flacon blanc, étiquette) restent opaques. Si aucun fond n'est trouvé, le visuel reste opaque."""
    rgb = img.convert("RGB")
    region = _region_fond(rgb)
    if rgb.width * rgb.height > COTE_MAX_TRANSPARENT ** 2 or max(rgb.size) > COTE_MAX_TRANSPARENT:
        rgb = rgb.copy()
        rgb.thumbnail((COTE_MAX_TRANSPARENT, COTE_MAX_TRANSPARENT), Image.LANCZOS)
    histogramme = region.histogram()
    couverture = histogramme[255] / max(1, sum(histogramme))
    rgba = rgb.convert("RGBA")
    if couverture == 0 or couverture > 0.97:  # pas de fond, ou tout est blanc : on ne touche à rien
        return rgba
    zone = region.filter(ImageFilter.MaxFilter(3)).resize(rgb.size, Image.BILINEAR).point(lambda v: 255 if v > 0 else 0)
    alpha = ImageChops.lighter(_plus_sombre(rgb).point(_RAMPE), ImageChops.invert(zone))
    rgba.putalpha(alpha)
    return rgba
