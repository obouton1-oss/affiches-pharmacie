"""Catalogue interne des produits.

Deux fichiers (dans le dossier de l'application) :
- catalogue.csv          : export du logiciel de pharmacie (CIP/EAN + désignation, éventuellement marque).
                           Le fichier n'est jamais modifié par l'application.
- catalogue_appris.csv   : produits enregistrés par l'application (code;nom;marque), mis à jour à chaque
                           affiche téléchargée. Ses lignes remplacent celles de catalogue.csv pour un même code.

Noms de colonnes reconnus (majuscules, accents et espaces ignorés) :
  code : code, cip, cip13, ean, ean13, gtin…      nom : nom, designation, libelle, produit…
  marque (facultatif) : marque, brand, laboratoire, fabricant
"""
import csv
import unicodedata
from pathlib import Path

from chemins import DONNEES as DOSSIER
FICHIER = DOSSIER / "catalogue.csv"
FICHIER_APPRIS = DOSSIER / "catalogue_appris.csv"
CLES_CODE = {"code", "cip", "cip13", "ean", "ean13", "gtin", "codeproduit", "codecip", "codeean"}
CLES_NOM = {"nom", "designation", "libelle", "produit", "denomination", "nomproduit", "libelleproduit"}
CLES_MARQUE = {"marque", "brand", "laboratoire", "fabricant"}
MAX_LIGNES = 30000
_cache = {"cle": None, "lignes": []}


def _sans_accents(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", s or "") if unicodedata.category(ch) != "Mn")


def _norm_entete(s: str) -> str:
    return "".join(ch for ch in _sans_accents(s).strip().lower() if ch.isalnum())


def _norm_mot(s: str) -> str:
    return "".join(ch for ch in _sans_accents(s).lower() if ch.isalnum())


def _lire_texte(fichier: Path) -> str:
    brut = fichier.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return brut.decode(enc)
        except UnicodeDecodeError:
            continue
    return brut.decode("utf-8", errors="replace")


def _lire_fichier(fichier: Path) -> list[list[str]]:
    """Retourne [[code, nom, marque], ...] pour un fichier."""
    if not fichier.exists():
        return []
    texte = _lire_texte(fichier)
    try:
        dialecte = csv.Sniffer().sniff(texte[:4096], delimiters=";,\t|")
    except csv.Error:
        dialecte = csv.excel
        dialecte.delimiter = ";"
    lignes = list(csv.reader(texte.splitlines(), dialecte))
    if not lignes:
        return []
    entetes = [_norm_entete(h) for h in lignes[0]]
    i_code = next((i for i, h in enumerate(entetes) if h in CLES_CODE), None)
    i_nom = next((i for i, h in enumerate(entetes) if h in CLES_NOM), None)
    i_marque = next((i for i, h in enumerate(entetes) if h in CLES_MARQUE), None)
    if i_code is None or i_nom is None:
        i_code, i_nom, i_marque, debut = 0, 1, (2 if len(lignes[0]) > 2 else None), 0  # pas d'en-tête reconnu
    else:
        debut = 1
    sortie = []
    for l in lignes[debut:]:
        if len(l) <= max(i_code, i_nom):
            continue
        code = "".join(ch for ch in l[i_code] if ch.isdigit())
        nom = l[i_nom].strip()
        marque = l[i_marque].strip() if i_marque is not None and len(l) > i_marque else ""
        if code and nom:
            sortie.append([code, nom, marque])
    return sortie


def _cle_fichiers():
    return tuple(f.stat().st_mtime if f.exists() else None for f in (FICHIER, FICHIER_APPRIS))


def charger() -> list[list[str]]:
    """Retourne [[code, nom, marque], ...] (les produits appris remplacent ceux de l'export)."""
    cle = _cle_fichiers()
    if _cache["cle"] == cle:
        return _cache["lignes"]
    par_code = {}
    for code, nom, marque in _lire_fichier(FICHIER):
        par_code.setdefault(code, [code, nom, marque])
    for code, nom, marque in _lire_fichier(FICHIER_APPRIS):
        par_code[code] = [code, nom, marque]
    lignes = list(par_code.values())[:MAX_LIGNES]
    _cache.update(cle=cle, lignes=lignes)
    return lignes


def marques_connues() -> list[str]:
    """Marques présentes dans le catalogue, les plus longues d'abord."""
    marques = {m for _, _, m in charger() if m}
    return sorted(marques, key=len, reverse=True)


def separer(nom: str, marque: str = "") -> tuple[str, str]:
    """Sépare un nom complet en (marque, détail).
    - marque connue (donnée, ou présente dans le catalogue) dans le nom : elle est retirée du détail ;
    - marque donnée mais absente du nom : le nom entier reste le détail ;
    - aucune marque connue : ("", nom), la ligne unique est alors affichée comme titre."""
    nom = (nom or "").strip()
    marque = (marque or "").strip()
    candidates = [marque] if marque else marques_connues()
    mots_nom = nom.split()
    cles_nom = [_norm_mot(x) for x in mots_nom]
    for m in candidates:
        cles_m = [_norm_mot(x) for x in m.split()]
        k = len(cles_m)
        if not k or len(cles_nom) < k:
            continue
        # au début du nom pour une marque cherchée dans le catalogue ; n'importe où pour une marque donnée
        positions = range(0, len(cles_nom) - k + 1) if marque else [0]
        for i in positions:
            if cles_nom[i:i + k] == cles_m:
                reste = " ".join(mots_nom[:i] + mots_nom[i + k:]).strip("-–—:,. ").strip()
                return m, reste
    return marque, nom


def nom_complet(marque: str, detail: str) -> str:
    """Nom enregistré dans le catalogue : « marque détail » sur une seule ligne."""
    return " ".join(" ".join(filter(None, [(marque or "").strip(), (detail or "").strip()])).split())


def enregistrer(code: str, marque: str, detail: str) -> None:
    """Ajoute ou met à jour le produit dans catalogue_appris.csv."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    nom = nom_complet(marque, detail)
    marque = (marque or "").strip()
    if not code or not nom:
        return
    existants = {c: [c, n, m] for c, n, m in _lire_fichier(FICHIER_APPRIS)}
    if existants.get(code) == [code, nom, marque]:
        return
    existants[code] = [code, nom, marque]
    with open(FICHIER_APPRIS, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["code", "nom", "marque"])
        w.writerows(existants.values())
    _cache["cle"] = None


def importer(octets: bytes) -> tuple[int, str]:
    """Remplace catalogue.csv par un export fourni (CSV). Retourne (nombre de produits, message d'erreur)."""
    temporaire = DOSSIER / "catalogue_import.tmp"
    try:
        temporaire.write_bytes(octets)
        lignes = _lire_fichier(temporaire)
        if not lignes:
            return 0, "Aucun produit reconnu : le fichier doit contenir une colonne de code (CIP/EAN) et une colonne de nom."
        temporaire.replace(FICHIER)
        _cache["cle"] = None
        return len(lignes), ""
    except Exception as e:
        return 0, f"Fichier illisible ({type(e).__name__})."
    finally:
        temporaire.unlink(missing_ok=True)
