# a-Shell-Jobagent

Stellenangebote der Bundesagentur für Arbeit abrufen, in zwei Stufen per
Large-Language-Modell (Mistral) filtern und bewerten, und daraus einen Bericht
erstellen, welche Stellen bewerbenswert sind.

Leitprinzip: Keep It Simple – wenige Dateien, keine Laufzeit-Abhängigkeiten,
nur Python-Standardbibliothek.

> Hinweis: Der gesamte Inhalt dieses Repositories wurde am iPhone/iPad mit
> [Vibe](https://vibe.mistral.ai/) von Mistral AI geschrieben.

## Voraussetzung: Mistral-API-Schlüssel

Dieses Programm filtert und bewertet Stellenangebote mit einem
Large-Language-Modell von Mistral. Dafür brauchst du **vorab** einen
Mistral-API-Schlüssel: kostenloses Konto unter
[console.mistral.ai](https://console.mistral.ai) anlegen, dort unter
„API Keys" einen Schlüssel erzeugen und ihn entweder als Umgebungsvariable
`MISTRAL_API_KEY` setzen oder in eine Datei `.env` im Arbeitsverzeichnis
mit der Zeile `MISTRAL_API_KEY=<schluessel>` eintragen (Details siehe
unten). Ohne Schlüssel bricht das Programm mit einer entsprechenden
Fehlermeldung ab. Die Nutzung der Mistral-API fällt unter die Preise von
Mistral; die Arbeitsamt-API ist frei.

## Installation

### a-Shell auf iPad/iPhone

Das Wheel `a_shell_jobagent-…-py3-none-any.whl` (Ordner `dist/`) z. B. per
AirDrop oder aus iCloud in a-Shell holen und installieren:

```
pip install a_shell_jobagent-0.2.0-py3-none-any.whl
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
2. Die Prompt-Inhaltsdateien vorab prüfen: Fehlende Pflichtdateien (Stufe 1
   und 2) werden aus den Vorlagen angelegt (in einem Schritt), danach Abbruch
   mit der Bitte, die persönlichen Platzhalter auszufüllen. Leere Dateien
   oder unveränderte Platzhalter werden ohne Überschreiben beanstandet. Die
   optionale Berichtsdatei braucht nicht zu existieren. Die Prüfung läuft
   bewusst vor allen Abrufen, damit ein Abbruch keine Rechenleistung
   verschwendet. Die Struktur-Prompts kommen aus dem Paket.
3. Alle Angebote des Zeitraums von der Arbeitsagentur-API holen (Pagination,
   ca. 6 s Pause zwischen Seiten, Backoff bei 429/5xx).
4. `stellen.json` mit allen Überschriften unverändert ins Arbeitsverzeichnis
   schreiben. Keine Angebote: Meldung `Keine Daten vorhanden`, Ende (Exit 0).
5. LLM-Stufe 1 (Aussortieren): Prompt-Datei `stufe1_prompts.txt`, die
   Datensätze roh in Batches (max. ~150 KB je Anfrage) an das Modell, zurück
   kommen die Referenznummern der zu behaltenden Angebote. Nichts übrig:
   `Keine Daten vorhanden`, Ende (Exit 0).
6. Details der Kandidaten holen und als `details.json` schreiben.
7. LLM-Stufe 2 (Positivauswahl): Prompt-Datei `stufe2_prompts.txt`, die
   Detail-Datensätze vollständig und roh in wenigen Batches (max. ~150 KB
   je Anfrage, damit der Kontext von Mistral Medium sicher reicht) an das
   Modell; Antwort je Job: Referenznummer, Entscheidung
   (angenommen/abgelehnt), Begründung (Schlüssel tolerant gegenüber
   Umlaut-Varianten wie `begründung`). Je Job erscheint genau eine
   `[Stufe 2]`-Zeile mit Referenznummer, Titel (falls im Datensatz
   vorhanden) und Begründung. Kein Job angenommen: Meldung
   `Keine Jobs gefunden`, Ende (Exit 0).
8. LLM-Stufe 3 (Bericht): Prompt-Datei `bericht_prompts.txt`, Detail-Datensätze
   der angenommenen Jobs plus Suchparameter an das Modell; die Antwort wird
   unverändert als `Bericht-<PLZ>-<ZEITRAUM>-<ENTFERNUNG>.txt` gespeichert
   (z. B. `Bericht-57080-7-25.txt`).
9. Aufräumen: ohne `--debug` werden `stellen.json` und `details.json`
   gelöscht; mit `--debug` bleiben sie liegen. Löschfehler brechen nicht ab.
10. Erfolgsmeldung, Exit-Code 0.

Alle drei LLM-Stufen erhalten die Daten roh und vollständig; der Code trifft
keine Feldauswahl und nimmt nur an, dass jeder Datensatz eine
`referenznummer` trägt (ausschließlich zur Verwaltung). Welche Felder
inhaltlich relevant sind, entscheidet allein die jeweilige Prompt-Datei.

## Die Prompt-Dateien: Inhalt und Form sind getrennt

Die **Struktur** (Aufgabe, Antwortform, Anführungszeichen-Regeln) liegt im
Paket (`a_shell_agent/struktur.py`) – sie kann im Arbeitsverzeichnis nicht
versehentlich beschädigt werden. Im **Arbeitsverzeichnis** stehen nur die
**persönlichen Inhalte**:

- `stufe1_prompts.txt` (Pflicht) – nur deine Jobprofile. Fehlt die Datei,
  schreibt das Programm eine kleine Vorlage mit Platzhalter
  `[[HIER: …]]` hinein und bricht ab.
- `stufe2_prompts.txt` (Pflicht) – nur deine Anforderungen, Stärken,
  Schwächen, Wünsche und Abneigungen. Gleiche Behandlung wie Stufe 1.
- `bericht_prompts.txt` (optional) – persönliche Wünsche an den Bericht
  (z. B. Ton, Länge). Fehlt die Datei, läuft der Bericht mit der
  Standardstruktur; es gibt keinen Abbruch.

Eine existierende Datei wird nie überschrieben. Bei den Pflichtdateien
führen eine leere Datei oder ein unveränderter Platzhalter zum Abbruch mit
Fehlermeldung. Das Programm setzt die Struktur automatisch vor deinen
Inhalt zusammen – du schreibst also nur noch *was* gefiltert werden soll,
nicht *wie* die Antwort auszusehen hat.

### Beispiel-Promptdateien

Im Ordner `beispiel-prompts/` liegen die drei Inhalts-Vorlagen als Dateien –
denselben Inhalt, den das Programm beim ersten Lauf automatisch erzeugt:

```
cp beispiel-prompts/stufe1_prompts.txt beispiel-prompts/stufe2_prompts.txt ~/Documents/Jobagent/
```

Danach nur noch die Platzhalter in Stufe 1 und 2 personalisieren. Die
optionale `bericht_prompts.txt` kannst du bei Bedarf selbst anlegen.

### Umstieg von älteren Versionen

Vor Version 0.2.0 enthielten die Prompt-Dateien Struktur und Inhalt
gemischt. Beim Umstieg: deine persönlichen Kriterien aus den alten Dateien
herauskopieren und als neuen Inhalt in die frisch erzeugten Dateien
einfüllen – die Strukturblöcke (Aufgabe, Antwortform) gehören nicht mehr
in die Dateien.

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
- `Prompt-Datei … fehlte: Vorlage wurde erstellt …` – Inhaltsdatei fehlte,
  Vorlage wurde erzeugt; persönlichen Teil ausfüllen.
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
