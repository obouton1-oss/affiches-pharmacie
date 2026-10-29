"""Entrée depuis le planning d'officine (module « Affiches promos » de la console centrale).

Le planning envoie vers l'outil avec « ?jeton=… » : un jeton signé (HMAC-SHA256) avec la clé partagée
PLANNING_CLE_JETONS (secret de l'outil, recopié depuis la console : Réglages → Modules).
Le jeton désigne la pharmacie (identifiant de son bloc [pharmacies.…]), expire au bout de 5 minutes et ne sert qu'une fois.
Sans clé définie, les jetons sont ignorés : seule la connexion par mot de passe fonctionne.
"""
import base64
import hashlib
import hmac
import json
import threading
import time

from chemins import reglage

DECALAGE_MAX = 120   # secondes de décalage d'horloge toléré entre le planning et l'outil
DUREE_MAX = 600      # un jeton annonçant une validité plus longue est refusé
_utilises: dict[str, float] = {}
_verrou = threading.Lock()


def _b64u_decoder(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def cle() -> str:
    return reglage("PLANNING_CLE_JETONS")


def active() -> bool:
    return bool(cle())


def verifier(jeton: str, maintenant: float | None = None):
    """Retourne (charge, "") si le jeton est valable (et le marque comme utilisé), sinon (None, raison)."""
    secret = cle()
    if not secret:
        return None, "Accès par le planning non configuré."
    maintenant = time.time() if maintenant is None else maintenant
    try:
        corps, signature = str(jeton).strip().split(".")
        attendue = hmac.new(secret.encode("utf-8"), corps.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(attendue, _b64u_decoder(signature)):
            return None, "Lien d'accès invalide."
        charge = json.loads(_b64u_decoder(corps).decode("utf-8"))
        iat, exp, nonce = int(charge["iat"]), int(charge["exp"]), str(charge["n"])
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return None, "Lien d'accès invalide."
    if charge.get("v") != 1 or exp - iat > DUREE_MAX or iat > maintenant + DECALAGE_MAX:
        return None, "Lien d'accès invalide."
    if maintenant > exp + DECALAGE_MAX:
        return None, "Lien d'accès expiré : rouvrir l'outil depuis le planning."
    with _verrou:
        for n, t in list(_utilises.items()):
            if t < maintenant:
                del _utilises[n]
        if nonce in _utilises:
            return None, "Lien d'accès déjà utilisé : rouvrir l'outil depuis le planning."
        _utilises[nonce] = exp + DECALAGE_MAX
    return charge, ""
