"""Types d'affiche d'une pharmacie : des jeux de réglages nommés, entre lesquels on passe en un clic.

Exemple : « Affiche standard » (A5, avec photo), « Petite affiche de rayon » (A6, sans photo), « Grande affiche vitrine »
(A4 paysage). Un type garde : le format et l'orientation (ou des dimensions personnalisées), le logo, la marque en
majuscules, la présence d'une photo, et le style (police, couleurs, bandeau du prix, ordre des éléments).
Le contenu d'une affiche (produit, prix, dates) n'en fait pas partie : il reste en place quand on change de type.

Un fichier par pharmacie, dans son dossier (voir pharmacie.py), sauvegardé en ligne avec les autres :
  types_affiche.json : {"version": 1, "actif": <identifiant>, "types": [{id, nom, format, paysage, largeur_mm,
                       hauteur_mm, logo, majuscules, photo, style}, ...]}
Tant que ce fichier n'existe pas, la pharmacie a un seul type, « Affiche standard », déduit de ses réglages d'origine
(style.json et preferences.json : réponses à la mise en route). Les affiches déjà enregistrées gardent leurs propres réglages.

Ce module ne dépend pas de Streamlit : les fonctions qui touchent à la session reçoivent l'état de session en paramètre.
"""
import json
import re
import uuid
from pathlib import Path

import affiche as af
import preferences

FICHIER = "types_affiche.json"
NOM_DEFAUT = "Affiche standard"
ID_DEFAUT = "standard"
MAX_TYPES = 12
LONGUEUR_NOM = 40
CHAMPS = ("format", "paysage", "largeur_mm", "hauteur_mm", "logo", "majuscules", "photo", "style")
_COULEURS = ("couleur_nom", "couleur_prix", "couleur_accent", "couleur_secondaire", "couleur_fond_prix", "couleur_cadre")
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
PERSONNALISE = "Personnalisé"


# ----------------------------------------------------------------------------
# Réglages d'un type
# ----------------------------------------------------------------------------
def style_normalise(style) -> dict:
    """Style complet et valide : toutes les clés du style par défaut (couleurs en majuscules), l'ordre des éléments
    seulement s'il diffère de l'ordre classique."""
    style = style if isinstance(style, dict) else {}
    res = dict(af.STYLE_DEFAUT)
    for cle in res:
        if cle in style:
            res[cle] = style[cle]
    for cle in _COULEURS:
        valeur = str(res[cle])
        res[cle] = valeur.upper() if _HEX.match(valeur) else str(af.STYLE_DEFAUT[cle]).upper()
    detail = res.get("couleur_detail")  # facultative : vide = même couleur que la marque
    res["couleur_detail"] = detail.upper() if isinstance(detail, str) and _HEX.match(detail) else None
    res["textes"] = af.textes_valides(res.get("textes"))  # police et style propres à chaque texte
    res["fond_prix"] = bool(res["fond_prix"])
    if res["cadre"] not in af.CADRES:
        res["cadre"] = af.STYLE_DEFAUT["cadre"]
    if res["police"] not in af.POLICES:
        res["police"] = af.STYLE_DEFAUT["police"]
    ordre = style.get("ordre")
    try:
        if ordre and tuple(ordre) != af.ORDRE_DEFAUT and af.ordre_valide(ordre) == tuple(ordre):
            res["ordre"] = list(ordre)
    except TypeError:
        pass
    return res


def _entier(valeur, defaut, mini=50, maxi=900) -> int:
    try:
        return max(mini, min(maxi, int(valeur)))
    except (TypeError, ValueError):
        return defaut


def normaliser_reglages(reglages: dict) -> dict:
    """Les champs d'un type (sans identifiant ni nom), valides et complets. Les dimensions ne comptent que pour un
    format personnalisé (None sinon)."""
    r = reglages if isinstance(reglages, dict) else {}
    format_ = r.get("format") if r.get("format") in list(af.FORMATS) + [PERSONNALISE] else "A5"
    perso = format_ == PERSONNALISE
    return {"format": format_,
            "paysage": bool(r.get("paysage", False)) and format_ in af.FORMATS,
            "largeur_mm": _entier(r.get("largeur_mm"), 100) if perso else None,
            "hauteur_mm": _entier(r.get("hauteur_mm"), 150) if perso else None,
            "logo": bool(r.get("logo", True)),
            "majuscules": bool(r.get("majuscules", True)),
            "photo": bool(r.get("photo", True)),
            "style": style_normalise(r.get("style"))}


def egaux(a: dict, b: dict) -> bool:
    """Mêmes réglages (l'identifiant et le nom ne comptent pas) ?"""
    return normaliser_reglages(a) == normaliser_reglages(b)


def resume(t: dict) -> str:
    """Description courte d'un type : « A5 paysage · avec photo »."""
    t = normaliser_reglages(t)
    if t["format"] == PERSONNALISE:
        format_ = f"{t['largeur_mm']} × {t['hauteur_mm']} mm"
    else:
        format_ = f"{t['format']} paysage" if t["paysage"] else f"{t['format']} portrait"
    return f"{format_} · {'avec photo' if t['photo'] else 'sans photo'}" + ("" if t["logo"] else " · sans logo")


def nom_valide(nom, autres_noms=()) -> str:
    """Nom nettoyé, ou ValueError avec le message à afficher."""
    nom = " ".join(str(nom or "").split())
    if not nom:
        raise ValueError("Donner un nom au type d'affiche.")
    if len(nom) > LONGUEUR_NOM:
        raise ValueError(f"Nom trop long ({LONGUEUR_NOM} caractères au plus).")
    if nom.lower() in {n.lower() for n in autres_noms}:
        raise ValueError(f"Un type d'affiche s'appelle déjà « {nom} » : choisir un autre nom.")
    return nom


# ----------------------------------------------------------------------------
# Fichier de la pharmacie
# ----------------------------------------------------------------------------
def type_depart(dossier: Path) -> dict:
    """Le premier type, déduit des réglages d'origine de la pharmacie (mise en route)."""
    prefs = preferences.charger(dossier)
    reglages = normaliser_reglages({"format": prefs["format"], "paysage": prefs["paysage"], "logo": prefs["logo"],
                                    "majuscules": prefs["majuscules"], "photo": True,
                                    "style": preferences.charger_style(dossier)})
    return {"id": ID_DEFAUT, "nom": NOM_DEFAUT, **reglages}


def _type_propre(brut) -> dict | None:
    if not isinstance(brut, dict) or not brut.get("id"):
        return None
    try:
        nom = nom_valide(brut.get("nom"))
    except ValueError:
        return None
    return {"id": str(brut["id"])[:40], "nom": nom, **normaliser_reglages(brut)}


def charger(dossier: Path) -> dict:
    """{"actif": identifiant, "types": [...]} ; sans fichier, le seul type est celui de la mise en route."""
    types, actif = [], None
    try:
        lu = json.loads((Path(dossier) / FICHIER).read_text(encoding="utf-8"))
        if isinstance(lu, dict):
            vus_ids, vus_noms = set(), set()
            for brut in lu.get("types") or []:
                t = _type_propre(brut)
                if t and t["id"] not in vus_ids and t["nom"].lower() not in vus_noms:
                    types.append(t)
                    vus_ids.add(t["id"])
                    vus_noms.add(t["nom"].lower())
            actif = lu.get("actif")
    except Exception:
        types = []
    types = types[:MAX_TYPES]
    if not types:
        types, actif = [type_depart(dossier)], ID_DEFAUT
    if actif not in {t["id"] for t in types}:
        actif = types[0]["id"]
    return {"actif": actif, "types": types}


def sauver(dossier: Path, donnees: dict) -> None:
    contenu = {"version": 1, "actif": donnees["actif"], "types": donnees["types"]}
    fichier = Path(dossier) / FICHIER
    temporaire = fichier.with_name(fichier.name + ".tmp")
    temporaire.write_text(json.dumps(contenu, ensure_ascii=False, indent=1), encoding="utf-8")
    temporaire.replace(fichier)


def trouver(donnees: dict, ident) -> dict:
    """Le type d'identifiant donné ; à défaut, le type actif du fichier."""
    for t in donnees["types"]:
        if t["id"] == ident:
            return t
    return next(t for t in donnees["types"] if t["id"] == donnees["actif"])


def existe(donnees: dict, ident) -> bool:
    return any(t["id"] == ident for t in donnees["types"])


# ----------------------------------------------------------------------------
# Modifications (retournent les données modifiées ; à enregistrer avec sauver)
# ----------------------------------------------------------------------------
def creer(donnees: dict, nom: str, reglages: dict) -> dict:
    """Ajoute un type avec ces réglages, et le rend actif. Retourne le nouveau type. ValueError si le nom ne convient pas
    ou si la pharmacie a déjà le maximum de types."""
    if len(donnees["types"]) >= MAX_TYPES:
        raise ValueError(f"{MAX_TYPES} types d'affiche au plus : en supprimer un avant d'en créer un autre.")
    nom = nom_valide(nom, [t["nom"] for t in donnees["types"]])
    ident = uuid.uuid4().hex[:8]
    t = {"id": ident, "nom": nom, **normaliser_reglages(reglages)}
    donnees["types"].append(t)
    donnees["actif"] = ident
    return t


def mettre_a_jour(donnees: dict, ident: str, reglages: dict) -> dict:
    """Remplace les réglages du type (son nom est conservé)."""
    t = trouver(donnees, ident)
    t.update(normaliser_reglages(reglages))
    return t


def renommer(donnees: dict, ident: str, nom: str) -> dict:
    t = trouver(donnees, ident)
    t["nom"] = nom_valide(nom, [x["nom"] for x in donnees["types"] if x["id"] != t["id"]])
    return t


def supprimer(donnees: dict, ident: str) -> dict:
    """Retire le type (le dernier ne peut pas l'être). Si c'était le type actif, le premier restant le devient."""
    if len(donnees["types"]) <= 1:
        raise ValueError("Il faut garder au moins un type d'affiche.")
    donnees["types"] = [t for t in donnees["types"] if t["id"] != ident]
    if donnees["actif"] == ident:
        donnees["actif"] = donnees["types"][0]["id"]
    return donnees


def correspondant(donnees: dict, reglages: dict):
    """Identifiant du type dont les réglages sont exactement ceux-ci (le type actif d'abord), ou None."""
    actif = trouver(donnees, donnees["actif"])
    for t in [actif] + [x for x in donnees["types"] if x is not actif]:
        if egaux(t, reglages):
            return t["id"]
    return None


# ----------------------------------------------------------------------------
# Session (l'état de session est passé en paramètre : Streamlit, ou un simple dictionnaire dans les tests)
# ----------------------------------------------------------------------------
def reglages_courants(ss) -> dict:
    """Les réglages en cours dans le formulaire (mêmes champs qu'un type), normalisés.

    L'orientation (w_paysage) n'est affichée que pour un format standard, et les dimensions (w_lg, w_ht) que pour le
    format personnalisé. Streamlit oublie un champ qui n'a pas été affiché lors du dernier affichage ; or cette fonction
    est aussi appelée par des boutons (« Mettre à jour ce type », « Créer tout de suite »), exécutés avant le nouvel
    affichage. Ces trois clés sont donc lues avec .get : quand elles manquent, c'est qu'elles ne concernent pas le format
    choisi, et normaliser_reglages les ignore dans ce cas."""
    format_ = ss["w_format"]
    return normaliser_reglages({"format": format_, "paysage": ss.get("w_paysage", False),
                                "largeur_mm": ss.get("w_lg"), "hauteur_mm": ss.get("w_ht"),
                                "logo": ss["w_logo"], "majuscules": ss["w_majuscules"],
                                "photo": ss["w_photo"], "style": ss["style"]})


def _geometrie(r: dict) -> tuple:
    return (r["format"], r["paysage"], r["largeur_mm"], r["hauteur_mm"])


def appliquer(ss, t: dict) -> None:
    """Met les réglages du type dans le formulaire. Si la taille ou l'orientation change, les éléments déplacés à la main
    sont remis en place (leur position ne vaut que pour l'ancienne mise en page) ; le produit, le prix et les dates restent."""
    r = normaliser_reglages(t)
    try:
        avant = _geometrie(reglages_courants(ss))
    except KeyError:
        avant = None
    ss["style"] = dict(r["style"])
    ss["w_police"] = r["style"]["police"]
    ss["w_format"], ss["w_paysage"] = r["format"], r["paysage"]
    if r["format"] == PERSONNALISE:
        ss["w_lg"], ss["w_ht"] = r["largeur_mm"], r["hauteur_mm"]
    ss["w_logo"], ss["w_majuscules"], ss["w_photo"] = r["logo"], r["majuscules"], r["photo"]
    ss["ver"] = ss.get("ver", 0) + 1  # les sélecteurs de couleur reprennent les valeurs du type
    ss["planche_cache"] = None
    if avant != _geometrie(r):
        ss["reglages"] = af.reglages_defaut()
        ss["element_actif"] = "marque"
