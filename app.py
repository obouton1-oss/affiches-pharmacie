"""Générateur d'affiches promo – Pharmacie Bouton.
Lancement :  streamlit run app.py
"""
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote

import base64
import json

import streamlit as st
import streamlit.components.v1 as components
from PIL import Image
from reportlab.lib.units import mm

import acces
import catalogue
import habillage
import historique
import images_produits as ip
import planche
import promos
import sauvegarde
import nettete
import nettoyage
from chemins import DONNEES, EN_LIGNE
from affiche import (ELEMENTS, FORMATS, MAX_VISUELS, POLICES, STYLE_DEFAUT, THEMES, apercu_png, disposition_a4,
                     libelle_dates, orienter, parse_prix, pdf_impression, rendu, reglages_defaut)

st.set_page_config(page_title="Affiches promo", page_icon="🏷️", layout="wide")
habillage.appliquer()  # feuille de style (aussi pour la page de mot de passe)
acces.verifier_acces()  # version en ligne : mot de passe commun (sans effet si aucun mot de passe n'est défini)


@st.cache_resource
def _demarrage():
    sauvegarde.restaurer()  # version en ligne : récupère le catalogue, le style et l'historique sauvegardés
    historique.purger()  # supprime les affiches de plus de 3 mois (et leurs visuels)
    nettoyage.vider_ancien_cache()  # anciennes versions : images nettoyées gardées sur le disque, inutiles
    nettete.vider_ancien_cache()
    return True


_demarrage()
habillage.entete("Affiches promo", "Pharmacie Bouton")

ONGLET_CREER, ONGLET_HISTORIQUE, ONGLET_PAGE = "Créer une affiche", "Historique", "Page A4 regroupée"
try:  # versions récentes de Streamlit : l'onglet affiché peut être changé par l'application
    onglet_creer, onglet_hist, onglet_page = st.tabs([ONGLET_CREER, ONGLET_HISTORIQUE, ONGLET_PAGE],
                                                     key="onglet", on_change="rerun")
    ONGLETS_PILOTABLES = True
except TypeError:
    onglet_creer, onglet_hist, onglet_page = st.tabs([ONGLET_CREER, ONGLET_HISTORIQUE, ONGLET_PAGE])
    ONGLETS_PILOTABLES = False

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
                    ("w_debut", date.today()), ("w_fin", date.today()), ("w_format", "A5"),
                    ("w_paysage", False), ("w_lg", 100), ("w_ht", 150), ("w_logo", True), ("w_majuscules", True),
                    ("w_nettoyer", True), ("w_rg", 0), ("w_rd", 0), ("w_rh", 0), ("w_rb", 0),
                    ("nettete_mode", "Rapide"), ("titre_page", planche.TITRE_DEFAUT), ("w_logo_page", True),
                    ("orientation_page", planche.PORTRAIT),
                    ("filtre_hist", ""),
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

FICHIER_STYLE = DONNEES / "style.json"
SEUIL_NETTETE = 700  # en dessous (côté le plus court, en pixels), la netteté peut être améliorée


def charger_style():
    st_ = dict(STYLE_DEFAUT)
    try:
        sauve = json.loads(FICHIER_STYLE.read_text(encoding="utf-8"))
        if "fond_prix" in sauve:  # les anciens fichiers (sans bandeau de prix) reprennent le nouveau style par défaut
            st_.update({k: v for k, v in sauve.items() if k in st_})
        elif sauve.get("police") in POLICES:
            st_["police"] = sauve["police"]
    except Exception:
        pass
    if st_["police"] not in POLICES:
        st_["police"] = STYLE_DEFAUT["police"]
    return st_


def sauver_style():
    try:
        FICHIER_STYLE.write_text(json.dumps(ss.style, ensure_ascii=False, indent=1), encoding="utf-8")
        sauvegarde.planifier("style.json")
    except Exception:
        pass


if "style" not in ss:
    ss.style = charger_style()
ss.setdefault("w_police", ss.style["police"])


def maj_style(cle, cle_widget):
    ss.style[cle] = ss[cle_widget]
    sauver_style()


def appliquer_theme():
    theme = THEMES.get(ss.theme_choisi)
    if theme:
        ss.style.update(theme)
        ss.ver += 1  # recrée les sélecteurs de couleur avec les nouvelles valeurs
        sauver_style()


def maj_slider(el, champ):
    v = ss[f"sl_{champ}_{el}_{ss.ver}"]
    ss.reglages[el][champ] = v / 100


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
                        promo=None, autres_visuels=(), paysage=False):
    """Enregistre l'affiche affichée à l'écran dans l'historique. Retourne son identifiant.
    promo : autre type de promotion (type, champs, options) ; prix = prix affiché, s'il y en a un.
    autres_visuels : les visuels suivants de la gamme (après le visuel principal).
    paysage : affiche en paysage (formats A4, A5, A6 ; un format personnalisé garde ses dimensions)."""
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
    ss.reglages = reglages_defaut()
    for el, g in (e.get("reglages") or {}).items():
        if el in ss.reglages:
            ss.reglages[el].update({k: float(v) for k, v in g.items() if k in ("dx", "dy", "s")})
    ss.style = dict(STYLE_DEFAUT)
    ss.style.update({k: v for k, v in (e.get("style") or {}).items() if k in STYLE_DEFAUT})
    if ss.style["police"] not in POLICES:
        ss.style["police"] = STYLE_DEFAUT["police"]
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
    ss.w_nettoyer, ss.nettete_mode, ss.traitement_desactive = False, "Désactivée", True
    ss.w_rg = ss.w_rd = ss.w_rh = ss.w_rb = 0
    ss.msg_ouvert = ("Affiche rouverte : modifier ce qui doit l'être, puis télécharger le PDF ou "
                     "l'enregistrer à nouveau.")
    if ONGLETS_PILOTABLES:
        ss.onglet = ONGLET_CREER
    else:
        ss.msg_hist = "Affiche rouverte : elle est prête dans l'onglet « Créer une affiche »."


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


def nouvelle_affiche():
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
    ss.ver += 1
    ss.msg_ouvert = ("Nouvelle affiche : le formulaire est vide. Le format, la police, les couleurs et le logo "
                     "sont conservés.")


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



def chercher_web(extra=()):
    """Recherche du visuel sur le web, de la plus fiable à la moins fiable : photos de pages de sites marchands qui
    contiennent le code (confirmées), puis pages au nom correspondant (probables), puis images non vérifiées.
    extra : mots saisis par l'utilisateur pour affiner la recherche."""
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
        ss.props_msg = ("Aucune page de site marchand contenant ce code n'a été trouvée : les images proposées ne sont "
                        "pas vérifiées, contrôler qu'elles montrent bien le produit." if res["autres"]
                        else "Aucune proposition trouvée : coller une image copiée, ou importer un fichier.")
    if res["nom"] and not ss.marque and not ss.detail:  # produit inconnu du catalogue : nom relevé sur les sites
        ss.marque, ss.detail = catalogue.separer(res["nom"], res["marque"])
        ss.info_nom = catalogue.nom_complet(ss.marque, ss.detail)


def legende_proposition(p):
    """Légende d'une proposition de visuel : fiabilité (code confirmé, probable, non vérifié), fond, site, taille."""
    niveau = p.get("niveau", "non vérifié")
    if niveau == "fort" or niveau == "moyen":
        n = p.get("nb_sites", 1)
        statut = "Code confirmé" + (f" · {n} sites" if n > 1 else "")
    elif niveau == "nom":
        statut = "Probable (code non retrouvé)"
    else:
        statut = "Non vérifié"
    dims = f"{p['largeur']}×{p['hauteur']} px" if p.get("largeur") else "taille inconnue"
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
                msg2 = "" if res2["verifies"] else ("images non vérifiées : contrôler qu'elles montrent bien le produit"
                                                    if res2["autres"] else "")
                if nom2 == code2 and res2["nom"]:  # produit inconnu : nom relevé sur les sites marchands
                    nom2 = catalogue.nom_complet(*catalogue.separer(res2["nom"], res2["marque"]))
                ss.cand_extra = {"code": code2, "nom": nom2, "image": img2, "props": props2, "msg": msg2}
            if ss.cand_extra:
                proposer_choix_extra(ss.cand_extra)
            habillage.sous_titre("Coller une image copiée")
            st.caption("Clic droit sur l'image (Google Images, site…) › « Copier l'image », puis cliquer dans le cadre et coller.")
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


RESERVE_APERCU = 270  # hauteur (px) occupée autour de l'affiche dans la colonne d'aperçu : marges, aide, boutons


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


with onglet_creer:
    if ss.get("msg_ouvert"):
        st.toast(ss.pop("msg_ouvert"), icon=":material/check_circle:")
    col_form, col_apercu = st.columns([3, 2], gap="large")

with col_form:
    cat = catalogue.charger()

    # ------------------------------------------------------------------ Étape 1 : produit, visuel, textes
    with st.container(key="etape_1"):
        c_titre, c_raz = st.columns([5, 3], vertical_alignment="center")
        with c_titre:
            habillage.titre_etape(1, "Produit", "Chercher le produit, vérifier son visuel et ses textes.")
        c_raz.button("Nouvelle affiche", key="raz_demander", on_click=demander_raz, icon=":material/restart_alt:",
                     use_container_width=True, disabled=ss.raz_attente,
                     help="Effacer la saisie en cours pour repartir de zéro (par exemple après une erreur)")
        if ss.raz_attente:
            with st.container(border=True):
                st.warning("Effacer l'affiche en cours (produit, visuels, prix, promotion, dates, éléments déplacés) ? "
                           "Si elle n'a pas été enregistrée dans l'historique, elle sera perdue. Le format, la police, "
                           "les couleurs et le logo sont conservés.")
                r1_, r2_ = st.columns(2)
                r1_.button("Oui, effacer", key="raz_confirmer", type="primary", on_click=nouvelle_affiche,
                           use_container_width=True)
                r2_.button("Annuler", key="raz_annuler", on_click=annuler_raz, use_container_width=True)
        sel = recherche_produit(catalogue=cat, en_ligne=True,
                                libelle="Produit (nom ou code CIP13 / EAN)",
                                key=f"recherche_{ss.raz}", default=None)

        # Nouvelle sélection dans la liste déroulante ou code saisi + Entrée
        if sel and sel.get("ts") != ss.derniere_sel:
            ss.derniere_sel = sel["ts"]
            ss.code = ip.nettoyer_code(sel["code"])
            ss.marque, ss.detail = catalogue.separer(sel.get("nom", ""), sel.get("marque", ""))
            ss.props, ss.props_msg, ss.props_choisie, ss.props_ouvertes = [], "", None, True
            reinitialiser()
            retablir_traitement()
            with st.spinner("Recherche du visuel…"):
                img, marque_trouvee, detail_trouve, journal = ip.rechercher(ss.code)
            ss.image, ss.journal = img, journal
            if not ss.marque and not ss.detail:  # rien dans le catalogue : on prend la fiche en ligne
                ss.marque, ss.detail = marque_trouvee, detail_trouve
            elif not ss.marque and marque_trouvee:  # nom connu, marque inconnue : on la déduit de la fiche en ligne
                ss.marque, ss.detail = catalogue.separer(ss.detail, marque_trouvee)
            if img is not None and img.info.get("origine") == "historique":
                # visuel d'une affiche enregistrée : déjà nettoyé, imprimé tel quel (comme après « Rouvrir »)
                ss.w_nettoyer, ss.nettete_mode, ss.traitement_desactive = False, "Désactivée", True
            elif img is None or not ip.analyser_image(img)["studio"]:
                chercher_web()  # un visuel absent ou de type « photo » est remplacé par une proposition studio

        if ss.pop("traitement_a_retablir", False):  # après « Rouvrir », un nouveau visuel est nettoyé comme d'habitude
            retablir_traitement()

        code_net = ss.code

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

        with st.expander("Autre visuel : chercher, coller ou importer une image", expanded=ss.image is None):
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
            if ss.props_msg:
                st.caption(ss.props_msg)
            habillage.sous_titre("Coller une image copiée")
            st.caption("Sur Google Images ou sur un site : clic droit sur l'image › « Copier l'image ». "
                       "Revenir ici, cliquer dans le cadre, puis coller.")
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
                    if ss.props_autres and st.button("Voir aussi des images non vérifiées", key="voir_autres"):
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
        with st.expander("Réglages du visuel : recadrage, netteté"):
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
                if min(visuel.size) < 600:
                    st.warning(f"Image de petite taille ({min(visuel.size)} px) : elle risque d'être floue à "
                               "l'impression. Essayer la netteté « Rapide » dans les réglages du visuel, ou choisir "
                               "une image plus grande.")
        else:
            visuel = None
        autres_visuels = zone_autres_images(cat, nettoyer_on, mode_nettete) if ss.image is not None else []

        # --- Textes de l'affiche
        habillage.sous_titre("Textes de l'affiche")
        marque = st.text_input("Marque (affichée en gros, sous la photo)", value=ss.marque)
        detail = st.text_area("Détail du produit (affiché plus petit sous la marque ; Entrée = passage à la ligne)",
                              value=ss.detail, height=80)
        ss.marque, ss.detail = marque, detail
        majuscules = st.checkbox("Marque en majuscules", key="w_majuscules")
        nom = catalogue.nom_complet(marque, detail)

    # ------------------------------------------------------------------ Étape 2 : offre
    with st.container(key="etape_2"):
        habillage.titre_etape(2, "Offre", "Le prix promo en gros, ou une autre offre (pourcentage, 2e produit, lot…).")
        type_promo = st.selectbox("Type de promotion", list(promos.TYPES), key="w_promo_type",
                                  format_func=lambda k: promos.TYPES[k]["libelle"], on_change=appliquer_defauts_promo,
                                  help="Par défaut : le prix promo en gros. Les autres types affichent l'offre elle-même "
                                       "(pourcentage, montant, 2e produit, lot, produit offert…).")
        resultat_promo, promo_enregistree = None, None
        if type_promo == promos.STANDARD:
            c1, c2 = st.columns(2, vertical_alignment="bottom")
            prix_txt = c1.text_input("Prix promo (€)", placeholder="7,90", key="w_prix")
            barre = c2.checkbox("Afficher un prix barré", key="w_barre_on")
            prix_barre_txt = c2.text_input("Prix barré (€)", placeholder="10,50", key="w_barre") if barre else ""
        else:
            prix_txt, barre, prix_barre_txt = "", False, ""
            resultat_promo, promo_enregistree = formulaire_promo(type_promo)

        avec_dates = st.checkbox("Afficher une plage de dates", key="w_dates_on")
        debut = fin = None
        if avec_dates:
            d1, d2 = st.columns(2)
            debut = d1.date_input("Du", format="DD/MM/YYYY", key="w_debut")
            fin = d2.date_input("Au", format="DD/MM/YYYY", key="w_fin")

    # ------------------------------------------------------------------ Étape 3 : mise en page
    with st.container(key="etape_3"):
        habillage.titre_etape(3, "Mise en page", "Format, logo, police et couleurs. Les éléments se déplacent "
                                                 "aussi sur l'aperçu.")
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
        logo = st.checkbox("Afficher le logo", key="w_logo")

        with st.expander("Police et couleurs"):
            st.selectbox("Police", POLICES, key="w_police",
                         on_change=maj_style, args=("police", "w_police"))
            st.selectbox("Thème de couleurs", ["— choisir un thème —"] + list(THEMES), key="theme_choisi",
                         on_change=appliquer_theme)
            cwf = f"cb_fond_{ss.ver}"
            st.checkbox("Prix sur fond coloré (bandeau)", ss.style["fond_prix"], key=cwf,
                        on_change=maj_style, args=("fond_prix", cwf))
            p1, p2 = st.columns(2)
            for cle, libelle, colonne in (("couleur_nom", "Couleur de la marque et du détail", p1),
                                          ("couleur_prix", "Couleur du prix", p2),
                                          ("couleur_fond_prix", "Couleur du fond du prix", p1),
                                          ("couleur_accent", "Filet sous le prix (sans fond)", p2),
                                          ("couleur_secondaire", "Dates et prix barré", p1)):
                cw = f"cp_{cle}_{ss.ver}"
                colonne.color_picker(libelle, ss.style[cle], key=cw, on_change=maj_style, args=(cle, cw))
            st.caption("Les choix de police et de couleurs sont mémorisés pour les prochaines affiches.")

        slot_reglages = st.container()  # « Réglages précis » (rempli quand l'aperçu est affiché)

    # ------------------------------------------------------------------ Étape 4 : impression
    with st.container(key="etape_4"):
        habillage.titre_etape(4, "Impression", "Le PDF se télécharge depuis l'aperçu, à droite.")
        i1, i2 = st.columns(2, vertical_alignment="bottom")
        exemplaires = i1.number_input("Nombre d'affiches à imprimer", 1, 500, key="w_exemplaires")
        par_feuille = disposition_a4(taille)[0]
        en_planche = False
        if par_feuille > 1:
            en_planche = i2.checkbox(f"{par_feuille} affiches par feuille A4", value=True)
        slot_feuille = st.container()  # « Feuille d'impression » (remplie quand l'aperçu est affiché)

    with st.expander(f"Catalogue produits ({len(cat)} produit(s))"):
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
        if catalogue.FICHIER_APPRIS.exists():
            st.download_button("Télécharger les produits mémorisés (copie de sauvegarde)",
                               catalogue.FICHIER_APPRIS.read_bytes(), "catalogue_appris.csv", "text/csv")
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
            if visuel is None:
                st.warning("Aucun visuel : l'affiche sera générée sans image.")
            dates_txt = libelle_dates(debut, fin) if avec_dates else ""
            unitaire, cadres = rendu(taille, marque, detail, prix, prix_barre, dates_txt, [visuel] + autres_visuels, logo,
                                     reglages=ss.reglages, majuscules=majuscules, style=ss.style, promo=rendu_promo)
            largeur_px = 900
            png = apercu_png(unitaire, dpi=int(round(largeur_px * 72 / taille[0])))
            ev = editeur_affiche(image="data:image/png;base64," + base64.b64encode(png).decode(),
                                 cadres=cadres, noms=ELEMENTS, ratio=taille[1] / taille[0],
                                 actif=ss.element_actif, reserve=RESERVE_APERCU, key="editeur", default=None)
            if ev and ev.get("ts") != ss.dernier_ev:
                ss.dernier_ev = ev["ts"]
                el = ev.get("el")
                if el in ELEMENTS:
                    ss.element_actif = el
                    if not ev.get("clic"):
                        g = ss.reglages[el]
                        g["dx"] += float(ev.get("ddx", 0))
                        g["dy"] -= float(ev.get("ddy", 0))
                        g["s"] = min(4.0, max(0.2, g["s"] * float(ev.get("ds", 1))))
                        ss.ver += 1
                    st.rerun()

            def memoriser_affiche():
                """Mémorise le produit (proposé directement la fois suivante, avec sa marque) et enregistre l'affiche
                dans l'historique. Retourne l'identifiant de l'affiche."""
                if code_net:
                    catalogue.enregistrer(code_net, marque, detail)
                    sauvegarde.planifier("catalogue_appris.csv")
                return enregistrer_affiche(code_net, marque, detail, prix, prix_barre, debut, fin, choix, visuel, png,
                                           promo_enregistree, autres_visuels, paysage)

            final, feuilles, par = pdf_impression(unitaire, taille, exemplaires, en_planche)
            fichier = re.sub(r"[^A-Za-z0-9_-]+", "_", nom)[:40] or "affiche"
            if st.download_button("Télécharger le PDF à imprimer", final,
                                  f"affiche_{fichier}_{format_txt.replace(' ', '_')}_x{exemplaires}.pdf",
                                  "application/pdf", type="primary", use_container_width=True,
                                  icon=":material/download:"):
                memoriser_affiche()  # l'affiche téléchargée est aussi conservée dans l'historique
                st.toast("Affiche enregistrée dans l'historique.", icon=":material/check_circle:")
            h1, h2 = st.columns(2)
            if h1.button("Enregistrer dans l'historique", use_container_width=True):
                memoriser_affiche()
                st.toast("Affiche enregistrée dans l'historique.", icon=":material/check_circle:")
            if h2.button("Enregistrer et ajouter à la page A4 regroupée", use_container_width=True):
                ajouter_regroupe(memoriser_affiche())
                st.toast("Affiche enregistrée et ajoutée à la page A4 regroupée (onglet « Page A4 regroupée »).",
                         icon=":material/check_circle:")
            apercu_affiche = True

# Réglages qui dépendent de l'aperçu : placés dans le formulaire (étapes 3 et 4), à côté de l'aperçu qui reste visible
with slot_reglages:
    if apercu_affiche:
        with st.expander("Réglages précis", expanded=False):
            st.caption("Pour déplacer ou agrandir un élément, le plus simple est de le faire glisser sur l'aperçu. "
                       "Ces réglages servent aux ajustements fins.")
            dispo = [e for e in ELEMENTS if e in cadres]
            if ss.element_actif not in dispo:
                ss.element_actif = dispo[0]
            el = st.radio("Élément", dispo, format_func=lambda e: ELEMENTS[e], horizontal=True,
                          key="element_actif")
            g = ss.reglages[el]
            st.slider("Taille (%)", 20, 400, int(round(g["s"] * 100)), key=f"sl_s_{el}_{ss.ver}",
                      on_change=maj_slider, args=(el, "s"))
            st.slider("Position horizontale (← →, %)", -50, 50, int(round(g["dx"] * 100)),
                      key=f"sl_dx_{el}_{ss.ver}", on_change=maj_slider, args=(el, "dx"))
            st.slider("Position verticale (↓ ↑, %)", -50, 50, int(round(g["dy"] * 100)),
                      key=f"sl_dy_{el}_{ss.ver}", on_change=maj_slider, args=(el, "dy"))
            b1, b2 = st.columns(2)
            b1.button("Réinitialiser cet élément", on_click=reinitialiser, args=(el,))
            b2.button("Tout réinitialiser", on_click=reinitialiser)
    else:
        st.caption("Les réglages de position apparaissent dès que l'aperçu est affiché.")

with slot_feuille:
    if apercu_affiche:
        with st.expander("Feuille d'impression"):
            st.image(apercu_png(final), use_container_width=True)
            st.caption(f"{exemplaires} affiche(s) → {feuilles} "
                       f"{'feuille(s) A4' if par > 1 else f'page(s) {format_txt}'}"
                       f"{f', {par} par feuille' if par > 1 else ''} (1re feuille affichée).")


# ----------------------------------------------------------------------------
# Onglet « Historique » : retrouver, rouvrir, supprimer, choisir les affiches à regrouper
# ----------------------------------------------------------------------------
MAX_AFFICHES_VISIBLES = 48


def _md(texte):
    """Neutralise les caractères de mise en forme dans un texte affiché."""
    for c in ("\\", "*", "_", "`", "[", "]", "$", "#", "<", ">", "~"):
        texte = texte.replace(c, "\\" + c)
    return texte


def _format_entree(e):
    """Format de l'affiche enregistrée, avec l'orientation si elle est en paysage (« A5 paysage »)."""
    format_ = e.get("format", "")
    return f"{format_} paysage" if format_ and e.get("orientation") == "Paysage" else format_


def _titre_entree(e):
    return (e.get("marque") or e.get("detail") or "Sans nom").strip().replace("\n", " ")


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
        recherche = c_filtre.text_input("Rechercher une affiche (marque, produit ou code)", key="filtre_hist")
        n_aff = len(entrees)
        c_info.caption(f"{n_aff} affiche{'s' if n_aff > 1 else ''} enregistrée{'s' if n_aff > 1 else ''}, "
                       f"conservée{'s' if n_aff > 1 else ''} {historique.DUREE_JOURS // 30} mois environ, "
                       "puis supprimée" + ("s" if n_aff > 1 else "") + " automatiquement.")
        mot = catalogue._norm_mot(recherche)
        visibles = [e for e in entrees
                    if not mot or mot in catalogue._norm_mot(f"{e.get('marque', '')} {e.get('detail', '')} {e.get('code', '')}")]
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
                    st.caption(f"{_md(detail_court)}  \n{_md(prix_aff)} · {_format_entree(e)}{gamme} · créée le "
                               f"{historique.date_creation(e)}")
                    b_ouvrir, b_page = st.columns(2)
                    b_ouvrir.button("Rouvrir", key=f"ouv_{ident}", on_click=rouvrir, args=(ident,),
                                    use_container_width=True, help="Recharger cette affiche pour la modifier ou la réimprimer")
                    if ident in ss.regroupe:
                        b_page.button("Dans la page ✓", key=f"ret_{ident}", on_click=retirer_regroupe, args=(ident,),
                                      use_container_width=True, help="Déjà dans la page A4 regroupée : cliquer pour la retirer")
                    else:
                        b_page.button("+ Page A4", key=f"aj_{ident}", on_click=ajouter_regroupe, args=(ident,),
                                      use_container_width=True, help="Ajouter à la page A4 regroupée")
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
                   json.dumps(ss.style, sort_keys=True), bool(ss.w_logo_page), ss.orientation_page)
            if ss.planche_cache is None or ss.planche_cache[0] != cle:
                groupes, debut_lot = [], 0
                for taille_lot in tailles:
                    groupes.append([{**e, "_images": historique.visuels(e["id"])}
                                    for e in choisies[debut_lot:debut_lot + taille_lot]])
                    debut_lot += taille_lot
                with st.spinner("Mise en page…"):
                    pdf_page = planche.pdf_planche(titre_page, groupes, ss.style, bool(ss.w_logo_page),
                                                   ss.orientation_page)
                    ss.planche_cache = (cle, pdf_page, planche.apercus_png(pdf_page, 80))
            _, pdf_page, apercus = ss.planche_cache
            nom_fichier = re.sub(r"[^A-Za-z0-9_-]+", "_", titre_page)[:40].strip("_") or "page"
            st.download_button("Télécharger la page A4 (PDF)", pdf_page, f"promos_{nom_fichier}.pdf",
                               "application/pdf", type="primary", icon=":material/download:")
    with col_d:
        for i, png_page in enumerate(apercus):
            st.image(png_page, caption=f"Feuille {i + 1} sur {len(apercus)}", use_container_width=True)
