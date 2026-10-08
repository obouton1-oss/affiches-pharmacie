"""Refaire le PDF d'une affiche de l'historique (téléchargement pour la conserver, ou réimpression).

L'historique garde les réglages de chaque affiche (produit, prix ou promotion, dates, format, style, positions) et ses visuels :
ce module en refait le PDF, tel qu'à l'enregistrement. Ne dépend pas de Streamlit.
"""
from datetime import date
from decimal import Decimal

from reportlab.lib.units import mm

import affiche as af
import promos


def style_entree(e: dict) -> dict:
    """Style de l'affiche enregistrée (police, couleurs, ordre des éléments), complété et validé."""
    style = dict(af.STYLE_DEFAUT)
    brut = e.get("style") or {}
    style.update({k: v for k, v in brut.items() if k in af.STYLE_DEFAUT})
    ordre = brut.get("ordre")
    if ordre and af.ordre_valide(ordre) == tuple(ordre):  # affiche faite avec un autre ordre des éléments
        style["ordre"] = list(ordre)
    if style["police"] not in af.POLICES:
        style["police"] = af.STYLE_DEFAUT["police"]
    return style


def reglages_entree(e: dict) -> dict:
    """Positions et tailles des éléments déplacés à la main sur l'affiche enregistrée."""
    reglages = af.reglages_defaut()
    for el, g in (e.get("reglages") or {}).items():
        if el in reglages:
            reglages[el].update({k: float(v) for k, v in g.items() if k in ("dx", "dy", "s")})
    return reglages


def taille_entree(e: dict):
    """Dimensions de la page de l'affiche, en points."""
    if e.get("format") == "Personnalisé":
        return (int(e.get("largeur_mm") or 100) * mm, int(e.get("hauteur_mm") or 150) * mm)
    format_ = e.get("format") if e.get("format") in af.FORMATS else "A5"
    return af.orienter(af.FORMATS[format_], e.get("orientation") == "Paysage")


def _date(texte):
    try:
        return date.fromisoformat(texte) if texte else None
    except (TypeError, ValueError):
        return None


def _decimal(valeur):
    try:
        return Decimal(str(valeur)) if valeur not in (None, "") else None
    except Exception:
        return None


def pdf_depuis_entree(e: dict, visuels=(), identite=None, logo_marque=None) -> bytes:
    """PDF (une page, au format de l'affiche) d'une entrée de l'historique. visuels : images PIL de l'affiche (le visuel
    principal d'abord) ; identite : nom et logo de la pharmacie (voir pharmacie.identite) ; logo_marque : logo de la marque
    à imprimer à la place de son nom (image PIL), s'il y en avait un."""
    rendu_promo = promos.rendu_entree(e)
    if rendu_promo:
        prix, prix_barre = rendu_promo["prix"], rendu_promo["prix_barre"]
    else:
        prix, prix_barre = _decimal(e.get("prix")), _decimal(e.get("prix_barre"))
    debut, fin = _date(e.get("debut")), _date(e.get("fin"))
    dates = af.libelle_dates(debut, fin) if (debut or fin) else ""
    pdf, _ = af.rendu(taille_entree(e), e.get("marque") or "", e.get("detail") or "", prix, prix_barre, dates,
                      list(visuels), bool(e.get("logo", True)), reglages=reglages_entree(e),
                      majuscules=bool(e.get("majuscules", True)), style=style_entree(e), promo=rendu_promo,
                      identite=identite, logo_marque=logo_marque)
    return pdf
