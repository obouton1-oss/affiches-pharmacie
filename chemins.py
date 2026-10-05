"""Dossiers de travail et réglages.

Par défaut, les fichiers de l'outil (catalogue, style, visuels mémorisés) sont rangés dans le dossier de
l'application. La variable AFFICHES_DONNEES désigne un autre dossier ; si le dossier choisi n'est pas
inscriptible (hébergement en ligne), un dossier temporaire est utilisé.

reglage(nom) lit un réglage dans les variables d'environnement, puis dans les « secrets » de Streamlit
(utilisés par l'hébergement en ligne : mot de passe d'accès, jeton de sauvegarde).
"""
import os
import tempfile
from pathlib import Path

CODE = Path(__file__).parent


def _inscriptible(dossier: Path) -> bool:
    try:
        dossier.mkdir(parents=True, exist_ok=True)
        essai = dossier / ".essai_ecriture"
        essai.write_text("ok")
        essai.unlink()
        return True
    except Exception:
        return False


_demande = os.environ.get("AFFICHES_DONNEES")
DONNEES = Path(_demande) if _demande else CODE
if not _inscriptible(DONNEES):
    DONNEES = Path(tempfile.gettempdir()) / "affiches_donnees"
    DONNEES.mkdir(parents=True, exist_ok=True)


def reglage(nom: str, defaut: str = "") -> str:
    valeur = os.environ.get(nom)
    if valeur and valeur.strip():
        return valeur.strip()
    try:
        import streamlit as st
        valeur = st.secrets.get(nom)
        if valeur:
            return str(valeur).strip()
    except Exception:
        pass  # pas de secrets définis : usage sur un seul poste
    return defaut


# Version en ligne = un mot de passe d'accès est défini. Certaines options gourmandes en mémoire y sont retirées.
EN_LIGNE = bool(reglage("AFFICHES_MOT_DE_PASSE"))
