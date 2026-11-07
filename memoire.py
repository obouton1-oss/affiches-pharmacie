"""Mémoire du serveur partagé (hébergement gratuit : environ 1 à 2,7 Go pour toutes les pharmacies connectées).

Sous Linux, la mémoire libérée par Python (grandes images, PDF) reste réservée par l'allocateur du système, par « arène »
(une par fil d'exécution, donc par session). rendre() la restitue au système ; elle est appelée à la fin de chaque
affichage (voir app.py). Durée : quelques millisecondes. Sans effet sous Windows et Mac.
"""
import ctypes
import threading

_verrou = threading.Lock()


def _libc():
    try:
        libc = ctypes.CDLL("libc.so.6")
        libc.mallopt(-8, 2)  # M_ARENA_MAX : 2 arènes au plus (moins de mémoire réservée et non rendue)
        return libc
    except Exception:
        return None


_LIBC = _libc()


def rendre() -> None:
    """Rend au système la mémoire libérée (sans ramasse-miettes Python : trop lent à chaque affichage)."""
    if _LIBC is None:
        return
    if _verrou.acquire(blocking=False):  # une session à la fois suffit ; les autres n'attendent pas
        try:
            _LIBC.malloc_trim(0)
        except Exception:
            pass
        finally:
            _verrou.release()
