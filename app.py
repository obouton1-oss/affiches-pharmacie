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
import images_produits as ip
import sauvegarde
import nettete
import nettoyage
from chemins import DONNEES, EN_LIGNE
from affiche import (ELEMENTS, FORMATS, POLICES, STYLE_DEFAUT, THEMES, apercu_png, disposition_a4,
                     libelle_dates, parse_prix, pdf_impression, rendu, reglages_defaut)

st.set_page_config(page_title="Affiches promo", page_icon="🏷️", layout="wide")
acces.verifier_acces()  # version en ligne : mot de passe commun (sans effet si aucun mot de passe n'est défini)


@st.cache_resource
def _demarrage():
    sauvegarde.restaurer()  # version en ligne : récupère le catalogue et le style sauvegardés
    return True


_demarrage()
st.title("Affiches promo – Pharmacie Bouton")

recherche_produit = components.declare_component(
    "recherche_produit", path=str(Path(__file__).parent / "composant_recherche"))

editeur_affiche = components.declare_component(
    "editeur_affiche", path=str(Path(__file__).parent / "composant_editeur"))

ss = st.session_state
for cle, defaut in (("image", None), ("marque", ""), ("detail", ""), ("journal", []), ("props", []),
                    ("props_msg", ""), ("code", ""), ("derniere_sel", None),
                    ("reglages", None), ("ver", 0), ("dernier_ev", None), ("element_actif", "marque")):
    ss.setdefault(cle, defaut)
if ss.reglages is None:
    ss.reglages = reglages_defaut()

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



def chercher_web():
    """Recherche de visuels type site marchand (fond blanc, haute résolution), classés."""
    nom_cherche = catalogue.nom_complet(ss.marque, ss.detail)
    requetes = [ss.code] + ([f"{nom_cherche} {ss.code}"] if nom_cherche else [])
    with st.spinner("Recherche d'un visuel type site marchand…"):
        ss.props, ss.props_msg = ip.propositions_web(requetes, nom=nom_cherche)


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


col_form, col_apercu = st.columns([1, 1])

with col_form:
    cat = catalogue.charger()
    sel = recherche_produit(catalogue=cat, en_ligne=True,
                            libelle="Produit (nom ou code CIP13 / EAN)",
                            key="recherche", default=None)

    # Nouvelle sélection dans la liste déroulante ou code saisi + Entrée
    if sel and sel.get("ts") != ss.derniere_sel:
        ss.derniere_sel = sel["ts"]
        ss.code = ip.nettoyer_code(sel["code"])
        ss.marque, ss.detail = catalogue.separer(sel.get("nom", ""), sel.get("marque", ""))
        ss.props, ss.props_msg = [], ""
        reinitialiser()
        with st.spinner("Recherche du visuel…"):
            img, marque_trouvee, detail_trouve, journal = ip.rechercher(ss.code)
        ss.image, ss.journal = img, journal
        if not ss.marque and not ss.detail:  # rien dans le catalogue : on prend la fiche en ligne
            ss.marque, ss.detail = marque_trouvee, detail_trouve
        elif not ss.marque and marque_trouvee:  # nom connu, marque inconnue : on la déduit de la fiche en ligne
            ss.marque, ss.detail = catalogue.separer(ss.detail, marque_trouvee)
        if img is None or not ip.analyser_image(img)["studio"]:
            chercher_web()  # un visuel absent ou de type « photo » est remplacé par une proposition studio

    code_net = ss.code

    # --- Visuel : recherche automatique, propositions web, import manuel
    if ss.journal:
        if ss.image is None:
            st.warning("Aucun visuel trouvé automatiquement.")
        with st.expander("Détail de la recherche"):
            for ligne in ss.journal:
                st.write("• " + ligne)

    if code_net:
        bt1, bt2 = st.columns(2)
        if bt1.button("Chercher un visuel type site marchand"):
            chercher_web()
        bt2.link_button("Ouvrir Google Images pour ce code",
                        f"https://www.google.com/search?tbm=isch&q={quote(code_net)}")
    if ss.props_msg:
        st.caption(ss.props_msg)
    if ss.props:
        st.write("Visuels proposés, classés du plus proche d'un visuel de site marchand au moins proche. "
                 "Vérifier que l'image correspond bien au produit, puis la choisir "
                 "(elle sera nettoyée automatiquement) :")
        cols = st.columns(4)
        for i, p in enumerate(ss.props):
            with cols[i % 4]:
                st.image(p["miniature"], use_container_width=True)
                dims = f"{p['largeur']}×{p['hauteur']} px" if p["largeur"] else "taille inconnue"
                etiquette = ("Fond blanc" if p["studio"] else "Photo") + (" · meilleur choix" if i == 0 and p["studio"] else "")
                st.caption(f"**{etiquette}**  \n{p['site']}  \n{dims}")
                if st.button("Choisir", key=f"choix{i}"):
                    try:
                        img = ip.rogner_marges_blanches(ip.telecharger_image(p["image"]))
                        ss.image = img
                        if code_net:
                            ip.enregistrer_image(code_net, img)
                        ss.props = []
                        st.rerun()
                    except Exception as e:
                        st.error(f"Téléchargement impossible ({type(e).__name__}).")

    televerse = st.file_uploader("Importer un visuel manuellement (glisser-déposer)",
                                 type=["png", "jpg", "jpeg", "webp"])
    if televerse is not None:
        img = ip.vers_rgb_blanc(Image.open(televerse))
        ss.image = img
        if code_net and st.checkbox("Mémoriser ce visuel pour ce code", value=True):
            ip.enregistrer_image(code_net, img)

    # --- Nettoyage du visuel
    nettoyer_on = st.checkbox("Nettoyer le visuel (supprimer le fond, ne garder que le produit)", value=True)
    with st.expander("Ajuster le recadrage (retirer un nom de site, un bord…)"):
        r1, r2 = st.columns(2)
        rg = r1.slider("Rogner à gauche (%)", 0, 40, 0)
        rd = r2.slider("Rogner à droite (%)", 0, 40, 0)
        rh = r1.slider("Rogner en haut (%)", 0, 40, 0)
        rb = r2.slider("Rogner en bas (%)", 0, 40, 0)
    # La netteté « haute qualité » exige beaucoup de mémoire : réservée à l'usage sur un poste (pas en ligne)
    choix_nettete = ["Désactivée", "Rapide"] + ([] if EN_LIGNE else ["Haute qualité (lent : jusqu'à 2 min)"])
    mode_nettete = st.radio("Netteté des visuels de faible résolution", choix_nettete, index=1,
                            horizontal=True, key="nettete_mode")
    if ss.image is not None:
        with st.spinner("Préparation du visuel… (la 1re fois, les modèles de détourage et de netteté se téléchargent)"):
            visuel, methode = visuel_final(ss.image, nettoyer_on, (rg, rd, rh, rb), mode_nettete)
        v1, v2 = st.columns(2)
        v1.image(ss.image, caption=f"Original ({min(ss.image.size)} px)", width=150)
        v2.image(visuel, caption=f"{methode} ({min(visuel.size)} px)", width=150)
        qualite = ip.analyser_image(ss.image)
        if min(visuel.size) < 600:
            st.warning(f"Résolution faible ({min(visuel.size)} px) : l'image risque d'être floue à l'impression. "
                       "Activer la netteté ou choisir un visuel plus grand.")
        elif not qualite["studio"]:
            st.caption("Visuel de type photo : essayer « Chercher un visuel type site marchand ».")
    else:
        visuel = None

    marque = st.text_input("Marque (affichée en gros, sous la photo)", value=ss.marque)
    detail = st.text_area("Détail du produit (affiché plus petit sous la marque ; Entrée = passage à la ligne)",
                          value=ss.detail, height=80)
    ss.marque, ss.detail = marque, detail
    majuscules = st.checkbox("Marque en majuscules", value=True)
    nom = catalogue.nom_complet(marque, detail)

    c1, c2 = st.columns(2)
    prix_txt = c1.text_input("Prix promo (€)", placeholder="7,90")
    barre = c2.checkbox("Afficher un prix barré")
    prix_barre_txt = c2.text_input("Prix barré (€)", placeholder="10,50") if barre else ""

    avec_dates = st.checkbox("Afficher une plage de dates")
    debut = fin = None
    if avec_dates:
        d1, d2 = st.columns(2)
        debut = d1.date_input("Du", value=date.today(), format="DD/MM/YYYY")
        fin = d2.date_input("Au", value=date.today(), format="DD/MM/YYYY")

    f1, f2 = st.columns(2)
    choix = f1.selectbox("Format", list(FORMATS) + ["Personnalisé"], index=1)
    if choix == "Personnalisé":
        lg = f2.number_input("Largeur (mm)", 50, 600, 100)
        ht = f2.number_input("Hauteur (mm)", 50, 900, 150)
        taille = (lg * mm, ht * mm)
    else:
        taille = FORMATS[choix]
    logo = st.checkbox("Afficher le logo", value=True)

    with st.expander("Police et couleurs"):
        st.selectbox("Police", POLICES, index=POLICES.index(ss.style["police"]), key="w_police",
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

    # --- Impression
    st.markdown("**Impression**")
    i1, i2 = st.columns(2)
    exemplaires = i1.number_input("Nombre d'affiches à imprimer", 1, 500, 1)
    par_feuille = disposition_a4(taille)[0]
    planche = False
    if par_feuille > 1:
        planche = i2.checkbox(f"{par_feuille} affiches par feuille A4", value=True)

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
                st.caption("Sauvegarde en ligne du catalogue et du style : activée.")

prix = parse_prix(prix_txt)
prix_barre = parse_prix(prix_barre_txt) if barre else None
erreurs = []
if prix_txt and prix is None:
    erreurs.append("Prix promo invalide.")
if barre and prix_barre_txt and prix_barre is None:
    erreurs.append("Prix barré invalide.")
if prix is not None and prix_barre is not None and prix_barre <= prix:
    erreurs.append("Le prix barré doit être supérieur au prix promo.")
if avec_dates and debut and fin and fin < debut:
    erreurs.append("La date de fin précède la date de début.")

with col_apercu:
    for e in erreurs:
        st.error(e)
    if prix is None or not nom:
        st.info("Renseigner la marque (ou le nom du produit) et le prix promo pour afficher l'aperçu.")
    elif not erreurs:
        if visuel is None:
            st.warning("Aucun visuel : l'affiche sera générée sans image.")
        dates_txt = libelle_dates(debut, fin) if avec_dates else ""
        unitaire, cadres = rendu(taille, marque, detail, prix, prix_barre, dates_txt, visuel, logo,
                                 reglages=ss.reglages, majuscules=majuscules, style=ss.style)
        largeur_px = 900
        png = apercu_png(unitaire, dpi=int(round(largeur_px * 72 / taille[0])))
        ev = editeur_affiche(image="data:image/png;base64," + base64.b64encode(png).decode(),
                             cadres=cadres, noms=ELEMENTS, ratio=taille[1] / taille[0],
                             actif=ss.element_actif, key="editeur", default=None)
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

        # Réglages précis de l'élément sélectionné
        with st.expander("Réglages précis", expanded=False):
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

        final, feuilles, par = pdf_impression(unitaire, taille, exemplaires, planche)
        fichier = re.sub(r"[^A-Za-z0-9_-]+", "_", nom)[:40] or "affiche"
        if st.download_button("Télécharger le PDF à imprimer", final,
                              f"affiche_{fichier}_{choix}_x{exemplaires}.pdf",
                              "application/pdf", type="primary"):
            if code_net:
                catalogue.enregistrer(code_net, marque, detail)  # proposé directement la fois suivante, avec sa marque
                sauvegarde.planifier("catalogue_appris.csv")
        with st.expander("Feuille d'impression"):
            st.image(apercu_png(final), use_container_width=True)
            st.caption(f"{exemplaires} affiche(s) → {feuilles} "
                       f"{'feuille(s) A4' if par > 1 else f'page(s) {choix}'}"
                       f"{f', {par} par feuille' if par > 1 else ''} (1re feuille affichée).")
