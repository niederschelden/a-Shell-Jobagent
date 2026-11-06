"""Die drei LLM-Stufen: Filter 1, Filter 2, Generator (Bericht).

Alle Stufen erhalten die Datensätze roh und vollständig; der Code trifft
keine Feldauswahl und nimmt nur an, dass jeder Datensatz ein Arbeitsangebot
mit einer Referenznummer ist (die ausschließlich zur Verwaltung dient).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from a_shell_agent.fehler import AgentFehler
from a_shell_agent.konfiguration import Konfiguration
from a_shell_agent.llm import MistralKlient

ANGENOMMEN_WERTE = {"angenommen", "ja", "akzeptiert", "true"}


@dataclass(frozen=True)
class Bewertung:
    """Zwischenspeicher der Stufe 2: Entscheidung je Job mit Begründung."""

    referenznummer: str
    angenommen: bool
    beggruendung: str


def stufe1_filtern(
    daten: list[dict[str, Any]], prompt: str, mistral: MistralKlient, modell: str
) -> list[dict[str, Any]]:
    """Filter-Stufe 1: Aussortieren anhand der Überschriften-Datensätze.

    Ein Aufruf mit allen Datensätzen; die Antwort nennt die Referenznummern
    der zu behaltenden Angebote. Unbekannte Referenznummern werden verworfen.
    """
    nutzer = json.dumps(daten, ensure_ascii=False)
    antwort = mistral.frage(
        modell, prompt, nutzer, json_objekt=True, debug_bezeichnung="Stufe 1 JSON"
    )
    gewaehlt = _referenznummern_aus(antwort)
    bekannt = {d.get("referenznummer") for d in daten}
    ungueltig = [r for r in gewaehlt if r not in bekannt]
    if ungueltig:
        print(
            f"[Stufe 1] Unbekannte Referenznummern verworfen: {ungueltig}",
            flush=True,
        )
    return [d for d in daten if d.get("referenznummer") in set(gewaehlt)]


def stufe2_bewerten(
    daten: list[dict[str, Any]], prompt: str, mistral: MistralKlient, modell: str
) -> list[Bewertung]:
    """Filter-Stufe 2: Positivauswahl je Detail-Datensatz, einzeln bewertet.

    Implementiert als Ja/Nein-Entscheidung je Job: Das Modell liefert pro
    Datensatz Referenznummer, Entscheidung (angenommen/abgelehnt) und
    Begründung; angenommen ist, was die Prompt-Datei als Annahme deklariert
    (Wert aus ANGENOMMEN_WERTE). Die Bedeutung legt allein die Prompt-Datei fest.
    """
    bewertungen: list[Bewertung] = []
    for datensatz in daten:
        referenznummer = str(datensatz.get("referenznummer", ""))
        nutzer = json.dumps(datensatz, ensure_ascii=False)
        antwort = mistral.frage(
            modell, prompt, nutzer, json_objekt=True, debug_bezeichnung="Stufe 2 JSON"
        )
        objekt = _json_objekt_aus(antwort, f"Stufe 2 ({referenznummer})")
        entscheidung = str(objekt.get("entscheidung", "")).strip().lower()
        angenommen = entscheidung in ANGENOMMEN_WERTE
        beggruendung = str(objekt.get("begruendung", "")).strip()
        bewertungen.append(Bewertung(referenznummer, angenommen, beggruendung))
        print(
            f"[Stufe 2] {referenznummer}: "
            f"{'angenommen' if angenommen else 'abgelehnt'} - {beggruendung}",
            flush=True,
        )
    return bewertungen


def stufe3_bericht(
    daten: list[dict[str, Any]],
    prompt: str,
    mistral: MistralKlient,
    modell: str,
    konfig: Konfiguration,
) -> str:
    """Generator-Stufe: fertiger Berichtstext aus den angenommenen Jobs.

    Die Detail-Datensätze werden vollständig, roh und einzeln übergeben,
    zusammen mit den Suchparametern. Die Antwort wird unverändert übernommen.
    """
    teile = [
        (
            f"Suchparameter: Postleitzahl {konfig.plz}, Zeitraum "
            f"{konfig.zeitraum} Tage, Entfernung {konfig.entfernung} km."
        )
    ]
    for datensatz in daten:
        teile.append(json.dumps(datensatz, ensure_ascii=False))
    nutzer = "\n\n".join(teile)
    bericht = mistral.frage(
        modell,
        prompt,
        nutzer,
        json_objekt=False,
        debug_bezeichnung="Stufe 3 Berichtsdaten",
    )
    if not bericht.strip():
        raise AgentFehler("Mistral hat für den Bericht eine leere Antwort geliefert.")
    return bericht


def _json_objekt_aus(antwort: str, kontext: str) -> dict[str, Any]:
    try:
        objekt = json.loads(antwort)
    except ValueError as e:
        raise AgentFehler(
            f"{kontext}: Antwort der LLM-Stufe ist kein gültiges JSON: {e}. "
            f"Antwort: {antwort[:200]!r}"
        ) from e
    if not isinstance(objekt, dict):
        raise AgentFehler(f"{kontext}: Antwort ist kein JSON-Objekt.")
    return objekt


def _referenznummern_aus(antwort: str) -> list[str]:
    objekt = _json_objekt_aus(antwort, "Stufe 1")
    if "referenznummern" in objekt:
        wert = objekt["referenznummern"]
        if isinstance(wert, list):
            return [str(e) for e in wert]
        raise AgentFehler("Stufe 1: 'referenznummern' ist keine Liste.")
    nummern: list[str] = []
    for wert in objekt.values():
        if isinstance(wert, list):
            nummern.extend(str(e) for e in wert)
        elif isinstance(wert, str):
            nummern.append(wert)
    return nummern
