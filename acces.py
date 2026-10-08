"""Accès à l'outil.

Mode « plusieurs pharmacies » (liste [pharmacies.…] dans les secrets, voir pharmacie.py) : la page demande de choisir
sa pharmacie et d'entrer son mot de passe ; chaque pharmacie n'accède qu'à ses propres données.
L'adresse peut retenir la pharmacie (« …?pharmacie=<identifiant> ») : en l'ajoutant aux favoris, la pharmacie est déjà
choisie à l'ouverture, il ne reste que le mot de passe à saisir.

Sinon (fonctionnement d'origine) : si le réglage AFFICHES_MOT_DE_PASSE (variable d'environnement ou secret Streamlit) est défini,
la page demande ce mot de passe commun avant d'afficher l'outil. Sans ce réglage (usage sur un seul poste), l'accès est libre.
"""
import hmac
import time

import streamlit as st

import habillage
import pharmacie
from chemins import reglage


def _connexion_pharmacie():
    """Écran de connexion du mode « plusieurs pharmacies » : s'arrête tant que la pharmacie n'est pas connectée."""
    if pharmacie.connectee():
        return
    config = pharmacie.configuration()
    noms = sorted(((fiche["nom"], ident) for ident, fiche in config.items()), key=lambda t: t[0].lower())
    identifiants = [ident for _, ident in noms]
    voulue = st.query_params.get("pharmacie", "")
    index = identifiants.index(voulue) if voulue in identifiants else None  # pharmacie retenue par l'adresse (favori)
    with st.container(key="connexion"):
        habillage.connexion_entete()
        with st.form("formulaire_acces"):
            choix = st.selectbox("Pharmacie", identifiants, index=index,
                                 format_func=lambda i: config[i]["nom"], placeholder="Choisir la pharmacie")
            saisie = st.text_input("Mot de passe", type="password")
            valide = st.form_submit_button("Entrer", type="primary", use_container_width=True)
        if valide:
            ctx, message = pharmacie.connecter(choix or "", saisie)
            if ctx is not None:
                pharmacie.ouvrir_session(ctx)
                st.query_params["pharmacie"] = ctx.id  # l'adresse retient la pharmacie : à mettre dans les favoris
                st.rerun()
            time.sleep(1.5)  # ralentit les essais répétés
            st.error(message)
        habillage.connexion_pied("Mot de passe oublié ? Le demander à l'administrateur de l'outil.")
    st.stop()


def verifier_acces():
    if pharmacie.multi():
        _connexion_pharmacie()
        return
    mot_de_passe = reglage("AFFICHES_MOT_DE_PASSE")
    if not mot_de_passe or st.session_state.get("acces_ok"):
        return
    with st.container(key="connexion"):
        habillage.connexion_entete("Affiches promo", "Pharmacie Bouton")
        with st.form("formulaire_acces"):
            saisie = st.text_input("Mot de passe", type="password")
            valide = st.form_submit_button("Entrer", type="primary", use_container_width=True)
        if valide:
            if hmac.compare_digest(saisie.encode("utf-8"), mot_de_passe.encode("utf-8")):
                st.session_state.acces_ok = True
                st.rerun()
            time.sleep(1.5)  # ralentit les essais répétés
            st.error("Mot de passe incorrect.")
    st.stop()
