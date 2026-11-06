"""Unit-Tests: kein Netzwerk, alle Clients sind Fakes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from a_shell_agent.api import ArbeitsamtKlient
from a_shell_agent.fehler import AgentFehler
from a_shell_agent.konfiguration import (
    DEFAULT_MODELL_BERICHT,
    DEFAULT_MODELL_STUFE1,
    DEFAULT_MODELL_STUFE2,
    ZEITRAUM_WERTE,
    baue_konfiguration,
)
from a_shell_agent.main import DATEI_DETAILS, DATEI_STELLEN, fuehre_aus
from a_shell_agent.pipeline import stufe1_filtern, stufe2_bewerten
from a_shell_agent.prompts import (
    DATEINAME_BERICHT,
    DATEINAME_STUFE1,
    DATEINAME_STUFE2,
    PLATZHALTER_MARKE,
    lade_prompt,
)
from a_shell_agent.umgebung import lade_env


def konfig(debug: bool = False) -> Any:
    return baue_konfiguration(["57080", "7", "25", *(["--debug"] if debug else [])])


class FakeArbeitsamt:
    def __init__(
        self, seiten: list[list[dict[str, Any]]], details: dict[str, dict[str, Any]]
    ) -> None:
        self.seiten = seiten
        self.details = details

    def hole_angebote(
        self, plz: str, zeitraum: int, entfernung: int
    ) -> list[dict[str, Any]]:
        angebote: list[dict[str, Any]] = []
        for seite in self.seiten:
            angebote.extend(seite)
            if len(seite) < 100:
                break
        return angebote

    def hole_detail(self, referenznummer: str) -> dict[str, Any]:
        if referenznummer not in self.details:
            raise AgentFehler(f"Detail fehlt: {referenznummer}")
        return self.details[referenznummer]


class FakeMistral:
    """Liefert je Aufruf die nächste vorbereitete Antwort."""

    def __init__(self, antworten: list[str]) -> None:
        self.antworten = antworten
        self.aufrufe: list[dict[str, Any]] = []

    def frage(
        self, modell: str, system: str, nutzer: str, json_objekt: bool = True
    ) -> str:
        self.aufrufe.append(
            {"modell": modell, "system": system, "nutzer": nutzer, "json": json_objekt}
        )
        if not self.antworten:
            raise AssertionError("FakeMistral: keine Antwort mehr vorbereitet")
        return self.antworten.pop(0)


ANGEBOT = {"referenznummer": "R1", "titel": "Entwickler"}
ANGEBOT2 = {"referenznummer": "R2", "titel": "Pfleger"}
DETAIL = {"referenznummer": "R1", "titel": "Entwickler", "ort": "Siegen"}


def schreibe_prompts(verzeichnis: Path) -> None:
    for name in (DATEINAME_STUFE1, DATEINAME_STUFE2):
        (verzeichnis / name).write_text(
            "Persönliche Kriterien: IT-Jobs.\n", encoding="utf-8"
        )
    (verzeichnis / DATEINAME_BERICHT).write_text(
        "Erstelle den Bericht.\n", encoding="utf-8"
    )


# ---------------------------------------------------------------- Parameter


@pytest.mark.parametrize(
    ("argv", "regexp"),
    [
        (["57", "7", "25"], "Postleitzahl"),
        (["ABCDE", "7", "25"], "Postleitzahl"),
        (["570801", "7", "25"], "Postleitzahl"),
        (["57080", "5", "25"], "Zeitraum"),
        (["57080", "abc", "25"], "Zeitraum"),
        (["57080", "7", "0"], "Entfernung"),
        (["57080", "7", "201"], "Entfernung"),
        (["57080", "7", "x"], "Entfernung"),
    ],
)
def test_parameter_ungueltig(argv: list[str], regexp: str) -> None:
    with pytest.raises(AgentFehler, match=regexp):
        baue_konfiguration(argv)


@pytest.mark.parametrize("zeitraum", ZEITRAUM_WERTE)
def test_zeitraum_grenzen(zeitraum: int) -> None:
    assert baue_konfiguration(["99999", str(zeitraum), "200"]).zeitraum == zeitraum


def test_entfernung_grenzen() -> None:
    assert baue_konfiguration(["99999", "7", "1"]).entfernung == 1
    assert baue_konfiguration(["99999", "7", "200"]).entfernung == 200


def test_em_dash_optionen() -> None:
    k = baue_konfiguration(["57080", "7", "25", "—debug"])
    assert k.debug is True


def test_modelle_default_und_override() -> None:
    k = baue_konfiguration(["57080", "7", "25"])
    assert k.modell_stufe1 == DEFAULT_MODELL_STUFE1 == "mistral-medium-latest"
    assert k.modell_stufe2 == DEFAULT_MODELL_STUFE2
    assert k.modell_bericht == DEFAULT_MODELL_BERICHT
    k = baue_konfiguration(
        ["57080", "7", "25", "--llm1", "a", "--llm2", "b", "--llm3", "c"]
    )
    assert (k.modell_stufe1, k.modell_stufe2, k.modell_bericht) == ("a", "b", "c")


# ------------------------------------------------------------- Pagination


def test_pagination_endet_bei_kurzer_seite() -> None:
    klient = ArbeitsamtKlient()
    klient.PAUSE_SEKUNDEN = 0.0
    voll = [{"referenznummer": f"R{i}"} for i in range(100)]
    seiten: list[list[dict[str, Any]]] = [voll, voll, voll[:37]]
    klient._get_json = lambda url: {  # type: ignore[method-assign]
        "stellenangebote": seiten.pop(0)
    }
    angebote = klient.hole_angebote("57080", 7, 25)
    assert len(angebote) == 237


def test_pagination_leere_letzte_seite() -> None:
    klient = ArbeitsamtKlient()
    klient.PAUSE_SEKUNDEN = 0.0
    voll = [{"referenznummer": f"R{i}"} for i in range(100)]
    seiten: list[list[dict[str, Any]]] = [voll, []]
    klient._get_json = lambda url: {  # type: ignore[method-assign]
        "stellenangebote": seiten.pop(0)
    }
    assert len(klient.hole_angebote("57080", 7, 25)) == 100


# --------------------------------------------------------------- Stufe 1/2


def test_stufe1_validiert_referenznummern() -> None:
    mistral = FakeMistral(['{"referenznummern": ["R1", "RX", "R2"]}'])
    ergebnis = stufe1_filtern([ANGEBOT, ANGEBOT2], "prompt", mistral, "modell")
    assert ergebnis == [ANGEBOT, ANGEBOT2]
    assert json.loads(mistral.aufrufe[0]["nutzer"]) == [ANGEBOT, ANGEBOT2]


def test_stufe1_verwirft_unbekannte() -> None:
    mistral = FakeMistral(['{"referenznummern": ["R1", "XXX"]}'])
    ergebnis = stufe1_filtern([ANGEBOT, ANGEBOT2], "prompt", mistral, "modell")
    assert ergebnis == [ANGEBOT]


def test_stufe1_ungueltiges_json_bricht_ab() -> None:
    mistral = FakeMistral(["kein json"])
    with pytest.raises(AgentFehler, match="kein gültiges JSON"):
        stufe1_filtern([ANGEBOT], "prompt", mistral, "modell")


def test_stufe2_bewertung_und_cache() -> None:
    mistral = FakeMistral(
        [
            '{"referenznummer": "R1", "entscheidung": "angenommen", "begruendung": "passt"}',
            '{"referenznummer": "R2", "entscheidung": "abgelehnt", "begruendung": "nein"}',
        ]
    )
    bewertungen = stufe2_bewerten([DETAIL, ANGEBOT2], "prompt", mistral, "modell")
    assert [(b.angenommen, b.beggruendung) for b in bewertungen] == [
        (True, "passt"),
        (False, "nein"),
    ]
    assert json.loads(mistral.aufrufe[0]["nutzer"]) == DETAIL
    assert mistral.aufrufe[0]["json"] is True


def test_stufe2_ungueltiges_json_bricht_ab() -> None:
    mistral = FakeMistral(["[]"])
    with pytest.raises(AgentFehler):
        stufe2_bewerten([DETAIL], "prompt", mistral, "modell")


def test_feld_agnostizismus_umbenannte_felder() -> None:
    """Alle Felder außer referenznummer sind ausgetauscht; die Stufen
    verarbeiten die Datensätze trotzdem roh und unverändert."""
    seltsam = {
        "referenznummer": "R9",
        "xyz_beruf": "Datenbankzauberer",
        "foo": {"verschachtelt": [1, 2, {"bar": "baz"}]},
    }
    mistral = FakeMistral(['{"referenznummern": ["R9"]}'])
    ergebnis = stufe1_filtern([seltsam], "prompt", mistral, "modell")
    assert ergebnis == [seltsam]
    assert json.loads(mistral.aufrufe[0]["nutzer"]) == [seltsam]

    mistral2 = FakeMistral(
        ['{"referenznummer": "R9", "entscheidung": "angenommen", "begruendung": "ok"}']
    )
    bewertungen = stufe2_bewerten([seltsam], "prompt", mistral2, "modell")
    assert bewertungen[0].angenommen is True
    assert json.loads(mistral2.aufrufe[0]["nutzer"]) == seltsam


# ---------------------------------------------------------------- Ablauf


def test_ablauf_vollstaendig(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    arbeitsamt = FakeArbeitsamt([[ANGEBOT, ANGEBOT2]], {"R1": DETAIL})
    mistral = FakeMistral(
        [
            '{"referenznummern": ["R1"]}',
            '{"referenznummer": "R1", "entscheidung": "angenommen", "begruendung": "passt"}',
            "Das ist der Bericht.",
        ]
    )
    schreibe_prompts(tmp_path)
    code = fuehre_aus(konfig(), arbeitsamt, mistral, tmp_path)
    assert code == 0
    bericht = tmp_path / "Bericht-57080-7-25.txt"
    assert bericht.read_text(encoding="utf-8") == "Das ist der Bericht."
    assert not (tmp_path / DATEI_STELLEN).exists()
    assert not (tmp_path / DATEI_DETAILS).exists()
    assert mistral.aufrufe[2]["json"] is False
    assert "Suchparameter: Postleitzahl 57080" in mistral.aufrufe[2]["nutzer"]
    assert json.dumps(DETAIL, ensure_ascii=False) in mistral.aufrufe[2]["nutzer"]


def test_ablauf_debug_laesst_dateien_liegen(tmp_path: Path) -> None:
    arbeitsamt = FakeArbeitsamt([[ANGEBOT]], {"R1": DETAIL})
    mistral = FakeMistral(
        [
            '{"referenznummern": ["R1"]}',
            '{"referenznummer": "R1", "entscheidung": "angenommen", "begruendung": "ok"}',
            "Bericht",
        ]
    )
    schreibe_prompts(tmp_path)
    assert fuehre_aus(konfig(debug=True), arbeitsamt, mistral, tmp_path) == 0
    assert json.loads((tmp_path / DATEI_STELLEN).read_text(encoding="utf-8")) == [
        ANGEBOT
    ]
    assert json.loads((tmp_path / DATEI_DETAILS).read_text(encoding="utf-8")) == [
        DETAIL
    ]


def test_ablauf_keine_daten(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mistral = FakeMistral([])
    code = fuehre_aus(konfig(), FakeArbeitsamt([[]], {}), mistral, tmp_path)
    assert code == 0
    assert "Keine Daten vorhanden" in capsys.readouterr().out
    assert not (tmp_path / DATEI_STELLEN).exists()


def test_ablauf_stufe1_leer(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mistral = FakeMistral(['{"referenznummern": []}'])
    schreibe_prompts(tmp_path)
    code = fuehre_aus(konfig(), FakeArbeitsamt([[ANGEBOT]], {}), mistral, tmp_path)
    assert code == 0
    assert "Keine Daten vorhanden" in capsys.readouterr().out


def test_ablauf_stufe2_leer(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mistral = FakeMistral(
        [
            '{"referenznummern": ["R1"]}',
            '{"referenznummer": "R1", "entscheidung": "abgelehnt", "begruendung": "nein"}',
        ]
    )
    schreibe_prompts(tmp_path)
    code = fuehre_aus(
        konfig(), FakeArbeitsamt([[ANGEBOT]], {"R1": DETAIL}), mistral, tmp_path
    )
    assert code == 0
    assert "Keine Jobs gefunden" in capsys.readouterr().out


def test_ablauf_detailabruf_teilweise_gescheitert(tmp_path: Path) -> None:
    mistral = FakeMistral(
        [
            '{"referenznummern": ["R1", "R2"]}',
            '{"referenznummer": "R1", "entscheidung": "angenommen", "begruendung": "ok"}',
            "Bericht",
        ]
    )
    schreibe_prompts(tmp_path)
    arbeitsamt = FakeArbeitsamt([[ANGEBOT, ANGEBOT2]], {"R1": DETAIL})
    assert fuehre_aus(konfig(), arbeitsamt, mistral, tmp_path) == 0


def test_ablauf_alle_details_gescheitert(tmp_path: Path) -> None:
    mistral = FakeMistral(['{"referenznummern": ["R1"]}'])
    schreibe_prompts(tmp_path)
    with pytest.raises(AgentFehler, match="Detailabruf"):
        fuehre_aus(konfig(), FakeArbeitsamt([[ANGEBOT]], {}), mistral, tmp_path)


def test_ablauf_stufe3_leere_antwort(tmp_path: Path) -> None:
    mistral = FakeMistral(
        [
            '{"referenznummern": ["R1"]}',
            '{"referenznummer": "R1", "entscheidung": "angenommen", "begruendung": "ok"}',
            "   ",
        ]
    )
    schreibe_prompts(tmp_path)
    with pytest.raises(AgentFehler, match="leere Antwort"):
        fuehre_aus(
            konfig(), FakeArbeitsamt([[ANGEBOT]], {"R1": DETAIL}), mistral, tmp_path
        )


# ---------------------------------------------------------- Prompt-Dateien


def test_prompt_fehlend_wird_erzeugt_und_abbruch(tmp_path: Path) -> None:
    pfad = tmp_path / DATEINAME_STUFE1
    with pytest.raises(AgentFehler, match="ausfüllen"):
        lade_prompt(pfad, "VORLAGE\n[[HIER: x]]", platzhalter_pflicht=True)
    assert pfad.read_text(encoding="utf-8") == "VORLAGE\n[[HIER: x]]"


def test_prompt_leer_abbruch_ohne_ueberschreiben(tmp_path: Path) -> None:
    pfad = tmp_path / DATEINAME_STUFE1
    pfad.write_text("", encoding="utf-8")
    with pytest.raises(AgentFehler, match="leer"):
        lade_prompt(pfad, "VORLAGE", platzhalter_pflicht=True)
    assert pfad.read_text(encoding="utf-8") == ""


def test_prompt_platzhalter_unveraendert_abbruch(tmp_path: Path) -> None:
    pfad = tmp_path / DATEINAME_STUFE2
    pfad.write_text(
        "Struktur...\n[[HIER: Deine Jobprofile eintragen]]", encoding="utf-8"
    )
    with pytest.raises(AgentFehler, match="Platzhalter"):
        lade_prompt(pfad, "VORLAGE", platzhalter_pflicht=True)
    assert pfad.read_text(encoding="utf-8").startswith("Struktur")


def test_prompt_personalisiert_ok(tmp_path: Path) -> None:
    pfad = tmp_path / DATEINAME_STUFE1
    pfad.write_text("Meine Kriterien: alles mit Linux.", encoding="utf-8")
    assert (
        lade_prompt(pfad, "VORLAGE", platzhalter_pflicht=True)
        == "Meine Kriterien: alles mit Linux."
    )


def test_bericht_prompt_fehlend_wird_erzeugt(tmp_path: Path) -> None:
    pfad = tmp_path / DATEINAME_BERICHT
    with pytest.raises(AgentFehler, match="prüfen"):
        lade_prompt(pfad, "BERICHTSVORLAGE", platzhalter_pflicht=False)
    assert pfad.read_text(encoding="utf-8") == "BERICHTSVORLAGE"


def test_bericht_prompt_leer_abbruch(tmp_path: Path) -> None:
    pfad = tmp_path / DATEINAME_BERICHT
    pfad.write_text("  ", encoding="utf-8")
    with pytest.raises(AgentFehler, match="leer"):
        lade_prompt(pfad, "VORLAGE", platzhalter_pflicht=False)
    assert pfad.read_text(encoding="utf-8") == "  "


def test_bericht_prompt_ohne_platzhalter_ok(tmp_path: Path) -> None:
    pfad = tmp_path / DATEINAME_BERICHT
    pfad.write_text("Schreibe den Bericht.", encoding="utf-8")
    assert lade_prompt(pfad, "V", platzhalter_pflicht=False) == "Schreibe den Bericht."


def test_vorlagen_enthalten_platzhaltermarke() -> None:
    from a_shell_agent import prompts

    assert PLATZHALTER_MARKE in prompts.VORLAGE_STUFE1
    assert PLATZHALTER_MARKE in prompts.VORLAGE_STUFE2
    assert PLATZHALTER_MARKE not in prompts.VORLAGE_BERICHT


# ------------------------------------------------------------------ .env


def test_env_loader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pfad = tmp_path / ".env"
    pfad.write_text(
        "# Kommentar\nMISTRAL_API_KEY=aus-datei\nLEER=\nOHNE\n", encoding="utf-8"
    )
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    assert lade_env(pfad) == {"MISTRAL_API_KEY": "aus-datei", "LEER": ""}
    monkeypatch.setenv("MISTRAL_API_KEY", "aus-umgebung")
    from a_shell_agent.umgebung import hole_mistral_schluessel

    assert hole_mistral_schluessel(tmp_path) == "aus-umgebung"
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    assert hole_mistral_schluessel(tmp_path) == "aus-datei"
    (tmp_path / "ohne.env").write_text("", encoding="utf-8")
    with pytest.raises(AgentFehler, match="MISTRAL_API_KEY fehlt"):
        hole_mistral_schluessel(tmp_path / "ohne.env")
