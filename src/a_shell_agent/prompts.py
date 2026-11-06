"""Prompt-Vorlagen und Laden der Prompt-Dateien aus dem Arbeitsverzeichnis."""

from __future__ import annotations

from pathlib import Path

from a_shell_agent.fehler import AgentFehler

DATEINAME_STUFE1 = "stufe1_prompts.txt"
DATEINAME_STUFE2 = "stufe2_prompts.txt"
DATEINAME_BERICHT = "bericht_prompts.txt"

PLATZHALTER_MARKE = "[[HIER:"

VORLAGE_STUFE1 = """\
# Stufe 1: Vorauswahl anhand der Überschriften.
# Der ganze Dateiinhalt wird als System-Prompt für LLM 1 verwendet.
# Persönliche Jobprofile gehören NUR an den markierten Platzhalter.

Du triffst eine Vorauswahl von Stellenangeboten.

Aufgabe: Du erhältst JSON-Datensätze, je Datensatz genau ein Arbeitsangebot,
vollständig und unverändert. Wähle die Angebote aus, die zu den im Folgenden
beschriebenen Jobprofilen möglichst passen. Schließe Angebote aus, bei denen
eine Passung offensichtlich nicht existiert. Im Zweifel lässt du ein Angebot
durch: falsch positive sind unkritisch, falsch negative verlieren echte
Chancen.

Antworte ausschließlich mit einem JSON-Objekt dieser Form:
{"referenznummern": ["<Referenznummer>", "..."]}
Nenne die Referenznummern aller Angebote, die du behalten willst. Nutze dabei
nur Referenznummern, die in den übergebenen Datensätzen vorkommen.

Jobprofile:
[[HIER: Deine Jobprofile eintragen]]
"""

VORLAGE_STUFE2 = """\
# Stufe 2: Positivauswahl anhand der Detail-Datensätze.
# Der ganze Dateiinhalt wird als System-Prompt für LLM 2 verwendet.
# Persönliche Anforderungen gehören NUR an den markierten Platzhalter.

Du bewertest Stellenangebote im Detail.

Aufgabe: Du erhältst JSON-Datensätze, je Datensatz genau ein Arbeitsangebot,
vollständig und unverändert. Prüfe anhand aller übergebenen Informationen,
ob das Angebot den im Folgenden beschriebenen spezifischen Anforderungen
des Nutzers entspricht. Nutze dafür alle Felder der Datensätze, die dir für
die Bewertung nützlich erscheinen.

Antworte ausschließlich mit einem JSON-Objekt dieser Form:
{"bewertungen": [
  {"referenznummer": "<Referenznummer>",
   "entscheidung": "angenommen" oder "abgelehnt",
   "begruendung": "<kurze Begründung deiner Entscheidung>"}
]}
Bewerte JEDES übergebene Angebot genau einmal. Nenne für jede Entscheidung
eine kurze Begründung von mindestens einem Satz - auch bei Ablehnung.

Anforderungen und Präferenzen:
[[HIER: Deine Anforderungen, Stärken, Schwächen, Wünsche und Abneigungen eintragen]]
"""

VORLAGE_BERICHT = """\
# Bericht-Stufe: fertiger Berichtstext aus den Detail-Datensätzen.
# Der ganze Dateiinhalt wird als System-Prompt für LLM 3 verwendet.
# Diese Vorlage enthält keine persönlichen Kriterien und ist ab Erzeugung
# einsatzbereit; du darfst sie frei umbauen.

Du bist Mitarbeiter der Bundesagentur für Arbeit und schreibst dem Nutzer
freundschaftlich-nett ein paar Zeilen, sinngemäß: „Guck dir bitte folgende
Jobs an, ich habe sie für dich zusammengefasst und mit den Links versehen."

Erstelle einen Bericht über die übergebenen Arbeitsangebote. Enthalten sein
sollen je Job: der Titel, die wichtigsten Eigenschaften des Jobs, die
Entfernung von der Suchpostleitzahl (die Suchparameter werden dir mit
übergeben) und weitere relevante Informationen, die in den Details vorhanden
sind. Verlinke jeden Job mit
https://www.arbeitsagentur.de/jobsuche/jobdetail/<Referenznummer>.

Schreibe den Bericht als fertigen Text ohne JSON-Formatierung.
"""


def lade_prompt(pfad: Path, vorlage: str, platzhalter_pflicht: bool) -> str:
    """Lädt eine Prompt-Datei aus dem Arbeitsverzeichnis (kein Paket-Fallback).

    Fehlt sie, wird die Vorlage hineingeschrieben und ein Abbruch ausgelöst.
    Ist sie leer (oder bei Stufe 1/2 noch nicht personalisiert), Abbruch ohne
    Überschreiben. Bei platzhalter_pflicht=False (Bericht) entfällt die
    Platzhalter-Prüfung.
    """
    if not pfad.exists():
        try:
            pfad.write_text(vorlage, encoding="utf-8")
        except OSError as e:
            raise AgentFehler(
                f"Prompt-Datei {pfad.name} fehlt und konnte nicht erstellt werden: {e}"
            ) from e
        if platzhalter_pflicht:
            raise AgentFehler(
                f"Prompt-Datei {pfad.name} fehlte: Vorlage wurde erstellt. "
                f"Bitte den persönlichen Teil am Platzhalter "
                f'"{PLATZHALTER_MARKE} ..." ausfüllen und erneut aufrufen.'
            )
        raise AgentFehler(
            f"Prompt-Datei {pfad.name} fehlte: Vorlage wurde erstellt. "
            f"Bitte die Vorlage prüfen, bei Bedarf anpassen und erneut aufrufen."
        )
    try:
        text = pfad.read_text(encoding="utf-8")
    except OSError as e:
        raise AgentFehler(f"Prompt-Datei {pfad.name} ist nicht lesbar: {e}") from e
    if not text.strip():
        raise AgentFehler(
            f"Prompt-Datei {pfad.name} ist leer. Bitte ausfüllen "
            f"(Vorlage siehe README) und erneut aufrufen."
        )
    if platzhalter_pflicht and PLATZHALTER_MARKE in text:
        raise AgentFehler(
            f"Prompt-Datei {pfad.name} enthält noch den unveränderten "
            f'Platzhalter "{PLATZHALTER_MARKE} ...". Bitte den persönlichen '
            f"Teil ausfüllen und erneut aufrufen."
        )
    return text


def lade_alle_prompts(arbeitsverzeichnis: Path) -> tuple[str, str, str]:
    """Prüft alle drei Prompt-Dateien vorab, in einem Schritt.

    Fehlende Dateien werden aus den Vorlagen angelegt (alle auf einmal, nicht
    nur die erste); danach Abbruch, damit der Nutzer personalisieren kann,
    bevor die teuren API-Abrufe und LLM-Stufen laufen. Leere Dateien oder
    unveränderte Platzhalter werden ohne Überschreiben beanstandet. Im
    Erfolgsfall werden die Inhalte (Stufe 1, Stufe 2, Bericht) geliefert.
    """
    definitionen: tuple[tuple[str, str, bool], ...] = (
        (DATEINAME_STUFE1, VORLAGE_STUFE1, True),
        (DATEINAME_STUFE2, VORLAGE_STUFE2, True),
        (DATEINAME_BERICHT, VORLAGE_BERICHT, False),
    )
    angelegt: list[str] = []
    probleme: list[str] = []
    inhalte: list[str] = []
    for name, vorlage, platzhalter_pflicht in definitionen:
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
        if platzhalter_pflicht and PLATZHALTER_MARKE in text:
            probleme.append(
                f"{name} enthält noch den unveränderten Platzhalter "
                f'"{PLATZHALTER_MARKE} ..."'
            )
            inhalte.append("")
            continue
        inhalte.append(text)
    if angelegt or probleme:
        teile: list[str] = []
        if angelegt:
            teile.append(
                "Prompt-Datei(en) fehlten, Vorlage(n) wurde(n) erstellt: "
                + ", ".join(angelegt)
                + ". Bitte den persönlichen Teil am Platzhalter ausfüllen "
                "bzw. die Vorlage prüfen und erneut aufrufen."
            )
        if probleme:
            teile.append("Probleme: " + "; ".join(probleme) + ".")
        raise AgentFehler(" ".join(teile))
    return inhalte[0], inhalte[1], inhalte[2]
