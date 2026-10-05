"""Sauvegarde durable (facultative) du catalogue et du style, pour la version en ligne.

Sur l'hébergement gratuit, les fichiers créés pendant l'utilisation disparaissent à chaque redémarrage.
Si le réglage HF_TOKEN (secret ou variable d'environnement) est défini, l'outil recopie automatiquement ces fichiers dans un dépôt PRIVÉ de type
« dataset » du compte Hugging Face (créé tout seul), puis les récupère au démarrage suivant.
Variable facultative : AFFICHES_DEPOT = « identifiant/nom-du-depot » pour choisir un autre dépôt.
Sans HF_TOKEN, rien n'est envoyé nulle part.
"""
import shutil
import threading

from chemins import DONNEES, reglage

FICHIERS = ("catalogue_appris.csv", "catalogue.csv", "style.json")
DELAI_ENVOI = 6.0  # secondes : regroupe les modifications rapprochées en un seul envoi
statut = {"restaures": [], "erreur": ""}
_verrou = threading.Lock()
_a_envoyer = set()
_minuteur = None
_depot = {"nom": None}
_cfg = {}


def _config() -> dict:
    """Lu une seule fois, depuis le fil principal de l'application."""
    if not _cfg:
        _cfg.update(jeton=reglage("HF_TOKEN"), depot=reglage("AFFICHES_DEPOT"))
    return _cfg


def activee() -> bool:
    return bool(_config()["jeton"])


def _api():
    from huggingface_hub import HfApi
    return HfApi(token=_config()["jeton"])


def _nom_depot(api) -> str:
    if _depot["nom"]:
        return _depot["nom"]
    nom = _config()["depot"]
    if not nom:
        nom = f"{api.whoami()['name']}/affiches-pharmacie-donnees"
    api.create_repo(repo_id=nom, repo_type="dataset", private=True, exist_ok=True)
    _depot["nom"] = nom
    return nom


def restaurer() -> None:
    """À appeler une fois au démarrage : récupère les fichiers sauvegardés (s'ils existent)."""
    if not activee():
        return
    try:
        from huggingface_hub import hf_hub_download
        api = _api()
        depot = _nom_depot(api)
    except Exception as e:
        statut["erreur"] = f"Sauvegarde indisponible ({type(e).__name__})."
        return
    for nom in FICHIERS:
        try:
            chemin = hf_hub_download(repo_id=depot, filename=nom, repo_type="dataset",
                                     token=_config()["jeton"])
            shutil.copyfile(chemin, DONNEES / nom)
            statut["restaures"].append(nom)
        except Exception:
            pass  # fichier pas encore sauvegardé (première utilisation) : normal


def _envoyer() -> None:
    global _minuteur
    with _verrou:
        noms = sorted(_a_envoyer)
        _a_envoyer.clear()
        _minuteur = None
    try:
        api = _api()
        depot = _nom_depot(api)
        for nom in noms:
            fichier = DONNEES / nom
            if fichier.exists():
                api.upload_file(path_or_fileobj=str(fichier), path_in_repo=nom, repo_id=depot,
                                repo_type="dataset", commit_message=f"Mise à jour de {nom}")
        statut["erreur"] = ""
    except Exception as e:
        statut["erreur"] = f"Sauvegarde en ligne impossible ({type(e).__name__})."


def planifier(nom: str) -> None:
    """Demande l'envoi du fichier (dans quelques secondes, sans bloquer l'outil)."""
    global _minuteur
    if not activee() or nom not in FICHIERS:
        return
    with _verrou:
        _a_envoyer.add(nom)
        if _minuteur is None:
            _minuteur = threading.Timer(DELAI_ENVOI, _envoyer)
            _minuteur.daemon = True
            _minuteur.start()
