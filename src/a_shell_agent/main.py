"""Orchestrierung des Gesamtablaufs (Schritte 1 bis 9)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from a_shell_agent.api import ArbeitsamtKlient
from a_shell_agent.debug import aktiviere as debug_aktiviere
from a_shell_agent.fehler import AgentFehler
from a_shell_agent.konfiguration import Konfiguration, baue_konfiguration
from a_shell_agent.llm import MistralKlient
from a_shell_agent.pipeline import (
    stufe1_filtern,
    stufe2_bewerten,
    stufe3_bericht,
)
from a_shell_agent.prompts import (
    DATEINAME_BERICHT,
    DATEINAME_STUFE1,
    DATEINAME_STUFE2,
    VORLAGE_BERICHT,
    VORLAGE_STUFE1,
    VORLAGE_STUFE2,
    lade_prompt,
)
from a_shell_agent.umgebung import hole_mistral_schluessel

DATEI_STELLEN = "stellen.json"
DATEI_DETAILS = "details.json"


def _schreibe_json(pfad: Path, daten: Any) -> None:
    try:
        pfad.write_text(
            json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as e:
        raise AgentFehler(f"{pfad.name} konnte nicht geschrieben werden: {e}") from e


def _ohne_refnr_ueberspringen(daten: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nutzbar: list[dict[str, Any]] = []
    for datensatz in daten:
        if datensatz.get("referenznummer"):
            nutzbar.append(datensatz)
        else:
            print(
                f"[Warnung] Datensatz ohne referenznummer übersprungen: "
                f"{json.dumps(datensatz, ensure_ascii=False)[:120]}",
                flush=True,
            )
    return nutzbar


def fuehre_aus(
    konfig: Konfiguration,
    arbeitsamt: ArbeitsamtKlient,
    mistral: MistralKlient,
    arbeitsverzeichnis: Path,
) -> int:
    """Führt die Schritte 2 bis 9 aus; Step 1 (Parameter) ist bereits geprüft."""
    debug_aktiviere(konfig)
    angebote = arbeitsamt.hole_angebote(konfig.plz, konfig.zeitraum, konfig.entfernung)
    _schreibe_json(arbeitsverzeichnis / DATEI_STELLEN, angebote)
    if not angebote:
        print("Keine Daten vorhanden")
        _aufraeumen(arbeitsverzeichnis, konfig.debug)
        return 0

    nutzbar = _ohne_refnr_ueberspringen(angebote)
    prompt1 = lade_prompt(
        arbeitsverzeichnis / DATEINAME_STUFE1, VORLAGE_STUFE1, platzhalter_pflicht=True
    )
    kandidaten = stufe1_filtern(nutzbar, prompt1, mistral, konfig.modell_stufe1)
    if not kandidaten:
        print("Keine Daten vorhanden")
        _aufraeumen(arbeitsverzeichnis, konfig.debug)
        return 0
    print(f"[Stufe 1] {len(kandidaten)} von {len(nutzbar)} Angeboten behalten")

    details: list[dict[str, Any]] = []
    fehlerzahl = 0
    for kandidat in kandidaten:
        referenznummer = str(kandidat["referenznummer"])
        try:
            details.append(arbeitsamt.hole_detail(referenznummer))
        except AgentFehler as e:
            fehlerzahl += 1
            print(
                f"[Warnung] Detailabruf fehlgeschlagen, übersprungen: {e}", flush=True
            )
    if details:
        _schreibe_json(arbeitsverzeichnis / DATEI_DETAILS, details)
    else:
        raise AgentFehler(
            "Kein einziger Detailabruf erfolgreich, details.json nicht geschrieben."
        )

    prompt2 = lade_prompt(
        arbeitsverzeichnis / DATEINAME_STUFE2, VORLAGE_STUFE2, platzhalter_pflicht=True
    )
    bewertungen = stufe2_bewerten(details, prompt2, mistral, konfig.modell_stufe2)
    angenommen = [b for b in bewertungen if b.angenommen]
    if not angenommen:
        print("Keine Jobs gefunden")
        _aufraeumen(arbeitsverzeichnis, konfig.debug)
        return 0

    refnr_zu_detail = {
        str(d.get("referenznummer")): d
        for d in details
        if d.get("referenznummer") is not None
    }
    details_angenommen = [
        refnr_zu_detail[b.referenznummer]
        for b in angenommen
        if b.referenznummer in refnr_zu_detail
    ]
    if not details_angenommen:
        raise AgentFehler(
            "Angenommene Jobs lassen sich den Detail-Datensätzen nicht zuordnen "
            "(referenznummer fehlt)."
        )

    prompt3 = lade_prompt(
        arbeitsverzeichnis / DATEINAME_BERICHT,
        VORLAGE_BERICHT,
        platzhalter_pflicht=False,
    )
    bericht = stufe3_bericht(
        details_angenommen, prompt3, mistral, konfig.modell_bericht, konfig
    )
    bericht_pfad = (
        arbeitsverzeichnis
        / f"Bericht-{konfig.plz}-{konfig.zeitraum}-{konfig.entfernung}.txt"
    )
    try:
        bericht_pfad.write_text(bericht, encoding="utf-8")
    except OSError as e:
        raise AgentFehler(f"Bericht konnte nicht geschrieben werden: {e}") from e
    print(f"Bericht geschrieben: {bericht_pfad.name}")

    _aufraeumen(arbeitsverzeichnis, konfig.debug)
    print("Fertig.")
    return 0


def _aufraeumen(arbeitsverzeichnis: Path, debug: bool) -> None:
    """Schritt 8: ohne --debug werden stellen.json und details.json gelöscht."""
    if debug:
        print(f"[Debug] {DATEI_STELLEN} und {DATEI_DETAILS} bleiben liegen.")
        return
    for name in (DATEI_STELLEN, DATEI_DETAILS):
        pfad = arbeitsverzeichnis / name
        try:
            pfad.unlink(missing_ok=True)
        except OSError as e:
            print(f"[Warnung] {name} konnte nicht gelöscht werden: {e}", flush=True)


def main(argv: list[str] | None = None) -> int:
    """Einstieg: Parameter prüfen, Clients bauen, Ablauf fahren."""
    try:
        konfig = baue_konfiguration(sys.argv[1:] if argv is None else argv)
        arbeitsverzeichnis = Path.cwd()
        schluessel = hole_mistral_schluessel(arbeitsverzeichnis)
        arbeitsamt = ArbeitsamtKlient()
        mistral = MistralKlient(schluessel)
        return fuehre_aus(konfig, arbeitsamt, mistral, arbeitsverzeichnis)
    except AgentFehler as e:
        print(f"Fehler: {e}", file=sys.stderr, flush=True)
        return 1
