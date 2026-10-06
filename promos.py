"""Types de promotion des affiches.

Le type « Prix promo (standard) » est le fonctionnement d'origine : un prix en gros, avec un prix barré facultatif.
Les autres types affichent l'offre elle-même (« le 2e à –50 % », « 3 pour 2 », « 2 achetés = 1 offert »…), ou
ajoutent le pourcentage / le montant de la remise en pastille à côté d'un prix promo calculé automatiquement.

composer(type, champs, options) retourne un dictionnaire :
  "rendu"   : ce qui est dessiné sur l'affiche (voir affiche.construire_pdf, paramètre « promo »), ou None si les
              informations sont incomplètes ou invalides ;
  "erreurs" : messages d'erreur à afficher ;
  "textes"  : textes qui peuvent être affichés ou non (libellés des cases « Faire apparaître ») ;
  "resume"  : calcul détaillé, pour l'écran uniquement (jamais imprimé).

Les textes peuvent contenir « ^{e} » : le « e » est alors écrit en exposant (2^{e} = 2ᵉ).
"""
import re
from decimal import Decimal, ROUND_HALF_UP

from affiche import parse_prix

STANDARD = "standard"
NBSP = " "
MOINS = "–"  # tiret demi-cadratin : présent dans toutes les polices de l'outil (le vrai signe moins ne l'est pas)

# Valeurs de départ de tous les champs (le type choisi n'en utilise qu'une partie)
CHAMPS_DEFAUT = {"pct": 20, "rang": 2, "n": 3, "m": 2, "achetes": 2, "offerts": 1, "montant": "3",
                 "prix_normal": "", "prix_lot": "", "prix_unite": "", "prix": "", "seuil": "50", "valeur": "10",
                 "unite": "€", "cadeau": "", "cond": "aucune", "kicker": "", "grand": "", "precision": ""}
OPTIONS_DEFAUT = {"pastille": True, "barre": True, "calcul": True, "calcul_fmt": "total"}

_PRIX_NORMAL = dict(nom="prix_normal", libelle="Prix normal d'un produit (€)", genre="prix", placeholder="10,50",
                    facultatif=True)

# Ordre des types = ordre du menu déroulant.
# champs : nom, libellé, genre (entier / prix / texte / choix) ; « si » = champ affiché seulement pour une valeur donnée.
TYPES = {
    STANDARD: {"libelle": "Prix promo (standard)"},
    "pourcentage": {
        "libelle": "Pourcentage de remise  ·  –20 %",
        "aide": "Affiche –20 %. Avec le prix normal, l'outil calcule aussi le prix promo.",
        "champs": [dict(nom="pct", libelle="Pourcentage de remise (%)", genre="entier", mini=1, maxi=99),
                   _PRIX_NORMAL],
        "defauts": {"pct": 20},
        "options": ["pastille", "barre"],
        "nom_pastille": "le pourcentage",
    },
    "montant": {
        "libelle": "Montant à déduire  ·  –3 €",
        "aide": "Affiche –3 €. Avec le prix normal, l'outil calcule aussi le prix promo.",
        "champs": [dict(nom="montant", libelle="Montant à déduire (€)", genre="prix", placeholder="3,00"),
                   _PRIX_NORMAL],
        "defauts": {"montant": "3"},
        "options": ["pastille", "barre"],
        "nom_pastille": "le montant",
    },
    "pct_nieme": {
        "libelle": "Pourcentage sur le 2e produit acheté  ·  le 2e à –50 %",
        "aide": "Remise sur le 2e (ou 3e…) produit acheté. Avec le prix normal, l'outil calcule le prix des "
                "produits achetés.",
        "champs": [dict(nom="rang", libelle="Remise sur le … produit acheté", genre="entier", mini=2, maxi=6),
                   dict(nom="pct", libelle="Pourcentage de remise (%)", genre="entier", mini=1, maxi=99),
                   _PRIX_NORMAL],
        "defauts": {"rang": 2, "pct": 50},
        "options": ["calcul"],
    },
    "pct_lot": {
        "libelle": "Pourcentage sur un lot  ·  lot de 3 à –30 %",
        "aide": "Remise sur tous les produits d'un lot. Avec le prix normal, l'outil calcule le prix du lot.",
        "champs": [dict(nom="n", libelle="Nombre de produits du lot", genre="entier", mini=2, maxi=10),
                   dict(nom="pct", libelle="Pourcentage de remise (%)", genre="entier", mini=1, maxi=99),
                   _PRIX_NORMAL],
        "defauts": {"n": 3, "pct": 30},
        "options": ["calcul"],
    },
    "offert": {
        "libelle": "Produit(s) offert(s)  ·  2 achetés = 1 offert",
        "aide": "Avec le prix normal, l'outil calcule le prix payé pour l'ensemble des produits reçus.",
        "champs": [dict(nom="achetes", libelle="Produits achetés", genre="entier", mini=1, maxi=10),
                   dict(nom="offerts", libelle="Produits offerts", genre="entier", mini=1, maxi=5),
                   _PRIX_NORMAL],
        "defauts": {"achetes": 2, "offerts": 1},
        "options": ["calcul"],
    },
    "n_pour_m": {
        "libelle": "N pour le prix de M  ·  3 pour 2",
        "aide": "Avec le prix normal, l'outil calcule le prix payé pour l'ensemble des produits reçus.",
        "champs": [dict(nom="n", libelle="Produits reçus", genre="entier", mini=2, maxi=10),
                   dict(nom="m", libelle="Produits payés", genre="entier", mini=1, maxi=9),
                   _PRIX_NORMAL],
        "defauts": {"n": 3, "m": 2},
        "options": ["calcul"],
    },
    "prix_lot": {
        "libelle": "Lot à prix fixe  ·  lot de 3 à 19,90 €",
        "aide": "Le prix du lot est mis en avant. Avec le prix normal d'un produit, l'outil peut afficher le prix "
                "sans promotion (barré) et le prix à l'unité.",
        "champs": [dict(nom="n", libelle="Nombre de produits du lot", genre="entier", mini=2, maxi=10),
                   dict(nom="prix_lot", libelle="Prix du lot (€)", genre="prix", placeholder="19,90"),
                   _PRIX_NORMAL],
        "defauts": {"n": 3},
        "options": ["barre", "calcul"],
    },
    "prix_degressif": {
        "libelle": "Prix réduit dès N achetés  ·  dès 2 : 8,90 € l'unité",
        "aide": "Le prix à l'unité est mis en avant, à partir d'un certain nombre de produits achetés.",
        "champs": [dict(nom="n", libelle="À partir de … produits achetés", genre="entier", mini=2, maxi=10),
                   dict(nom="prix_unite", libelle="Prix à l'unité dans l'offre (€)", genre="prix",
                        placeholder="8,90"),
                   _PRIX_NORMAL],
        "defauts": {"n": 2},
        "options": ["barre", "calcul"],
    },
    "seuil": {
        "libelle": "Remise selon le montant d'achat  ·  –10 € dès 50 € d'achat",
        "aide": "Remise sur le panier, à partir d'un montant d'achat.",
        "champs": [dict(nom="seuil", libelle="À partir de … € d'achat", genre="prix", placeholder="50"),
                   dict(nom="valeur", libelle="Remise", genre="prix", placeholder="10"),
                   dict(nom="unite", libelle="Unité de la remise", genre="choix", choix=["€", "%"])],
        "defauts": {"seuil": "50", "valeur": "10", "unite": "€"},
        "options": [],
    },
    "cadeau": {
        "libelle": "Cadeau offert  ·  une trousse offerte dès 2 produits",
        "aide": "Un cadeau est offert, sans condition ou à partir d'un nombre de produits ou d'un montant d'achat.",
        "champs": [dict(nom="cadeau", libelle="Cadeau offert", genre="texte", placeholder="Une trousse de toilette"),
                   dict(nom="cond", libelle="Condition", genre="choix",
                        choix=[("aucune", "Aucune condition"), ("produits", "À partir de N produits achetés"),
                               ("montant", "À partir d'un montant d'achat")]),
                   dict(nom="n", libelle="À partir de … produits achetés", genre="entier", mini=1, maxi=10,
                        si=("cond", "produits")),
                   dict(nom="seuil", libelle="À partir de … € d'achat", genre="prix", placeholder="40",
                        si=("cond", "montant"))],
        "defauts": {"cond": "aucune"},
        "options": [],
    },
    "plus_produit": {
        "libelle": "Format promo  ·  +25 % de produit offert",
        "aide": "Le produit contient davantage pour le même prix. Le prix promo est facultatif.",
        "champs": [dict(nom="pct", libelle="Produit en plus (%)", genre="entier", mini=1, maxi=500),
                   dict(nom="prix", libelle="Prix promo (€) – facultatif", genre="prix", placeholder="7,90",
                        facultatif=True)],
        "defauts": {"pct": 25},
        "options": [],
    },
    "libre": {
        "libelle": "Autre offre (texte libre)",
        "aide": "Pour toute offre non prévue ci-dessus : le texte principal est écrit en grand dans le bandeau.",
        "champs": [dict(nom="kicker", libelle="Petit texte au-dessus (facultatif)", genre="texte",
                        placeholder="Journée de la peau", facultatif=True),
                   dict(nom="grand", libelle="Texte principal (en grand)", genre="texte", placeholder="–15 % en cabine")],
        "defauts": {},
        "options": [],
    },
}

# Libellés des cases « Faire apparaître … » (avec le texte qui sera affiché, rempli par l'application)
LIBELLES_OPTIONS = {
    "pastille": "Faire apparaître {nom} dans une pastille",
    "barre": "Faire apparaître le prix sans promotion, barré",
    "calcul": "Faire apparaître le calcul",
}
FORMATS_CALCUL = {"total": "Prix de l'ensemble", "unite": "Prix à l'unité"}


# ----------------------------------------------------------------------------
# Mise en forme des nombres et des montants
# ----------------------------------------------------------------------------
def arrondi(x) -> Decimal:
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def eur(x) -> str:
    """15 -> '15 €' ; 12,5 -> '12,50 €' (espace insécable avant le €)."""
    ent, cts = divmod(int(arrondi(x) * 100), 100)
    return f"{ent},{cts:02d}{NBSP}€" if cts else f"{ent}{NBSP}€"


def nombre(x) -> str:
    """10 -> '10' ; 12,5 -> '12,5'."""
    return format(Decimal(x).normalize(), "f").replace(".", ",")


def pct_texte(p, signe=MOINS) -> str:
    return f"{signe}{nombre(p)}{NBSP}%"


def ordinal(n: int) -> str:
    return "1^{er}" if n == 1 else f"{n}^{{e}}"


def pluriel(n: int, mot: str) -> str:
    return f"{n} {mot}{'S' if n > 1 else ''}"


def sans_balises(texte: str) -> str:
    """Texte brut (sans exposant ni espace insécable), pour l'écran."""
    return re.sub(r"\^\{([^}]*)\}", r"\1", texte).replace(NBSP, " ")


# ----------------------------------------------------------------------------
# Lecture des champs
# ----------------------------------------------------------------------------
def _prix(ch, cle, libelle, res, obligatoire=False):
    brut = ch.get(cle)
    if brut is None or not str(brut).strip():
        if obligatoire:
            res["erreurs"].append(f"{libelle} : à renseigner.")
        return None
    v = parse_prix(brut)
    if v is None or v <= 0:
        res["erreurs"].append(f"{libelle} : montant invalide.")
        return None
    return v


def _entier(ch, cle, libelle, mini, maxi, res):
    try:
        v = int(ch.get(cle))
    except (TypeError, ValueError):
        res["erreurs"].append(f"{libelle} : nombre entier attendu.")
        return None
    if not mini <= v <= maxi:
        res["erreurs"].append(f"{libelle} : doit être compris entre {mini} et {maxi}.")
        return None
    return v


def _resume(nb, paye, normal) -> str:
    eco = normal - paye
    if nb == 1:
        return f"Prix promo : {eur(paye)} au lieu de {eur(normal)} (économie : {eur(eco)})."
    return (f"{nb} produits : {eur(paye)} au lieu de {eur(normal)}, soit {eur(paye / nb)} l'unité "
            f"(économie : {eur(eco)}).")


def _calcul_multiple(res, nb, paye, normal, prix_unite):
    """Textes du calcul pour une offre portant sur `nb` produits payés `paye` au lieu de `normal`."""
    res["textes"]["calcul"] = {
        "total": f"Soit {eur(paye)} les {nb} au lieu de {eur(normal)}",
        "unite": f"Soit {eur(paye / nb)} l'unité au lieu de {eur(prix_unite)}",
    }
    res["resume"] = _resume(nb, paye, normal)


# ----------------------------------------------------------------------------
# Un compositeur par type : remplit res["rendu"] (sans la précision libre, ajoutée à la fin)
# ----------------------------------------------------------------------------
def _pourcentage(ch, op, res):
    pct = _entier(ch, "pct", "Pourcentage", 1, 99, res)
    prix = _prix(ch, "prix_normal", "Prix normal", res)
    if pct is None or res["erreurs"]:
        return
    texte = pct_texte(pct)
    if prix is None:
        res["rendu"] = {"grand": texte}
        return
    nouveau = arrondi(prix * (100 - pct) / 100)
    if nouveau <= 0 or nouveau >= prix:
        res["erreurs"].append("Avec ce pourcentage, le prix promo calculé ne change pas ou est nul.")
        return
    res["textes"].update(pastille=texte, barre=eur(prix))
    res["resume"] = _resume(1, nouveau, prix)
    res["rendu"] = {"prix": nouveau, "prix_barre": prix if op.get("barre", True) else None,
                    "pastille": texte if op.get("pastille", True) else ""}


def _montant(ch, op, res):
    montant = _prix(ch, "montant", "Montant à déduire", res, obligatoire=True)
    prix = _prix(ch, "prix_normal", "Prix normal", res)
    if montant is None or res["erreurs"]:
        return
    texte = MOINS + eur(montant)
    if prix is None:
        res["rendu"] = {"grand": texte}
        return
    nouveau = prix - montant
    if nouveau <= 0:
        res["erreurs"].append("Le montant à déduire doit être inférieur au prix normal.")
        return
    res["textes"].update(pastille=texte, barre=eur(prix))
    res["resume"] = _resume(1, nouveau, prix)
    res["rendu"] = {"prix": nouveau, "prix_barre": prix if op.get("barre", True) else None,
                    "pastille": texte if op.get("pastille", True) else ""}


def _pct_nieme(ch, op, res):
    rang = _entier(ch, "rang", "Rang du produit", 2, 6, res)
    pct = _entier(ch, "pct", "Pourcentage", 1, 99, res)
    prix = _prix(ch, "prix_normal", "Prix normal", res)
    if rang is None or pct is None or res["erreurs"]:
        return
    res["rendu"] = {"kicker": f"LE {ordinal(rang)} À", "grand": pct_texte(pct), "lignes": []}
    if prix is not None:
        _calcul_multiple(res, rang, prix * (rang - Decimal(pct) / 100), prix * rang, prix)


def _pct_lot(ch, op, res):
    n = _entier(ch, "n", "Taille du lot", 2, 10, res)
    pct = _entier(ch, "pct", "Pourcentage", 1, 99, res)
    prix = _prix(ch, "prix_normal", "Prix normal", res)
    if n is None or pct is None or res["erreurs"]:
        return
    res["rendu"] = {"kicker": f"LOT DE {n}", "grand": pct_texte(pct), "lignes": []}
    if prix is not None:
        _calcul_multiple(res, n, prix * n * (100 - pct) / 100, prix * n, prix)


def _offert(ch, op, res):
    achetes = _entier(ch, "achetes", "Produits achetés", 1, 10, res)
    offerts = _entier(ch, "offerts", "Produits offerts", 1, 5, res)
    prix = _prix(ch, "prix_normal", "Prix normal", res)
    if achetes is None or offerts is None or res["erreurs"]:
        return
    res["rendu"] = {"kicker": pluriel(achetes, "ACHETÉ"), "grand": pluriel(offerts, "OFFERT"), "lignes": []}
    if prix is not None:
        total = achetes + offerts
        _calcul_multiple(res, total, prix * achetes, prix * total, prix)


def _n_pour_m(ch, op, res):
    n = _entier(ch, "n", "Produits reçus", 2, 10, res)
    m = _entier(ch, "m", "Produits payés", 1, 9, res)
    prix = _prix(ch, "prix_normal", "Prix normal", res)
    if n is not None and m is not None and m >= n:
        res["erreurs"].append("Le nombre de produits payés doit être inférieur au nombre de produits reçus.")
    if n is None or m is None or res["erreurs"]:
        return
    res["rendu"] = {"grand": f"{n} POUR {m}", "lignes": []}
    if prix is not None:
        _calcul_multiple(res, n, prix * m, prix * n, prix)


def _prix_lot(ch, op, res):
    n = _entier(ch, "n", "Taille du lot", 2, 10, res)
    lot = _prix(ch, "prix_lot", "Prix du lot", res, obligatoire=True)
    prix = _prix(ch, "prix_normal", "Prix normal", res)
    if n is not None and lot is not None and prix is not None and prix * n <= lot:
        res["erreurs"].append(f"Le prix du lot doit être inférieur à {n} × le prix normal ({eur(prix * n)}).")
    if n is None or lot is None or res["erreurs"]:
        return
    res["rendu"] = {"kicker": f"LOT DE {n}", "prix": lot, "lignes": []}
    res["textes"]["calcul"] = {"total": f"Soit {eur(lot / n)} l'unité"}
    if prix is not None:
        res["textes"]["barre"] = eur(prix * n)
        res["resume"] = _resume(n, lot, prix * n)
        if op.get("barre", True):
            res["rendu"]["prix_barre"] = prix * n
    else:
        res["resume"] = f"Soit {eur(lot / n)} l'unité."


def _prix_degressif(ch, op, res):
    n = _entier(ch, "n", "Nombre de produits achetés", 2, 10, res)
    unite = _prix(ch, "prix_unite", "Prix à l'unité", res, obligatoire=True)
    prix = _prix(ch, "prix_normal", "Prix normal", res)
    if unite is not None and prix is not None and prix <= unite:
        res["erreurs"].append("Le prix à l'unité de l'offre doit être inférieur au prix normal.")
    if n is None or unite is None or res["erreurs"]:
        return
    res["rendu"] = {"kicker": f"DÈS {n} ACHETÉS", "prix": unite, "lignes": ["Prix à l'unité"]}
    if prix is not None:
        res["textes"]["barre"] = eur(prix)
        res["textes"]["calcul"] = {"total": f"Soit {eur(unite * n)} les {n} au lieu de {eur(prix * n)}"}
        res["resume"] = _resume(n, unite * n, prix * n)
        if op.get("barre", True):
            res["rendu"]["prix_barre"] = prix
    else:
        res["textes"]["calcul"] = {"total": f"Soit {eur(unite * n)} les {n}"}
        res["resume"] = f"Soit {eur(unite * n)} les {n}."


def _seuil(ch, op, res):
    seuil = _prix(ch, "seuil", "Montant d'achat", res, obligatoire=True)
    valeur = _prix(ch, "valeur", "Remise", res, obligatoire=True)
    pourcent = ch.get("unite") == "%"
    if pourcent and valeur is not None and valeur >= 100:
        res["erreurs"].append("Remise : doit être inférieure à 100 %.")
    if seuil is None or valeur is None or res["erreurs"]:
        return
    res["rendu"] = {"kicker": f"DÈS {eur(seuil)} D'ACHAT",
                    "grand": pct_texte(valeur) if pourcent else MOINS + eur(valeur), "lignes": []}


def _cadeau(ch, op, res):
    cadeau = str(ch.get("cadeau") or "").strip()
    if not cadeau:
        res["erreurs"].append("Cadeau offert : à renseigner.")
    cond, kicker = ch.get("cond") or "aucune", "CADEAU"
    if cond == "produits":
        n = _entier(ch, "n", "Nombre de produits achetés", 1, 10, res)
        kicker = f"DÈS {n} ACHETÉS" if n is not None else kicker
    elif cond == "montant":
        seuil = _prix(ch, "seuil", "Montant d'achat", res, obligatoire=True)
        kicker = f"DÈS {eur(seuil)} D'ACHAT" if seuil is not None else kicker
    if res["erreurs"]:
        return
    res["rendu"] = {"kicker": kicker, "grand": "OFFERT", "lignes": [cadeau]}


def _plus_produit(ch, op, res):
    pct = _entier(ch, "pct", "Produit en plus", 1, 500, res)
    prix = _prix(ch, "prix", "Prix promo", res)
    if pct is None or res["erreurs"]:
        return
    if prix is not None:
        res["rendu"] = {"kicker": f"FORMAT +{pct}{NBSP}% OFFERT", "prix": prix, "lignes": []}
    else:
        res["rendu"] = {"kicker": "FORMAT PROMO", "grand": f"+{pct}{NBSP}%", "lignes": ["de produit offert"]}


def _libre(ch, op, res):
    grand = str(ch.get("grand") or "").strip()
    if not grand:
        res["erreurs"].append("Texte principal : à renseigner.")
        return
    res["rendu"] = {"kicker": str(ch.get("kicker") or "").strip(), "grand": grand, "lignes": []}


_COMPOSEURS = {"pourcentage": _pourcentage, "montant": _montant, "pct_nieme": _pct_nieme, "pct_lot": _pct_lot,
               "offert": _offert, "n_pour_m": _n_pour_m, "prix_lot": _prix_lot, "prix_degressif": _prix_degressif,
               "seuil": _seuil, "cadeau": _cadeau, "plus_produit": _plus_produit, "libre": _libre}


def champs_visibles(type_: str, champs: dict):
    """Champs du type, sans ceux dont la condition d'affichage n'est pas remplie."""
    liste = []
    for f in (TYPES.get(type_) or {}).get("champs", []):
        si = f.get("si")
        if si and (champs or {}).get(si[0]) != si[1]:
            continue
        liste.append(f)
    return liste


def composer(type_: str, champs: dict, options: dict | None = None) -> dict:
    """Voir l'en-tête du module. Pour le type standard, « rendu » vaut None (affiche d'origine)."""
    champs, options = dict(champs or {}), dict(options or {})
    res = {"rendu": None, "erreurs": [], "textes": {}, "resume": ""}
    if type_ not in _COMPOSEURS:
        return res
    _COMPOSEURS[type_](champs, options, res)
    rendu = res["rendu"]
    if rendu is None:
        return res
    lignes = list(rendu.pop("lignes", []))
    calcul = res["textes"].get("calcul")
    if calcul and options.get("calcul", True) and "calcul" in TYPES[type_].get("options", []):
        lignes.append(calcul.get(options.get("calcul_fmt") or "total") or next(iter(calcul.values())))
    precision = str(champs.get("precision") or "").strip()
    if precision:
        lignes.append(precision)
    rendu["ligne"] = "\n".join(lignes)
    rendu.setdefault("kicker", "")
    rendu.setdefault("grand", "")
    rendu.setdefault("prix", None)
    rendu.setdefault("prix_barre", None)
    rendu.setdefault("pastille", "")
    return res


# ----------------------------------------------------------------------------
# Résumés pour l'historique (l'entrée enregistrée contient « promo » : type, champs, options)
# ----------------------------------------------------------------------------
def rendu_entree(entree: dict):
    """Rendu de la promotion d'une entrée de l'historique, ou None pour une affiche « prix promo » standard."""
    promo = entree.get("promo") or {}
    if promo.get("type") in (None, STANDARD):
        return None
    return composer(promo["type"], promo.get("champs"), promo.get("options"))["rendu"]


def _virgule(valeur) -> str:
    return str(valeur).replace(".", ",")


def _phrase(texte: str) -> str:
    """'2 ACHETÉS 1 OFFERT' -> '2 achetés 1 offert' (les mots tout en majuscules passent en minuscules)."""
    mots = [m.lower() if m.isupper() and (len(m) > 1 or m == "À") else m for m in texte.split(" ")]
    phrase = " ".join(mots)
    return phrase[:1].upper() + phrase[1:]


def resume_entree(entree: dict) -> str:
    """Texte court de l'offre : « 7,90 € (au lieu de 10,50 €) », « Le 2e à –50 % », « Lot de 3 : 19,90 € »…"""
    rendu = rendu_entree(entree)
    if rendu is None:
        if not entree.get("prix"):
            return ""
        texte = f"{_virgule(entree['prix'])} €"
        if entree.get("prix_barre"):
            texte += f" (au lieu de {_virgule(entree['prix_barre'])} €)"
        return texte
    kicker = rendu.get("kicker") or ""
    if rendu.get("grand"):
        liaison = " " if not kicker or kicker.endswith(" À") else " : "  # « Le 2e à –50 % », « Lot de 3 : –30 % »
        texte = _phrase(f"{kicker}{liaison}{rendu['grand']}".strip())
    else:
        texte = _phrase(f"{kicker} : {eur(rendu['prix'])}" if kicker else eur(rendu["prix"]))
    if rendu.get("pastille"):
        texte = f"{rendu['pastille']} · {texte}"
    if rendu.get("prix_barre") is not None:
        texte += f" (au lieu de {eur(rendu['prix_barre'])})"
    return sans_balises(texte)
