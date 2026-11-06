# a-Shell-Jobagent

Stellenangebote der Bundesagentur für Arbeit abrufen, in zwei Stufen per
Large-Language-Modell (Mistral) filtern und bewerten, und daraus einen Bericht
erstellen, welche Stellen bewerbenswert sind.

Leitprinzip: Keep It Simple – wenige Dateien, keine Laufzeit-Abhängigkeiten,
nur Python-Standardbibliothek.

## Installation

### a-Shell auf iPad/iPhone

Das Wheel `a_shell_jobagent-…-py3-none-any.whl` (Ordner `dist/`) z. B. per
AirDrop oder aus iCloud in a-Shell holen und installieren:

```
pip install a_shell_jobagent-1.0.1-py3-none-any.whl
```

### Desktop

```
uv run pytest -q && uv build
```

ist das einzige Build-Kommando: die Tests müssen durchlaufen, bevor das Wheel
gebaut wird. Zur lokalen Installation: `pip install dist/a_shell_jobagent-*.whl`.
Voraussetzung: Python >= 3.11.

## Aufruf

```
python -m a_shell_agent <PLZ> <ZEITRAUM> <ENTFERNUNG> [--debug] [--llm1 MODELL] [--llm2 MODELL] [--llm3 MODELL]
```

| Parameter | Bedeutung |
|---|---|
| `<PLZ>` | Postleitzahl, genau 5 Ziffern |
| `<ZEITRAUM>` | veröffentlicht seit N Tagen; erlaubt: 1, 3, 7, 14, 30 |
| `<ENTFERNUNG>` | Suchradius in km, ganze Zahl 1 bis 200 |
| `--debug` | `stellen.json` und `details.json` am Ende nicht löschen; zusätzlich Debug-Zeilen auf stdout: `[API] GET <url>` vor jeder Arbeitsamt-Anfrage (inkl. Wiederholungen) und `[LLM] Sende <Bezeichnung> an <modell> (<KB>)` vor jeder Mistral-Anfrage |
| `--llm1 MODELL` | Mistral-Modell Stufe 1 (Aussortieren), sonst `mistral-medium-latest` |
| `--llm2 MODELL` | Mistral-Modell Stufe 2 (Positivauswahl), sonst `mistral-medium-latest` |
| `--llm3 MODELL` | Mistral-Modell Stufe 3 (Bericht), sonst `mistral-medium-latest` |

Hinweis: Führende Geviertstriche (`—debug`) werden automatisch wie
Doppelbindestriche (`--debug`) behandelt – nützlich auf Tastaturen, die
`--` gern zu `—` machen. Wichtig: Python-Modulnamen dürfen keine Bindestriche
enthalten; deshalb heißt das Modul `a_shell_agent` (Unterstriche), während das
pip-Paket `a-shell-jobagent` heißt.

Beispiel:

```
python -m a_shell_agent 57080 7 25
```

## Ablauf

1. Parameter prüfen (ungültige Werte: Abbruch mit Fehlermeldung).
2. Alle Angebote des Zeitraums von der Arbeitsagentur-API holen (Pagination,
   ca. 6 s Pause zwischen Seiten, Backoff bei 429/5xx).
3. `stellen.json` mit allen Überschriften unverändert ins Arbeitsverzeichnis
   schreiben. Keine Angebote: Meldung `Keine Daten vorhanden`, Ende (Exit 0).
4. LLM-Stufe 1 (Aussortieren): Prompt-Datei `stufe1_prompts.txt`, die
   Datensätze roh in Batches (max. ~150 KB je Anfrage) an das Modell, zurück
   kommen die Referenznummern der zu behaltenden Angebote. Nichts übrig:
   `Keine Daten vorhanden`, Ende (Exit 0).
5. Details der Kandidaten holen und als `details.json` schreiben.
6. LLM-Stufe 2 (Positivauswahl): Prompt-Datei `stufe2_prompts.txt`, die
   Detail-Datensätze vollständig und roh in wenigen Batches (max. ~150 KB
   je Anfrage, damit der Kontext von Mistral Medium sicher reicht) an das
   Modell; Antwort je Job: Referenznummer, Entscheidung
   (angenommen/abgelehnt), Begründung (Schlüssel tolerant gegenüber
   Umlaut-Varianten wie `begründung`). Je Job erscheint genau eine
   `[Stufe 2]`-Zeile mit Referenznummer, Titel (falls im Datensatz
   vorhanden) und Begründung. Kein Job angenommen: Meldung
   `Keine Jobs gefunden`, Ende (Exit 0).
7. LLM-Stufe 3 (Bericht): Prompt-Datei `bericht_prompts.txt`, Detail-Datensätze
   der angenommenen Jobs plus Suchparameter an das Modell; die Antwort wird
   unverändert als `Bericht-<PLZ>-<ZEITRAUM>-<ENTFERNUNG>.txt` gespeichert
   (z. B. `Bericht-57080-7-25.txt`).
8. Aufräumen: ohne `--debug` werden `stellen.json` und `details.json`
   gelöscht; mit `--debug` bleiben sie liegen. Löschfehler brechen nicht ab.
9. Erfolgsmeldung, Exit-Code 0.

Alle drei LLM-Stufen erhalten die Daten roh und vollständig; der Code trifft
keine Feldauswahl und nimmt nur an, dass jeder Datensatz eine
`referenznummer` trägt (ausschließlich zur Verwaltung). Welche Felder
inhaltlich relevant sind, entscheidet allein die jeweilige Prompt-Datei.

## Die drei Prompt-Dateien

Die Prompt-Dateien liegen im **Arbeitsverzeichnis** (dem Verzeichnis des
Aufrufs), nicht im Paket. Fehlt eine Datei, schreibt das Programm die
mitgelieferte Vorlage hinein und bricht ab:

- `stufe1_prompts.txt` – Jobprofile am Platzhalter `[[HIER: …]]` ausfüllen.
- `stufe2_prompts.txt` – Anforderungen, Stärken, Schwächen, Wünsche und
  Abneigungen am Platzhalter `[[HIER: …]]` ausfüllen.
- `bericht_prompts.txt` – enthält keine persönlichen Kriterien und ist ab
  Erzeugung einsatzbereit; der Abbruch nach dem Erzeugen dient nur der
  einmaligen Prüfung, danach darf die Vorlage frei umbaut werden.

Eine existierende Datei wird nie überschrieben. Eine leere Datei oder eine
mit unverändertem Platzhalter führt zum Abbruch mit Fehlermeldung.

## API-Schlüssel (.env)

Der Mistral-API-Schlüssel wird aus der Umgebungsvariablen `MISTRAL_API_KEY`
gelesen; fehlt sie, aus der Datei `.env` im Arbeitsverzeichnis (Zeilen der
Form `SCHLÜSSEL=WERT`). Bereits gesetzte Umgebungsvariablen gewinnen.
Die `.env` sollte nicht in Git landen (siehe `.gitignore`).

## Dateien im Arbeitsverzeichnis

| Datei | Bedeutung |
|---|---|
| `stellen.json` | alle Überschriften-Datensätze (nur mit `--debug` dauerhaft) |
| `details.json` | alle Detail-Datensätze (nur mit `--debug` dauerhaft) |
| `Bericht-<PLZ>-<ZEITRAUM>-<ENTFERNUNG>.txt` | der fertige Bericht |
| `stufe1_prompts.txt`, `stufe2_prompts.txt`, `bericht_prompts.txt` | Prompts |
| `.env` | optionaler API-Schlüssel |

## Bekannte Fehlermeldungen

- `Ungültige Postleitzahl: …` – nicht genau 5 Ziffern.
- `Ungültiger Zeitraum: …` – kein erlaubter Wert (1, 3, 7, 14, 30).
- `Ungültige Entfernung: …` – keine ganze Zahl zwischen 1 und 200.
- `MISTRAL_API_KEY fehlt. …` – weder Umgebungsvariable noch `.env`.
- `… nach 5 Versuchen unerreichbar` – Arbeitsagentur-API trotz Backoff
  (2/4/8 s) nicht erreichbar.
- `Mistral … nach 3 Versuchen unerreichbar` – Mistral-API trotz Backoff nicht
  erreichbar.
- `Antwort der LLM-Stufe ist kein gültiges JSON` – Stufe 1 oder 2 lieferte
  kein JSON-Objekt.
- `Mistral hat für den Bericht eine leere Antwort geliefert.` – Stufe 3.
- `Prompt-Datei … fehlte: Vorlage wurde erstellt …` – Prompt-Datei fehlte,
  Vorlage wurde erzeugt; persönlichen Teil ausfüllen bzw. Vorlage prüfen.
- `Prompt-Datei … ist leer` bzw. `… enthält noch den unveränderten Platzhalter`
  – Prompt-Datei ohne Überschreiben beanstandet.
- `Kein einziger Detailabruf erfolgreich …` – alle Detailabrufe schlugen fehl.

`Keine Daten vorhanden` und `Keine Jobs gefunden` sind **keine** Fehler:
normales Ende mit Exit-Code 0.

## Entwicklung

```
uv run pytest -q     # Tests (kein Netzwerk nötig, Fakes statt APIs)
uv run mypy          # strenge Typprüfung
uv run ruff check .  # Linter
uv run pytest -q && uv build   # Tests + Wheel-Build in einem Kommando
```
