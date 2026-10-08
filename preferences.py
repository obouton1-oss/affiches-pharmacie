"""Préférences d'une pharmacie : style des affiches (police, couleurs, ordre des éléments), habitudes de départ
(format, orientation, majuscules, logo) et nom affiché.

Deux fichiers dans le dossier de la pharmacie (voir pharmacie.py), tous deux sauvegardés en ligne :
  style.json        : réglages de dessin passés au moteur d'affiche (police, couleurs, bandeau du prix, ordre des éléments) ;
  preferences.json  : habitudes de départ et état de la « mise en route » (faite ou non).

Ce module ne dépend pas de Streamlit. Il contient aussi le relevé des couleurs d'un visuel existant (simple analyse
d'image, sans intelligence artificielle) pour proposer des couleurs à la pharmacie.
"""
import colorsys
import json
from pathlib import Path

from PIL import Image

import affiche as af

FICHIER_STYLE = "style.json"
FICHIER_PREFERENCES = "preferences.json"
PREFERENCES_DEFAUT = {"faite": False, "nom": "", "format": "A5", "paysage": False, "majuscules": True, "logo": True}

# Ordres proposés (de haut en bas, sous le visuel)
ORDRES = {
    "Classique": af.ORDRE_DEFAUT,
    "Prix juste sous la photo": ("prix_barre", "prix", "marque", "detail", "dates"),
    "Marque, prix, puis produit": ("marque", "prix_barre", "prix", "detail", "dates"),
}


# ----------------------------------------------------------------------------
# Style (style.json)
# ----------------------------------------------------------------------------
def charger_style(dossier: Path) -> dict:
    st_ = dict(af.STYLE_DEFAUT)
    try:
        sauve = json.loads((Path(dossier) / FICHIER_STYLE).read_text(encoding="utf-8"))
        if "fond_prix" in sauve:  # les anciens fichiers (sans bandeau de prix) reprennent le nouveau style par défaut
            st_.update({k: v for k, v in sauve.items() if k in st_})
            if "ordre" in sauve and af.ordre_valide(sauve["ordre"]) == tuple(sauve["ordre"]):
                st_["ordre"] = list(sauve["ordre"])  # absent tant que la pharmacie n'a pas choisi un autre ordre
        elif sauve.get("police") in af.POLICES:
            st_["police"] = sauve["police"]
    except Exception:
        pass
    if st_["police"] not in af.POLICES:
        st_["police"] = af.STYLE_DEFAUT["police"]
    return st_


def sauver_style(dossier: Path, style: dict) -> None:
    (Path(dossier) / FICHIER_STYLE).write_text(json.dumps(style, ensure_ascii=False, indent=1), encoding="utf-8")


# ----------------------------------------------------------------------------
# Préférences (preferences.json)
# ----------------------------------------------------------------------------
def charger(dossier: Path) -> dict:
    prefs = dict(PREFERENCES_DEFAUT)
    try:
        lu = json.loads((Path(dossier) / FICHIER_PREFERENCES).read_text(encoding="utf-8"))
        if isinstance(lu, dict):
            prefs["faite"] = bool(lu.get("faite", False))
            prefs["nom"] = str(lu.get("nom") or "").strip()[:80]
            if lu.get("format") in af.FORMATS:
                prefs["format"] = lu["format"]
            prefs["paysage"] = bool(lu.get("paysage", False))
            prefs["majuscules"] = bool(lu.get("majuscules", True))
            prefs["logo"] = bool(lu.get("logo", True))
    except Exception:
        pass
    return prefs


def sauver(dossier: Path, prefs: dict) -> None:
    propre = {**PREFERENCES_DEFAUT, **{k: prefs[k] for k in PREFERENCES_DEFAUT if k in prefs}}
    (Path(dossier) / FICHIER_PREFERENCES).write_text(json.dumps(propre, ensure_ascii=False, indent=1), encoding="utf-8")


# ----------------------------------------------------------------------------
# Couleurs d'un visuel existant
# ----------------------------------------------------------------------------
def _hex(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*[int(v) for v in rgb])


def _hsv(rgb):
    return colorsys.rgb_to_hsv(*(v / 255 for v in rgb))


def palette(image: Image.Image, n: int = 6) -> list:
    """Couleurs dominantes d'une image : [(« #RRGGBB », part de l'image entre 0 et 1), ...], les plus présentes d'abord.
    Le blanc, le fond de page, est écarté."""
    img = image.convert("RGB")
    img.thumbnail((160, 160))
    reduit = img.quantize(colors=max(n * 2, 8), method=Image.Quantize.MEDIANCUT)
    couleurs = reduit.getcolors() or []
    tableau = reduit.getpalette() or []
    total = sum(c for c, _ in couleurs) or 1
    res = []
    for c, idx in sorted(couleurs, reverse=True):
        rgb = tuple(tableau[idx * 3: idx * 3 + 3])
        if min(rgb) >= 235:  # blanc ou presque
            continue
        if any(sum(abs(a - b) for a, b in zip(rgb, deja)) < 60 for deja, _ in ((_rgb(h), p) for h, p in res)):
            continue  # nuance trop proche d'une couleur déjà retenue
        res.append((_hex(rgb), c / total))
        if len(res) >= n:
            break
    return res


def _rgb(hexe: str):
    h = hexe.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def proposer_couleurs(pal: list) -> dict:
    """Propose des couleurs d'affiche à partir d'une palette (à faire valider par la pharmacie) :
    nom et marque = couleur sombre la plus présente, accent = couleur la plus vive, bandeau du prix = couleur claire et vive
    s'il y en a une (le texte du prix est alors noir ou blanc, selon la clarté du fond)."""
    if not pal:
        return {}
    cles = [(h, part, _hsv(_rgb(h))) for h, part in pal]
    sombres = [c for c in cles if c[2][2] < 0.62 and c[2][1] > 0.15] or [c for c in cles if c[2][2] < 0.62] or cles
    nom = max(sombres, key=lambda c: c[1])[0]
    vives = sorted(cles, key=lambda c: c[2][1] * c[2][2], reverse=True)
    accent = vives[0][0]
    clairs = [c for c in vives if c[2][2] >= 0.75 and c[2][1] >= 0.45]
    res = {"couleur_nom": nom, "couleur_accent": accent}
    if clairs:
        fond = clairs[0][0]
        res.update(fond_prix=True, couleur_fond_prix=fond, couleur_prix=_texte_sur(fond))
    else:
        res.update(fond_prix=False, couleur_prix=nom)
    return res


def _texte_sur(fond_hex: str) -> str:
    """Noir ou blanc, selon ce qui se lit le mieux sur ce fond."""
    r, g, b = (v / 255 for v in _rgb(fond_hex))
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#000000" if luminance > 0.45 else "#FFFFFF"
