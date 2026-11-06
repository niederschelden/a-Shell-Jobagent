"""Die drei LLM-Stufen: Filter 1, Filter 2, Generator (Bericht).

Alle Stufen erhalten die Datensätze roh und vollständig; der Code trifft
keine Feldauswahl und nimmt nur an, dass jeder Datensatz ein Arbeitsangebot
mit einer Referenznummer ist (die ausschließlich zur Verwaltung dient).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from a_shell_agent.debug import debug_zeile
from a_shell_agent.fehler import AgentFehler
from a_shell_agent.konfiguration import Konfiguration
from a_shell_agent.llm import MistralKlient

ANGENOMMEN_WERTE = {"angenommen", "ja", "akzeptiert", "true"}

# Obergrenze je LLM-Anfrage in Bytes (~40k Tokens); ein Call für alles
# kann das Kontextfenster von Mistral Medium sprengen (Prompt und Antwort
# zählen beide hinein; gemessene Grenze aktuell 262.144 Tokens).
BATCH_MAX_BYTES = 150_000


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

    Die Datensätze gehen roh und vollständig in Batches bis BATCH_MAX_BYTES
    (wie Stufe 2: ein Call für alles kann das Kontextfenster sprengen). Je
    Batch nennt die Antwort die Referenznummern der zu behaltenden Angebote;
    vereinigt werden sie über alle Batches. Unbekannte Referenznummern
    werden verworfen.
    """
    bekannt = {d.get("referenznummer") for d in daten}
    gewaehlt: list[str] = []
    for batch in _baetche(daten):
        nutzer = json.dumps(batch, ensure_ascii=False)
        antwort = mistral.frage(
            modell, prompt, nutzer, json_objekt=True, debug_bezeichnung="Stufe 1 JSON"
        )
        gewaehlt.extend(_referenznummern_aus(antwort))
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
    """Filter-Stufe 2: Positivauswahl der Detail-Datensätze in Batches.

    Implementiert als Ja/Nein-Entscheidung je Job. Die Datensätze gehen roh
    und vollständig in möglichst wenigen Anfragen: gesammelt in Batches bis
    STUFE2_MAX_BYTES (~40k Tokens, sicher im Kontextfenster von
    mistral-medium-latest; ein einziger Call für alles könnte je nach
    Routing-Version das 128k-Fenster sprengen). Je Batch liefert das Modell
    eine Liste mit Referenznummer, Entscheidung (angenommen/abgelehnt) und
    Begründung; angenommen ist der Wert aus ANGENOMMEN_WERTE. Die Bedeutung
    legt allein die Prompt-Datei fest.
    """
    bewertungen: list[Bewertung] = []
    for batch in _baetche(daten):
        nutzer = json.dumps(batch, ensure_ascii=False)
        antwort = mistral.frage(
            modell, prompt, nutzer, json_objekt=True, debug_bezeichnung="Stufe 2 JSON"
        )
        debug_zeile(f"[LLM] Antwort Stufe 2: {antwort[:500]}")
        resultate = _bewertungen_aus(antwort, batch)
        for datensatz, b in zip(batch, resultate, strict=True):
            print(
                f"[Stufe 2] {b.referenznummer}{_anzeigetitel(datensatz)}: "
                f"{'angenommen' if b.angenommen else 'abgelehnt'} - {b.beggruendung}",
                flush=True,
            )
        bewertungen.extend(resultate)
    return bewertungen


def _anzeigetitel(datensatz: dict[str, Any]) -> str:
    """Rein für die Ausgabe: erster Titel-artiger Stringwert des Datensatzes.

    Heuristik ohne Bewertungseinfluss: Schlüssel mit "titel"/"title" (nach
    Normalisierung), sonst leerer Zusatz.
    """
    for schluessel, wert in datensatz.items():
        if not isinstance(wert, str) or not wert:
            continue
        n = _normiere(schluessel)
        if "titel" in n or "title" in n:
            return f" ({wert})"
    return ""


def _baetche(daten: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Teilt die Datensätze greedig in Batches bis zur Byte-Obergrenze."""
    baetche: list[list[dict[str, Any]]] = []
    aktuell: list[dict[str, Any]] = []
    groesse = 2  # "[]" plus Reserve für Trennzeichen
    for datensatz in daten:
        n = len(json.dumps(datensatz, ensure_ascii=False).encode("utf-8"))
        if aktuell and groesse + n > BATCH_MAX_BYTES:
            baetche.append(aktuell)
            aktuell, groesse = [], 2
        aktuell.append(datensatz)
        groesse += n + 1
    if aktuell:
        baetche.append(aktuell)
    return baetche


def _bewertungen_aus(antwort: str, batch: list[dict[str, Any]]) -> list[Bewertung]:
    """Liest die Bewertungen eines Batches; tolerante Schlüssel (Umlaute)."""
    objekt = _json_objekt_aus(antwort, "Stufe 2")
    eintraege = [e for e in objekt.values() if isinstance(e, list)]
    bewertet: dict[str, Bewertung] = {}
    for eintrag in eintraege:
        for item in eintrag:
            if not isinstance(item, dict):
                continue
            norm = {_normiere(k): v for k, v in item.items()}
            refnr = str(norm.get("referenznummer", "")).strip()
            if not refnr:
                continue
            entscheidung = str(norm.get("entscheidung", "")).strip().lower()
            beggruendung = str(norm.get("begruendung", "")).strip()
            bewertet[refnr] = Bewertung(
                refnr, entscheidung in ANGENOMMEN_WERTE, beggruendung
            )
    resultate: list[Bewertung] = []
    for datensatz in batch:
        refnr = str(datensatz.get("referenznummer", ""))
        if refnr in bewertet:
            resultate.append(bewertet[refnr])
        else:
            print(
                f"[Warnung] Stufe 2: {refnr} wurde vom Modell nicht bewertet, "
                f"gilt als abgelehnt.",
                flush=True,
            )
            resultate.append(Bewertung(refnr, False, "vom Modell nicht bewertet"))
    return resultate


def _normiere(schluessel: str) -> str:
    klein = schluessel.strip().lower()
    return (
        klein.replace("ü", "ue")
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ß", "ss")
    )


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
        objekt = json.loads(_bereinige(antwort))
    except ValueError as e:
        raise AgentFehler(
            f"{kontext}: Antwort der LLM-Stufe ist kein gültiges JSON: {e}. "
            f"Antwort: {antwort[:200]!r}"
        ) from e
    if not isinstance(objekt, dict):
        raise AgentFehler(f"{kontext}: Antwort ist kein JSON-Objekt.")
    return objekt


def _bereinige(antwort: str) -> str:
    """Macht LLM-Antworten JSON-fähig: entfernt Code-Fences und ersetzt
    typografische Anführungszeichen („ “ etc.) durch ASCII-Äquivalente."""
    text = antwort.strip()
    if text.startswith("```"):
        text = (
            text.removeprefix("```json")
            .removeprefix("```JSON")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )
    return (
        text.replace("\u201e", '"')
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201a", "'")
    )


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
