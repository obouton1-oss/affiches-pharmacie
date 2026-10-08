"""Mise en route d'une pharmacie : quelques questions pour régler son affiche type, avec aperçu en direct.

Le même écran sert trois fois :
  - à la première connexion d'une nouvelle pharmacie (« premiere_fois ») : il remplace l'outil, une seule fois ;
  - dans l'onglet « Ma pharmacie » : on y règle à tout moment le type d'affiche choisi ;
  - à la création d'un nouveau type d'affiche (« nouveau_type ») : il remplace l'outil le temps de répondre aux questions,
    avec en plus le nom du type, le format personnalisé et le choix « avec ou sans photo ».
Les réponses sont enregistrées dans le dossier de la pharmacie :
  - style.json : police, couleurs, bandeau du prix, ordre des éléments ;
  - preferences.json : nom affiché, format, orientation, majuscules, logo ;
  - types_affiche.json : les types d'affiche (voir types_affiche.py).
Ces réglages sont ceux du « type d'affiche » : la première fois, ils forment le premier type, « Affiche standard » ; ensuite,
l'onglet « Ma pharmacie » règle le type d'affiche choisi dans l'onglet « Créer une affiche ».
Le nom et le logo de la pharmacie valent pour tous les types. Le logo est enregistré dès son import.
Pour relever les couleurs d'une affiche existante, on peut importer une image (PNG, JPG) ou un PDF (première page).
"""
import io
import json
from decimal import Decimal

import streamlit as st
from PIL import Image, ImageDraw
from reportlab.lib.units import mm

import affiche as af
import habillage
import pharmacie
import preferences
import sauvegarde
import types_affiche

TAILLE_MAX_VISUEL = 8_000_000  # octets
CLES_STYLE = {"mer_c_nom": "couleur_nom", "mer_c_prix": "couleur_prix", "mer_c_fond": "couleur_fond_prix",
              "mer_c_accent": "couleur_accent"}
PERSONNALISE = types_affiche.PERSONNALISE


# ----------------------------------------------------------------------------
# État du formulaire
# ----------------------------------------------------------------------------
def _initialiser(ctx, reglages: dict, prefs: dict, nom_type: str = "", permet_perso: bool = False) -> None:
    """Remplit le formulaire avec les réglages d'un type (réglages normalisés : voir types_affiche.normaliser_reglages).
    permet_perso : le format personnalisé peut se régler ici (création d'un type) ; sinon il se règle dans « Créer une
    affiche » et l'écran propose A5 à la place."""
    ss = st.session_state
    style = reglages["style"]
    ss.mer_ordre = list(af.ordre_valide(style.get("ordre")))
    ss.mer_fond = "Sur fond coloré" if style.get("fond_prix") else "Sans fond (prix souligné)"
    for cle, champ in CLES_STYLE.items():
        ss[cle] = style[champ]
    ss.mer_police = style["police"]
    ss.mer_nom = prefs["nom"] or ctx.nom
    perso = reglages["format"] == PERSONNALISE
    ss.mer_format = reglages["format"] if (permet_perso or not perso) else "A5"
    ss.mer_paysage = bool(reglages["paysage"])  # (champ affiché seulement hors format personnalisé)
    # Streamlit oublie un champ qui n'est pas affiché pendant un affichage : les dimensions de départ sont gardées à part
    # (mer_lg0, mer_ht0) et servent de valeur de départ aux champs, affichés seulement pour un format personnalisé.
    ss.mer_lg0 = min(600, max(50, int(reglages["largeur_mm"] or 100)))  # bornes des champs de saisie
    ss.mer_ht0 = min(900, max(50, int(reglages["hauteur_mm"] or 150)))
    ss.mer_majuscules, ss.mer_logo, ss.mer_photo = reglages["majuscules"], reglages["logo"], reglages["photo"]
    ss.mer_type_nom = nom_type
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


def _reglages_ecran() -> dict:
    """Les réglages d'un type d'affiche correspondant aux réponses en cours."""
    ss = st.session_state
    return types_affiche.normaliser_reglages({
        "format": ss.mer_format, "paysage": ss.get("mer_paysage", False), "largeur_mm": ss.get("mer_lg", ss.get("mer_lg0")),
        "hauteur_mm": ss.get("mer_ht", ss.get("mer_ht0")), "logo": ss.mer_logo, "majuscules": ss.mer_majuscules,
        "photo": ss.get("mer_photo", True), "style": _brouillon_style()})


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


def _annuler_nouveau_type() -> None:
    """Retour à l'outil sans rien créer : l'affiche en cours est telle qu'on l'a laissée."""
    ss = st.session_state
    ss.ecran_nouveau_type = False
    ss.retour_ecran_nouveau_type = True  # voir app.py : les champs de l'affiche en cours sont réaffirmés au retour
    ss.mer_sync = None
    ss.pop("mer_erreur", None)


def _creer_type(ctx) -> None:
    """Crée le type d'affiche avec les réponses de l'écran, le choisit, et revient à l'outil."""
    ss = st.session_state
    donnees = types_affiche.charger(ctx.dossier)
    try:
        t = types_affiche.creer(donnees, ss.get("mer_type_nom", ""), _reglages_ecran())
    except ValueError as erreur:
        ss.mer_erreur = str(erreur)
        return
    try:
        types_affiche.sauver(ctx.dossier, donnees)
    except OSError:
        ss.mer_erreur = "Enregistrement impossible pour le moment : réessayer dans un instant."
        return
    sauvegarde.planifier(types_affiche.FICHIER)
    ss.pop("mer_erreur", None)
    ss.ecran_nouveau_type = False
    ss.retour_ecran_nouveau_type = True
    ss.mer_sync = None  # le formulaire se relit au prochain affichage
    ss.type_actif = ss.w_type = t["id"]
    ss.type_nouveau_nom = ""
    types_affiche.appliquer(ss, t)  # le type devient le type en cours : l'affiche en cours prend ses réglages
    ss.msg_ouvert = f"Type « {t['nom']} » créé : {types_affiche.resume(t)}. L'affiche en cours est conservée."


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
def _png(format_, paysage, nom, logo, date_logo, afficher_logo, majuscules, style_json, largeur_px, photo=True,
         largeur_mm=0, hauteur_mm=0):
    """Aperçu mis en mémoire : un changement qui ne concerne pas l'aperçu ne le redessine pas (date_logo : pour renouveler
    l'image quand le logo est remplacé)."""
    if format_ == PERSONNALISE:
        taille = (largeur_mm * mm, hauteur_mm * mm)
    else:
        taille = af.orienter(af.FORMATS[format_], paysage)
    pdf, _ = af.rendu(taille, "Marque", "Nom du produit 40 ml", Decimal("7.90"), Decimal("10.50"),
                      "Du 1er au 31 octobre 2026", [_visuel_exemple()] if photo else [], afficher_logo, majuscules=majuscules,
                      style=json.loads(style_json), identite={"nom": nom, "logo": logo or None})
    return af.apercu_png(pdf, dpi=max(20, int(round(largeur_px * 72 / taille[0]))))


def _etat_apercu(ctx, nouveau_type: bool, type_: dict, perso: bool) -> dict:
    """Ce qui sert à l'aperçu, lu une fois par affichage : nom imprimé, taille de page, photo, logo, majuscules. Les champs
    « nom », « paysage » et « dimensions » ne sont pas toujours affichés : l'écran ne dépend d'eux qu'avec précaution."""
    ss = st.session_state
    if perso:  # type personnalisé réglé depuis « Ma pharmacie » : la taille est celle du type
        format_, paysage = PERSONNALISE, False
        largeur, hauteur = int(type_["largeur_mm"] or 100), int(type_["hauteur_mm"] or 150)
    else:
        format_ = ss.mer_format
        paysage = bool(ss.get("mer_paysage", False)) and format_ != PERSONNALISE
        largeur, hauteur = int(ss.get("mer_lg", ss.get("mer_lg0", 100))), int(ss.get("mer_ht", ss.get("mer_ht0", 150)))
    nom = pharmacie.nom_affiche(ctx) if nouveau_type else (ss.mer_nom or "").strip()
    chemin = pharmacie.logo(ctx)
    try:
        date_logo = chemin.stat().st_mtime_ns if chemin else 0
    except OSError:
        date_logo = 0
    return {"format": format_, "paysage": paysage, "largeur_mm": largeur, "hauteur_mm": hauteur, "nom": nom,
            "logo": str(chemin or ""), "date_logo": date_logo, "afficher_logo": bool(ss.mer_logo),
            "majuscules": bool(ss.mer_majuscules), "photo": bool(ss.get("mer_photo", True))}


def _apercu_png(e: dict, style: dict, largeur_px: int = 380, ordre=None, paysage=None) -> bytes:
    if ordre is not None:
        style = {**style, "ordre": list(ordre)}
    perso = e["format"] == PERSONNALISE
    return _png(e["format"], bool(e["paysage"] if paysage is None else paysage) and not perso, e["nom"], e["logo"],
                e["date_logo"], e["afficher_logo"], e["majuscules"], json.dumps(style, sort_keys=True), largeur_px,
                e["photo"], e["largeur_mm"] if perso else 0, e["hauteur_mm"] if perso else 0)


@st.cache_data(max_entries=6, show_spinner=False)
def _relever(octets: bytes):
    """Couleurs d'un fichier importé (image ou PDF) : (palette, miniature PNG, message d'erreur)."""
    try:
        img = preferences.ouvrir_visuel(octets)
    except ValueError as erreur:
        return [], None, str(erreur)
    pal = preferences.palette(img)
    mini = img.copy()
    mini.thumbnail((300, 300))
    tampon = io.BytesIO()
    mini.save(tampon, format="PNG")
    return pal, tampon.getvalue(), ""


# ----------------------------------------------------------------------------
# Écran
# ----------------------------------------------------------------------------
def afficher(ctx, premiere_fois: bool, nouveau_type: bool = False) -> None:
    """Affiche les questions. premiere_fois : écran de départ, qui remplace l'outil (st.stop à la fin) ;
    nouveau_type : écran de création d'un type d'affiche, qui remplace aussi l'outil."""
    ss = st.session_state
    ss.setdefault("mer_logo_n", 0)
    donnees_types = types_affiche.charger(ctx.dossier)
    type_ = types_affiche.trouver(donnees_types, ss.get("type_actif"))
    perso = type_["format"] == PERSONNALISE and not nouveau_type  # dimensions saisies dans l'onglet « Créer une affiche »
    if nouveau_type:
        try:
            depart = types_affiche.reglages_courants(ss)  # le nouveau type part des réglages de l'affiche en cours
        except KeyError:
            depart = types_affiche.normaliser_reglages(type_)
        signature = json.dumps(["nouveau", ctx.id, ss.get("nt_n", 0)])
        if ss.get("mer_sync") != signature or "mer_ordre" not in ss:
            _initialiser(ctx, depart, preferences.charger(ctx.dossier), nom_type=ss.get("type_nouveau_nom", ""),
                         permet_perso=True)
            ss.mer_sync = signature
    else:
        prefs = {**preferences.charger(ctx.dossier), "format": type_["format"] if not perso else "A5",
                 "paysage": type_["paysage"], "majuscules": type_["majuscules"], "logo": type_["logo"]}
        style_courant = ss.get("style") or type_["style"]
        signature = json.dumps([style_courant, type_["id"], types_affiche.normaliser_reglages(type_)], sort_keys=True)
        if ss.get("mer_sync") != signature or "mer_ordre" not in ss:
            reglages = {**types_affiche.normaliser_reglages(type_), "style": types_affiche.style_normalise(style_courant)}
            _initialiser(ctx, reglages, prefs)  # première ouverture, ou style ou type modifié ailleurs depuis
            ss.mer_sync = signature
        ss.mer_photo = type_["photo"]

    if nouveau_type:
        st.subheader("Nouveau type d'affiche")
        st.write("Les mêmes questions qu'à la première visite, pour régler un nouveau type d'affiche. Les réglages de "
                 "départ sont ceux de l'affiche en cours ; le produit, le prix et les dates de l'affiche en cours ne sont "
                 "pas touchés. Le nom et le logo de la pharmacie sont communs à tous les types (onglet « Ma pharmacie »).")
        st.button("Annuler, revenir à mes affiches", key="mer_annuler_haut", on_click=_annuler_nouveau_type,
                  type="tertiary", icon=":material/arrow_back:")
    elif premiere_fois:
        st.subheader(f"Bienvenue, {pharmacie.nom_affiche(ctx)}")
        st.write("Quelques questions pour régler votre affiche type : elle s'affichera ensuite toujours ainsi, "
                 "et tout reste modifiable plus tard dans l'onglet « Ma pharmacie ». Cet écran n'apparaît qu'une fois.")
    else:
        st.subheader("Mes affiches : réglages")
        if len(donnees_types["types"]) > 1:
            st.info(f"Ces réglages sont ceux du type d'affiche « {type_['nom']} », choisi dans l'onglet « Créer une "
                    "affiche » : pour régler un autre type, le choisir d'abord là-bas. Le nom et le logo valent pour "
                    "tous les types.")
        else:
            st.caption(f"Ces réglages sont ceux de votre type d'affiche « {type_['nom']} ». D'autres types (petite affiche "
                       "de rayon, sans photo…) se créent dans l'onglet « Créer une affiche », « Gérer les types ».")
        st.caption("L'aperçu à droite montre un produit d'exemple. Les réglages s'appliquent dès l'enregistrement.")
    if ss.get("mer_msg"):
        st.success(ss.pop("mer_msg"))
    apercu = _etat_apercu(ctx, nouveau_type, type_, perso)

    col_form, col_apercu = st.columns([3, 2], gap="large")
    with col_form:
        # ---- 1. Nom et logo (ou nom du type)
        if nouveau_type:
            habillage.titre_etape(1, "Nom de ce type d'affiche",
                                  "Par exemple « Petite affiche de rayon » ou « Grande affiche vitrine » : c'est le nom "
                                  "proposé en haut de la page pour passer d'un type à l'autre.")
            st.text_input("Nom du type d'affiche", key="mer_type_nom", max_chars=types_affiche.LONGUEUR_NOM,
                          placeholder="ex. Petite affiche de rayon")
        else:
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

        # ---- 2. Visuel existant (image ou PDF)
        habillage.titre_etape(2, "Une affiche que vous utilisez déjà (facultatif)",
                              "Importer l'image ou le PDF d'une de vos affiches : l'outil en relève les couleurs principales et "
                              "les propose pour vos affiches. La mise en page de ce visuel n'est pas reproduite : "
                              "l'ordre des éléments se choisit à l'étape 3.")
        cle_import = f"mer_visuel_nouveau_{ss.get('nt_n', 0)}" if nouveau_type else "mer_visuel_existant"
        visuel = st.file_uploader("Image (PNG, JPG) ou PDF d'une affiche de votre pharmacie",
                                  type=["png", "jpg", "jpeg", "pdf"], key=cle_import)
        if visuel is not None:
            if visuel.size > TAILLE_MAX_VISUEL:
                st.error("Fichier trop volumineux (8 Mo au plus).")
            else:
                pal, miniature, erreur = _relever(visuel.getvalue())
                if erreur:
                    st.error(erreur)
                else:
                    c_mini, c_coul = st.columns([1, 3], vertical_alignment="center")
                    est_pdf = (visuel.name or "").lower().endswith(".pdf")
                    c_mini.image(miniature, caption="Première page du PDF" if est_pdf else None, use_container_width=True)
                    with c_coul:
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
                st.image(_apercu_png(apercu, base, largeur_px=300, ordre=ordre, paysage=False), use_container_width=True)
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
        habillage.titre_etape(6, "Format et habitudes", "Format, orientation, majuscules et logo de ce type d'affiche.")
        h1, h2 = st.columns(2)
        if nouveau_type:
            h1.selectbox("Format", list(af.FORMATS) + [PERSONNALISE], key="mer_format",
                         help="« Personnalisé » : saisir la largeur et la hauteur en millimètres.")
            if ss.mer_format == PERSONNALISE:
                with h2:
                    d1, d2 = st.columns(2)
                    d1.number_input("Largeur (mm)", 50, 600, value=ss.mer_lg0, key="mer_lg")
                    d2.number_input("Hauteur (mm)", 50, 900, value=ss.mer_ht0, key="mer_ht")
            else:
                h2.checkbox("Affiche en paysage (à l'horizontale)", key="mer_paysage")
        elif perso:
            h1.caption(f"Format personnalisé ({type_['largeur_mm']} × {type_['hauteur_mm']} mm), réglé dans l'onglet "
                       "« Créer une affiche ».")
        else:
            h1.selectbox("Format", list(af.FORMATS), key="mer_format")
            h2.checkbox("Affiche en paysage (à l'horizontale)", key="mer_paysage")
        st.checkbox("Marque en majuscules", key="mer_majuscules")
        st.checkbox("Afficher le logo (ou le nom) en pied d'affiche", key="mer_logo")
        if nouveau_type:
            st.checkbox("Avec la photo du produit", key="mer_photo",
                        help="Décocher pour une affiche sans image (par exemple une petite affiche de rayon).")

        # ---- Enregistrement (exécuté avant le réaffichage : les réglages sont déjà pris en compte à l'écran suivant)
        st.write("")
        if ss.get("mer_erreur"):
            st.error(ss.pop("mer_erreur"))
        if nouveau_type:
            b1, b2 = st.columns([2, 3])
            b1.button("Créer ce type", type="primary", key="mer_creer_type", use_container_width=True,
                      on_click=_creer_type, args=(ctx,), disabled=len(donnees_types["types"]) >= types_affiche.MAX_TYPES)
            b2.button("Annuler", key="mer_annuler", on_click=_annuler_nouveau_type)
            if len(donnees_types["types"]) >= types_affiche.MAX_TYPES:
                st.caption(f"{types_affiche.MAX_TYPES} types d'affiche au plus : en supprimer un avant d'en créer un autre.")
        else:
            b1, b2 = st.columns([2, 3])
            b1.button("Enregistrer" + (" et commencer" if premiere_fois else ""), type="primary", key="mer_enregistrer",
                      use_container_width=True, on_click=_enregistrer, args=(ctx, premiere_fois, False))
            if premiere_fois:
                b2.button("Passer pour le moment", key="mer_passer", on_click=_enregistrer, args=(ctx, premiere_fois, True),
                          help="Les réglages habituels sont gardés ; tout se règle plus tard dans « Ma pharmacie ».")

    with col_apercu:
        with st.container(key="mer_apercu"):
            st.image(_apercu_png(apercu, _brouillon_style(), largeur_px=420), caption="Aperçu avec un produit d'exemple",
                     use_container_width=True)

    if premiere_fois or nouveau_type:
        st.stop()


def _enregistrer(ctx, premiere_fois: bool, passer: bool) -> None:
    ss = st.session_state
    style = _brouillon_style()
    nom = (ss.mer_nom or "").strip()
    donnees_types = types_affiche.charger(ctx.dossier)
    type_ = types_affiche.trouver(donnees_types, ss.get("type_actif"))
    perso = type_["format"] == PERSONNALISE
    prefs = {**preferences.charger(ctx.dossier),  # garde ce que l'écran ne règle pas (ex. « Premiers pas » déjà lus)
             "faite": True, "nom": "" if nom == ctx.nom else nom,
             "format": type_["format"] if perso else ss.mer_format,
             "paysage": False if perso else bool(ss.mer_paysage), "majuscules": bool(ss.mer_majuscules),
             "logo": bool(ss.mer_logo)}
    if passer:  # on garde les réglages en place
        style = ss.get("style") or type_["style"]
        prefs = {**preferences.charger(ctx.dossier), "faite": True}
    try:
        preferences.sauver_style(ctx.dossier, style)
        preferences.sauver(ctx.dossier, prefs)
        sauvegarde.planifier(preferences.FICHIER_STYLE)
        sauvegarde.planifier(preferences.FICHIER_PREFERENCES)
        if not passer:  # ces réglages sont ceux du type d'affiche choisi
            reglages = {**type_, "format": prefs["format"], "paysage": prefs["paysage"], "logo": prefs["logo"],
                        "majuscules": prefs["majuscules"], "style": style}
            type_ = types_affiche.mettre_a_jour(donnees_types, type_["id"], reglages)
            types_affiche.sauver(ctx.dossier, donnees_types)
            sauvegarde.planifier(types_affiche.FICHIER)
    except OSError:
        ss.mer_erreur = "Enregistrement impossible pour le moment : réessayer dans un instant."
        return
    ss.type_actif = type_["id"]
    ss.w_type = type_["id"]
    ss.mer_sync = None  # le formulaire se relit au prochain affichage
    types_affiche.appliquer(ss, type_)  # style, format, orientation, majuscules, logo : repris par l'outil tout de suite
    st.toast("Réglages enregistrés." if not passer else "Réglages habituels conservés.", icon=":material/check_circle:")
