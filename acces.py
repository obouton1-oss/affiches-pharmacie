"""Accès protégé par un mot de passe commun (utilisé pour la version en ligne).

Si le réglage AFFICHES_MOT_DE_PASSE (variable d'environnement ou secret Streamlit) est défini, la page demande ce mot de passe avant
d'afficher l'outil. Sans cette variable (usage sur un seul poste), l'accès est libre.
"""
import hmac
import time

import streamlit as st

from chemins import reglage


def verifier_acces():
    mot_de_passe = reglage("AFFICHES_MOT_DE_PASSE")
    if not mot_de_passe or st.session_state.get("acces_ok"):
        return
    st.title("Affiches promo – Pharmacie Bouton")
    with st.form("formulaire_acces"):
        saisie = st.text_input("Mot de passe", type="password")
        valide = st.form_submit_button("Entrer")
    if valide:
        if hmac.compare_digest(saisie.encode("utf-8"), mot_de_passe.encode("utf-8")):
            st.session_state.acces_ok = True
            st.rerun()
        time.sleep(1.5)  # ralentit les essais répétés
        st.error("Mot de passe incorrect.")
    st.stop()
