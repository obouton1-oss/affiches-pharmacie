"""Mode « plusieurs pharmacies » : chaque pharmacie a son identifiant, son mot de passe, son nom, son logo et son
propre espace de données (catalogue, style, historique des affiches).

Le mode est activé par la liste des pharmacies, écrite par l'administrateur dans les « secrets » de l'application
(ou dans la variable d'environnement AFFICHES_PHARMACIES, au format JSON, pour les essais en local) :

    [pharmacies.bouton]
    nom = "Pharmacie Bouton"
    mot_de_passe = "pbkdf2$..."      # empreinte produite par l'onglet « Ma pharmacie » (jamais le mot de passe lui-même)
    donnees = "racine"               # facultatif : reprend les données déjà enregistrées avant ce mode (une seule pharmacie)
    admin = true                     # facultatif : peut ajouter des pharmacies

    [pharmacies.exemple]
    nom = "Pharmacie Exemple"
    mot_de_passe = "pbkdf2$..."

Attention : dans le fichier des secrets, ces blocs [pharmacies.…] doivent venir APRÈS les réglages simples
(AFFICHES_MOT_DE_PASSE, HF_TOKEN…), sinon ceux-ci seraient rangés dans le dernier bloc.

Sans liste de pharmacies, l'outil fonctionne comme avant (une seule pharmacie, mot de passe commun facultatif).
Les données d'une pharmacie sont rangées dans <DONNEES>/pharmacies/<identifiant>/ et, pour la sauvegarde en ligne,
sous « pharmacies/<identifiant>/ » dans le dépôt privé : une pharmacie ne lit jamais celles d'une autre.
"""
import hashlib
import hmac
import io
import json
import os
import re
import secrets as alea
import threading
import time
import unicodedata
from pathlib import Path
from typing import NamedTuple

from chemins import CODE, DONNEES

ITERATIONS = 200_000
MAX_ITERATIONS = 2_000_000
ID_VALIDE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
NOM_DEFAUT = "Pharmacie Bouton"
LOGO_DEFAUT = CODE / "logo.png"
FICHIER_LOGO = "logo_pharmacie.png"
FICHIER_PREFERENCES = "preferences.json"  # écrit par preferences.py
COTE_MAX_LOGO = 800       # pixels
TAILLE_MAX_LOGO = 5_000_000  # octets
ESSAIS_MAX, FENETRE_ESSAIS = 8, 600.0  # 8 échecs en 10 minutes : connexion bloquée un moment pour cet identifiant


class Contexte(NamedTuple):
    id: str
    nom: str
    prefixe: str   # début des chemins dans la sauvegarde (« pharmacies/<id>/ ») ; vide pour la pharmacie d'origine
    dossier: Path  # dossier local des données de la pharmacie
    admin: bool = False
    defaut: bool = False  # fonctionnement d'origine : une seule pharmacie, sans liste


DEFAUT = Contexte("", NOM_DEFAUT, "", DONNEES, False, True)
_verrou = threading.Lock()
_essais: dict[str, list[float]] = {}
_forcee = {"id": None}  # identifiant imposé hors d'une session Streamlit (essais automatiques)
_config_cache: dict = {"cle": None, "valeur": {}}
_fausse = {"empreinte": None}


# ----------------------------------------------------------------------------
# Mots de passe
# ----------------------------------------------------------------------------
def hacher(mot_de_passe: str, sel: bytes | None = None, iterations: int = ITERATIONS) -> str:
    """Empreinte du mot de passe (« pbkdf2$itérations$sel$empreinte »), à ranger dans les secrets."""
    sel = sel if sel is not None else os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", mot_de_passe.encode("utf-8"), sel, iterations)
    return f"pbkdf2${iterations}${sel.hex()}${h.hex()}"


def verifier(mot_de_passe: str, empreinte: str) -> bool:
    try:
        methode, iterations, sel, h = str(empreinte).split("$")
        iterations = int(iterations)
        if methode != "pbkdf2" or not 1 <= iterations <= MAX_ITERATIONS:
            return False
        calcul = hashlib.pbkdf2_hmac("sha256", mot_de_passe.encode("utf-8"), bytes.fromhex(sel), iterations)
        return hmac.compare_digest(calcul, bytes.fromhex(h))
    except (ValueError, TypeError):
        return False


def mot_de_passe_provisoire() -> str:
    """Mot de passe à communiquer à la pharmacie (sans caractères ambigus : 0/O, 1/l/I)."""
    alphabet = "abcdefghjkmnpqrstuvwxyzACDEFGHJKLMNPQRTUVWXYZ23456789"
    return "-".join("".join(alea.choice(alphabet) for _ in range(4)) for _ in range(3))


# ----------------------------------------------------------------------------
# Liste des pharmacies
# ----------------------------------------------------------------------------
def _brut(env: str):
    """Contenu de la liste : variable d'environnement AFFICHES_PHARMACIES (JSON), sinon secrets Streamlit."""
    if env:
        try:
            return json.loads(env)
        except ValueError:
            return None
    try:
        import streamlit as st
        table = st.secrets.get("pharmacies")
        return dict(table) if table else None
    except Exception:
        return None


def configuration() -> dict:
    """{identifiant: {"nom", "mot_de_passe", "racine", "admin"}} des pharmacies valides."""
    env = os.environ.get("AFFICHES_PHARMACIES", "").strip()
    cle = ("env", env) if env else ("secrets", None)
    if _config_cache["cle"] == cle:  # les secrets ne changent pas tant que l'application tourne (redémarrage à chaque modification)
        return _config_cache["valeur"]
    brut = _brut(env)
    res, racine_prise = {}, False
    for ident, fiche in (brut.items() if isinstance(brut, dict) else []):
        ident = str(ident).strip().lower()
        try:
            nom = str(fiche.get("nom") or "").strip()
            empreinte = str(fiche.get("mot_de_passe") or "")
            racine = str(fiche.get("donnees") or "") == "racine"
            admin = bool(fiche.get("admin"))
        except AttributeError:
            continue
        if not ID_VALIDE.match(ident) or not nom or not empreinte.startswith("pbkdf2$"):
            continue  # fiche incomplète ou identifiant non conforme : ignorée
        racine, racine_prise = racine and not racine_prise, racine_prise or racine  # une seule pharmacie « racine »
        res[ident] = {"nom": nom, "mot_de_passe": empreinte, "racine": racine, "admin": admin}
    _config_cache.update(cle=cle, valeur=res)
    return res


def multi() -> bool:
    """Vrai si une liste de pharmacies est définie (sinon : fonctionnement d'origine)."""
    return bool(configuration())


def _construire(ident: str, fiche: dict) -> Contexte:
    if fiche["racine"]:
        dossier, prefixe = DONNEES, ""
    else:
        prefixe, dossier = f"pharmacies/{ident}/", DONNEES / "pharmacies" / ident
    dossier.mkdir(parents=True, exist_ok=True)
    return Contexte(ident, fiche["nom"], prefixe, dossier, fiche["admin"], False)


# ----------------------------------------------------------------------------
# Connexion
# ----------------------------------------------------------------------------
def _bloque(ident: str) -> bool:
    maintenant = time.time()
    recents = [t for t in _essais.get(ident, []) if maintenant - t < FENETRE_ESSAIS]
    _essais[ident] = recents
    return len(recents) >= ESSAIS_MAX


def connecter(identifiant: str, mot_de_passe: str):
    """Retourne (contexte, "") si l'identifiant et le mot de passe sont bons, sinon (None, message)."""
    ident = (identifiant or "").strip().lower()[:60]
    with _verrou:
        if len(_essais) > 2000:
            _essais.clear()
        if _bloque(ident):
            return None, "Trop d'essais : réessayer dans quelques minutes."
    fiche = configuration().get(ident)
    if _fausse["empreinte"] is None:
        _fausse["empreinte"] = hacher("empreinte-factice")
    empreinte = fiche["mot_de_passe"] if fiche else _fausse["empreinte"]  # même durée de calcul si l'identifiant est inconnu
    if verifier(mot_de_passe or "", empreinte) and fiche:
        with _verrou:
            _essais.pop(ident, None)
        return _construire(ident, fiche), ""
    with _verrou:
        _essais.setdefault(ident, []).append(time.time())
    return None, "Identifiant ou mot de passe incorrect."


def _etat():
    try:
        import streamlit as st
        return st.session_state
    except Exception:
        return {}


def ouvrir_session(ctx: Contexte) -> None:
    _etat()["pharmacie_id"] = ctx.id


def fermer_session() -> None:
    try:
        import streamlit as st
        st.session_state.clear()
    except Exception:
        pass
    _forcee["id"] = None


def contexte() -> Contexte:
    """Pharmacie de la session en cours (ou fonctionnement d'origine s'il n'y a pas de liste de pharmacies)."""
    config = configuration()
    if not config:
        return DEFAUT
    ident = _forcee["id"] or _etat().get("pharmacie_id")
    fiche = config.get(ident) if ident else None
    if fiche is None:
        raise PermissionError("Aucune pharmacie connectée.")
    return _construire(ident, fiche)


def connectee() -> bool:
    if not multi():
        return True
    ident = _forcee["id"] or _etat().get("pharmacie_id")
    return bool(ident) and ident in configuration()


# ----------------------------------------------------------------------------
# Logo et identité visuelle
# ----------------------------------------------------------------------------
def logo(ctx: Contexte | None = None) -> Path | None:
    """Logo de la pharmacie : celui qu'elle a importé ; à défaut, le logo d'origine pour la pharmacie d'origine."""
    ctx = ctx or contexte()
    perso = ctx.dossier / FICHIER_LOGO
    if perso.exists():
        return perso
    return LOGO_DEFAUT if (ctx.defaut or ctx.prefixe == "") else None


def nom_affiche(ctx: Contexte | None = None) -> str:
    """Nom imprimé sur les affiches : celui que la pharmacie a choisi (preferences.json), sinon celui de la liste."""
    ctx = ctx or contexte()
    if ctx.defaut:
        return ctx.nom
    try:
        perso = json.loads((ctx.dossier / FICHIER_PREFERENCES).read_text(encoding="utf-8")).get("nom")
        return str(perso).strip()[:80] if perso and str(perso).strip() else ctx.nom
    except (OSError, ValueError, AttributeError):
        return ctx.nom


def identite(ctx: Contexte | None = None):
    """{"nom", "logo"} à passer au dessin des affiches ; None pour la pharmacie d'origine inchangée."""
    ctx = ctx or contexte()
    chemin = logo(ctx)
    nom = nom_affiche(ctx)
    if ctx.defaut or (ctx.prefixe == "" and nom == NOM_DEFAUT and chemin == LOGO_DEFAUT):
        return None
    return {"nom": nom, "logo": chemin}


def empreinte_identite(ctx: Contexte | None = None):
    """Valeur qui change quand le nom ou le logo change (sert à renouveler les mises en page gardées en mémoire)."""
    ctx = ctx or contexte()
    chemin = logo(ctx)
    try:
        date = chemin.stat().st_mtime_ns if chemin else 0
    except OSError:
        date = 0
    return (nom_affiche(ctx), str(chemin or ""), date)


def enregistrer_logo(octets: bytes):
    """Enregistre le logo importé (PNG avec transparence conservée, 800 px au plus). Retourne (ok, message)."""
    from PIL import Image, ImageOps
    if len(octets) > TAILLE_MAX_LOGO:
        return False, "Fichier trop volumineux (5 Mo au plus)."
    try:
        img = Image.open(io.BytesIO(octets))
        img.load()
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGBA")
    except Exception:
        return False, "Image illisible : importer un fichier PNG ou JPG."
    if max(img.size) < 40:
        return False, "Image trop petite pour servir de logo."
    img.thumbnail((COTE_MAX_LOGO, COTE_MAX_LOGO), Image.LANCZOS)
    ctx = contexte()
    cible = ctx.dossier / FICHIER_LOGO
    cible.parent.mkdir(parents=True, exist_ok=True)
    img.save(cible, format="PNG")
    import sauvegarde
    sauvegarde.planifier(FICHIER_LOGO)
    return True, "Logo enregistré."


def supprimer_logo() -> None:
    ctx = contexte()
    (ctx.dossier / FICHIER_LOGO).unlink(missing_ok=True)
    import sauvegarde
    sauvegarde.supprimer(FICHIER_LOGO)


# ----------------------------------------------------------------------------
# Administration : produire la fiche d'une nouvelle pharmacie
# ----------------------------------------------------------------------------
def identifiant_depuis_nom(nom: str, existants=()) -> str:
    """« Pharmacie du Centre » -> « pharmacie-du-centre » (sans accents, unique parmi `existants`)."""
    sans = "".join(c for c in unicodedata.normalize("NFD", nom or "") if unicodedata.category(c) != "Mn")
    base = re.sub(r"[^a-z0-9]+", "-", sans.lower()).strip("-")[:36].strip("-") or "pharmacie"
    if len(base) < 2:
        base = f"{base}-p"
    ident, n = base, 2
    while ident in existants:
        ident, n = f"{base}-{n}", n + 1
    return ident


def fiche_secrets(ident: str, nom: str, mot_de_passe: str, racine: bool = False, admin: bool = False) -> str:
    """Bloc à ajouter À LA FIN des secrets de l'application (ou à mettre à la place de l'ancien bloc de la pharmacie)."""
    bloc = (f"[pharmacies.{ident}]\n"
            f"nom = {json.dumps(nom.strip(), ensure_ascii=False)}\n"
            f"mot_de_passe = \"{hacher(mot_de_passe)}\"\n")
    if racine:
        bloc += 'donnees = "racine"\n'
    if admin:
        bloc += "admin = true\n"
    return bloc
