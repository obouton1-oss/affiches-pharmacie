"""Mise en route d'une pharmacie : quelques questions pour régler son affiche type, avec aperçu en direct.

Affichée une seule fois, à la première connexion d'une nouvelle pharmacie (« premiere_fois »), puis disponible à tout moment
dans l'onglet « Ma pharmacie ». Les réponses sont enregistrées dans le dossier de la pharmacie :
  - style.json : police, couleurs, bandeau du prix, ordre des éléments (utilisés pour toutes ses affiches) ;
  - preferences.json : nom affiché, format, orientation, majuscules, logo (valeurs de départ des nouvelles affiches).
Le logo est enregistré dès son import.
"""
import json
from decimal import Decimal

import streamlit as st
from PIL import Image, ImageDraw

import affiche as af
import habillage
import pharmacie
import preferences
import sauvegarde

TAILLE_MAX_VISUEL = 8_000_000  # octets
CLES_STYLE = {"mer_c_nom": "couleur_nom", "mer_c_prix": "couleur_prix", "mer_c_fond": "couleur_fond_prix",
              "mer_c_accent": "couleur_accent"}


# ----------------------------------------------------------------------------
# État du formulaire
# ----------------------------------------------------------------------------
def _initialiser(ctx, style: dict, prefs: dict) -> None:
    ss = st.session_state
    ss.mer_ordre = list(af.ordre_valide(style.get("ordre")))
    ss.mer_fond = "Sur fond coloré" if style.get("fond_prix") else "Sans fond (prix souligné)"
    for cle, champ in CLES_STYLE.items():
        ss[cle] = style[champ]
    ss.mer_police = style["police"]
    ss.mer_nom = prefs["nom"] or ctx.nom
    ss.mer_format, ss.mer_paysage = prefs["format"], prefs["paysage"]
    ss.mer_majuscules, ss.mer_logo = prefs["majuscules"], prefs["logo"]
    ss.mer_sync = json.dumps(style, sort_keys=True)


def _brouillon_style() -> dict:
    """Style correspondant aux réponses en cours (sert à l'aperçu et à l'enregistrement)."""
    ss = st.session_state
    st_ = dict(af.STYLE_DEFAUT)
    for cle, champ in CLES_STYLE.items():
        st_[champ] = ss[cle]
    st_["police"] = ss.mer_police
    st_["fond_prix"] = ss.mer_fond == "Sur fond coloré"
    if tuple(ss.mer_ordre) != af.ORDRE_DEFAUT:
        st_["ordre"] = list(ss.mer_ordre)
    return st_


# ----------------------------------------------------------------------------
# Actions (appelées avant le réaffichage)
# ----------------------------------------------------------------------------
def _choisir_ordre(ordre) -> None:
    st.session_state.mer_ordre = list(ordre)


def _deplacer(i: int, sens: int) -> None:
    ordre = st.session_state.mer_ordre
    j = i + sens
    if 0 <= i < len(ordre) and 0 <= j < len(ordre):
        ordre[i], ordre[j] = ordre[j], ordre[i]


def _appliquer_theme() -> None:
    theme = af.THEMES.get(st.session_state.mer_theme)
    if theme:
        ss = st.session_state
        ss.mer_c_nom, ss.mer_c_prix = theme["couleur_nom"], theme["couleur_prix"]
        ss.mer_c_accent = theme["couleur_accent"]
        ss.mer_fond = "Sur fond coloré" if theme["fond_prix"] else "Sans fond (prix souligné)"
        if theme.get("couleur_fond_prix"):
            ss.mer_c_fond = theme["couleur_fond_prix"]


def _appliquer_couleurs_visuel(proposition: dict) -> None:
    ss = st.session_state
    ss.mer_c_nom = proposition.get("couleur_nom", ss.mer_c_nom)
    ss.mer_c_accent = proposition.get("couleur_accent", ss.mer_c_accent)
    ss.mer_c_prix = proposition.get("couleur_prix", ss.mer_c_prix)
    ss.mer_fond = "Sur fond coloré" if proposition.get("fond_prix") else "Sans fond (prix souligné)"
    if proposition.get("couleur_fond_prix"):
        ss.mer_c_fond = proposition["couleur_fond_prix"]
    ss.mer_msg = "Couleurs du visuel appliquées : à vérifier sur l'aperçu."


def _retirer_logo() -> None:
    pharmacie.supprimer_logo()
    st.session_state.mer_logo_n += 1
    st.session_state.mer_msg = "Logo retiré."


# ----------------------------------------------------------------------------
# Aperçu
# ----------------------------------------------------------------------------
def _visuel_exemple() -> Image.Image:
    """Flacon gris : simple repère de place pour le visuel du produit."""
    img = Image.new("RGB", (420, 1000), "white")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((120, 20, 300, 140), radius=30, fill="#B9BEC6")
    d.rounded_rectangle((60, 120, 360, 980), radius=70, fill="#D5D9DF")
    d.rounded_rectangle((100, 420, 320, 600), radius=20, fill="#F4F5F7")
    return img


@st.cache_data(max_entries=80, show_spinner=False)
def _png(format_, paysage, nom, logo, date_logo, afficher_logo, majuscules, style_json, largeur_px):
    """Aperçu mis en mémoire : un changement qui ne concerne pas l'aperçu ne le redessine pas (date_logo : pour renouveler
    l'image quand le logo est remplacé)."""
    taille = af.orienter(af.FORMATS[format_], paysage)
    pdf, _ = af.rendu(taille, "Marque", "Nom du produit 40 ml", Decimal("7.90"), Decimal("10.50"),
                      "Du 1er au 31 octobre 2026", [_visuel_exemple()], afficher_logo, majuscules=majuscules,
                      style=json.loads(style_json), identite={"nom": nom, "logo": logo or None})
    return af.apercu_png(pdf, dpi=int(round(largeur_px * 72 / taille[0])))


def _apercu_png(ctx, style: dict, largeur_px: int = 380, ordre=None, paysage=None) -> bytes:
    ss = st.session_state
    if ordre is not None:
        style = {**style, "ordre": list(ordre)}
    chemin = pharmacie.logo(ctx)
    try:
        date_logo = chemin.stat().st_mtime_ns if chemin else 0
    except OSError:
        date_logo = 0
    return _png(ss.mer_format, bool(ss.mer_paysage if paysage is None else paysage), (ss.mer_nom or "").strip(),
                str(chemin or ""), date_logo, bool(ss.mer_logo), bool(ss.mer_majuscules),
                json.dumps(style, sort_keys=True), largeur_px)


# ----------------------------------------------------------------------------
# Écran
# ----------------------------------------------------------------------------
def afficher(ctx, premiere_fois: bool) -> None:
    """Affiche les questions. premiere_fois : écran de départ, qui remplace l'outil (st.stop à la fin)."""
    ss = st.session_state
    ss.setdefault("mer_logo_n", 0)
    prefs = preferences.charger(ctx.dossier)
    style_courant = ss.get("style") or preferences.charger_style(ctx.dossier)
    if ss.get("mer_sync") != json.dumps(style_courant, sort_keys=True) or "mer_ordre" not in ss:
        _initialiser(ctx, style_courant, prefs)  # première ouverture, ou style modifié ailleurs depuis (onglet « Créer »)

    if premiere_fois:
        st.subheader(f"Bienvenue, {pharmacie.nom_affiche(ctx)}")
        st.write("Quelques questions pour régler votre affiche type : elle s'affichera ensuite toujours ainsi, "
                 "et tout reste modifiable plus tard dans l'onglet « Ma pharmacie ». Cet écran n'apparaît qu'une fois.")
    else:
        st.subheader("Mes affiches : réglages")
        st.caption("L'aperçu à droite montre un produit d'exemple. Les couleurs, la police et l'ordre des éléments "
                   "s'appliquent à toutes les affiches dès l'enregistrement ; le format, l'orientation et les "
                   "habitudes de départ, à la prochaine ouverture de l'outil.")
    if ss.get("mer_msg"):
        st.success(ss.pop("mer_msg"))

    col_form, col_apercu = st.columns([3, 2], gap="large")
    with col_form:
        # ---- 1. Nom et logo
        habillage.titre_etape(1, "Nom et logo", "Imprimés en pied de l'affiche. Le logo est facultatif.")
        st.text_input("Nom affiché sur les affiches", key="mer_nom", max_chars=80)
        chemin_logo = pharmacie.logo(ctx)
        if chemin_logo:
            st.image(str(chemin_logo), width=120)
        else:
            st.caption("Aucun logo importé : seul le nom figure en pied d'affiche.")
        fichier_logo = st.file_uploader("Importer ou remplacer le logo (PNG ou JPG)", type=["png", "jpg", "jpeg"],
                                        key=f"mer_logo_import_{ss.mer_logo_n}")
        if fichier_logo is not None:
            ok, message = pharmacie.enregistrer_logo(fichier_logo.getvalue())
            if ok:
                ss.mer_logo_n += 1
                ss.mer_msg = message
                st.rerun()
            st.error(message)
        if (ctx.dossier / pharmacie.FICHIER_LOGO).exists():
            st.button("Retirer le logo importé", on_click=_retirer_logo, key="mer_retirer_logo")

        # ---- 2. Visuel existant
        habillage.titre_etape(2, "Un visuel de promo que vous utilisez déjà (facultatif)",
                              "Importer l'image d'une de vos affiches : l'outil en relève les couleurs principales et les "
                              "propose pour vos affiches. La mise en page de ce visuel n'est pas reproduite : "
                              "l'ordre des éléments se choisit à l'étape 3.")
        visuel = st.file_uploader("Image d'une affiche ou d'un visuel de votre pharmacie", type=["png", "jpg", "jpeg"],
                                  key="mer_visuel_existant")
        if visuel is not None:
            if visuel.size > TAILLE_MAX_VISUEL:
                st.error("Fichier trop volumineux (8 Mo au plus).")
            else:
                try:
                    pal = preferences.palette(Image.open(visuel))
                except Exception:
                    pal = []
                    st.error("Image illisible : importer un fichier PNG ou JPG.")
                if pal:
                    puces = "".join(f'<span style="display:inline-block;width:42px;height:42px;border-radius:8px;'
                                    f'margin:0 8px 8px 0;border:1px solid #D0D4DA;background:{h}" title="{h}"></span>'
                                    for h, _ in pal)
                    st.html(f"<div>Couleurs relevées :</div><div>{puces}</div>")
                    proposition = preferences.proposer_couleurs(pal)
                    st.button("Utiliser ces couleurs pour mes affiches", key="mer_utiliser_couleurs",
                              on_click=_appliquer_couleurs_visuel, args=(proposition,))
                else:
                    st.info("Aucune couleur marquée trouvée sur cette image (elle est presque entièrement blanche).")

        # ---- 3. Ordre des éléments
        habillage.titre_etape(3, "Ordre des éléments", "De haut en bas, sous la photo du produit.")
        st.caption("Choisir une disposition, ou régler l'ordre à la main plus bas.")
        base = _brouillon_style()
        colonnes = st.columns(len(preferences.ORDRES))
        for col, (nom, ordre) in zip(colonnes, preferences.ORDRES.items()):
            with col:
                st.image(_apercu_png(ctx, base, largeur_px=300, ordre=ordre, paysage=False), use_container_width=True)
                choisi = tuple(ss.mer_ordre) == tuple(ordre)
                st.button(nom + (" ✓" if choisi else ""), key=f"mer_ordre_{nom}", on_click=_choisir_ordre, args=(ordre,),
                          type="primary" if choisi else "secondary", use_container_width=True)
        with st.expander("Régler l'ordre à la main"):
            for i, el in enumerate(ss.mer_ordre):
                c_nom, c_haut, c_bas = st.columns([4, 1, 1], vertical_alignment="center")
                c_nom.write(f"{i + 1}. {af.LIBELLES_ORDRE[el]}")
                c_haut.button("↑", key=f"mer_h_{el}", on_click=_deplacer, args=(i, -1), disabled=i == 0, help="Monter")
                c_bas.button("↓", key=f"mer_b_{el}", on_click=_deplacer, args=(i, 1),
                             disabled=i == len(ss.mer_ordre) - 1, help="Descendre")
            st.button("Revenir à l'ordre classique", key="mer_ordre_classique", on_click=_choisir_ordre,
                      args=(af.ORDRE_DEFAUT,))

        # ---- 4. Prix promo
        habillage.titre_etape(4, "Prix promo", "Le prix mis en avant : sur un fond de couleur, ou sans fond.")
        st.radio("Présentation du prix", ["Sur fond coloré", "Sans fond (prix souligné)"], key="mer_fond",
                 horizontal=True, label_visibility="collapsed")
        p1, p2 = st.columns(2)
        p1.color_picker("Couleur du fond", key="mer_c_fond", disabled=ss.mer_fond != "Sur fond coloré")
        p2.color_picker("Couleur du prix", key="mer_c_prix")
        if ss.mer_fond != "Sur fond coloré":
            st.caption("Sans fond, un trait est tracé sous le prix, de la couleur d'accentuation (étape 5).")

        # ---- 5. Police et couleurs
        habillage.titre_etape(5, "Police et couleurs", "Partir d'un thème tout fait, puis ajuster si besoin.")
        t1, t2 = st.columns(2)
        t1.selectbox("Thème de couleurs", ["— choisir un thème —"] + list(af.THEMES), key="mer_theme",
                     on_change=_appliquer_theme)
        t2.selectbox("Police", af.POLICES, key="mer_police")
        k1, k2 = st.columns(2)
        k1.color_picker("Couleur de la marque et du produit", key="mer_c_nom")
        k2.color_picker("Couleur d'accentuation (traits, pastilles)", key="mer_c_accent")

        # ---- 6. Habitudes
        habillage.titre_etape(6, "Habitudes de départ", "Valeurs proposées d'office pour chaque nouvelle affiche.")
        h1, h2 = st.columns(2)
        h1.selectbox("Format", list(af.FORMATS), key="mer_format")
        h2.checkbox("Affiche en paysage (à l'horizontale)", key="mer_paysage")
        st.checkbox("Marque en majuscules", key="mer_majuscules")
        st.checkbox("Afficher le logo (ou le nom) en pied d'affiche", key="mer_logo")

        # ---- Enregistrement (exécuté avant le réaffichage : les réglages sont déjà pris en compte à l'écran suivant)
        st.write("")
        if ss.get("mer_erreur"):
            st.error(ss.pop("mer_erreur"))
        b1, b2 = st.columns([2, 3])
        b1.button("Enregistrer" + (" et commencer" if premiere_fois else ""), type="primary", key="mer_enregistrer",
                  use_container_width=True, on_click=_enregistrer, args=(ctx, premiere_fois, False))
        if premiere_fois:
            b2.button("Passer pour le moment", key="mer_passer", on_click=_enregistrer, args=(ctx, premiere_fois, True),
                      help="Les réglages habituels sont gardés ; tout se règle plus tard dans « Ma pharmacie ».")

    with col_apercu:
        with st.container(key="mer_apercu"):
            st.image(_apercu_png(ctx, _brouillon_style(), largeur_px=420), caption="Aperçu avec un produit d'exemple",
                     use_container_width=True)

    if premiere_fois:
        st.stop()


def _enregistrer(ctx, premiere_fois: bool, passer: bool) -> None:
    ss = st.session_state
    style = _brouillon_style()
    nom = (ss.mer_nom or "").strip()
    prefs = {"faite": True, "nom": "" if nom == ctx.nom else nom, "format": ss.mer_format,
             "paysage": bool(ss.mer_paysage), "majuscules": bool(ss.mer_majuscules), "logo": bool(ss.mer_logo)}
    if passer:  # on garde les réglages en place
        style = ss.get("style") or preferences.charger_style(ctx.dossier)
        prefs = {**preferences.charger(ctx.dossier), "faite": True}
    try:
        preferences.sauver_style(ctx.dossier, style)
        preferences.sauver(ctx.dossier, prefs)
        sauvegarde.planifier(preferences.FICHIER_STYLE)
        sauvegarde.planifier(preferences.FICHIER_PREFERENCES)
    except OSError:
        ss.mer_erreur = "Enregistrement impossible pour le moment : réessayer dans un instant."
        return
    ss.style = style
    ss.w_police = style["police"]
    ss.ver = ss.get("ver", 0) + 1  # les sélecteurs de couleur de l'outil reprennent les nouvelles valeurs
    ss.mer_sync = json.dumps(style, sort_keys=True)
    ss.planche_cache = None
    if premiere_fois:  # aucune affiche en cours : les habitudes s'appliquent tout de suite
        ss.w_format, ss.w_paysage = prefs["format"], prefs["paysage"] and prefs["format"] in af.FORMATS
        ss.w_majuscules, ss.w_logo = prefs["majuscules"], prefs["logo"]
    st.toast("Réglages enregistrés." if not passer else "Réglages habituels conservés.", icon=":material/check_circle:")
