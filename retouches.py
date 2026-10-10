"""Retouches directes sur l'aperçu : barre de réglages de l'élément cliqué, et « Annuler / Rétablir ».

L'aperçu (composant_editeur) envoie des événements ; ils sont appliqués ici, au tout début de chaque exécution, avant
que les champs du formulaire ne soient dessinés (Streamlit interdit de modifier la valeur d'un champ déjà affiché).

Événements reçus (dictionnaires, avec un horodatage « ts ») :
  {el, clic, ddx, ddy, ds}             clic, glisser ou agrandir un élément (comme avant)
  {action: "maj", el, champ, valeur}   un réglage de la barre : texte, couleur, fond, police, style, majuscules,
                                       forme, devant
  {action: "taille", el, facteur}      plus grand / plus petit
  {action: "tourner", el, angle}       rotation d'un élément ajouté
  {action: "reinit" | "dupliquer" | "supprimer" | "masquer_logo" | "texte_marque", el}
  {action: "ajouter", type}            nouveau texte, prix ou forme
  {action: "police_affiche" | "theme", valeur}
  {action: "annuler" | "retablir" | "deselect"}

Ce module ne dessine rien : il lit et modifie l'état de la session (st.session_state, ou un simple dictionnaire
dans les essais).
"""
import copy
import json
import re

import affiche as af
import elements_libres as libres

MAX_HISTORIQUE = 60  # nombre de retours en arrière possibles

# Ce que « Annuler » remet en l'état : l'aspect de l'affiche et ses textes (pas le produit ni les visuels)
_CHAMPS_HISTORIQUE = ("style", "reglages", "elements", "marque", "detail", "w_prix", "w_barre", "w_barre_on",
                      "w_majuscules", "w_logo", "w_logo_marque")
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Couleur de chaque texte de l'affiche : clé du style (plusieurs textes partagent parfois une couleur)
COULEUR_ELEMENT = {"marque": "couleur_nom", "detail": "couleur_detail", "prix": "couleur_prix",
                   "prix_barre": "couleur_secondaire", "dates": "couleur_secondaire", "ligne": "couleur_nom"}
PARTAGE = {"marque": "Couleur partagée avec le texte sous le prix (et le détail s'il n'a pas sa propre couleur).",
           "ligne": "Couleur partagée avec la marque.",
           "prix_barre": "Couleur partagée avec les dates.", "dates": "Couleur partagée avec le prix barré.",
           "pastille": "Couleur partagée avec le fond du prix."}
MAX_TEXTE = {"marque": 80, "detail": 300, "prix": 12, "prix_barre": 12}


# ----------------------------------------------------------------------------
# Annuler / Rétablir
# ----------------------------------------------------------------------------
def _instantane(ss):
    return {c: copy.deepcopy(ss.get(c)) for c in _CHAMPS_HISTORIQUE}


def _empreinte(instantane):
    return json.dumps(instantane, sort_keys=True, default=str)


def noter(ss):
    """Enregistre l'état présent s'il a changé depuis la dernière fois (l'état précédent devient annulable)."""
    actuel = _instantane(ss)
    empreinte = _empreinte(actuel)
    present = ss.get("retouche_present")
    if present is None:
        ss["retouche_present"], ss["retouche_empreinte"] = actuel, empreinte
        ss.setdefault("retouche_annuler", [])
        ss.setdefault("retouche_retablir", [])
        return
    if empreinte != ss.get("retouche_empreinte"):
        ss["retouche_annuler"] = (list(ss.get("retouche_annuler") or []) + [present])[-MAX_HISTORIQUE:]
        ss["retouche_retablir"] = []
        ss["retouche_present"], ss["retouche_empreinte"] = actuel, empreinte


def oublier(ss):
    """Repart d'un historique vide (nouvelle affiche, affiche rouverte) : on ne revient pas à l'affiche d'avant."""
    ss["retouche_present"], ss["retouche_empreinte"] = None, None
    ss["retouche_annuler"], ss["retouche_retablir"] = [], []


def _restaurer(ss, inst):
    for c in _CHAMPS_HISTORIQUE:
        if c in inst and inst[c] is not None:
            ss[c] = copy.deepcopy(inst[c])
    ss["w_police"] = ss["style"].get("police", "Helvetica")
    ss["ver"] = ss.get("ver", 0) + 1  # les champs du formulaire reprennent les valeurs rétablies
    ss["retouche_present"], ss["retouche_empreinte"] = _instantane(ss), _empreinte(_instantane(ss))


def annuler(ss):
    noter(ss)
    pile = list(ss.get("retouche_annuler") or [])
    if not pile:
        return False
    ss["retouche_retablir"] = list(ss.get("retouche_retablir") or []) + [ss["retouche_present"]]
    ss["retouche_annuler"] = pile[:-1]
    _restaurer(ss, pile[-1])
    return True


def retablir(ss):
    noter(ss)
    pile = list(ss.get("retouche_retablir") or [])
    if not pile:
        return False
    ss["retouche_annuler"] = list(ss.get("retouche_annuler") or []) + [ss["retouche_present"]]
    ss["retouche_retablir"] = pile[:-1]
    _restaurer(ss, pile[-1])
    return True


def peut_annuler(ss):
    return bool(ss.get("retouche_annuler"))


def peut_retablir(ss):
    return bool(ss.get("retouche_retablir"))


# ----------------------------------------------------------------------------
# Description de la barre de réglages de chaque élément (envoyée à l'aperçu)
# ----------------------------------------------------------------------------
def _style_texte(style, texte):
    etat = af.textes_valides(style.get("textes")).get(texte, {})
    return {"police": etat.get("police"), "gras": etat.get("gras", af.TEXTES[texte][1]),
            "italique": etat.get("italique", False), "souligne": etat.get("souligne", False)}


def _couleur_style(style, cle):
    if cle == "couleur_detail":
        return af.couleur_detail(style)
    return style.get(cle) or af.STYLE_DEFAUT.get(cle) or "#000000"


def outils(ss, cadres, *, standard=True, logo_marque=False):
    """Pour chaque élément présent sur l'aperçu, ce que sa barre de réglages propose et les valeurs actuelles."""
    style = ss["style"]
    res = {}
    for el in cadres:
        if el in af.ELEMENTS:
            o = {"titre": af.ELEMENTS[el], "taille": True, "actions": ["reinit"]}
            if el == "marque" and logo_marque:
                o["titre"] = "Logo de la marque"
                o["actions"].append("texte_marque")
            elif el in af.TEXTES:
                o["styles"] = _style_texte(style, el)
            if el in COULEUR_ELEMENT and not (el == "marque" and logo_marque):
                o["couleur"] = _couleur_style(style, COULEUR_ELEMENT[el])
                if el in PARTAGE:
                    o["aide_couleur"] = PARTAGE[el]
            if el == "pastille":
                o["couleur"] = _couleur_style(style, "couleur_fond_prix" if style.get("fond_prix") else "couleur_prix")
                o["aide_couleur"] = PARTAGE["pastille"]
            if el == "prix":
                o["fond"] = {"actif": bool(style.get("fond_prix")), "couleur": _couleur_style(style, "couleur_fond_prix")}
            if el == "marque" and not logo_marque:
                o["texte"] = {"valeur": ss.get("marque", ""), "multi": False, "max": MAX_TEXTE["marque"],
                              "indication": "Marque"}
                o["majuscules"] = bool(ss.get("w_majuscules"))
            elif el == "detail":
                o["texte"] = {"valeur": ss.get("detail", ""), "multi": True, "max": MAX_TEXTE["detail"],
                              "indication": "Détail du produit"}
            elif el == "prix" and standard:
                o["texte"] = {"valeur": ss.get("w_prix", ""), "multi": False, "max": MAX_TEXTE["prix"],
                              "indication": "Prix, ex. 7,90", "prix": True}
            elif el == "prix_barre" and standard:
                o["texte"] = {"valeur": ss.get("w_barre", ""), "multi": False, "max": MAX_TEXTE["prix_barre"],
                              "indication": "Prix barré, ex. 10,50", "prix": True}
            if el == "logo":
                o["actions"].append("masquer_logo")
            res[el] = o
        elif el.startswith("libre_"):
            e = next((x for x in ss.get("elements") or [] if x["id"] == el[6:]), None)
            if e is None:
                continue
            o = {"titre": libres.TYPES[e["type"]], "taille": True, "libre": True, "devant": e["devant"],
                 "couleur": e["couleur"], "actions": ["tourner", "dupliquer", "supprimer"]}
            if e["type"] == "texte":
                o["texte"] = {"valeur": e["texte"], "multi": True, "max": 200, "indication": "Texte"}
            elif e["type"] == "prix":
                o["texte"] = {"valeur": e["texte"], "multi": False, "max": 12, "indication": "Prix, ex. 9,90",
                              "prix": True}
                o["fond"] = {"actif": e["fond"], "couleur": e["couleur_fond"]}
            else:
                o["titre"] = libres.FORMES[e["forme"]][0]
                o["texte"] = {"valeur": e["texte"], "multi": False, "max": 60,
                              "indication": "Texte dans la forme (facultatif)"}
                o["forme"] = e["forme"]
            if e["type"] != "forme" or e["texte"].strip():
                o["styles"] = {"police": e["police"], "gras": e["gras"], "italique": e["italique"],
                               "souligne": e["souligne"]}
            res[el] = o
    return res


def couleurs_affiche(ss):
    """Les couleurs déjà utilisées sur l'affiche (proposées en premier dans la palette)."""
    style = ss["style"]
    vues = []
    for cle in ("couleur_nom", "couleur_detail", "couleur_prix", "couleur_fond_prix", "couleur_accent",
                "couleur_secondaire"):
        c = _couleur_style(style, cle).upper()
        if c not in vues:
            vues.append(c)
    for e in ss.get("elements") or []:
        for c in (e.get("couleur"), e.get("couleur_fond") if e.get("fond") else None):
            if c and c.upper() not in vues:
                vues.append(c.upper())
    return vues[:10]


# ----------------------------------------------------------------------------
# Application des événements
# ----------------------------------------------------------------------------
def _hex(v):
    return v.upper() if isinstance(v, str) and _HEX.match(v) else None


def _maj_texte_style(ss, el, modif):
    """Police, gras, italique, souligné d'un texte de l'affiche (seuls les écarts à l'origine sont gardés)."""
    style = ss["style"]
    brut = dict(_style_texte(style, el))
    brut.update(modif)
    style["textes"] = af.textes_valides({**(style.get("textes") or {}), el: brut})


def _modif_styles(champ, valeur):
    if champ == "police":
        return {"police": valeur if valeur in af.POLICES else None}
    if champ == "style" and isinstance(valeur, dict):
        return {k: bool(valeur[k]) for k in ("gras", "italique", "souligne") if k in valeur}
    return None


def _maj_standard(ss, el, champ, valeur):
    style = ss["style"]
    if champ == "texte":
        texte = str(valeur or "").replace("\r", "")
        if el == "marque":
            ss["marque"] = texte.replace("\n", " ")[:MAX_TEXTE["marque"]]
        elif el == "detail":
            ss["detail"] = texte[:MAX_TEXTE["detail"]]
        elif el == "prix":
            ss["w_prix"] = texte.replace("\n", " ").strip()[:MAX_TEXTE["prix"]]
        elif el == "prix_barre":
            ss["w_barre"] = texte.replace("\n", " ").strip()[:MAX_TEXTE["prix_barre"]]
            ss["w_barre_on"] = True
        return
    if champ == "couleur":
        c = _hex(valeur)
        if c is None:
            return
        if el == "pastille":
            style["couleur_fond_prix" if style.get("fond_prix") else "couleur_prix"] = c
        elif el in COULEUR_ELEMENT:
            style[COULEUR_ELEMENT[el]] = c
        return
    if el == "prix" and champ == "fond":
        style["fond_prix"] = bool(valeur)
        return
    if el == "prix" and champ == "couleur_fond":
        c = _hex(valeur)
        if c:
            style["couleur_fond_prix"], style["fond_prix"] = c, True
        return
    if el == "marque" and champ == "majuscules":
        ss["w_majuscules"] = bool(valeur)
        return
    modif = _modif_styles(champ, valeur)
    if modif is not None and el in af.TEXTES:
        _maj_texte_style(ss, el, modif)


def _maj_libre(ss, ident, champ, valeur):
    e = next((x for x in ss.get("elements") or [] if x["id"] == ident), None)
    if e is None:
        return
    modif = _modif_styles(champ, valeur)
    if modif is None:
        if champ in ("texte", "forme"):
            modif = {champ: str(valeur or "")}
        elif champ in ("couleur", "couleur_fond"):
            c = _hex(valeur)
            if c is None:
                return
            modif = {champ: c}
            if champ == "couleur_fond":
                modif["fond"] = True
        elif champ in ("devant", "fond"):
            modif = {champ: bool(valeur)}
        else:
            return
    nouveau = libres.element_valide({**e, **modif})
    ss["elements"] = [nouveau if x["id"] == ident else x for x in ss["elements"]]


def _selectionner(ss, el):
    if el in af.ELEMENTS:
        ss["element_actif"], ss["selection_libre"] = el, False
    elif isinstance(el, str) and el.startswith("libre_"):
        ss["element_libre_actif"], ss["selection_libre"] = el[6:], True
    ss["selection_apercu"] = True


def appliquer(ss, ev):
    """Applique ce que l'aperçu a envoyé : un événement seul, ou un lot {ts, lot: [événements]} (gestes rapprochés :
    l'aperçu renvoie chaque geste jusqu'à ce qu'il soit confirmé, voir traites()). Retourne True si quelque chose de
    nouveau a été pris en compte. Un événement mal formé est ignoré : il ne doit jamais empêcher l'outil de s'afficher."""
    if not isinstance(ev, dict) or ev.get("ts") is None or ev.get("ts") == ss.get("dernier_ev"):
        return False
    if isinstance(ev.get("lot"), list):
        ss["dernier_ev"] = ev["ts"]
        deja = list(ss.get("evenements_traites") or [])
        nouveau = False
        for e in ev["lot"][:20]:
            if not isinstance(e, dict) or e.get("id") is None or e["id"] in deja:
                continue
            deja.append(e["id"])
            nouveau = _appliquer_un(ss, {**e, "ts": ("lot", e["id"])}) or nouveau
        ss["evenements_traites"] = deja[-40:]
        ss["dernier_ev"] = ev["ts"]
        return nouveau
    return _appliquer_un(ss, ev)


def traites(ss):
    """Identifiants des derniers gestes pris en compte (renvoyés à l'aperçu, qui cesse alors de les renvoyer)."""
    return list(ss.get("evenements_traites") or [])[-20:]


def _appliquer_un(ss, ev):
    if ev.get("ts") == ss.get("dernier_ev"):
        return False
    ss["dernier_ev"] = ev["ts"]
    noter(ss)
    try:
        _appliquer(ss, ev)
    except (TypeError, ValueError, KeyError, AttributeError, StopIteration):
        pass
    noter(ss)
    return True


def _nombre(v, defaut, mini, maxi):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return defaut
    return defaut if x != x else max(mini, min(maxi, x))


def _appliquer(ss, ev):
    action, el = ev.get("action"), ev.get("el")
    libre = isinstance(el, str) and el.startswith("libre_")
    ident = el[6:] if libre else None
    existe = el in af.ELEMENTS or (libre and any(x["id"] == ident for x in ss.get("elements") or []))

    if action == "annuler":
        annuler(ss)
        return
    if action == "retablir":
        retablir(ss)
        return
    if action == "deselect":
        ss["selection_apercu"] = False
        return
    if action == "ajouter":
        type_ = ev.get("type")
        if type_ in libres.TYPES:
            if len(ss.get("elements") or []) >= libres.MAX_ELEMENTS:
                ss["msg_alerte"] = f"Une affiche porte au plus {libres.MAX_ELEMENTS} éléments ajoutés."
            else:
                e = libres.nouveau(type_, ss["style"])
                ss["elements"] = list(ss.get("elements") or []) + [e]
                _selectionner(ss, "libre_" + e["id"])
                ss["ver"] = ss.get("ver", 0) + 1
        noter(ss)
        return
    if action == "police_affiche":
        if ev.get("valeur") in af.POLICES:
            ss["style"]["police"] = ss["w_police"] = ev["valeur"]
            ss["ver"] = ss.get("ver", 0) + 1
        noter(ss)
        return
    if action == "theme":
        theme = af.THEMES.get(ev.get("valeur"))
        if theme:
            ss["style"].update(theme)
            ss["style"]["couleur_detail"] = None
            ss["theme_choisi"] = ev["valeur"]
            ss["ver"] = ss.get("ver", 0) + 1
        noter(ss)
        return
    if not existe:
        return

    _selectionner(ss, el)
    if action is None:  # clic, glisser ou agrandir avec la poignée
        if not ev.get("clic"):
            ddx, ddy = _nombre(ev.get("ddx"), 0.0, -2.0, 2.0), _nombre(ev.get("ddy"), 0.0, -2.0, 2.0)
            ds = _nombre(ev.get("ds"), 1.0, 0.1, 10.0)
            if libre:
                ss["elements"] = [libres.deplacer(x, ddx, ddy, ds) if x["id"] == ident else x for x in ss["elements"]]
            else:
                g = ss["reglages"][el]
                g["dx"] += ddx
                g["dy"] -= ddy
                g["s"] = min(4.0, max(0.2, g["s"] * ds))
            ss["ver"] = ss.get("ver", 0) + 1
    elif action == "maj":
        if libre:
            _maj_libre(ss, ident, ev.get("champ"), ev.get("valeur"))
        else:
            _maj_standard(ss, el, ev.get("champ"), ev.get("valeur"))
        ss["ver"] = ss.get("ver", 0) + 1
    elif action == "taille":
        f = _nombre(ev.get("facteur"), 1.0, 0.5, 2.0)
        if libre:
            ss["elements"] = [libres.deplacer(x, 0, 0, f) if x["id"] == ident else x for x in ss["elements"]]
        else:
            g = ss["reglages"][el]
            g["s"] = min(4.0, max(0.2, g["s"] * f))
        ss["ver"] = ss.get("ver", 0) + 1
    elif action == "tourner" and libre:
        angle = _nombre(ev.get("angle"), 0.0, -360.0, 360.0)
        nouveaux = []
        for x in ss["elements"]:
            if x["id"] == ident:
                rot = ((x["rot"] + angle + 180) % 360) - 180
                x = libres.element_valide({**x, "rot": rot})
            nouveaux.append(x)
        ss["elements"] = nouveaux
        ss["ver"] = ss.get("ver", 0) + 1
    elif action == "reinit" and not libre:
        ss["reglages"][el] = af.reglages_defaut()[el]
        ss["ver"] = ss.get("ver", 0) + 1
    elif action == "dupliquer" and libre:
        e = next(x for x in ss["elements"] if x["id"] == ident)
        if len(ss["elements"]) >= libres.MAX_ELEMENTS:
            ss["msg_alerte"] = f"Une affiche porte au plus {libres.MAX_ELEMENTS} éléments ajoutés."
        else:
            copie = libres.dupliquer(e)
            ss["elements"] = ss["elements"] + [copie]
            _selectionner(ss, "libre_" + copie["id"])
            ss["ver"] = ss.get("ver", 0) + 1
    elif action == "supprimer" and libre:
        ids = [x["id"] for x in ss["elements"]]
        restants = [x for x in ss["elements"] if x["id"] != ident]
        ss["elements"] = restants
        ss["element_libre_actif"] = restants[min(ids.index(ident), len(restants) - 1)]["id"] if restants else None
        ss["selection_libre"] = False
        ss["selection_apercu"] = False
        ss["ver"] = ss.get("ver", 0) + 1
    elif action == "masquer_logo" and el == "logo":
        ss["w_logo"] = False
        ss["selection_apercu"] = False
        ss["msg_ouvert"] = "Logo masqué. Pour le remettre : étape 3, case « Afficher le logo »."
    elif action == "texte_marque" and el == "marque":
        ss["w_logo_marque"] = False
        ss["ver"] = ss.get("ver", 0) + 1
