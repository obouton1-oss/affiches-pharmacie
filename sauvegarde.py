"""Sauvegarde durable (facultative) du catalogue, du style et de l'historique des affiches, pour la version en ligne.

Sur l'hébergement gratuit, les fichiers créés pendant l'utilisation disparaissent à chaque redémarrage.
Si le réglage HF_TOKEN (secret ou variable d'environnement) est défini, l'outil recopie automatiquement ces fichiers dans un dépôt PRIVÉ de type
« dataset » du compte Hugging Face (créé tout seul), puis les récupère au démarrage suivant.
Sont sauvegardés : catalogue_appris.csv, catalogue.csv, style.json et le dossier historique/ (affiches des 3 derniers mois).
Variable facultative : AFFICHES_DEPOT = « identifiant/nom-du-depot » pour choisir un autre dépôt.
Sans HF_TOKEN, rien n'est envoyé nulle part.
"""
import shutil
import threading

from chemins import DONNEES, reglage

FICHIERS = ("catalogue_appris.csv", "catalogue.csv", "style.json")
PREFIXE_HISTORIQUE = "historique/"
DELAI_ENVOI = 6.0  # secondes : regroupe les modifications rapprochées en un seul envoi
statut = {"restaures": [], "erreur": ""}
_verrou = threading.Lock()
_a_envoyer = set()
_a_supprimer = set()
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


def _nom_autorise(nom: str) -> bool:
    return nom in FICHIERS or (nom.startswith(PREFIXE_HISTORIQUE) and ".." not in nom)


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
    try:  # historique des affiches (plusieurs fichiers : téléchargement en parallèle)
        from huggingface_hub import snapshot_download
        snapshot_download(repo_id=depot, repo_type="dataset", allow_patterns=[PREFIXE_HISTORIQUE + "*"],
                          local_dir=str(DONNEES), token=_config()["jeton"])
        statut["restaures"].append("historique")
    except Exception:
        pass  # rien de sauvegardé pour l'instant : normal


def _envoyer() -> None:
    global _minuteur
    with _verrou:
        ajouts = sorted(_a_envoyer)
        suppressions = sorted(_a_supprimer)
        _a_envoyer.clear()
        _a_supprimer.clear()
        _minuteur = None
    try:
        from huggingface_hub import CommitOperationAdd, CommitOperationDelete
        api = _api()
        depot = _nom_depot(api)
        operations = [CommitOperationAdd(path_in_repo=nom, path_or_fileobj=str(DONNEES / nom))
                      for nom in ajouts if (DONNEES / nom).exists()]
        if suppressions:  # on ne supprime que ce qui existe réellement dans la sauvegarde
            presents = set(api.list_repo_files(depot, repo_type="dataset"))
            operations += [CommitOperationDelete(path_in_repo=nom) for nom in suppressions if nom in presents]
        if operations:  # un seul envoi pour toutes les modifications
            api.create_commit(repo_id=depot, repo_type="dataset", operations=operations,
                              commit_message="Mise à jour des données de l'outil d'affiches")
        statut["erreur"] = ""
    except Exception as e:
        statut["erreur"] = f"Sauvegarde en ligne impossible ({type(e).__name__})."
        with _verrou:  # à renvoyer lors de la prochaine modification
            _a_envoyer.update(ajouts)
            _a_supprimer.update(suppressions)


def _programmer() -> None:
    global _minuteur
    if _minuteur is None:
        _minuteur = threading.Timer(DELAI_ENVOI, _envoyer)
        _minuteur.daemon = True
        _minuteur.start()


def planifier(nom: str) -> None:
    """Demande l'envoi du fichier (dans quelques secondes, sans bloquer l'outil)."""
    if not activee() or not _nom_autorise(nom):
        return
    with _verrou:
        _a_supprimer.discard(nom)
        _a_envoyer.add(nom)
        _programmer()


def supprimer(nom: str) -> None:
    """Demande la suppression du fichier dans la sauvegarde (dans quelques secondes)."""
    if not activee() or not nom.startswith(PREFIXE_HISTORIQUE) or not _nom_autorise(nom):
        return
    with _verrou:
        _a_envoyer.discard(nom)
        _a_supprimer.add(nom)
        _programmer()
