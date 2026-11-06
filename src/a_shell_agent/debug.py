"""Debug-Ausgaben auf stdout, ausschließlich bei aktivem Debug-Schalter."""

from __future__ import annotations

from a_shell_agent.konfiguration import Konfiguration

_konfiguration: Konfiguration | None = None


def aktiviere(konfiguration: Konfiguration) -> None:
    """Merkt das Konfigurationsobjekt, aus dem der Debug-Schalter gelesen wird."""
    global _konfiguration
    _konfiguration = konfiguration


def debug_zeile(text: str) -> None:
    """Schreibt text nur bei aktivem Debug nach stdout, mit sofortigem Flush."""
    if _konfiguration is not None and _konfiguration.debug:
        print(text, flush=True)
