"""Générateur d'affiches promo – Pharmacie Bouton.
Lancement :  streamlit run app.py
"""
import calendar
import re
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import base64
import hashlib
import html
import json

import streamlit as st
import streamlit.components.v1 as components
from PIL import Image
from reportlab.lib.units import mm

import acces
import catalogue
import elements_libres as libres
import habillage
import historique
import images_produits as ip
import logos_marques
import marques
import mise_en_route
import planche
import promos
import reimpression
import retouches
import sauvegarde
import nettete
import nettoyage
import pharmacie
import preferences
import types_affiche
from chemins import EN_LIGNE
from affiche import (CADRES, DOSSIER_POLICES, ELEMENTS, FORMATS, MAX_VISUELS, POLICES, TEXTES, THEMES, apercu_png,
                     couleur_detail, couleur_kicker,
                     disposition_a4, libelle_dates, orienter, parse_prix, pdf_impression, rendu, reglages_defaut,
                     textes_valides)

st.set_page_config(page_title="Affiches promo", page_icon="🏷️", layout="wide")
habillage.appliquer()  # feuille de style (aussi pour la page de mot de passe)
acces.verifier_acces()  # mot de passe commun (version en ligne) ou choix de la pharmacie + mot de passe (plusieurs pharmacies)

MULTI = pharmacie.multi()  # plusieurs pharmacies : chaque session ne voit que les données de sa pharmacie
EN_LIGNE = EN_LIGNE or MULTI
CTX = pharmacie.contexte()
IDENTITE = pharmacie.identite(CTX)  # nom et logo à dessiner sur les affiches ; None = pharmacie d'origine


@st.cache_resource
def _demarrage():
    if not MULTI:
        sauvegarde.restaurer()  # version en ligne : récupère le catalogue, le style et l'historique sauvegardés
        historique.purger()  # supprime les affiches de plus de 3 mois (et leurs visuels)
    nettoyage.vider_ancien_cache()  # anciennes versions : images nettoyées gardées sur le disque, inutiles
    nettete.vider_ancien_cache()
    return True


@st.cache_resource
def _demarrage_pharmacie(identifiant, prefixe):
    """Une fois par pharmacie et par démarrage de l'application : récupère ses données sauvegardées, purge son historique."""
    sauvegarde.restaurer(prefixe)
    historique.purger()
    return True


_demarrage()
if MULTI:
    _demarrage_pharmacie(CTX.id, CTX.prefixe)
_entete = st.columns([10, 1.5, 2] if MULTI else [10, 1.5], vertical_alignment="center")
with _entete[0]:
    habillage.entete("Affiches promo", pharmacie.nom_affiche(CTX))
with _entete[1]:
    with st.popover("Aide", icon=":material/help:", use_container_width=True):
        habillage.contenu_aide()
if MULTI:  # poste partagé : on peut quitter sa pharmacie sans chercher l'onglet « Ma pharmacie »
    _entete[2].button("Se déconnecter", key="entete_deconnexion", on_click=pharmacie.fermer_session,
                      icon=":material/logout:", type="tertiary", use_container_width=True)

# habitudes de la pharmacie (format, orientation…) et style de ses affiches ; à la première connexion d'une nouvelle
# pharmacie, les questions de mise en route remplacent l'outil (la pharmacie d'origine n'est pas concernée)
PREFS = preferences.charger(CTX.dossier)
TYPES = types_affiche.charger(CTX.dossier)  # types d'affiche de la pharmacie (format, logo, photo, style : voir types_affiche.py)
if st.session_state.get("type_actif") not in {t["id"] for t in TYPES["types"]}:
    st.session_state.type_actif = TYPES["actif"]
TYPE = types_affiche.trouver(TYPES, st.session_state.type_actif)
if "style" not in st.session_state:
    st.session_state.style = dict(TYPE["style"])
# Aperçu des polices : composant invisible (affiché à chaque exécution, écrans de mise en route compris) qui écrit chaque
# nom de police dans sa propre police dans les listes de choix
apercu_polices = components.declare_component("apercu_polices", path=str(DOSSIER_POLICES))
with st.container(key="apercu_polices"):
    apercu_polices(key="apercu_polices_composant", default=None)
if MULTI and not PREFS["faite"] and CTX.prefixe != "":
    mise_en_route.afficher(CTX, premiere_fois=True)  # s'arrête ici (st.stop) tant que la mise en route n'est pas faite

# Création d'un nouveau type d'affiche, pas à pas : le même écran de questions remplace l'outil le temps des réponses.
# Streamlit oublie les champs qui ne sont pas affichés pendant un affichage, et le navigateur repart d'un champ vide
# quand il réapparaît : les champs de l'affiche en cours sont donc réaffirmés pendant l'écran (pour ne pas être oubliés),
# puis une dernière fois au retour (pour que le navigateur les réaffiche). Ainsi l'affiche en cours est retrouvée telle
# quelle, après « Annuler » comme après la création du type. (Les sélecteurs de couleur et les curseurs, dont la valeur
# vient de l'affiche elle-même, n'ont pas besoin de ce traitement.)
_CHAMPS_A_GARDER = ("w_",)
_AUTRES_CHAMPS_A_GARDER = ("nettete_mode", "theme_choisi", "element_actif", "filtre_hist", "titre_page",
                           "orientation_page", "type_nouveau_nom")


def _garder_les_champs() -> None:
    etat = st.session_state
    for cle in list(etat.keys()):
        if isinstance(cle, str) and (cle.startswith(_CHAMPS_A_GARDER) or cle in _AUTRES_CHAMPS_A_GARDER):
            try:
                etat[cle] = etat[cle]
            except Exception:  # champ qui ne se laisse pas réaffirmer : sans importance
                pass


if st.session_state.get("ecran_nouveau_type"):
    _garder_les_champs()
    mise_en_route.afficher(CTX, premiere_fois=False, nouveau_type=True)  # s'arrête ici (st.stop)
elif st.session_state.pop("retour_ecran_nouveau_type", False):
    _garder_les_champs()

ONGLET_CREER, ONGLET_HISTORIQUE, ONGLET_PAGE = "Créer une affiche", "Historique", "Page A4 regroupée"
ONGLET_PHARMACIE = "Ma pharmacie"
_noms_onglets = [ONGLET_CREER, ONGLET_HISTORIQUE, ONGLET_PAGE] + ([ONGLET_PHARMACIE] if MULTI else [])
try:  # versions récentes de Streamlit : l'onglet affiché peut être changé par l'application
    _onglets = st.tabs(_noms_onglets, key="onglet", on_change="rerun")
    ONGLETS_PILOTABLES = True
except TypeError:
    _onglets = st.tabs(_noms_onglets)
    ONGLETS_PILOTABLES = False
onglet_creer, onglet_hist, onglet_page = _onglets[:3]
onglet_pharmacie = _onglets[3] if MULTI else None

recherche_produit = components.declare_component(
    "recherche_produit", path=str(Path(__file__).parent / "composant_recherche"))

editeur_affiche = components.declare_component(
    "editeur_affiche", path=str(Path(__file__).parent / "composant_editeur"))

collage_image = components.declare_component(
    "collage_image", path=str(Path(__file__).parent / "composant_collage"))

ss = st.session_state
for cle, defaut in (("image", None), ("marque", ""), ("detail", ""), ("journal", []), ("props", []),
                    ("props_msg", ""), ("code", ""), ("derniere_sel", None),
                    ("reglages", None), ("ver", 0), ("dernier_ev", None), ("element_actif", "marque"),
                    ("regroupe", []), ("suppr_attente", None), ("msg_hist", ""), ("planche_cache", None),
                    # valeurs de départ des champs du formulaire (modifiables aussi par « Rouvrir » dans l'historique)
                    ("w_prix", ""), ("w_barre_on", False), ("w_barre", ""), ("w_dates_on", False),
                    ("w_debut", date.today()), ("w_fin", date.today()), ("w_format", TYPE["format"]),
                    ("w_paysage", TYPE["paysage"]), ("w_lg", TYPE["largeur_mm"] or 100),
                    ("w_ht", TYPE["hauteur_mm"] or 150), ("w_logo", TYPE["logo"]),
                    ("w_majuscules", TYPE["majuscules"]), ("w_photo", TYPE["photo"]),
                    # logo de la marque (voir marques.py) et gestion des types d'affiche
                    ("w_logo_marque", True), ("cle_marque_vue", None), ("logo_marque_n", 0), ("derniere_collee_marque", None),
                    ("televerse_marque_vu", None), ("logos_props", None), ("logos_lot", []),
                    ("type_suppr_attente", False), ("w_type", TYPE["id"]),
                    ("w_nettoyer", True), ("w_rg", 0), ("w_rd", 0), ("w_rh", 0), ("w_rb", 0),
                    ("nettete_mode", "Rapide"), ("titre_page", planche.TITRE_DEFAUT), ("w_logo_page", True),
                    ("orientation_page", planche.PORTRAIT), ("logo_n", 0),
                    ("filtre_hist", ""),
                    # éléments ajoutés à la main (texte, prix, forme) et élément ajouté choisi
                    ("elements", []), ("element_libre_actif", None), ("selection_libre", False),
                    # plusieurs visuels (gamme) et « Nouvelle affiche »
                    ("extras", []), ("extra_uid", 0), ("extra_n", 0), ("ajout_extra", False), ("cand_extra", None),
                    ("derniere_sel_extra", None), ("raz", 0), ("raz_attente", False), ("w_exemplaires", 1),
                    # recherche web vérifiée, collage d'image
                    ("props_autres", []), ("journal_web", []), ("info_nom", ""), ("derniere_collee", None),
                    ("derniere_collee_extra", None),
                    # propositions gardées après un choix (« Changer de photo »)
                    ("props_choisie", None), ("props_ouvertes", True)):
    ss.setdefault(cle, defaut)
# autres types de promotion : type choisi, champs et cases « Faire apparaître »
for cle, defaut in ([("w_promo_type", promos.STANDARD)]
                    + [(f"w_promo_{k}", v) for k, v in promos.CHAMPS_DEFAUT.items()]
                    + [(f"w_promo_opt_{k}", v) for k, v in promos.OPTIONS_DEFAUT.items()]):
    ss.setdefault(cle, defaut)
if ss.reglages is None:
    ss.reglages = reglages_defaut()
for _el, _reglage in reglages_defaut().items():  # éléments ajoutés depuis (texte sous le prix, pastille)
    ss.reglages.setdefault(_el, _reglage)

SEUIL_NETTETE = 700  # en dessous (côté le plus court, en pixels), la netteté peut être améliorée


ss.setdefault("w_police", ss.style["police"])
ss.setdefault("selection_apercu", False)  # un élément de l'aperçu est choisi (sa barre de réglages est affichée)

# Retouches faites directement sur l'aperçu (barre de réglages, glisser, clavier, annuler) : appliquées ici, avant que
# les champs du formulaire ne soient dessinés, pour qu'ils affichent tout de suite les nouvelles valeurs
retouches.noter(ss)
retouches.appliquer(ss, ss.get("editeur"))


def maj_style(cle, cle_widget):
    ss.style[cle] = ss[cle_widget]  # vaut pour l'affiche en cours ; « Mettre à jour ce type » le garde pour la suite


def maj_detail_autre(cle_widget):
    """Case « Détail du produit d'une autre couleur que la marque » : cochée, le détail part de la couleur de la
    marque (à modifier ensuite) ; décochée, il suit de nouveau la couleur de la marque."""
    ss.style["couleur_detail"] = (ss.style.get("couleur_detail") or ss.style["couleur_nom"]) if ss[cle_widget] else None


def appliquer_theme():
    theme = THEMES.get(ss.theme_choisi)
    if theme:
        ss.style.update(theme)
        ss.style["couleur_detail"] = None  # un thème colore la marque et le détail de la même couleur
        ss.style["couleur_kicker"] = None  # et le petit texte de l'offre comme le prix
        ss.ver += 1  # recrée les sélecteurs de couleur avec les nouvelles valeurs


COMME_AFFICHE = "Police de l'affiche"
STYLES_TEXTE = ["gras", "italique", "souligne"]
ICONES_STYLE = {"gras": ":material/format_bold:", "italique": ":material/format_italic:",
                "souligne": ":material/format_underlined:"}
AIDE_STYLE = "Gras, italique, souligné (on peut en choisir plusieurs, ou aucun)"
DERRIERE, DEVANT = "Derrière le visuel", "Devant le visuel"


def maj_texte(texte, cle_police, cle_style):
    """Police, gras, italique et souligné d'un texte de l'affiche (marque, détail, prix, petit texte de l'offre, prix
    barré, texte sous le prix, dates) ; ce qui est comme à l'origine n'est pas gardé."""
    styles = ss[cle_style] or []
    brut = {"police": ss[cle_police] if ss[cle_police] in POLICES else None, "gras": "gras" in styles,
            "italique": "italique" in styles, "souligne": "souligne" in styles}
    ss.style["textes"] = textes_valides({**(ss.style.get("textes") or {}), texte: brut})
    ss.ver += 1  # le même réglage est aussi proposé ailleurs (sous le champ de saisie, « Police et couleurs ») : à jour partout


def etat_texte(texte):
    """Police (None = celle de l'affiche), gras, italique, souligné d'un texte, tels qu'ils s'impriment. Le petit texte
    de l'offre suit le prix tant qu'il n'a pas son propre style."""
    textes = textes_valides(ss.style.get("textes"))
    source = "prix" if texte == "kicker" and "kicker" not in textes else texte
    etat = textes.get(source, {})
    return {"police": etat.get("police"), "gras": etat.get("gras", TEXTES[source][1]),
            "italique": etat.get("italique", False), "souligne": etat.get("souligne", False)}


COULEUR_TEXTE = {"marque": "couleur_nom", "detail": "couleur_detail", "prix": "couleur_prix",
                 "kicker": "couleur_kicker", "prix_barre": "couleur_secondaire", "dates": "couleur_secondaire"}


def couleur_de(cle):
    """Couleur imprimée pour une clé de couleur du style (le détail suit la marque, le petit texte de l'offre suit le
    prix, tant qu'ils n'ont pas la leur)."""
    if cle == "couleur_detail":
        return couleur_detail(ss.style)
    if cle == "couleur_kicker":
        return couleur_kicker(ss.style)
    return ss.style.get(cle) or ss.style["couleur_nom"]


def maj_couleur(cle, cle_widget):
    ss.style[cle] = ss[cle_widget]
    ss.ver += 1  # sélecteurs de couleur des autres endroits à jour


def maj_taille(el, cle_widget):
    ss.reglages[el]["s"] = ss[cle_widget] / 100
    ss.ver += 1


def reglages_texte(el, aide_couleur=""):
    """Sous le champ où l'on écrit un texte : sa police, gras / italique / souligné, sa couleur et sa taille sur l'affiche
    (les mêmes réglages que dans « Police et couleurs », « Réglages précis » et la barre au-dessus de l'aperçu)."""
    etat = etat_texte(el)
    options = [COMME_AFFICHE] + POLICES
    kp, ks = f"sf_police_{el}_{ss.ver}", f"sf_style_{el}_{ss.ver}"
    kc, kt = f"sf_couleur_{el}_{ss.ver}", f"sf_taille_{el}_{ss.ver}"
    cle_couleur = COULEUR_TEXTE[el]
    with st.container(key=f"reglages_texte_{el}"):
        c1, c2, c3, c4 = st.columns([3, 2.3, 0.9, 3.2], vertical_alignment="center")
        c1.selectbox("Police", options, index=options.index(etat["police"]) if etat["police"] in POLICES else 0,
                     key=kp, label_visibility="collapsed", on_change=maj_texte, args=(el, kp, ks),
                     help="Police de ce texte")
        c2.segmented_control("Style", STYLES_TEXTE, selection_mode="multi",
                             default=[s for s in STYLES_TEXTE if etat[s]], format_func=ICONES_STYLE.get, key=ks,
                             label_visibility="collapsed", help=AIDE_STYLE, on_change=maj_texte, args=(el, kp, ks))
        c3.color_picker("Couleur", couleur_de(cle_couleur), key=kc, label_visibility="collapsed",
                        on_change=maj_couleur, args=(cle_couleur, kc), help=aide_couleur or "Couleur de ce texte")
        c4.slider("Taille", 20, 400, int(round(ss.reglages[el]["s"] * 100)), step=5, format="%d %%", key=kt,
                  label_visibility="collapsed", on_change=maj_taille, args=(el, kt),
                  help="Taille de ce texte sur l'affiche (100 % = taille calculée par l'outil)")


def reinitialiser_textes():
    ss.style["textes"] = {}
    ss.ver += 1


def ligne_texte(texte, libelle):
    """Une ligne « libellé, police, gras / italique / souligné » pour un texte de l'affiche."""
    etat = etat_texte(texte)
    options = [COMME_AFFICHE] + POLICES
    kp, ks = f"tx_police_{texte}_{ss.ver}", f"tx_style_{texte}_{ss.ver}"
    c1, c2, c3 = st.columns([2.2, 3, 2.6], vertical_alignment="center")
    c1.markdown(libelle)
    c2.selectbox(libelle, options, index=options.index(etat["police"]) if etat.get("police") in POLICES else 0,
                 key=kp, label_visibility="collapsed", on_change=maj_texte, args=(texte, kp, ks))
    actifs = {"gras": etat["gras"], "italique": etat["italique"], "souligne": etat["souligne"]}
    c3.segmented_control(libelle, STYLES_TEXTE, selection_mode="multi", default=[s for s in STYLES_TEXTE if actifs[s]],
                         format_func=ICONES_STYLE.get, key=ks, label_visibility="collapsed", help=AIDE_STYLE,
                         on_change=maj_texte, args=(texte, kp, ks))


def _trouver_element(ident):
    for e in ss.elements:
        if e["id"] == ident:
            return e
    return None


def ajouter_element(type_):
    if len(ss.elements) >= libres.MAX_ELEMENTS:
        ss.msg_alerte = f"Une affiche porte au plus {libres.MAX_ELEMENTS} éléments ajoutés."
        return
    e = libres.nouveau(type_, ss.style)
    ss.elements = ss.elements + [e]
    ss.element_libre_actif, ss.selection_libre, ss.selection_apercu = e["id"], True, True
    ss.ver += 1


def maj_element(ident, champ, cle_widget):
    """Un réglage d'un élément ajouté : la valeur du champ de formulaire est reportée sur l'élément."""
    e = _trouver_element(ident)
    if e is None:
        return
    v = ss[cle_widget]
    if champ in ("t", "l", "h", "cx", "cy"):  # curseurs en pourcentage
        modif = {champ: v / 100}
    elif champ == "devant":
        modif = {"devant": v == DEVANT}
    elif champ == "police":
        modif = {"police": v if v in POLICES else None}
    elif champ == "style":
        v = v or []
        modif = {"gras": "gras" in v, "italique": "italique" in v, "souligne": "souligne" in v}
    else:
        modif = {champ: v}
    nouveau = libres.element_valide({**e, **modif})
    ss.elements = [nouveau if x["id"] == ident else x for x in ss.elements]
    if champ in ("forme",):
        ss.ver += 1  # la taille proposée dépend de la forme


def supprimer_element(ident):
    restants = [x for x in ss.elements if x["id"] != ident]
    ids = [x["id"] for x in ss.elements]
    ss.elements = restants
    ss.element_libre_actif = (restants[min(ids.index(ident), len(restants) - 1)]["id"] if restants else None)
    ss.selection_libre = bool(restants) and ss.selection_libre
    ss.selection_apercu = ss.selection_apercu and ss.selection_libre
    ss.ver += 1


def dupliquer_element(ident):
    e = _trouver_element(ident)
    if e is None or len(ss.elements) >= libres.MAX_ELEMENTS:
        if e is not None:
            ss.msg_alerte = f"Une affiche porte au plus {libres.MAX_ELEMENTS} éléments ajoutés."
        return
    copie = libres.dupliquer(e)
    ss.elements = ss.elements + [copie]
    ss.element_libre_actif, ss.selection_libre, ss.selection_apercu = copie["id"], True, True
    ss.ver += 1


def choisir_libre(cle_widget, ids):
    """Un élément ajouté est choisi dans la liste (le choix est gardé à part, car le libellé de chaque élément change
    avec son texte, et Streamlit recrée alors la liste)."""
    ss.element_libre_actif = ids[ss[cle_widget]] if ss[cle_widget] in ids else ss.element_libre_actif
    ss.selection_libre = ss.selection_apercu = True


def choisir_standard():
    ss.selection_libre = False
    ss.selection_apercu = True


def maj_slider(el, champ, cle_widget):
    """Curseur de « Réglages précis ». La clé du curseur est transmise telle qu'affichée : un autre réglage traité
    juste avant (même passage) peut avoir changé ss.ver, et recalculer la clé ici la manquerait (KeyError)."""
    v = ss.get(cle_widget)
    if v is None:
        return
    ss.reglages[el][champ] = v / 100
    ss.ver += 1  # le même réglage est aussi sous le champ de saisie et sur la barre de l'aperçu : à jour partout


def reinitialiser(el=None):
    if el is None:
        ss.reglages = reglages_defaut()
    else:
        ss.reglages[el] = reglages_defaut()[el]
    ss.ver += 1


def appliquer_defauts_promo():
    """Au changement de type de promotion : valeurs de départ propres au type (le prix normal saisi est conservé)."""
    for cle, valeur in (promos.TYPES[ss.w_promo_type].get("defauts") or {}).items():
        ss[f"w_promo_{cle}"] = valeur
    for cle, valeur in promos.OPTIONS_DEFAUT.items():
        ss[f"w_promo_opt_{cle}"] = valeur


def formulaire_promo(type_):
    """Champs, cases « Faire apparaître » et précision du type de promotion choisi.
    Retourne (résultat de promos.composer, promotion à enregistrer dans l'historique)."""
    spec = promos.TYPES[type_]
    options = {k: ss[f"w_promo_opt_{k}"] for k in promos.OPTIONS_DEFAUT}
    with st.container(border=True):
        st.caption(spec["aide"])
        rangee, rempli = None, 0
        for f in promos.champs_visibles(type_, {k: ss[f"w_promo_{k}"] for k in promos.CHAMPS_DEFAUT}):
            cle = f"w_promo_{f['nom']}"
            if f["genre"] == "texte":
                st.text_input(f["libelle"], placeholder=f.get("placeholder", ""), key=cle)
                rangee = None
                continue
            if rangee is None or rempli == 2:  # deux champs par ligne
                rangee, rempli = st.columns(2), 0
            colonne, rempli = rangee[rempli], rempli + 1
            if f["genre"] == "entier":
                colonne.number_input(f["libelle"], f["mini"], f["maxi"], step=1, key=cle)
            elif f["genre"] == "prix":
                colonne.text_input(f["libelle"], placeholder=f.get("placeholder", ""), key=cle)
            else:
                choix = [c if isinstance(c, tuple) else (c, c) for c in f["choix"]]
                colonne.selectbox(f["libelle"], [v for v, _ in choix], format_func=dict(choix).get, key=cle)
        champs = {k: ss[f"w_promo_{k}"] for k in promos.CHAMPS_DEFAUT}
        res = promos.composer(type_, champs, options)

        # Cases « Faire apparaître » : seulement celles qui ont un sens pour ce type (prix normal renseigné)
        textes = res["textes"]
        proposees = [o for o in spec["options"] if o in textes]
        if proposees:
            st.markdown("**Faire apparaître sur l'affiche**")
            for opt in proposees:
                st.checkbox(promos.LIBELLES_OPTIONS[opt].format(nom=spec.get("nom_pastille", "")),
                            key=f"w_promo_opt_{opt}")
                if opt == "calcul":
                    if len(textes["calcul"]) > 1 and ss["w_promo_opt_calcul"]:
                        st.radio("Calcul affiché", list(promos.FORMATS_CALCUL), horizontal=True,
                                 format_func=promos.FORMATS_CALCUL.get, key="w_promo_opt_calcul_fmt")
                    texte = textes["calcul"].get(options["calcul_fmt"]) or next(iter(textes["calcul"].values()))
                else:
                    texte = textes[opt]
                st.caption(f"Texte affiché : « {promos.sans_balises(texte)} »")
        elif spec["options"]:
            st.caption("Renseigner le prix normal pour que l'outil calcule les prix et propose de faire apparaître "
                       "le calcul. Sans prix normal, seule l'offre est affichée.")
        if res["resume"]:
            st.caption("Calcul : " + promos.sans_balises(res["resume"]))
        # Police, style, couleur et taille du texte de l'offre et de son petit texte au-dessus, réglables séparément
        if res.get("rendu"):
            habillage.petit_titre("Texte principal de l'offre")
            reglages_texte("prix")
            if (res["rendu"].get("kicker") or "").strip():
                habillage.petit_titre(f"Petit texte au-dessus : « {promos.sans_balises(res['rendu']['kicker'])} »")
                reglages_texte("kicker")
                g = ss.reglages.get("kicker") or {}
                if any(abs(float(g.get(k, d)) - d) > 1e-9 for k, d in (("dx", 0.0), ("dy", 0.0), ("s", 1.0))):
                    st.button("Remettre le petit texte à sa place, au-dessus de l'offre", key="kicker_raz",
                              on_click=reinitialiser, args=("kicker",), type="tertiary", icon=":material/restart_alt:")
                else:
                    st.caption("Il peut aussi être déplacé seul sur l'aperçu : il quitte alors le bandeau de l'offre.")
        st.text_input("Précision sous l'offre (facultatif)", key="w_promo_precision",
                      placeholder="ex. : sur toute la gamme, dans la limite des stocks disponibles")

    enregistre = {f["nom"]: champs[f["nom"]] for f in promos.champs_visibles(type_, champs)}
    enregistre["precision"] = champs["precision"]
    options_enregistrees = {o: options[o] for o in spec["options"]}
    if "calcul" in spec["options"]:
        options_enregistrees["calcul_fmt"] = options["calcul_fmt"]
    return res, {"type": type_, "champs": enregistre, "options": options_enregistrees}


def orientation_changee():
    """Au passage portrait <-> paysage : la mise en page change, les éléments déplacés à la main sont remis en place."""
    ss.reglages = reglages_defaut()
    ss.element_actif = "marque"
    ss.ver += 1


def retablir_traitement():
    """Après « Rouvrir », le nettoyage et la netteté sont désactivés (le visuel enregistré est déjà traité) :
    on les réactive dès qu'un nouveau visuel est choisi."""
    if ss.get("traitement_desactive"):
        ss.traitement_desactive = False
        ss.w_nettoyer, ss.nettete_mode = True, "Rapide"


# --- Historique des affiches et page A4 regroupée
def _prix_texte(valeur):
    return str(valeur).replace(".", ",") if valeur not in (None, "") else ""


def _date_ou_aujourdhui(texte):
    try:
        return date.fromisoformat(texte)
    except (TypeError, ValueError):
        return date.today()


def enregistrer_affiche(code, marque, detail, prix, prix_barre, debut, fin, choix, visuel_affiche, apercu,
                        promo=None, autres_visuels=(), paysage=False, logo_marque=None):
    """Enregistre l'affiche affichée à l'écran dans l'historique. Retourne son identifiant.
    promo : autre type de promotion (type, champs, options) ; prix = prix affiché, s'il y en a un.
    autres_visuels : les visuels suivants de la gamme (après le visuel principal).
    paysage : affiche en paysage (formats A4, A5, A6 ; un format personnalisé garde ses dimensions).
    logo_marque : clé du logo de la marque imprimé à la place de son nom (voir marques.py), ou None."""
    params = {"code": code, "marque": marque, "detail": detail,
              "prix": str(prix) if prix is not None else None,
              "prix_barre": str(prix_barre) if prix_barre is not None else None,
              "debut": debut.isoformat() if debut else None, "fin": fin.isoformat() if fin else None,
              "format": choix, "largeur_mm": int(ss.w_lg) if choix == "Personnalisé" else None,
              "hauteur_mm": int(ss.w_ht) if choix == "Personnalisé" else None,
              "logo": bool(ss.w_logo), "majuscules": bool(ss.w_majuscules),
              "reglages": ss.reglages, "style": ss.style}
    if promo:
        params["promo"] = promo
    if logo_marque:
        params["logo_marque"] = logo_marque
    if ss.elements:
        params["elements"] = ss.elements  # absent sans élément ajouté : les anciennes affiches ne changent pas
    if visuel_affiche is None and not autres_visuels:
        params["sans_photo"] = True  # distingue l'affiche sans photo de la même affiche avec photo (historique.identifiant)
    params["type_affiche"] = TYPE["nom"]  # le type d'affiche choisi (pour s'y retrouver dans l'historique)
    if paysage:
        params["orientation"] = "Paysage"  # absent pour le portrait : les anciennes affiches gardent leur identifiant
    return historique.enregistrer(params, visuel_affiche, apercu, autres_visuels)


def rouvrir(ident):
    """Recharge une affiche de l'historique dans le formulaire (le visuel enregistré est repris tel quel)."""
    e = historique.charger(ident)
    if e is None:
        ss.msg_hist = "Cette affiche n'existe plus dans l'historique."
        return
    ss.code = e.get("code") or ""
    ss.marque, ss.detail = e.get("marque", ""), e.get("detail", "")
    visuels = historique.visuels(ident)
    ss.image = visuels[0] if visuels else None
    ss.extras = [{"uid": _nouvel_uid(), "image": v, "nom": "", "code": "", "traite": True} for v in visuels[1:]]
    ss.ajout_extra, ss.cand_extra, ss.extra_n = False, None, ss.extra_n + 1
    ss.props, ss.props_msg, ss.journal = [], "", []
    ss.props_autres, ss.journal_web, ss.info_nom = [], [], ""
    ss.props_choisie, ss.props_ouvertes = None, True
    ss.reglages = reimpression.reglages_entree(e)
    ss.elements = reimpression.elements_entree(e)
    ss.element_libre_actif, ss.selection_libre = (ss.elements[0]["id"] if ss.elements else None), False
    ss.style = reimpression.style_entree(e)
    ss.w_police = ss.style["police"]
    ss.ver += 1  # recrée les sélecteurs de couleur avec les valeurs de l'affiche
    promo = e.get("promo") or {}
    ss.w_promo_type = promo.get("type") if promo.get("type") in promos.TYPES else promos.STANDARD
    for cle, defaut in promos.CHAMPS_DEFAUT.items():
        ss[f"w_promo_{cle}"] = (promo.get("champs") or {}).get(cle, defaut)
    for cle, defaut in promos.OPTIONS_DEFAUT.items():
        ss[f"w_promo_opt_{cle}"] = (promo.get("options") or {}).get(cle, defaut)
    standard = ss.w_promo_type == promos.STANDARD
    ss.w_prix = _prix_texte(e.get("prix")) if standard else ""
    ss.w_barre_on = bool(e.get("prix_barre")) and standard
    ss.w_barre = _prix_texte(e.get("prix_barre")) if standard else ""
    ss.w_dates_on = bool(e.get("debut") or e.get("fin"))
    ss.w_debut, ss.w_fin = _date_ou_aujourdhui(e.get("debut")), _date_ou_aujourdhui(e.get("fin"))
    ss.w_format = e.get("format") if e.get("format") in list(FORMATS) + ["Personnalisé"] else "A5"
    ss.w_paysage = e.get("orientation") == "Paysage" and ss.w_format in FORMATS
    ss.w_lg, ss.w_ht = int(e.get("largeur_mm") or 100), int(e.get("hauteur_mm") or 150)
    ss.w_logo, ss.w_majuscules = bool(e.get("logo", True)), bool(e.get("majuscules", True))
    ss.w_photo = bool(visuels)  # une affiche enregistrée sans visuel se rouvre sans photo (case à cocher pour en ajouter une)
    ss.w_logo_marque = bool(e.get("logo_marque")) and marques.charger(CTX.dossier, e.get("logo_marque")) is not None
    ss.cle_marque_vue = marques.trouver(CTX.dossier, ss.marque)  # la case « logo de la marque » garde l'état enregistré
    donnees_types = types_affiche.charger(CTX.dossier)
    ident_type = types_affiche.correspondant(donnees_types, types_affiche.reglages_courants(ss))
    if ident_type:  # les réglages de l'affiche sont ceux d'un type : on se place sur ce type
        ss.type_actif = ss.w_type = ident_type
    ss.w_nettoyer, ss.nettete_mode, ss.traitement_desactive = False, "Désactivée", True
    ss.w_rg = ss.w_rd = ss.w_rh = ss.w_rb = 0
    ss.selection_apercu = False
    retouches.oublier(ss)  # « Annuler » ne ramène pas à l'affiche d'avant
    ss.msg_ouvert = ("Affiche rouverte : modifier ce qui doit l'être, puis télécharger le PDF ou "
                     "l'enregistrer à nouveau.")
    if ONGLETS_PILOTABLES:
        ss.onglet = ONGLET_CREER
    else:
        ss.msg_hist = "Affiche rouverte : elle est prête dans l'onglet « Créer une affiche »."


# --- Types d'affiche : jeux de réglages nommés (format, logo, photo, style), voir types_affiche.py
def _enregistrer_types(donnees):
    types_affiche.sauver(CTX.dossier, donnees)
    sauvegarde.planifier(types_affiche.FICHIER)


def choisir_type():
    """Un type d'affiche est choisi en haut de la page : ses réglages passent dans le formulaire ; le produit, le prix
    et les dates de l'affiche en cours restent."""
    donnees = types_affiche.charger(CTX.dossier)
    ident = ss.get("w_type")
    ss.type_suppr_attente = False
    if not types_affiche.existe(donnees, ident):  # clic sur le type déjà choisi : la sélection se décoche, on la rétablit
        ss.w_type = ss.type_actif
        return
    if ident == ss.type_actif:
        return
    t = types_affiche.trouver(donnees, ident)
    ss.type_actif = ident
    types_affiche.appliquer(ss, t)
    donnees["actif"] = ident
    _enregistrer_types(donnees)
    ss.msg_ouvert = f"Type « {t['nom']} » : {types_affiche.resume(t)}."


def creer_type():
    """Nouveau type d'affiche avec les réglages de l'affiche en cours."""
    donnees = types_affiche.charger(CTX.dossier)
    try:
        t = types_affiche.creer(donnees, ss.get("type_nouveau_nom", ""), types_affiche.reglages_courants(ss))
    except ValueError as erreur:
        ss.msg_alerte = str(erreur)
        return
    _enregistrer_types(donnees)
    ss.type_actif = ss.w_type = t["id"]
    ss.type_nouveau_nom = ""
    ss.msg_ouvert = f"Type « {t['nom']} » créé ({types_affiche.resume(t)}). Il est proposé en haut de la page."


def commencer_nouveau_type():
    """« Créer pas à pas… » : ouvre l'écran de questions (voir mise_en_route.py) pour régler un nouveau type d'affiche."""
    ss.ecran_nouveau_type = True
    ss.nt_n = ss.get("nt_n", 0) + 1  # l'écran repart des réglages de l'affiche en cours
    ss.type_suppr_attente = False


def maj_type():
    """Les réglages en cours deviennent ceux du type d'affiche choisi."""
    donnees = types_affiche.charger(CTX.dossier)
    t = types_affiche.mettre_a_jour(donnees, ss.type_actif, types_affiche.reglages_courants(ss))
    _enregistrer_types(donnees)
    ss.msg_ouvert = f"Type « {t['nom']} » mis à jour : {types_affiche.resume(t)}."


def renommer_type():
    donnees = types_affiche.charger(CTX.dossier)
    try:
        t = types_affiche.renommer(donnees, ss.type_actif, ss.get(f"type_renom_{ss.type_actif}", ""))
    except ValueError as erreur:
        ss.msg_alerte = str(erreur)
        return
    _enregistrer_types(donnees)
    ss.msg_ouvert = f"Type renommé : « {t['nom']} »."


def demander_suppression_type():
    ss.type_suppr_attente = True


def annuler_suppression_type():
    ss.type_suppr_attente = False


def supprimer_type():
    """Retire le type choisi ; les affiches déjà enregistrées gardent leurs réglages. Le premier type restant prend la place."""
    donnees = types_affiche.charger(CTX.dossier)
    ss.type_suppr_attente = False
    ancien = types_affiche.trouver(donnees, ss.type_actif)
    try:
        types_affiche.supprimer(donnees, ancien["id"])
    except ValueError as erreur:
        ss.msg_alerte = str(erreur)
        return
    suivant = types_affiche.trouver(donnees, donnees["actif"])
    _enregistrer_types(donnees)
    ss.type_actif = ss.w_type = suivant["id"]
    types_affiche.appliquer(ss, suivant)
    ss.msg_ouvert = f"Type « {ancien['nom']} » supprimé. Type choisi : « {suivant['nom']} »."


# --- Plusieurs visuels (gamme) sur une même affiche
def _nouvel_uid():
    ss.extra_uid += 1
    return ss.extra_uid


def ouvrir_ajout():
    ss.ajout_extra, ss.cand_extra = True, None
    ss.extra_n += 1  # champs de recherche et d'import vides


def fermer_ajout():
    ss.ajout_extra, ss.cand_extra = False, None
    ss.extra_n += 1


def ajouter_extra(img, code="", nom="", traite=False):
    """Ajoute une image à la gamme (MAX_VISUELS - 1 au plus, en plus du visuel principal) et referme l'ajout.
    traite : visuel déjà nettoyé (repris d'une affiche enregistrée), à imprimer tel quel."""
    if len(ss.extras) < MAX_VISUELS - 1:
        ss.extras.append({"uid": _nouvel_uid(), "image": img, "nom": nom, "code": code, "traite": traite})
        ss.traitement_a_retablir = True  # pris en compte au prochain affichage, avant les cases concernées
    fermer_ajout()


def retirer_extra(uid):
    ss.extras = [x for x in ss.extras if x["uid"] != uid]


# --- « Nouvelle affiche » : efface la saisie en cours, garde les réglages (format, police, couleurs, logo)
def demander_raz():
    ss.raz_attente = True


def annuler_raz():
    ss.raz_attente = False


def _aide_comprise():
    """« J'ai compris » : le panneau « Premiers pas » ne s'affiche plus pour cette pharmacie."""
    try:
        preferences.marquer_aide_vue(CTX.dossier)
        sauvegarde.planifier(preferences.FICHIER_PREFERENCES)
    except OSError:
        pass


def _fin_de_mois(jour):
    return jour.replace(day=calendar.monthrange(jour.year, jour.month)[1])


def raccourci_dates(nom):
    """Dates préréglées (boutons sous « Afficher une plage de dates »)."""
    auj = date.today()
    if nom == "mois":
        debut = auj.replace(day=1)
    elif nom == "fin_mois":
        debut = auj
    else:  # mois prochain
        debut = _fin_de_mois(auj) + timedelta(days=1)
    ss.w_debut, ss.w_fin = debut, _fin_de_mois(debut)


def choisir_logo(marque, octets, depuis_lot=False):
    cle_nouveau, message = marques.enregistrer(CTX.dossier, marque, octets)
    if cle_nouveau is None:
        ss.msg_ouvert = message
        return
    ss.msg_ouvert = message
    ss.cle_marque_vue = None  # le nouveau logo est proposé d'office
    ss.logo_marque_n += 1
    if depuis_lot:
        ss.logos_lot = [b for b in ss.logos_lot if b["cle"] != marques.cle(marque)]
    elif ss.logos_props and ss.logos_props.get("cle") == marques.cle(marque):
        ss.logos_props = None


def afficher_propositions_logo(bloc, prefixe):
    """Logos proposés pour une marque, chacun avec un bouton « Utiliser ce logo »."""
    if not bloc["props"]:
        st.warning(bloc["msg"] or "Aucun logo trouvé.")
        st.link_button("Chercher sur Google Images",
                       "https://www.google.com/search?tbm=isch&q=" + quote(bloc["marque"] + " logo"))
        return
    st.caption("Cliquer sur le logo à garder (il est enregistré pour toutes les affiches de la marque). "
               "Les logos de Wikidata sont ceux déclarés officiellement pour la marque.")
    cols = st.columns(4)
    for i, p in enumerate(bloc["props"]):
        with cols[i % 4]:
            with st.container(border=True):
                st.image(ip.vers_rgb_blanc(p["miniature"]), use_container_width=True)
                st.caption(f"{p['source']} · {p['largeur']}×{p['hauteur']} px")
                st.button("Utiliser ce logo", key=f"logo_{prefixe}_{bloc['cle']}_{i}", on_click=choisir_logo,
                          args=(bloc["marque"], p["octets"], prefixe == "lot"), use_container_width=True)


def nouvelle_affiche(garder_serie=False):
    """Efface la saisie en cours. garder_serie : enchaîner avec l'affiche suivante d'une même série de promotions
    (le type de promotion et les dates sont gardés, le produit et le prix repartent de zéro)."""
    type_promo = ss.w_promo_type
    dates = (ss.w_dates_on, ss.w_debut, ss.w_fin)
    ss.raz += 1  # recherche et import de fichier repartent vides
    ss.raz_attente = False
    # produit et visuels
    ss.code, ss.marque, ss.detail = "", "", ""
    ss.image, ss.journal, ss.props, ss.props_msg, ss.derniere_sel = None, [], [], "", None
    ss.props_autres, ss.journal_web, ss.info_nom = [], [], ""
    ss.props_choisie, ss.props_ouvertes = None, True
    ss.derniere_collee = ss.derniere_collee_extra = None
    ss.extras, ss.ajout_extra, ss.cand_extra, ss.derniere_sel_extra = [], False, None, None
    ss.televerse_vu = None
    ss.w_rg = ss.w_rd = ss.w_rh = ss.w_rb = 0
    ss.w_nettoyer, ss.nettete_mode, ss.traitement_desactive = True, "Rapide", False
    # prix et promotion
    ss.w_prix, ss.w_barre_on, ss.w_barre = "", False, ""
    ss.w_promo_type = promos.STANDARD
    for cle, valeur in promos.CHAMPS_DEFAUT.items():
        ss[f"w_promo_{cle}"] = valeur
    for cle, valeur in promos.OPTIONS_DEFAUT.items():
        ss[f"w_promo_opt_{cle}"] = valeur
    # dates, nombre d'exemplaires, éléments déplacés
    ss.w_dates_on, ss.w_debut, ss.w_fin = False, date.today(), date.today()
    ss.w_exemplaires = 1
    ss.reglages = reglages_defaut()
    ss.element_actif = "marque"
    if not garder_serie:  # d'une affiche à la suivante d'une série, les éléments ajoutés (« NOUVEAU »…) restent
        ss.elements, ss.element_libre_actif, ss.selection_libre = [], None, False
    ss.selection_libre = ss.selection_libre and bool(ss.elements)
    ss.selection_apercu = False
    retouches.oublier(ss)
    ss.ver += 1
    ss.msg_ouvert = ("Nouvelle affiche : le formulaire est vide. Le format, la police, les couleurs et le logo "
                     "sont conservés.")
    if garder_serie:
        ss.w_promo_type = type_promo
        appliquer_defauts_promo()  # valeurs de départ propres au type de promotion gardé
        ss.w_dates_on, ss.w_debut, ss.w_fin = dates
        ss.msg_ouvert = ("Affiche suivante : produit et prix effacés ; format, style, type de promotion"
                         + (", dates" if dates[0] else "") + (" et éléments ajoutés" if ss.elements else "")
                         + " conservés.")


def ajouter_regroupe(ident):
    if ident not in ss.regroupe:
        ss.regroupe.append(ident)


def retirer_regroupe(ident):
    if ident in ss.regroupe:
        ss.regroupe.remove(ident)


def deplacer_regroupe(i, sens):
    j = i + sens
    if 0 <= i < len(ss.regroupe) and 0 <= j < len(ss.regroupe):
        ss.regroupe[i], ss.regroupe[j] = ss.regroupe[j], ss.regroupe[i]


def vider_regroupe():
    ss.regroupe = []


def demander_suppression(ident):
    ss.suppr_attente = ident


def annuler_suppression():
    ss.suppr_attente = None


def confirmer_suppression(ident):
    historique.supprimer(ident)
    retirer_regroupe(ident)
    ss.suppr_attente = None
    ss.msg_hist = "Affiche supprimée de l'historique."



MSG_AUCUNE_IMAGE = ("Aucune image fiable n'a été trouvée automatiquement : rien de lié à ce produit (ni le code ni le "
                    "nom dans les titres). Ajouter un mot précis (la gamme, par exemple) dans « Affiner la recherche », "
                    "ou chercher ce code sur Google Images, copier l'image puis la coller ici.")


def chercher_web(extra=(), auto=False):
    """Recherche du visuel sur le web, de la plus fiable à la moins fiable : photos de pages de sites marchands qui
    contiennent le code (confirmées), puis pages au nom correspondant (probables), puis images dont le titre cite le
    code ou correspond au nom du produit (code non vérifié) ; les images sans rapport sont écartées.
    extra : mots saisis par l'utilisateur pour affiner la recherche.
    auto : recherche lancée d'office après la saisie du code ; la meilleure photo confirmée par le code, sur fond
    blanc, est alors retenue directement (les autres restent proposées)."""
    nom_cherche = catalogue.nom_complet(ss.marque, ss.detail)
    with st.status("Recherche du visuel sur les sites marchands…", expanded=False) as statut:
        res = ip.rechercher_visuels(ss.code, nom=nom_cherche, requetes_extra=extra,
                                    progression=lambda texte: statut.update(label=texte))
        statut.update(label="Recherche terminée", state="complete")
    ss.journal_web = res["journal"]
    ss.info_nom = ""
    ss.props_choisie, ss.props_ouvertes = None, True  # nouvelles propositions : affichées en grand
    if res["verifies"]:
        ss.props, ss.props_autres, ss.props_msg = res["verifies"], res["autres"], ""
    else:
        ss.props, ss.props_autres = res["autres"], []
        ss.props_msg = "" if res["autres"] else MSG_AUCUNE_IMAGE
    if res["nom"] and not ss.marque and not ss.detail:  # produit inconnu du catalogue : nom relevé sur les sites
        ss.marque, ss.detail = catalogue.separer(res["nom"], res["marque"])
        ss.info_nom = catalogue.nom_complet(ss.marque, ss.detail)
    meilleure = ss.props[0] if ss.props else None
    if auto and meilleure and meilleure.get("verifie") and meilleure.get("studio"):
        try:
            ss.image = ip.rogner_marges_blanches(ip.obtenir_image(meilleure))
            ss.props_choisie, ss.props_ouvertes = meilleure.get("image"), False
            ss.choix_auto = meilleure.get("image")
            retablir_traitement()
        except Exception:
            pass  # téléchargement impossible : la photo reste à choisir dans les propositions


def legende_proposition(p):
    """Légende d'une proposition de visuel : fiabilité (code confirmé, probable, non vérifié), fond, site, taille."""
    niveau = p.get("niveau", "non vérifié")
    if niveau == "fort" or niveau == "moyen":
        n = p.get("nb_sites", 1)
        statut = "Code confirmé" + (f" · {n} sites" if n > 1 else "")
    elif niveau == "nom":
        statut = "Probable (code non retrouvé)"
    elif niveau == "indice":
        statut = "Code cité dans le titre ou l'adresse"
    elif niveau == "titre":
        statut = "Titre cohérent · code non vérifié"
    else:
        statut = "Non vérifié"
    dims = f"{p['largeur']}×{p['hauteur']} px" if p.get("largeur") else "taille inconnue"
    nette = p.get("nette")
    if nette:  # résolution réelle (une miniature agrandie est grande mais floue)
        grand = max(p.get("largeur") or 0, p.get("hauteur") or 0)
        qualite = ("Très nette" if nette >= 1200 else "Nette" if nette >= 700 else "Correcte" if nette >= 400
                   else "Petite")
        dims = f"{qualite} · {dims}" + (f" (détails ≈ {nette} px)" if grand and nette < grand * 0.8 else "")
        if p.get("agrandie"):
            dims += " · version d'origine retrouvée"
    fond = "Fond blanc" if p.get("studio") else "Photo"
    return f"**{statut}**  \n{fond} · {dims}  \n{p['site']}"


def est_meilleur_choix(p, i):
    """Première proposition confirmée sur fond blanc : le bouton « Choisir » est mis en avant."""
    return i == 0 and bool(p.get("verifie")) and bool(p.get("studio"))


def miniature_proposition(p, cote=300):
    """Miniature carrée (les boutons « Choisir » restent alignés) ; adresse web telle quelle pour les images non vérifiées."""
    m = p["miniature"]
    return m if isinstance(m, str) else _miniature_carree(m, cote)


def visuel_final(img, nettoyer_on, rogne, mode_nettete="Désactivée"):
    """Recadrage manuel éventuel, nettoyage (suppression du fond), puis netteté si la résolution est faible."""
    if img is None:
        return None, ""
    g, d, h, b = rogne
    if any(rogne):
        w, ht = img.size
        img = img.crop((int(w * g / 100), int(ht * h / 100), w - int(w * d / 100), ht - int(ht * b / 100)))
    methode = "Visuel d'origine"
    if nettoyer_on:
        img, methode = nettoyage.nettoyer(img)
    if mode_nettete != "Désactivée" and min(img.size) < SEUIL_NETTETE:
        img, m2 = nettete.ameliorer(img, "qualite" if mode_nettete.startswith("Haute") else "rapide")
        methode = f"{methode} · {m2}"
    return img, methode


def _nom_court(texte, maxi=26):
    texte = " ".join((texte or "").split())
    return texte if len(texte) <= maxi else texte[:maxi - 1].rstrip() + "…"


def _md(texte):
    """Neutralise les caractères de mise en forme dans un texte affiché."""
    for c in ("\\", "*", "_", "`", "[", "]", "$", "#", "<", ">", "~"):
        texte = texte.replace(c, "\\" + c)
    return texte


def _miniature_carree(img, cote=180):
    """Miniature carrée (fond blanc, fin cadre gris) : les images de la gamme restent alignées dans la liste."""
    from PIL import ImageDraw
    mini = img.convert("RGB").copy()
    mini.thumbnail((cote - 12, cote - 12), Image.LANCZOS)
    fond = Image.new("RGB", (cote, cote), "white")
    fond.paste(mini, ((cote - mini.width) // 2, (cote - mini.height) // 2))
    ImageDraw.Draw(fond).rectangle((0, 0, cote - 1, cote - 1), outline="#E3E3E3")
    return fond


def proposer_choix_extra(cand):
    """Visuels proposés pour l'image à ajouter (aucun visuel « site marchand » trouvé automatiquement)."""
    options = []
    if cand["image"] is not None:
        options.append({"miniature": cand["image"], "p": None, "legende": "**Bases publiques**  \nPhoto"})
    for i, p in enumerate(cand["props"]):
        options.append({"miniature": miniature_proposition(p), "p": p, "legende": legende_proposition(p),
                        "meilleur": est_meilleur_choix(p, i)})
    if not options:
        st.warning(f"Aucun visuel trouvé pour {cand['nom'] or cand['code']}"
                   + (f" ({cand['msg']})" if cand["msg"] else "") + ". Coller ou importer une image ci-dessous.")
        return
    if cand.get("msg"):
        st.caption(cand["msg"])
    st.write(f"Visuels proposés pour {cand['nom'] or cand['code']}. Vérifier que l'image correspond bien au "
             "produit, puis la choisir :")
    colonnes = st.columns(4)
    for i, o in enumerate(options):
        with colonnes[i % 4]:
            st.image(o["miniature"], use_container_width=True)
            choisi = st.button("Choisir", key=f"ex_choix_{ss.raz}_{ss.extra_n}_{i}",
                               type="primary" if o.get("meilleur") else "secondary")
            st.caption(o["legende"])
            if choisi:
                try:
                    img = cand["image"] if o["p"] is None else ip.rogner_marges_blanches(ip.obtenir_image(o["p"]))
                    ajouter_extra(img, cand["code"], cand["nom"])
                    st.rerun()
                except Exception as e:
                    st.error(f"Téléchargement impossible ({type(e).__name__}).")


def zone_autres_images(cat, nettoyer_on, mode_nettete):
    """« Autres images » de l'affiche (gamme de produits) : liste, retrait et ajout, jusqu'à MAX_VISUELS images au
    total avec le visuel principal. Retourne les visuels à imprimer (images PIL) des autres produits."""
    finaux = []
    with st.container(border=True):
        st.markdown(f"**Autres images sur l'affiche** · {1 + len(ss.extras)} sur {MAX_VISUELS}")
        if not ss.extras and not ss.ajout_extra:
            st.caption(f"Pour présenter une gamme : jusqu'à {MAX_VISUELS - 1} autres produits, affichés côte à côte "
                       "avec le premier sur une seule rangée.")
        if ss.extras:
            with st.spinner("Préparation des visuels…"):
                colonnes = st.columns(MAX_VISUELS - 1)
                for i, ex in enumerate(ss.extras):
                    img = ex["image"] if ex["traite"] else visuel_final(ex["image"], nettoyer_on, (0, 0, 0, 0),
                                                                        mode_nettete)[0]
                    finaux.append(img)
                    with colonnes[i]:
                        st.image(_miniature_carree(img), width=120)
                        st.caption(_nom_court(ex["nom"] or ex["code"] or f"Image {i + 2}")
                                   + (" · résolution faible" if min(img.size) < 600 else ""))
                        st.button("Retirer", key=f"ex_rm_{ex['uid']}", on_click=retirer_extra, args=(ex["uid"],),
                                  use_container_width=True, help="Retirer cette image de l'affiche")
        if len(ss.extras) >= MAX_VISUELS - 1:
            st.caption(f"Maximum atteint : {MAX_VISUELS} images sur l'affiche. Retirer une image pour en ajouter une autre.")
        elif not ss.ajout_extra:
            st.button("Ajouter une autre image", key="ex_ajouter", on_click=ouvrir_ajout, icon=":material/add:",
                      help="Chercher un autre produit (nom ou code) ou importer une image")
        else:
            sel2 = recherche_produit(catalogue=cat, en_ligne=True,
                                     libelle="Produit à ajouter (nom ou code CIP13 / EAN)",
                                     key=f"recherche_extra_{ss.raz}_{ss.extra_n}", default=None)
            if sel2 and sel2.get("ts") != ss.derniere_sel_extra:
                ss.derniere_sel_extra = sel2["ts"]
                code2 = ip.nettoyer_code(sel2["code"])
                marque2, detail2 = catalogue.separer(sel2.get("nom", ""), sel2.get("marque", ""))
                nom2 = catalogue.nom_complet(marque2, detail2) or code2
                with st.spinner("Recherche du visuel…"):
                    img2, marque_trouvee, detail_trouve, _journal = ip.rechercher(code2)
                if not marque2 and not detail2:
                    nom2 = catalogue.nom_complet(marque_trouvee, detail_trouve) or code2
                if img2 is not None and img2.info.get("origine") == "historique":
                    ajouter_extra(img2, code2, nom2, traite=True)  # visuel d'une affiche enregistrée : repris tel quel
                    st.rerun()
                if img2 is not None and ip.analyser_image(img2)["studio"]:
                    ajouter_extra(img2, code2, nom2)  # visuel « site marchand » : pris directement
                    st.rerun()
                with st.status("Recherche d'un visuel sur les sites marchands…", expanded=False) as statut2:
                    res2 = ip.rechercher_visuels(code2, nom=nom2 if nom2 != code2 else "",
                                                 progression=lambda texte: statut2.update(label=texte))
                    statut2.update(label="Recherche terminée", state="complete")
                props2 = res2["verifies"] or res2["autres"]
                msg2 = "" if res2["verifies"] else ("code non confirmé sur les pages : contrôler que l'image montre "
                                                    "bien le produit" if res2["autres"] else
                                                    "aucune image fiable trouvée automatiquement")
                if nom2 == code2 and res2["nom"]:  # produit inconnu : nom relevé sur les sites marchands
                    nom2 = catalogue.nom_complet(*catalogue.separer(res2["nom"], res2["marque"]))
                ss.cand_extra = {"code": code2, "nom": nom2, "image": img2, "props": props2, "msg": msg2}
            if ss.cand_extra:
                proposer_choix_extra(ss.cand_extra)
            habillage.sous_titre("Coller une image copiée")
            st.caption("Clic droit sur l'image (Google Images, site…) › « Copier l'image », puis cliquer dans le cadre et "
                       "coller. L'adresse de la page du produit peut aussi être collée.")
            collee2 = collage_image(key=f"collage_extra_{ss.raz}_{ss.extra_n}", default=None)
            if collee2 and collee2.get("ts") != ss.derniere_collee_extra:
                ss.derniere_collee_extra = collee2["ts"]
                try:
                    with st.spinner("Lecture de l'image…"):
                        img3 = ip.rogner_marges_blanches(ip.image_depuis_collage(collee2))
                except Exception as e:
                    st.error(f"Image impossible à lire ({type(e).__name__}). Copier l'image elle-même (et non son adresse).")
                else:
                    cand3 = ss.cand_extra or {}
                    ajouter_extra(img3, cand3.get("code", ""), cand3.get("nom") or "Image collée")
                    st.rerun()
            fichier2 = st.file_uploader("ou importer une image depuis l'ordinateur (glisser-déposer)",
                                        type=["png", "jpg", "jpeg", "webp"],
                                        key=f"televerse_extra_{ss.raz}_{ss.extra_n}")
            if fichier2 is not None:
                cand = ss.cand_extra or {}
                ajouter_extra(ip.vers_rgb_blanc(Image.open(fichier2)), cand.get("code", ""),
                              cand.get("nom") or Path(fichier2.name).stem)
                st.rerun()
            st.button("Annuler l'ajout", key="ex_annuler", on_click=fermer_ajout)
    return finaux


RESERVE_APERCU = 250  # hauteur (px) occupée autour de l'affiche dans la colonne d'aperçu : marges, boutons (la barre de réglages est comptée par l'aperçu)


def _manquants(nom, resultat_promo, rappels, prix, prix_txt):
    """Ce qu'il reste à renseigner pour que l'aperçu s'affiche (en clair)."""
    manque = []
    if not nom:
        manque.append("la marque ou le nom du produit")
    if resultat_promo:
        manque += [re.sub(r"\s*:?\s*à renseigner\.?$", "", r).strip() for r in rappels] or ["l'offre"]
    elif prix is None:
        manque.append("le prix promo" if not prix_txt else "un prix promo valide")
    return manque


def barre_types():
    """En haut de « Créer une affiche » : choisir le type d'affiche (en un clic), et les gérer (créer, mettre à jour,
    renommer, supprimer)."""
    types = TYPES["types"]
    reglages = types_affiche.reglages_courants(ss)
    modifie = not types_affiche.egaux(reglages, TYPE)
    with st.container(key="barre_types"):
        c_choix, c_gerer = st.columns([7, 2], vertical_alignment="center")
        with c_choix:
            if len(types) > 1:
                ids = [t["id"] for t in types]
                noms = {t["id"]: t["nom"] for t in types}
                if ss.get("w_type") not in ids:
                    ss.w_type = ss.type_actif
                st.pills("Type d'affiche", ids, format_func=noms.get, key="w_type", on_change=choisir_type,
                         selection_mode="single",
                         help="Chaque type garde son format, son logo, sa photo (ou non) et son style. Changer de type "
                              "ne touche pas au produit, au prix ni aux dates de l'affiche en cours.")
            else:
                st.markdown(f"**Type d'affiche** : {_md(TYPE['nom'])}")
        with c_gerer.popover("Gérer les types", icon=":material/tune:", use_container_width=True):
            st.markdown("**Créer un nouveau type**")
            st.caption("Par exemple « Petite affiche de rayon » (A6, sans photo) ou « Grande affiche vitrine » (A4 "
                       "paysage). Il part des réglages de l'affiche en cours : format, orientation, logo, photo, police "
                       "et couleurs.")
            st.text_input("Nom du nouveau type", key="type_nouveau_nom", max_chars=types_affiche.LONGUEUR_NOM,
                          placeholder="ex. Petite affiche de rayon")
            plein = len(types) >= types_affiche.MAX_TYPES
            st.button("Créer pas à pas…", key="type_pas_a_pas", on_click=commencer_nouveau_type, type="primary",
                      icon=":material/auto_fix_high:", use_container_width=True, disabled=plein,
                      help="Les mêmes questions qu'à la première visite, avec aperçu, et possibilité d'importer une "
                           "affiche existante (image ou PDF) pour en reprendre les couleurs.")
            st.button("Créer tout de suite, avec les réglages en cours", key="type_creer", on_click=creer_type,
                      disabled=plein or not ss.get("type_nouveau_nom", "").strip(), use_container_width=True)
            st.divider()
            st.markdown(f"**Type choisi : {_md(TYPE['nom'])}**")
            if modifie:
                st.button("Mettre à jour ce type avec les réglages en cours", key="type_maj_popover",
                          on_click=maj_type, use_container_width=True)
            st.text_input("Nom", value=TYPE["nom"], key=f"type_renom_{TYPE['id']}",
                          max_chars=types_affiche.LONGUEUR_NOM)
            st.button("Renommer", key="type_renommer", on_click=renommer_type)
            if len(types) > 1:
                if ss.type_suppr_attente:
                    st.warning("Supprimer ce type d'affiche ? Les affiches déjà enregistrées ne sont pas touchées.")
                    b_oui, b_non = st.columns(2)
                    b_oui.button("Oui, supprimer", key="type_suppr_oui", on_click=supprimer_type, type="primary",
                                 use_container_width=True)
                    b_non.button("Annuler", key="type_suppr_non", on_click=annuler_suppression_type,
                                 use_container_width=True)
                else:
                    st.button("Supprimer ce type", key="type_suppr", on_click=demander_suppression_type,
                              type="tertiary", icon=":material/delete:")
        c_etat, c_maj = st.columns([7, 2], vertical_alignment="center")
        c_etat.caption(types_affiche.resume(reglages) + (f" · :orange[réglages modifiés depuis « {_md(TYPE['nom'])} »]"
                                                          if modifie else ""))
        if modifie:
            c_maj.button("Mettre à jour ce type", key="type_maj", on_click=maj_type, use_container_width=True,
                         help="Garder les réglages en cours (format, logo, photo, police, couleurs) dans ce type d'affiche")


with onglet_creer:
    if ss.get("msg_ouvert"):
        st.toast(ss.pop("msg_ouvert"), icon=":material/check_circle:")
    if ss.get("msg_alerte"):
        st.toast(ss.pop("msg_alerte"), icon=":material/error:")
    if MULTI and not PREFS["aide_vue"]:  # visite guidée : une seule fois par pharmacie, jusqu'au clic sur « J'ai compris »
        with st.container(key="premiers_pas"):
            habillage.premiers_pas()
            st.button("J'ai compris, ne plus afficher", key="aide_comprise", on_click=_aide_comprise, type="primary")
    barre_types()
    col_form, col_apercu = st.columns([3, 2], gap="large")

with col_form:
    cat = catalogue.charger()

    # ------------------------------------------------------------------ Étape 1 : produit, visuel, textes
    with st.container(key="etape_1"):
        c_titre, c_raz = st.columns([5, 3], vertical_alignment="center")
        with c_titre:
            ph_etape1 = st.empty()  # titre redessiné plus bas (coche verte quand le produit est renseigné)
            with ph_etape1:
                habillage.titre_etape(1, "Produit", "Chercher le produit, vérifier son visuel et ses textes.",
                                      fait=bool(ss.marque or ss.detail))
        c_raz.button("Nouvelle affiche", key="raz_demander", on_click=demander_raz, icon=":material/restart_alt:",
                     use_container_width=True, disabled=ss.raz_attente,
                     help="Effacer la saisie en cours pour repartir de zéro (par exemple après une erreur)")
        if ss.raz_attente:
            with st.container(border=True):
                st.warning("Effacer l'affiche en cours (produit, visuels, prix, promotion, dates, éléments déplacés ou ajoutés) ? "
                           "Si elle n'a pas été enregistrée dans l'historique, elle sera perdue. Le format, la police, "
                           "les couleurs et le logo sont conservés.")
                r1_, r2_ = st.columns(2)
                r1_.button("Oui, effacer", key="raz_confirmer", type="primary", on_click=nouvelle_affiche,
                           use_container_width=True)
                r2_.button("Annuler", key="raz_annuler", on_click=annuler_raz, use_container_width=True)
        sel = recherche_produit(catalogue=cat, en_ligne=True,
                                libelle="Produit (nom ou code CIP13 / EAN)", focus=True, remonter=ss.raz > 0,
                                key=f"recherche_{ss.raz}", default=None)

        # Affiche vide : les dernières affiches faites sont proposées, pour en reprendre une en un clic
        if not (ss.marque or ss.detail) and ss.image is None:
            recents = historique.lister()[:4]
            if recents:
                with st.container(key="recents"):
                    st.caption("Ou reprendre une affiche récente (la rouvre pour la modifier) :")
                    cols_recents = st.columns(4)  # 4 emplacements : un bouton seul ne s'étire pas sur toute la largeur
                    for col_r, e_r in zip(cols_recents, recents):
                        col_r.button(_md(_nom_court(e_r.get("marque") or e_r.get("detail") or "Sans nom", 20)),
                                     key=f"rec_{e_r['id']}", on_click=rouvrir, args=(e_r["id"],),
                                     use_container_width=True,
                                     help="Rouvrir cette affiche : modifier le prix, les dates ou le produit")

        # Nouvelle sélection dans la liste déroulante ou code saisi + Entrée
        if sel and sel.get("ts") != ss.derniere_sel:
            ss.derniere_sel = sel["ts"]
            ss.code = ip.nettoyer_code(sel["code"])
            ss.marque, ss.detail = catalogue.separer(sel.get("nom", ""), sel.get("marque", ""))
            ss.props, ss.props_msg, ss.props_choisie, ss.props_ouvertes = [], "", None, True
            reinitialiser()
            retablir_traitement()
            with st.spinner("Recherche du visuel…" if ss.w_photo else "Recherche du produit…"):
                img, marque_trouvee, detail_trouve, journal = ip.rechercher(ss.code)
            if not ss.w_photo:  # affiche sans photo : seul le nom du produit est utile
                img, journal = None, []
            ss.image, ss.journal = img, journal
            if not ss.marque and not ss.detail:  # rien dans le catalogue : on prend la fiche en ligne
                ss.marque, ss.detail = marque_trouvee, detail_trouve
            elif not ss.marque and marque_trouvee:  # nom connu, marque inconnue : on la déduit de la fiche en ligne
                ss.marque, ss.detail = catalogue.separer(ss.detail, marque_trouvee)
            if not ss.w_photo:
                pass
            elif img is not None and img.info.get("origine") == "historique":
                # visuel d'une affiche enregistrée : déjà nettoyé, imprimé tel quel (comme après « Rouvrir »)
                ss.w_nettoyer, ss.nettete_mode, ss.traitement_desactive = False, "Désactivée", True
            elif img is None or not ip.analyser_image(img)["studio"]:
                chercher_web(auto=True)  # un visuel absent ou de type « photo » : la meilleure photo confirmée le remplace

        if ss.pop("traitement_a_retablir", False):  # après « Rouvrir », un nouveau visuel est nettoyé comme d'habitude
            retablir_traitement()

        code_net = ss.code

        avec_photo = bool(ss.w_photo)  # type d'affiche sans photo, ou case décochée : ni recherche ni visuel
        if avec_photo:
            # --- Visuel : recherche automatique, propositions web, import manuel
            if ss.journal or ss.journal_web:
                if ss.image is None:
                    st.warning("Aucun visuel retenu automatiquement : choisir l'une des photos proposées ci-dessous."
                               if ss.props else
                               "Aucun visuel trouvé automatiquement pour ce produit : coller une image copiée "
                               "ou importer un fichier ci-dessous.")
                with st.expander("Détail de la recherche"):
                    for ligne in list(ss.journal) + list(ss.journal_web):
                        st.write("• " + ligne)

            slot_visuel = st.container()   # visuel retenu (rempli plus bas, une fois les réglages lus)
            slot_options = st.container()  # case « Nettoyage IA », visible d'office sous le visuel
            slot_props = st.container()    # propositions de visuels (remplies après la recherche web éventuelle)

            with st.expander("Autre visuel : chercher, coller ou importer une image", expanded=ss.image is None,
                             icon=":material/image_search:"):
                if code_net:
                    bt1, bt2 = st.columns(2)
                    if bt1.button("Relancer la recherche sur les sites marchands", use_container_width=True):
                        chercher_web()
                        st.rerun()
                    bt2.link_button("Ouvrir Google Images pour ce code", use_container_width=True,
                                    url=f"https://www.google.com/search?tbm=isch&q={quote(code_net)}")
                    with st.container(key="zone_affiner"), st.form(f"affiner_{ss.raz}", border=False):
                        c_mots, c_lancer = st.columns([3, 1], vertical_alignment="bottom")
                        mots = c_mots.text_input("Affiner la recherche (nom, marque…)", placeholder="ex. granions masque éclat",
                                                 help="Ajoute ces mots à la recherche ; une page ne compte que si elle "
                                                      "contient le code du produit.")
                        affiner = c_lancer.form_submit_button("Chercher", use_container_width=True)
                    if affiner and mots.strip():
                        chercher_web(extra=[mots])
                        st.rerun()
                habillage.sous_titre("Coller une image copiée")
                st.caption("Sur Google Images ou sur un site : clic droit sur l'image › « Copier l'image ». "
                           "Revenir ici, cliquer dans le cadre, puis coller. On peut aussi coller l'adresse de la page "
                           "du produit (sa photo principale est prise) ; l'outil va chercher la photo en grand si le "
                           "site la propose.")
                collee = collage_image(key=f"collage_{ss.raz}", default=None)
                if collee and collee.get("ts") != ss.derniere_collee:  # une image collée n'est prise en compte qu'une fois
                    ss.derniere_collee = collee["ts"]
                    try:
                        with st.spinner("Lecture de l'image…"):
                            img = ip.rogner_marges_blanches(ip.image_depuis_collage(collee))
                    except Exception as e:
                        st.error(f"Image impossible à lire ({type(e).__name__}). Copier l'image elle-même (et non son "
                                 "adresse), ou l'importer comme fichier ci-dessous.")
                    else:
                        ss.image = img
                        ss.props_choisie, ss.props_ouvertes = None, False  # propositions repliées, toujours disponibles
                        retablir_traitement()
                        st.toast("Image collée : elle est utilisée pour l'affiche.", icon=":material/check_circle:")
                        if max(img.size) < 500:
                            st.toast(f"Image collée petite ({max(img.size)} px), floue à l'impression : sur Google "
                                     "Images, cliquer d'abord sur l'image pour l'ouvrir en grand, puis la copier ; ou "
                                     "coller l'adresse de la page du produit.", icon=":material/warning:")
                televerse = st.file_uploader("Ou importer un fichier image (glisser-déposer)",
                                             type=["png", "jpg", "jpeg", "webp"], key=f"televerse_{ss.raz}")
                if televerse is not None:
                    fichier_televerse = (televerse.name, televerse.size)
                    if ss.get("televerse_vu") != fichier_televerse:  # le fichier n'est pris en compte qu'une fois
                        ss.televerse_vu = fichier_televerse
                        img = ip.vers_rgb_blanc(Image.open(televerse))
                        ss.image = img
                        ss.props_choisie, ss.props_ouvertes = None, False
                        retablir_traitement()

            with slot_props:
                if not ss.props and ss.props_msg and code_net:
                    st.warning(ss.props_msg)
                    st.link_button("Chercher ce code sur Google Images", icon=":material/image_search:",
                                   url=f"https://www.google.com/search?tbm=isch&q={quote(code_net)}")
                if ss.props:
                    # après un choix (ou un collage), les propositions restent accessibles, repliées
                    cadre = (st.container() if ss.props_ouvertes or ss.image is None else
                             st.expander(f"Changer de photo : revoir les {len(ss.props)} photos proposées", expanded=False))
                    with cadre:
                        if any(p.get("verifie") for p in ss.props):
                            intro = ("Photos trouvées sur des sites marchands dont la page contient bien ce code, la "
                                     "meilleure d'abord. Vérifier l'image, puis la choisir :")
                        elif any(p.get("niveau") == "nom" for p in ss.props):
                            intro = ("Aucune page ne contient ce code : ces photos viennent de pages dont le nom "
                                     "correspond. À vérifier avec soin, puis choisir :")
                        elif any(p.get("niveau") in ("indice", "titre") for p in ss.props):
                            intro = ("Aucune page n'a confirmé ce code. Ces images ont un titre (ou une adresse) qui cite "
                                     "le code ou correspond au nom du produit : contrôler l'emballage (gamme, contenance), "
                                     "puis choisir :")
                        else:
                            intro = ("Images du web non vérifiées (rien ne prouve qu'elles montrent ce produit). "
                                     "Vérifier l'image, puis la choisir :")
                        st.write(intro)
                        if ss.info_nom:
                            st.caption(f"Nom relevé sur les sites marchands et repris dans les textes : « {ss.info_nom} » "
                                       "(à vérifier).")
                        cols = st.columns(4)
                        for i, p in enumerate(ss.props):
                            retenue = ss.props_choisie is not None and p.get("image") == ss.props_choisie
                            with cols[i % 4]:
                                st.image(miniature_proposition(p), use_container_width=True)
                                choisi = st.button("Photo retenue" if retenue else "Choisir", key=f"choix{i}",
                                                   disabled=retenue,
                                                   type="primary" if ss.props_choisie is None and est_meilleur_choix(p, i)
                                                   else "secondary")
                                st.caption(legende_proposition(p))
                                if choisi:
                                    try:
                                        img = ip.rogner_marges_blanches(ip.obtenir_image(p))
                                        ss.image = img
                                        ss.props_choisie, ss.props_ouvertes = p.get("image"), False
                                        retablir_traitement()
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"Téléchargement impossible ({type(e).__name__}).")
                        if ss.props_autres and st.button("Voir aussi d'autres images (code non vérifié)", key="voir_autres"):
                            ss.props, ss.props_autres = list(ss.props) + list(ss.props_autres), []
                            st.rerun()

            # --- Nettoyage IA : case visible d'office, sous le visuel retenu
            with slot_options:
                if ss.image is not None:
                    nettoyer_on = st.checkbox("Nettoyage IA : supprimer le fond, ne garder que le produit", key="w_nettoyer",
                                              help="Décocher pour imprimer la photo telle quelle (par exemple si le "
                                                   "détourage abîme le produit).")
                else:
                    nettoyer_on = bool(ss.w_nettoyer)

            # --- Réglages du visuel (repliés : la plupart du temps, les valeurs par défaut suffisent)
            with st.expander("Réglages du visuel : recadrage, netteté", icon=":material/crop:"):
                st.markdown("**Recadrage** (retirer un nom de site, un bord…)")
                r1, r2 = st.columns(2)
                rg = r1.slider("Rogner à gauche (%)", 0, 40, key="w_rg")
                rd = r2.slider("Rogner à droite (%)", 0, 40, key="w_rd")
                rh = r1.slider("Rogner en haut (%)", 0, 40, key="w_rh")
                rb = r2.slider("Rogner en bas (%)", 0, 40, key="w_rb")
                # La netteté « haute qualité » exige beaucoup de mémoire : réservée à l'usage sur un poste (pas en ligne)
                choix_nettete = ["Désactivée", "Rapide"] + ([] if EN_LIGNE else ["Haute qualité (lent : jusqu'à 2 min)"])
                mode_nettete = st.radio("Netteté des visuels de faible résolution", choix_nettete,
                                        horizontal=True, key="nettete_mode")

            if ss.image is not None:
                with slot_visuel:
                    with st.spinner("Préparation du visuel… (la 1re fois, les modèles de détourage et de netteté se téléchargent)"):
                        visuel, methode = visuel_final(ss.image, nettoyer_on, (rg, rd, rh, rb), mode_nettete)
                    v1, v2, _v3 = st.columns([1, 1, 2])
                    v1.image(_miniature_carree(ss.image, 260), caption=f"Original ({min(ss.image.size)} px)", width=130)
                    v2.image(_miniature_carree(visuel, 260), caption=f"Visuel retenu ({min(visuel.size)} px)", width=130)
                    st.caption(f"Traitement appliqué : {methode}.")
                    retenue_auto = next((p for p in ss.props if p.get("image") == ss.get("choix_auto")), None)
                    if retenue_auto and ss.props_choisie == ss.get("choix_auto"):
                        n_sites = retenue_auto.get("nb_sites", 1)
                        st.caption("Photo retenue automatiquement : code confirmé sur "
                                   + (f"{n_sites} sites" if n_sites > 1 else retenue_auto.get("site", "un site"))
                                   + ", fond blanc. Les autres propositions sont dans « Changer de photo » ci-dessous.")
                    if min(visuel.size) < 600:
                        st.warning(f"Image de petite taille ({min(visuel.size)} px) : elle risque d'être floue à "
                                   "l'impression. Essayer la netteté « Rapide » dans les réglages du visuel, ou choisir "
                                   "une image plus grande.")
            else:
                visuel = None
            autres_visuels = zone_autres_images(cat, nettoyer_on, mode_nettete) if ss.image is not None else []
        else:
            visuel, autres_visuels = None, []
            nettoyer_on, mode_nettete = bool(ss.w_nettoyer), "Désactivée"
            rg = rd = rh = rb = 0
            st.caption("Affiche sans photo : seul le texte est imprimé. Pour ajouter une photo à cette affiche, "
                       "cocher « Avec la photo du produit » dans la mise en page (étape 3).")


        # --- Textes de l'affiche
        habillage.sous_titre("Textes de l'affiche")
        marque = st.text_input("Marque (affichée en gros, sous la photo)", value=ss.marque)
        reglages_texte("marque", "Couleur de la marque (le détail du produit la suit tant qu'il n'a pas la sienne)")
        detail = st.text_area("Détail du produit (affiché plus petit sous la marque ; Entrée = passage à la ligne)",
                              value=ss.detail, height=80)
        reglages_texte("detail")
        ss.marque, ss.detail = marque, detail
        majuscules = st.checkbox("Marque en majuscules", key="w_majuscules")
        nom = catalogue.nom_complet(marque, detail)

        # --- Logo de la marque : imprimé à la place de son nom (importé une fois, retrouvé à chaque affiche de la marque)
        logo_marque_img, cle_logo = None, None
        if marque.strip():
            cle_marque = marques.trouver(CTX.dossier, marque)
            if cle_marque != ss.cle_marque_vue:  # autre marque : son logo, s'il existe, est proposé d'office
                ss.cle_marque_vue = cle_marque
                ss.w_logo_marque = True
            logo_connu = marques.charger(CTX.dossier, cle_marque) if cle_marque else None
            if logo_connu is not None:
                c_logo, c_case = st.columns([1, 4], vertical_alignment="center")
                c_logo.image(logo_connu, width=90)
                c_case.checkbox("Imprimer le logo de la marque à la place de son nom", key="w_logo_marque",
                                help="Décocher pour écrire le nom de la marque en texte, comme d'habitude.")
                if ss.w_logo_marque:
                    logo_marque_img, cle_logo = logo_connu, cle_marque
            props_logo = ss.logos_props if (ss.logos_props or {}).get("cle") == marques.cle(marque) else None
            with st.expander("Logo de la marque : " + ("remplacer" if logo_connu is not None else "ajouter, pour l'imprimer "
                                                                                              "à la place du nom"),
                             expanded=props_logo is not None):
                if st.button(f"Chercher le logo de « {marque.strip()[:40]} »", icon=":material/search:",
                             help="Cherche le vrai logo de la marque (Wikidata, Wikipédia, Commons, puis le web) ; "
                                  "rien n'est enregistré avant votre choix."):
                    with st.spinner("Recherche du logo…"):
                        trouves, msg_l, _inc = logos_marques.chercher(marque)
                    ss.logos_props = {"cle": marques.cle(marque), "marque": marque.strip(), "props": trouves, "msg": msg_l}
                    st.rerun()
                if props_logo is not None:
                    afficher_propositions_logo(props_logo, "aff")
                st.caption("Importer ou coller une fois le logo de la marque (SVR, Avène…) : l'outil le retrouve ensuite "
                           f"à chaque affiche « {_md(marque.strip())} ». Un logo sur fond transparent (PNG) est idéal ; "
                           "les marges blanches sont retirées automatiquement.")
                collee_m = collage_image(key=f"collage_marque_{ss.raz}_{ss.logo_marque_n}", default=None)
                reussi = False
                if collee_m and collee_m.get("ts") != ss.derniere_collee_marque:
                    ss.derniere_collee_marque = collee_m["ts"]
                    try:
                        img_logo = ip.image_depuis_collage(collee_m)
                    except Exception as e:
                        st.error(f"Image impossible à lire ({type(e).__name__}). Copier l'image elle-même (et non son "
                                 "adresse), ou l'importer comme fichier.")
                    else:
                        cle_nouveau, message = marques.enregistrer(CTX.dossier, marque, img_logo)
                        reussi = cle_nouveau is not None
                        if reussi:
                            ss.msg_ouvert = message
                        else:
                            st.error(message)
                fichier_logo = st.file_uploader("Ou importer un fichier (PNG ou JPG)", type=["png", "jpg", "jpeg"],
                                                key=f"televerse_marque_{ss.raz}_{ss.logo_marque_n}")
                if fichier_logo is not None and ss.televerse_marque_vu != (fichier_logo.name, fichier_logo.size):
                    ss.televerse_marque_vu = (fichier_logo.name, fichier_logo.size)
                    cle_nouveau, message = marques.enregistrer(CTX.dossier, marque, fichier_logo.getvalue())
                    reussi = cle_nouveau is not None
                    if reussi:
                        ss.msg_ouvert = message
                    else:
                        st.error(message)
                if reussi:
                    ss.logo_marque_n += 1  # champs d'import vides
                    ss.cle_marque_vue = None  # le nouveau logo est proposé d'office au prochain affichage
                    st.rerun()
        with ph_etape1:  # coche verte quand le produit est renseigné
            habillage.titre_etape(1, "Produit", "Chercher le produit, vérifier son visuel et ses textes.", fait=bool(nom))

    # ------------------------------------------------------------------ Étape 2 : offre
    with st.container(key="etape_2"):
        ph_etape2 = st.empty()  # titre redessiné plus bas (coche verte quand l'offre est complète)
        with ph_etape2:
            habillage.titre_etape(2, "Offre", "Le prix promo en gros, ou une autre offre (pourcentage, 2e produit, lot…).",
                                  fait=bool(ss.get("etape2_fait")))
        type_promo = st.selectbox("Type de promotion", list(promos.TYPES), key="w_promo_type",
                                  format_func=lambda k: promos.TYPES[k]["libelle"], on_change=appliquer_defauts_promo,
                                  help="Par défaut : le prix promo en gros. Les autres types affichent l'offre elle-même "
                                       "(pourcentage, montant, 2e produit, lot, produit offert…).")
        resultat_promo, promo_enregistree = None, None
        if type_promo == promos.STANDARD:
            c1, c2 = st.columns(2, vertical_alignment="bottom")
            prix_txt = c1.text_input("Prix promo (€)", placeholder="7,90", key="w_prix",
                                     help="Écrire 7,90 ou 7.9 : l'outil met le prix en forme sur l'affiche.")
            barre = c2.checkbox("Afficher un prix barré", key="w_barre_on")
            prix_barre_txt = c2.text_input("Prix barré (€)", placeholder="10,50", key="w_barre") if barre else ""
            reglages_texte("prix")
            if barre:
                habillage.petit_titre("Prix barré")
                reglages_texte("prix_barre", "Couleur partagée avec les dates")
        else:
            prix_txt, barre, prix_barre_txt = "", False, ""
            resultat_promo, promo_enregistree = formulaire_promo(type_promo)

        avec_dates = st.checkbox("Afficher une plage de dates", key="w_dates_on")
        debut = fin = None
        if avec_dates:
            with st.container(key="raccourcis_dates"):
                r_mois, r_fin, r_suivant, _r = st.columns([1.2, 1.7, 1.4, 2])
                r_mois.button("Ce mois-ci", key="dates_mois", on_click=raccourci_dates, args=("mois",),
                              use_container_width=True, help="Du 1er au dernier jour du mois en cours")
                r_fin.button("Jusqu'à fin de mois", key="dates_fin_mois", on_click=raccourci_dates,
                             args=("fin_mois",), use_container_width=True,
                             help="D'aujourd'hui au dernier jour du mois en cours")
                r_suivant.button("Mois prochain", key="dates_mois_suivant", on_click=raccourci_dates,
                                 args=("mois_suivant",), use_container_width=True,
                                 help="Du 1er au dernier jour du mois suivant")
            d1, d2 = st.columns(2)
            debut = d1.date_input("Du", format="DD/MM/YYYY", key="w_debut")
            fin = d2.date_input("Au", format="DD/MM/YYYY", key="w_fin")

    # ------------------------------------------------------------------ Étape 3 : mise en page
    with st.container(key="etape_3"):
        habillage.titre_etape(3, "Mise en page", "Format, logo, police et couleurs. Astuce : un clic sur un élément de "
                                                 "l'aperçu affiche ses réglages (texte, couleur, police), juste au-dessus.")
        f1, f2 = st.columns([2, 3], vertical_alignment="bottom")
        choix = f1.selectbox("Format", list(FORMATS) + ["Personnalisé"], key="w_format")
        if choix == "Personnalisé":
            with f2:
                f2a, f2b = st.columns(2)
                lg = f2a.number_input("Largeur (mm)", 50, 600, key="w_lg")
                ht = f2b.number_input("Hauteur (mm)", 50, 900, key="w_ht")
            taille = (lg * mm, ht * mm)
            paysage = False  # l'orientation découle des dimensions saisies (largeur supérieure à la hauteur = paysage)
        else:
            paysage = f2.checkbox("Affiche en paysage (à l'horizontale)", key="w_paysage",
                                  on_change=orientation_changee,
                                  help="Le visuel passe à gauche, la marque, le prix et le logo à droite ; la mise en "
                                       "page s'adapte toute seule. Les éléments déplacés à la main sont remis en place "
                                       "quand on change d'orientation.")
            taille = orienter(FORMATS[choix], paysage)
        format_txt = f"{choix} paysage" if paysage else choix
        logo = st.checkbox("Afficher le logo" if (IDENTITE is None or IDENTITE["logo"]) else "Afficher le nom de la pharmacie",
                           key="w_logo")
        st.checkbox("Avec la photo du produit", key="w_photo",
                    help="Décocher pour une affiche sans image (par exemple une petite affiche de rayon) : la recherche "
                         "de photo est alors ignorée.")

        with st.expander("Police et couleurs", icon=":material/palette:"):
            st.selectbox("Police de l'affiche (celle de tous les textes, sauf choix contraire plus bas)", POLICES,
                         key="w_police", on_change=maj_style, args=("police", "w_police"))
            st.selectbox("Thème de couleurs", ["— choisir un thème —"] + list(THEMES), key="theme_choisi",
                         on_change=appliquer_theme)
            cwf = f"cb_fond_{ss.ver}"
            st.checkbox("Prix sur fond coloré (bandeau)", ss.style["fond_prix"], key=cwf,
                        on_change=maj_style, args=("fond_prix", cwf))
            cdm = f"cb_detail_autre_{ss.ver}"
            detail_autre = st.checkbox("Détail du produit d'une autre couleur que la marque",
                                       bool(ss.style.get("couleur_detail")), key=cdm,
                                       on_change=maj_detail_autre, args=(cdm,),
                                       help="Décochée : la marque et le détail du produit ont la même couleur.")
            p1, p2 = st.columns(2)
            champs_couleurs = [("couleur_nom", "Couleur de la marque" if detail_autre
                                else "Couleur de la marque et du détail")]
            if detail_autre:
                champs_couleurs.append(("couleur_detail", "Couleur du détail"))
            if type_promo != promos.STANDARD:
                champs_couleurs.append(("couleur_kicker", "Petit texte au-dessus de l'offre"))
            champs_couleurs += [("couleur_prix", "Couleur du prix"), ("couleur_fond_prix", "Couleur du fond du prix"),
                                ("couleur_accent", "Filet sous le prix (sans fond)"),
                                ("couleur_secondaire", "Dates et prix barré")]
            for i, (cle, libelle) in enumerate(champs_couleurs):
                cw = f"cp_{cle}_{ss.ver}"
                (p1 if i % 2 == 0 else p2).color_picker(libelle, couleur_de(cle), key=cw, on_change=maj_couleur,
                                                        args=(cle, cw))
            st.markdown("**Police et style de chaque texte**")
            st.caption("Chaque texte peut avoir sa police, en gras, en italique ou souligné, indépendamment des autres.")
            for texte, (libelle, _) in TEXTES.items():
                if texte == "kicker" and type_promo == promos.STANDARD:
                    continue  # pas de petit texte au-dessus du prix promo
                ligne_texte(texte, libelle)
            st.button("Remettre les polices et styles d'origine", key="textes_raz", on_click=reinitialiser_textes,
                      type="tertiary", icon=":material/restart_alt:",
                      disabled=not textes_valides(ss.style.get("textes")))
            st.caption("Ces choix valent pour l'affiche en cours et les suivantes ; « Mettre à jour ce type » les garde "
                       "pour la suite.")

        with st.expander("Cadre (en option)" + ("" if ss.style.get("cadre", "aucun") == "aucun"
                                                 else f" : {CADRES.get(ss.style.get('cadre'), '')}"),
                         icon=":material/crop_square:"):
            k1, k2 = st.columns([3, 2], vertical_alignment="bottom")
            ckc = f"cadre_{ss.ver}"
            k1.selectbox("Style du cadre", list(CADRES), format_func=CADRES.get, key=ckc,
                         index=list(CADRES).index(ss.style.get("cadre")) if ss.style.get("cadre") in CADRES else 0,
                         on_change=maj_style, args=("cadre", ckc))
            ckk = f"cp_couleur_cadre_{ss.ver}"
            k2.color_picker("Couleur du cadre", ss.style.get("couleur_cadre", "#175848"), key=ckk,
                            on_change=maj_style, args=("couleur_cadre", ckk),
                            disabled=ss.style.get("cadre", "aucun") == "aucun")
            st.caption("Aucun cadre par défaut. Le cadre choisi reste appliqué aux affiches suivantes ; "
                       "revenir à « Aucun cadre » pour l'enlever.")

        slot_elements = st.container()  # « Ajouter un texte, un prix ou une forme » (rempli après l'aperçu)
        slot_reglages = st.container()  # « Réglages précis » (rempli quand l'aperçu est affiché)

    # ------------------------------------------------------------------ Étape 4 : impression
    with st.container(key="etape_4"):
        habillage.titre_etape(4, "Impression", "Le PDF se télécharge depuis l'aperçu, à droite.")
        i1, i2 = st.columns(2, vertical_alignment="bottom")
        exemplaires = i1.number_input("Nombre d'affiches à imprimer", 1, 500, key="w_exemplaires",
                                      help="Nombre d'exemplaires dans le PDF à imprimer.")
        par_feuille = disposition_a4(taille)[0]
        en_planche = False
        if par_feuille > 1:
            en_planche = i2.checkbox(f"{par_feuille} affiches par feuille A4", value=True)
        slot_feuille = st.container()  # « Feuille d'impression » (remplie quand l'aperçu est affiché)

    with st.expander(f"Catalogue produits ({len(cat)} produit(s))", icon=":material/inventory_2:"):
        st.caption("Les produits des affiches téléchargées sont mémorisés avec leur marque. Un export du logiciel "
                   "de pharmacie (CSV : colonnes code ; nom, et marque si possible) peut aussi être importé : "
                   "il remplace l'export précédent et n'efface pas les produits mémorisés.")
        if ss.get("msg_import"):
            st.success(ss.pop("msg_import"))
        export = st.file_uploader("Importer un export du logiciel de pharmacie (CSV)", type=["csv", "txt"],
                                  key="import_catalogue")
        if export is not None and ss.get("import_fait") != (export.name, export.size):
            ss.import_fait = (export.name, export.size)
            nb, erreur = catalogue.importer(export.getvalue())
            if erreur:
                st.error(erreur)
            else:
                sauvegarde.planifier("catalogue.csv")
                ss.msg_import = f"{nb} produit(s) importé(s)."
                st.rerun()
        if catalogue.fichier_appris().exists():
            st.download_button("Télécharger les produits mémorisés (copie de sauvegarde)",
                               catalogue.fichier_appris().read_bytes(), "catalogue_appris.csv", "text/csv")
        if sauvegarde.activee():
            if sauvegarde.statut["erreur"]:
                st.warning(sauvegarde.statut["erreur"])
            else:
                st.caption("Sauvegarde en ligne du catalogue, du style et de l'historique des affiches : activée.")

rendu_promo = resultat_promo["rendu"] if resultat_promo else None  # autre type de promotion : ce qui est dessiné
if resultat_promo:
    prix, prix_barre = rendu_promo["prix"] if rendu_promo else None, rendu_promo["prix_barre"] if rendu_promo else None
else:
    prix = parse_prix(prix_txt)
    prix_barre = parse_prix(prix_barre_txt) if barre else None
erreurs = list(resultat_promo["erreurs"]) if resultat_promo else []
if prix_txt and prix is None:
    erreurs.append("Prix promo invalide.")
if barre and prix_barre_txt and prix_barre is None:
    erreurs.append("Prix barré invalide.")
if prix is not None and prix_barre is not None and prix_barre <= prix:
    erreurs.append("Le prix barré doit être supérieur au prix promo.")
if avec_dates and debut and fin and fin < debut:
    erreurs.append("La date de fin précède la date de début.")

offre_complete = (rendu_promo is not None if resultat_promo else prix is not None) and not erreurs
ss.etape2_fait = offre_complete
with ph_etape2:  # coche verte quand l'offre est complète et valide
    habillage.titre_etape(2, "Offre", "Le prix promo en gros, ou une autre offre (pourcentage, 2e produit, lot…).",
                          fait=offre_complete)

apercu_affiche, cadres, final, feuilles, par = False, {}, None, 0, 1
with col_apercu:
    with st.container(key="apercu_fixe"):
        rappels = [e for e in erreurs if e.endswith("à renseigner.")]  # champ pas encore rempli : simple rappel
        for e in erreurs:
            if e not in rappels:
                st.error(e)
        offre_prete = rendu_promo is not None if resultat_promo else prix is not None
        if not offre_prete or not nom:
            habillage.etat_vide(_manquants(nom, resultat_promo, rappels, prix, prix_txt))
        elif not erreurs:
            if visuel is None and avec_photo:
                st.warning("Aucun visuel : l'affiche sera générée sans image.")
            dates_txt = libelle_dates(debut, fin) if avec_dates else ""
            unitaire, cadres = rendu(taille, marque, detail, prix, prix_barre, dates_txt, [visuel] + autres_visuels, logo,
                                     reglages=ss.reglages, majuscules=majuscules, style=ss.style, promo=rendu_promo,
                                     identite=IDENTITE, logo_marque=logo_marque_img, elements=ss.elements)
            largeur_px = 900
            png = apercu_png(unitaire, dpi=int(round(largeur_px * 72 / taille[0])))
            noms_editeur = {**ELEMENTS, **{f"libre_{e['id']}": html.escape(libres.nom(e)) for e in ss.elements}}
            if logo_marque_img is not None:
                noms_editeur["marque"] = "Logo de la marque"
            actif_editeur = None
            if ss.selection_apercu:
                actif_editeur = (f"libre_{ss.element_libre_actif}" if ss.selection_libre and ss.element_libre_actif
                                 else ss.element_actif)
                if actif_editeur not in cadres:
                    actif_editeur = None
            retouches.noter(ss)  # modifications faites dans le formulaire pendant cette exécution
            # Les clics, déplacements et réglages faits sur l'aperçu sont appliqués au début de l'exécution suivante
            # (voir retouches.appliquer)
            editeur_affiche(image="data:image/png;base64," + base64.b64encode(png).decode(),
                            cadres=cadres, noms=noms_editeur, ratio=taille[1] / taille[0], actif=actif_editeur,
                            outils=retouches.outils(ss, cadres, standard=type_promo == promos.STANDARD,
                                                    logo_marque=logo_marque_img is not None),
                            polices=POLICES, police_affiche=ss.style["police"], themes=list(THEMES),
                            couleurs=retouches.couleurs_affiche(ss),
                            formes={k: v[0] for k, v in libres.FORMES.items()},
                            plein=len(ss.elements) >= libres.MAX_ELEMENTS,
                            annuler=retouches.peut_annuler(ss), retablir=retouches.peut_retablir(ss),
                            traites=retouches.traites(ss),
                            reserve=RESERVE_APERCU + (0 if visuel is not None or not avec_photo else 62),
                            key="editeur", default=None)  # sans visuel : un avertissement s'ajoute au-dessus de l'affiche

            def memoriser_affiche():
                """Mémorise le produit (proposé directement la fois suivante, avec sa marque) et enregistre l'affiche
                dans l'historique. Retourne l'identifiant de l'affiche."""
                if code_net:
                    catalogue.enregistrer(code_net, marque, detail)
                    sauvegarde.planifier("catalogue_appris.csv")
                return enregistrer_affiche(code_net, marque, detail, prix, prix_barre, debut, fin, choix, visuel, png,
                                           promo_enregistree, autres_visuels, paysage, cle_logo)

            final, feuilles, par = pdf_impression(unitaire, taille, exemplaires, en_planche)
            fichier = re.sub(r"[^A-Za-z0-9_-]+", "_", nom)[:40] or "affiche"
            if st.download_button("Télécharger le PDF à imprimer", final,
                                  f"affiche_{fichier}_{format_txt.replace(' ', '_')}_x{exemplaires}.pdf",
                                  "application/pdf", type="primary", use_container_width=True,
                                  icon=":material/download:"):
                memoriser_affiche()  # l'affiche téléchargée est aussi conservée dans l'historique
                st.toast("Affiche enregistrée dans l'historique.", icon=":material/check_circle:")
            h1, h2, h3 = st.columns(3)
            if h1.button("Enregistrer", key="enreg_hist", icon=":material/bookmark_add:", use_container_width=True,
                         help="Enregistrer l'affiche dans l'historique"):
                memoriser_affiche()
                st.toast("Affiche enregistrée dans l'historique.", icon=":material/check_circle:")
            if h2.button("Page A4", key="enreg_page", icon=":material/library_add:", use_container_width=True,
                         help="Enregistrer l'affiche et l'ajouter à la page A4 regroupée"):
                ajouter_regroupe(memoriser_affiche())
                st.toast("Affiche enregistrée et ajoutée à la page A4 regroupée (onglet « Page A4 regroupée »).",
                         icon=":material/check_circle:")

            def enregistrer_puis_suivante():
                """Enchaîner : l'affiche est enregistrée (rien n'est perdu), puis le formulaire repart pour la suivante."""
                memoriser_affiche()
                nouvelle_affiche(garder_serie=True)

            h3.button("Suivante", key="passer_suivante", on_click=enregistrer_puis_suivante,
                      icon=":material/skip_next:", use_container_width=True,
                      help="Enregistrer et passer à la suivante : l'affiche est enregistrée dans l'historique, puis le "
                           "produit et le prix sont effacés. "
                           "Le format, le style, le type de promotion, les dates et les éléments ajoutés sont conservés.")
            apercu_affiche = True

# Éléments ajoutés à la main : texte, prix ou forme, de la couleur voulue, derrière (par défaut) ou devant le visuel
with slot_elements:
    with st.expander("Ajouter un texte, un prix ou une forme", icon=":material/add_box:"):
        st.caption("Un texte, un prix ou une forme (carré, rond, flèche, étoile…) à poser sur l'affiche : on choisit sa "
                   "couleur, et s'il passe derrière le visuel (par défaut) ou devant. Il se déplace ensuite sur l'aperçu, "
                   "comme les autres éléments.")
        a1, a2, a3 = st.columns(3)
        a1.button("Texte", key="ajout_texte", on_click=ajouter_element, args=("texte",), icon=":material/title:",
                  use_container_width=True)
        a2.button("Prix", key="ajout_prix", on_click=ajouter_element, args=("prix",), icon=":material/euro:",
                  use_container_width=True)
        a3.button("Forme", key="ajout_forme", on_click=ajouter_element, args=("forme",), icon=":material/category:",
                  use_container_width=True)
        if not ss.elements:
            st.caption("Aucun élément ajouté.")
        else:
            par_id = {e["id"]: e for e in ss.elements}
            if ss.element_libre_actif not in par_id:
                ss.element_libre_actif = ss.elements[0]["id"]
            etiquettes = [f"{n}. {libres.nom(e)}" for n, e in enumerate(ss.elements, 1)]  # numérotées : toutes différentes
            cle_liste = f"elements_libres_liste_{ss.ver}_{hashlib.md5(chr(10).join(etiquettes).encode()).hexdigest()[:8]}"
            st.radio("Éléments ajoutés", etiquettes, index=list(par_id).index(ss.element_libre_actif),
                     format_func=_md, key=cle_liste, label_visibility="collapsed", on_change=choisir_libre,
                     args=(cle_liste, dict(zip(etiquettes, par_id))))
            ident = ss.element_libre_actif
            e = par_id[ident]

            def cle(champ):
                return f"lib_{champ}_{ident}_{ss.ver}"

            def reglage(champ):
                return {"on_change": maj_element, "args": (ident, champ, cle(champ)), "key": cle(champ)}

            st.radio("Position par rapport au visuel", [DERRIERE, DEVANT], index=int(e["devant"]), horizontal=True,
                     help="Derrière : le visuel et les textes passent par-dessus l'élément (par exemple une forme de "
                          "couleur en fond). Devant : l'élément recouvre le reste.", **reglage("devant"))
            if e["type"] == "texte":
                st.text_area("Texte", e["texte"], height=80, max_chars=200, **reglage("texte"))
            elif e["type"] == "prix":
                st.text_input("Prix", e["texte"], max_chars=12, placeholder="ex. 9,90", **reglage("texte"))
                if not libres.prix_valide(e["texte"]):
                    st.caption(":orange[Prix non reconnu : saisir par exemple 9,90 ou 12.]")
            else:
                st.selectbox("Forme", list(libres.FORMES), index=list(libres.FORMES).index(e["forme"]),
                             format_func=lambda f: libres.FORMES[f][0], **reglage("forme"))
                st.text_input("Texte dans la forme (facultatif)", e["texte"], max_chars=60, **reglage("texte"))
            libelle_couleur = {"texte": "Couleur du texte", "prix": "Couleur du prix", "forme": "Couleur de la forme"}
            k1, k2 = st.columns(2)
            k1.color_picker(libelle_couleur[e["type"]], e["couleur"], **reglage("couleur"))
            if e["type"] == "prix":
                k2.checkbox("Prix sur fond coloré", e["fond"], **reglage("fond"))
                if e["fond"]:
                    k1.color_picker("Couleur du fond", e["couleur_fond"], **reglage("couleur_fond"))
            if e["type"] != "forme" or e["texte"].strip():
                options = [COMME_AFFICHE] + POLICES
                t1, t2 = st.columns([3, 2], vertical_alignment="bottom")
                t1.selectbox("Police du texte" if e["type"] != "prix" else "Police du prix", options,
                             index=options.index(e["police"]) if e["police"] in POLICES else 0, **reglage("police"))
                t2.segmented_control("Style", STYLES_TEXTE, selection_mode="multi", format_func=ICONES_STYLE.get,
                                     default=[s for s, on in (("gras", e["gras"]), ("italique", e["italique"]),
                                                              ("souligne", e["souligne"])) if on],
                                     help=AIDE_STYLE, **reglage("style"))
                if e["type"] == "forme":
                    st.caption("Le texte écrit dans une forme est blanc sur un fond foncé, presque noir sur un fond clair.")
            if e["type"] == "forme":
                st.slider("Largeur (%)", 3, 160, int(round(e["l"] * 100)), **reglage("l"))
                if libres.FORMES[e["forme"]][1]:
                    st.slider("Hauteur (%)", 3, 160, int(round(e["h"] * 100)), **reglage("h"))
            else:
                mini, maxi = (2, 50) if e["type"] == "texte" else (3, 60)
                st.slider("Taille (%)", mini, maxi, int(min(maxi, max(mini, round(e["t"] * 100)))), **reglage("t"))
            st.slider("Rotation (°)", -180, 180, int(round(e["rot"] / 5) * 5), step=5, **reglage("rot"))
            st.slider("Position horizontale (%)", -25, 125, int(round(e["cx"] * 100)), **reglage("cx"))
            st.slider("Position verticale (%)", -25, 125, int(round(e["cy"] * 100)), **reglage("cy"))
            b1, b2 = st.columns(2)
            b1.button("Dupliquer", key=f"lib_dup_{ident}", on_click=dupliquer_element, args=(ident,),
                      icon=":material/content_copy:", use_container_width=True)
            b2.button("Supprimer", key=f"lib_sup_{ident}", on_click=supprimer_element, args=(ident,),
                      icon=":material/delete:", use_container_width=True)
            st.caption("Astuce : un clic sur un élément de l'aperçu le sélectionne ici ; le faire glisser le déplace, "
                       "tirer sa poignée l'agrandit. Un élément caché derrière le visuel se choisit plutôt dans la liste "
                       "ci-dessus, ou en cliquant sur la partie qui dépasse.")

# Réglages qui dépendent de l'aperçu : placés dans le formulaire (étapes 3 et 4), à côté de l'aperçu qui reste visible
with slot_reglages:
    if apercu_affiche:
        with st.expander("Réglages précis", expanded=False, icon=":material/tune:"):
            st.caption("Pour déplacer ou agrandir un élément, le plus simple est de le faire glisser sur l'aperçu. "
                       "Ces réglages servent aux ajustements fins.")
            dispo = [e for e in ELEMENTS if e in cadres]
            if ss.element_actif not in dispo:
                ss.element_actif = dispo[0]
            el = st.radio("Élément", dispo, format_func=lambda e: ELEMENTS[e], horizontal=True,
                          key="element_actif", on_change=choisir_standard)
            g = ss.reglages[el]
            ks_, kx_, ky_ = f"sl_s_{el}_{ss.ver}", f"sl_dx_{el}_{ss.ver}", f"sl_dy_{el}_{ss.ver}"
            st.slider("Taille (%)", 20, 400, int(round(g["s"] * 100)), key=ks_,
                      on_change=maj_slider, args=(el, "s", ks_))
            st.slider("Position horizontale (← →, %)", -50, 50, int(round(g["dx"] * 100)),
                      key=kx_, on_change=maj_slider, args=(el, "dx", kx_))
            st.slider("Position verticale (↓ ↑, %)", -50, 50, int(round(g["dy"] * 100)),
                      key=ky_, on_change=maj_slider, args=(el, "dy", ky_))
            b1, b2 = st.columns(2)
            b1.button("Réinitialiser cet élément", on_click=reinitialiser, args=(el,))
            b2.button("Tout réinitialiser", on_click=reinitialiser)
    else:
        st.caption("Les réglages de position apparaissent dès que l'aperçu est affiché.")

with slot_feuille:
    if apercu_affiche:
        with st.expander("Feuille d'impression", icon=":material/print:"):
            st.image(apercu_png(final), use_container_width=True)
            st.caption(f"{exemplaires} affiche(s) → {feuilles} "
                       f"{'feuille(s) A4' if par > 1 else f'page(s) {format_txt}'}"
                       f"{f', {par} par feuille' if par > 1 else ''} (1re feuille affichée).")


# ----------------------------------------------------------------------------
# Onglet « Historique » : retrouver, rouvrir, supprimer, choisir les affiches à regrouper
# ----------------------------------------------------------------------------
MAX_AFFICHES_VISIBLES = 48


def _format_entree(e):
    """Format de l'affiche enregistrée, avec l'orientation si elle est en paysage (« A5 paysage »)."""
    format_ = e.get("format", "")
    return f"{format_} paysage" if format_ and e.get("orientation") == "Paysage" else format_


def _titre_entree(e):
    return (e.get("marque") or e.get("detail") or "Sans nom").strip().replace("\n", " ")


def _conservation(e):
    """Durée de conservation restante d'une affiche enregistrée (en rouge ou orange quand la suppression approche)."""
    n = historique.jours_restants(e)
    if n <= 0:
        return ":red[Suppression imminente]"
    mot = "jour" if n == 1 else "jours"
    if n <= 3:
        return f":red[Suppression dans {n} {mot}]"
    if n <= 14:
        return f":orange[Suppression dans {n} jours]"
    return f"Conservée encore {n} jours"


def _fabriquer_pdf(e, dossier, identite):
    """Fonction qui refait le PDF de l'affiche au moment du téléchargement (seulement si on clique : les 48 PDF de la page
    ne sont pas préparés d'avance). Elle ne s'appuie que sur ses paramètres, car elle s'exécute hors de la session."""
    def fabriquer():
        logo = marques.charger(dossier, e.get("logo_marque")) if e.get("logo_marque") else None
        return reimpression.pdf_depuis_entree(e, historique.visuels(e["id"], dossier), identite, logo)
    return fabriquer


def bouton_telecharger(e):
    """Télécharger le PDF d'une affiche enregistrée, pour la conserver sur l'ordinateur."""
    ident = e["id"]
    nom_fichier = "affiche_" + (re.sub(r"[^A-Za-z0-9_-]+", "_", catalogue.nom_complet(e.get("marque", ""), e.get("detail", "")))
                                [:40].strip("_") or "affiche") + "_" + _format_entree(e).replace(" ", "_") + ".pdf"
    fabriquer = _fabriquer_pdf(e, CTX.dossier, IDENTITE)
    aide = "Enregistrer le PDF de cette affiche sur l'ordinateur (pour la garder au-delà de 3 mois)"
    try:
        st.download_button("Télécharger le PDF", fabriquer, nom_fichier, "application/pdf", key=f"tel_{ident}",
                           icon=":material/download:", use_container_width=True, on_click="ignore", help=aide)
    except Exception:  # version de Streamlit sans téléchargement différé : le PDF est préparé sur demande
        if ss.get("pdf_pret", (None,))[0] != ident:
            if st.button("Télécharger le PDF", key=f"prep_{ident}", icon=":material/download:",
                         use_container_width=True, help=aide):
                ss.pdf_pret = (ident, fabriquer())
                st.rerun()
        else:
            st.download_button("Enregistrer le PDF", ss.pdf_pret[1], nom_fichier, "application/pdf",
                               key=f"tel_{ident}", icon=":material/download:", use_container_width=True)


with onglet_hist:
    historique.purger()
    entrees = historique.lister()
    if ss.get("msg_hist"):
        st.success(ss.pop("msg_hist"))
    if EN_LIGNE and not sauvegarde.activee():
        st.warning("La sauvegarde en ligne n'est pas configurée : l'historique sera perdu au prochain redémarrage "
                   "de l'application (réglage HF_TOKEN à ajouter dans les secrets).")
    elif sauvegarde.activee() and sauvegarde.statut["erreur"]:
        st.warning(sauvegarde.statut["erreur"])
    if not entrees:
        st.info(f"Aucune affiche pour l'instant. Une affiche téléchargée ou enregistrée depuis l'onglet "
                f"« {ONGLET_CREER} » apparaît ici automatiquement.")
    else:
        c_filtre, c_info = st.columns([2, 3], vertical_alignment="bottom")
        recherche = c_filtre.text_input("Rechercher une affiche (marque, produit, code ou type)", key="filtre_hist")
        n_aff = len(entrees)
        c_info.caption(f"{n_aff} affiche{'s' if n_aff > 1 else ''} enregistrée{'s' if n_aff > 1 else ''}, "
                       f"conservée{'s' if n_aff > 1 else ''} {historique.DUREE_JOURS // 30} mois environ, "
                       "puis supprimée" + ("s" if n_aff > 1 else "") + " automatiquement. « Télécharger le PDF » "
                       "garde une affiche sur l'ordinateur ; l'enregistrer à nouveau la conserve 3 mois de plus.")
        mot = catalogue._norm_mot(recherche)
        visibles = [e for e in entrees
                    if not mot or mot in catalogue._norm_mot(f"{e.get('marque', '')} {e.get('detail', '')} {e.get('code', '')} {e.get('type_affiche', '')}")]
        if not visibles:
            st.info("Aucune affiche ne correspond à la recherche.")
        if len(visibles) > MAX_AFFICHES_VISIBLES:
            st.caption(f"Les {MAX_AFFICHES_VISIBLES} affiches les plus récentes sur {len(visibles)} sont affichées : "
                       "utiliser la recherche pour retrouver les autres.")
        colonnes = st.columns(4)
        for i, e in enumerate(visibles[:MAX_AFFICHES_VISIBLES]):
            ident = e["id"]
            with colonnes[i % 4]:
                with st.container(border=True):
                    st.image(historique.apercu_octets(e), use_container_width=True)
                    st.markdown(f"**{_md(_titre_entree(e))}**")
                    detail_court = (e.get("detail") or "").strip().replace("\n", " ")
                    if len(detail_court) > 60:
                        detail_court = detail_court[:57].rstrip() + "…"
                    prix_aff = promos.resume_entree(e)
                    gamme = f" · {e['nb_visuels']} visuels" if e.get("nb_visuels", 0) > 1 else ""
                    type_txt = (f"Type « {_md(e['type_affiche'])} » · "
                                if e.get("type_affiche") and len(TYPES["types"]) > 1 else "")
                    st.caption(f"{_md(detail_court)}  \n{_md(prix_aff)} · {_format_entree(e)}{gamme}  \n"
                               f"{type_txt}créée le {historique.date_creation(e)}  \n{_conservation(e)}")
                    b_ouvrir, b_page = st.columns(2)
                    b_ouvrir.button("Rouvrir", key=f"ouv_{ident}", on_click=rouvrir, args=(ident,),
                                    use_container_width=True, help="Recharger cette affiche pour la modifier ou la réimprimer")
                    if ident in ss.regroupe:
                        b_page.button("Dans la page ✓", key=f"ret_{ident}", on_click=retirer_regroupe, args=(ident,),
                                      use_container_width=True, help="Déjà dans la page A4 regroupée : cliquer pour la retirer")
                    else:
                        b_page.button("+ Page A4", key=f"aj_{ident}", on_click=ajouter_regroupe, args=(ident,),
                                      use_container_width=True, help="Ajouter à la page A4 regroupée")
                    bouton_telecharger(e)
                    if ss.suppr_attente == ident:
                        st.warning("Supprimer cette affiche de l'historique ?")
                        b_oui, b_non = st.columns(2)
                        b_oui.button("Oui, supprimer", key=f"oui_{ident}", on_click=confirmer_suppression,
                                     args=(ident,), type="primary", use_container_width=True)
                        b_non.button("Annuler", key=f"non_{ident}", on_click=annuler_suppression,
                                     use_container_width=True)
                    else:
                        st.button("Supprimer", key=f"sup_{ident}", on_click=demander_suppression, args=(ident,),
                                  type="tertiary", use_container_width=True,
                                  help="Supprimer cette affiche (par exemple créée par erreur)")


# ----------------------------------------------------------------------------
# Onglet « Page A4 regroupée » : jusqu'à 6 affiches par feuille, avec un titre personnalisable
# ----------------------------------------------------------------------------
with onglet_page:
    col_g, col_d = st.columns([3, 2], gap="large")
    pdf_page, apercus, titre_page = None, [], ""
    with col_g:
        with st.container(key="etape_page_reglages"):
            st.caption(f"Regroupe plusieurs affiches de l'historique sur une même feuille A4 (portrait ou paysage), "
                       f"avec un titre. {planche.MAX_PAR_PAGE} affiches au maximum par feuille : au-delà, plusieurs "
                       "feuilles sont créées, avec le même titre. Les polices et couleurs utilisées sont celles "
                       f"choisies dans l'onglet « {ONGLET_CREER} ».")
            st.text_input("Titre de la page", key="titre_page", placeholder=planche.TITRE_DEFAUT,
                          help="Exemples : Promos du mois, Promos du moment, Offres de la semaine. Laisser vide pour une page sans titre.")
            o1, o2 = st.columns(2, vertical_alignment="bottom")
            o1.radio("Orientation de la page", planche.ORIENTATIONS, horizontal=True, key="orientation_page")
            o2.checkbox("Logo de la pharmacie en pied de page", key="w_logo_page")

        choisies = [e for e in (historique.charger(i) for i in ss.regroupe) if e]
        ss.regroupe = [e["id"] for e in choisies]  # oublie les affiches supprimées ou expirées
        if not choisies:
            st.info(f"Aucune affiche sélectionnée. Dans l'onglet « {ONGLET_HISTORIQUE} », cliquer sur « + Page A4 » "
                    "sous les affiches à regrouper.")
        else:
            tailles = planche.repartir(len(choisies))
            with st.container(key="etape_page_liste"):
                st.markdown(f"**{len(choisies)} affiche(s) sélectionnée(s) → {len(tailles)} feuille(s) A4** "
                            f"({' + '.join(str(t) for t in tailles)} affiche(s))")
                for i, e in enumerate(choisies):
                    ligne, monter, descendre, retirer = st.columns([8, 1.2, 1.2, 2.4], vertical_alignment="center")
                    ligne.write(f"{i + 1}. {_md(_titre_entree(e))} – {_md(promos.resume_entree(e))}")
                    monter.button("↑", key=f"mh_{i}", on_click=deplacer_regroupe, args=(i, -1), disabled=i == 0,
                                  help="Monter")
                    descendre.button("↓", key=f"mb_{i}", on_click=deplacer_regroupe, args=(i, 1),
                                     disabled=i == len(choisies) - 1, help="Descendre")
                    retirer.button("Retirer", key=f"rr_{e['id']}", on_click=retirer_regroupe, args=(e["id"],),
                                   use_container_width=True)
                st.button("Tout retirer de la page", on_click=vider_regroupe)

            titre_page = ss.titre_page.strip()
            cle = (titre_page, tuple(e["id"] for e in choisies), tuple(e["cree"] for e in choisies),
                   json.dumps(ss.style, sort_keys=True), bool(ss.w_logo_page), ss.orientation_page,
                   pharmacie.empreinte_identite(CTX))
            if ss.planche_cache is None or ss.planche_cache[0] != cle:
                groupes, debut_lot = [], 0
                for taille_lot in tailles:
                    groupes.append([{**e, "_images": historique.visuels(e["id"]),
                                     "_logo_marque": marques.charger(CTX.dossier, e.get("logo_marque"))}
                                    for e in choisies[debut_lot:debut_lot + taille_lot]])
                    debut_lot += taille_lot
                with st.spinner("Mise en page…"):
                    pdf_page = planche.pdf_planche(titre_page, groupes, ss.style, bool(ss.w_logo_page),
                                                   ss.orientation_page, IDENTITE)
                    ss.planche_cache = (cle, pdf_page, planche.apercus_png(pdf_page, 80))
            _, pdf_page, apercus = ss.planche_cache
            nom_fichier = re.sub(r"[^A-Za-z0-9_-]+", "_", titre_page)[:40].strip("_") or "page"
            st.download_button("Télécharger la page A4 (PDF)", pdf_page, f"promos_{nom_fichier}.pdf",
                               "application/pdf", type="primary", icon=":material/download:")
    with col_d:
        for i, png_page in enumerate(apercus):
            st.image(png_page, caption=f"Feuille {i + 1} sur {len(apercus)}", use_container_width=True)


# ----------------------------------------------------------------------------
# Onglet « Ma pharmacie » (plusieurs pharmacies) : logo, déconnexion et, pour l'administrateur, ajout de pharmacies
# ----------------------------------------------------------------------------
def _se_deconnecter():
    pharmacie.fermer_session()


def supprimer_logo_marque(cle_marque):
    marques.supprimer(CTX.dossier, cle_marque)
    ss.cle_marque_vue = None
    ss.msg_ouvert = "Logo retiré : le nom de la marque est de nouveau écrit en texte."


if MULTI:
    with onglet_pharmacie:
        st.subheader(pharmacie.nom_affiche(CTX))
        st.caption("Chaque pharmacie a ses propres affiches, son catalogue, ses couleurs et son logo : "
                   "aucune autre pharmacie n'y a accès.")
        mise_en_route.afficher(CTX, premiere_fois=False)
        st.divider()
        habillage.sous_titre("Logos de marques")
        st.caption("Le logo d'une marque (SVR, Avène…) est imprimé à la place de son nom sur les affiches de cette marque. "
                   f"Pour en ajouter un : dans « {ONGLET_CREER} », sous le nom de la marque, « Logo de la marque », "
                   "ou la recherche automatique ci-dessous.")
        with st.expander("Chercher automatiquement les logos de plusieurs marques", expanded=bool(ss.logos_lot)):
            liste = st.text_area("Marques (une par ligne, 10 au plus)", key="w_logos_liste", height=120,
                                 placeholder="La Roche-Posay\nAvène\nGallia\nGuigoz\nGranions")
            if st.button("Chercher les logos", icon=":material/search:", disabled=not liste.strip()):
                noms = list(dict.fromkeys(n.strip() for n in liste.splitlines() if n.strip()))[:10]
                barre = st.progress(0.0, text="Recherche des logos…")
                lot = []
                for j, n in enumerate(noms):
                    barre.progress(j / len(noms), text=f"Recherche du logo « {n} »…")
                    trouves, msg_l, _inc = logos_marques.chercher(n, web="si_besoin")
                    lot.append({"cle": marques.cle(n), "marque": n, "props": trouves, "msg": msg_l})
                ss.logos_lot = lot
                st.rerun()
            for bloc in ss.logos_lot:
                st.markdown(f"**{_md(bloc['marque'])}**" + (" — logo déjà enregistré (le choix le remplace)"
                                                             if marques.trouver(CTX.dossier, bloc["marque"]) else ""))
                afficher_propositions_logo(bloc, "lot")
        logos = marques.lister(CTX.dossier)
        if not logos:
            st.caption("Aucun logo de marque pour l'instant.")
        else:
            cols_logos = st.columns(4)
            for i, m in enumerate(logos):
                with cols_logos[i % 4]:
                    with st.container(border=True):
                        img_m = marques.charger(CTX.dossier, m["cle"])
                        if img_m is not None:
                            st.image(img_m, use_container_width=True)
                        st.markdown(f"**{_md(m['nom'])}**")
                        st.button("Supprimer", key=f"marque_sup_{m['cle']}", on_click=supprimer_logo_marque,
                                  args=(m["cle"],), type="tertiary", use_container_width=True,
                                  help="Retirer ce logo : le nom de la marque est de nouveau écrit en texte")
        st.divider()
        st.button("Se déconnecter", on_click=_se_deconnecter)

        if CTX.admin:
            st.divider()
            habillage.sous_titre("Administration : ajouter une pharmacie ou changer un mot de passe")
            config = pharmacie.configuration()
            st.caption("Cet outil ne garde jamais les mots de passe eux-mêmes, seulement leur empreinte chiffrée. "
                       "Le bloc produit ci-dessous est à coller à la fin des secrets de l'application ; "
                       "l'application redémarre alors et déconnecte tout le monde : à faire hors des heures de pointe.")
            existante = st.selectbox("Pharmacie", [None] + list(config), key="adm_choix",
                                     format_func=lambda i: "Nouvelle pharmacie" if i is None else config[i]["nom"])
            if existante is None:
                nom_adm = st.text_input("Nom de la nouvelle pharmacie", key="adm_nom")
            else:
                nom_adm = config[existante]["nom"]
            if st.button("Préparer la fiche", disabled=not nom_adm.strip()):
                ident = existante or pharmacie.identifiant_depuis_nom(nom_adm, config)
                mdp = pharmacie.mot_de_passe_provisoire()
                ss.adm_fiche = {"ident": ident, "nom": nom_adm.strip(), "mdp": mdp,
                                "bloc": pharmacie.fiche_secrets(
                                    ident, nom_adm, mdp, racine=bool(existante and config[existante]["racine"]),
                                    admin=bool(existante and config[existante]["admin"]))}
            fiche = ss.get("adm_fiche")
            if fiche:
                st.write(f"**{fiche['nom']}** (identifiant « {fiche['ident']} »). Mot de passe à communiquer à la "
                         "pharmacie (affiché une seule fois ici) :")
                st.code(fiche["mdp"], language=None)
                st.write("À coller à la fin des secrets :" if fiche["ident"] not in config else
                         "À mettre à la place de l'ancien bloc de cette pharmacie dans les secrets :")
                st.code(fiche["bloc"], language="toml")
                st.button("Effacer ce mot de passe de l'écran", on_click=lambda: ss.pop("adm_fiche", None))

