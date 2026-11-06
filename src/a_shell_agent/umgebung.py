"""Umgebungsvariablen und eigener, stdlib-only .env-Loader."""

from __future__ import annotations

import os
from pathlib import Path

from a_shell_agent.fehler import AgentFehler

ENV_DATEI = ".env"


def lade_env(pfad: Path) -> dict[str, str]:
    """Liest Zeilen der Form SCHLÜSSEL=WERT.

    Bereits gesetzte Umgebungsvariablen gewinnen.
    """
    werte: dict[str, str] = {}
    try:
        text = pfad.read_text(encoding="utf-8")
    except OSError:
        return werte
    for zeile in text.splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        schluessel, _, wert = zeile.partition("=")
        werte[schluessel.strip()] = wert.strip()
    return werte


def hole_mistral_schluessel(arbeitsverzeichnis: Path) -> str:
    """API-Schlüssel aus MISTRAL_API_KEY, sonst aus .env im Arbeitsverzeichnis."""
    schluessel = os.environ.get("MISTRAL_API_KEY", "").strip()
    if not schluessel:
        schluessel = (
            lade_env(arbeitsverzeichnis / ENV_DATEI).get("MISTRAL_API_KEY", "").strip()
        )
    if not schluessel:
        raise AgentFehler(
            "MISTRAL_API_KEY fehlt. Zwei Wege: Umgebungsvariable setzen "
            "(export MISTRAL_API_KEY=...) oder eine Datei .env im "
            "Arbeitsverzeichnis mit der Zeile MISTRAL_API_KEY=... anlegen. "
            "Bereits gesetzte Umgebungsvariablen gewinnen über .env."
        )
    return schluessel
