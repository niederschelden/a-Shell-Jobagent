"""Inhalt-Promptdateien im Arbeitsverzeichnis und Kombination mit der Struktur.

Trennung von Inhalt und Form: Die Aufgabenstruktur (Form) liegt als Konstanten
in struktur.py im Paket; die Dateien im Arbeitsverzeichnis enthalten NUR die
persönlichen Inhalte. Fehlt eine Inhaltsdatei, wird eine kleine Vorlage mit
Platzhalter hineingeschrieben und abgebrochen; die Berichtsdatei ist optional.
"""

from __future__ import annotations

from pathlib import Path

from a_shell_agent.fehler import AgentFehler
from a_shell_agent.struktur import (
    STRUKTUR_BERICHT,
    STRUKTUR_STUFE1,
    STRUKTUR_STUFE2,
)

DATEINAME_STUFE1 = "stufe1_prompts.txt"
DATEINAME_STUFE2 = "stufe2_prompts.txt"
DATEINAME_BERICHT = "bericht_prompts.txt"

PLATZHALTER_MARKE = "[[HIER:"

# Inhalt-Vorlagen: nur persönliche Inhalte, keine Aufgabenstruktur.
VORLAGE_STUFE1 = """\
# Nur deine persönlichen Jobprofile - die Aufgabenstruktur liefert das Programm.
[[HIER: Deine Jobprofile eintragen]]
"""

VORLAGE_STUFE2 = """\
# Nur deine persönlichen Anforderungen - die Aufgabenstruktur liefert das Programm.
[[HIER: Deine Anforderungen, Stärken, Schwächen, Wünsche und Abneigungen eintragen]]
"""

VORLAGE_BERICHT = """\
# Optional: persönliche Wünsche an den Bericht (z. B. Ton, Länge, Gliederung).
# Diese Datei ist optional - fehlt sie, läuft der Bericht mit der Standardstruktur.
[[HIER: Deine Wünsche an den Bericht]]
"""


def lade_inhalt(pfad: Path, vorlage: str, platzhalter_pflicht: bool) -> str:
    """Lädt eine Inhaltsdatei aus dem Arbeitsverzeichnis.

    Stufe 1 und 2 (platzhalter_pflicht=True): Fehlt die Datei, wird die
    Vorlage hineingeschrieben und abgebrochen; leere Datei oder unveränderter
    Platzhalter führen zum Abbruch ohne Überschreiben.
    Bericht (platzhalter_pflicht=False): Die Datei ist optional - fehlt sie
    oder ist sie leer bzw. unverändert, gilt der Inhalt als leer; es gibt
    keinen Abbruch und keine Erzeugung.
    """
    if not pfad.exists():
        if not platzhalter_pflicht:
            return ""
        try:
            pfad.write_text(vorlage, encoding="utf-8")
        except OSError as e:
            raise AgentFehler(
                f"Prompt-Datei {pfad.name} fehlt und konnte nicht erstellt werden: {e}"
            ) from e
        raise AgentFehler(
            f"Prompt-Datei {pfad.name} fehlte: Vorlage wurde erstellt. Bitte den "
            f'persönlichen Teil am Platzhalter "{PLATZHALTER_MARKE} ..." '
            f"ausfüllen und erneut aufrufen."
        )
    try:
        text = pfad.read_text(encoding="utf-8")
    except OSError as e:
        raise AgentFehler(f"Prompt-Datei {pfad.name} ist nicht lesbar: {e}") from e
    if not text.strip():
        if not platzhalter_pflicht:
            return ""
        raise AgentFehler(
            f"Prompt-Datei {pfad.name} ist leer. Bitte ausfüllen "
            f"(Vorlage siehe README) und erneut aufrufen."
        )
    if PLATZHALTER_MARKE in text:
        if not platzhalter_pflicht:
            return ""
        raise AgentFehler(
            f"Prompt-Datei {pfad.name} enthält noch den unveränderten "
            f'Platzhalter "{PLATZHALTER_MARKE} ...". Bitte den persönlichen '
            f"Teil ausfüllen und erneut aufrufen."
        )
    return text


def lade_alle_prompts(arbeitsverzeichnis: Path) -> tuple[str, str, str]:
    """Prüft die Inhaltsdateien vorab und kombiniert sie mit der Struktur.

    Fehlende Pflichtdateien (Stufe 1 und 2) werden aus den Vorlagen angelegt
    (alle in einem Schritt), danach Abbruch - vor den teuren Abrufen. Im
    Erfolgsfall werden die fertigen System-Prompts geliefert: Struktur aus
    dem Paket plus persönlicher Inhalt aus dem Arbeitsverzeichnis.
    """
    angelegt: list[str] = []
    probleme: list[str] = []
    inhalte: list[str] = []

    pflicht: tuple[tuple[str, str], ...] = (
        (DATEINAME_STUFE1, VORLAGE_STUFE1),
        (DATEINAME_STUFE2, VORLAGE_STUFE2),
    )
    for name, vorlage in pflicht:
        pfad = arbeitsverzeichnis / name
        if not pfad.exists():
            try:
                pfad.write_text(vorlage, encoding="utf-8")
            except OSError as e:
                probleme.append(f"{name} konnte nicht erstellt werden: {e}")
                inhalte.append("")
                continue
            angelegt.append(name)
            inhalte.append("")
            continue
        try:
            text = pfad.read_text(encoding="utf-8")
        except OSError as e:
            probleme.append(f"{name} ist nicht lesbar: {e}")
            inhalte.append("")
            continue
        if not text.strip():
            probleme.append(f"{name} ist leer")
            inhalte.append("")
            continue
        if PLATZHALTER_MARKE in text:
            probleme.append(
                f"{name} enthält noch den unveränderten Platzhalter "
                f'"{PLATZHALTER_MARKE} ..."'
            )
            inhalte.append("")
            continue
        inhalte.append(text)

    bericht_inhalt = lade_inhalt(
        arbeitsverzeichnis / DATEINAME_BERICHT,
        VORLAGE_BERICHT,
        platzhalter_pflicht=False,
    )

    if angelegt or probleme:
        teile: list[str] = []
        if angelegt:
            teile.append(
                "Prompt-Datei(en) fehlten, Vorlage(n) wurde(n) erstellt: "
                + ", ".join(angelegt)
                + ". Bitte den persönlichen Teil am Platzhalter ausfüllen "
                "und erneut aufrufen."
            )
        if probleme:
            teile.append("Probleme: " + "; ".join(probleme) + ".")
        raise AgentFehler(" ".join(teile))

    prompt1 = STRUKTUR_STUFE1 + "\n" + inhalte[0]
    prompt2 = STRUKTUR_STUFE2 + "\n" + inhalte[1]
    prompt3 = (
        STRUKTUR_BERICHT + "\n\n" + bericht_inhalt
        if bericht_inhalt
        else STRUKTUR_BERICHT
    )
    return prompt1, prompt2, prompt3
