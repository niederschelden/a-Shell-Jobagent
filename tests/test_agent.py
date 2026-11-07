"""Unit-Tests: kein Netzwerk, alle Clients sind Fakes."""

from __future__ import annotations

import email
import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

import a_shell_agent.pipeline as pipeline
from a_shell_agent import debug
from a_shell_agent.api import ArbeitsamtKlient
from a_shell_agent.fehler import AgentFehler
from a_shell_agent.konfiguration import (
    DEFAULT_MODELL_BERICHT,
    DEFAULT_MODELL_STUFE1,
    DEFAULT_MODELL_STUFE2,
    ZEITRAUM_WERTE,
    baue_konfiguration,
)
from a_shell_agent.llm import MistralKlient
from a_shell_agent.main import DATEI_DETAILS, DATEI_STELLEN, fuehre_aus
from a_shell_agent.pipeline import stufe1_filtern, stufe2_bewerten
from a_shell_agent.prompts import (
    DATEINAME_BERICHT,
    DATEINAME_STUFE1,
    DATEINAME_STUFE2,
    PLATZHALTER_MARKE,
    lade_alle_prompts,
    lade_inhalt,
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
        self,
        modell: str,
        system: str,
        nutzer: str,
        json_objekt: bool = True,
        debug_bezeichnung: str = "",
    ) -> str:
        self.aufrufe.append(
            {
                "modell": modell,
                "system": system,
                "nutzer": nutzer,
                "json": json_objekt,
                "debug_bezeichnung": debug_bezeichnung,
            }
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
            "IT-Anwendungsentwicklung, Linux, IT im Gesundheitswesen.\n",
            encoding="utf-8",
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
    urls: list[str] = []

    def fake_get(url: str) -> dict[str, Any]:
        urls.append(url)
        return {"stellenangebote": seiten.pop(0)}

    klient._get_json = fake_get  # type: ignore[method-assign]
    angebote = klient.hole_angebote("57080", 7, 25)
    assert len(angebote) == 237
    assert urls[0].endswith("size=100&page=1")
    assert urls[-1].endswith("page=3")


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
            '{"bewertungen": [{"referenznummer": "R1", "entscheidung": "angenommen", "begruendung": "passt"}, {"referenznummer": "R2", "entscheidung": "abgelehnt", "begruendung": "nein"}]}',
        ]
    )
    bewertungen = stufe2_bewerten([DETAIL, ANGEBOT2], "prompt", mistral, "modell")
    assert [(b.angenommen, b.beggruendung) for b in bewertungen] == [
        (True, "passt"),
        (False, "nein"),
    ]
    assert json.loads(mistral.aufrufe[0]["nutzer"]) == [DETAIL, ANGEBOT2]
    assert mistral.aufrufe[0]["json"] is True


def test_stufe2_ungueltiges_json_bricht_ab() -> None:
    mistral = FakeMistral(["[]"])
    with pytest.raises(AgentFehler):
        stufe2_bewerten([DETAIL], "prompt", mistral, "modell")


def test_stufe2_batching_nach_groesse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "BATCH_MAX_BYTES", 1000)
    gross = {"daten": "x" * 900}  # ~900 Bytes je Datensatz
    mistral = FakeMistral(
        [
            '{"bewertungen": [{"referenznummer": "R0", "entscheidung": "abgelehnt", "begruendung": "a"}]}',
            '{"bewertungen": [{"referenznummer": "R1", "entscheidung": "abgelehnt", "begruendung": "b"}]}',
            '{"bewertungen": [{"referenznummer": "R2", "entscheidung": "angenommen", "begruendung": "c"}]}',
        ]
    )
    bewertungen = stufe2_bewerten(
        [
            gross | {"referenznummer": "R0"},
            gross | {"referenznummer": "R1"},
            gross | {"referenznummer": "R2"},
        ],
        "prompt",
        mistral,
        "modell",
    )
    assert len(mistral.aufrufe) == 3  # drei Batches à einem Datensatz
    assert [b.beggruendung for b in bewertungen] == ["a", "b", "c"]


def test_stufe1_batching_nach_groesse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stufe 1 schickt Überschriften ebenfalls in Batches und vereinigt
    die gewählten Referenznummern über alle Batches."""
    monkeypatch.setattr(pipeline, "BATCH_MAX_BYTES", 1000)
    daten = [{"referenznummer": f"R{i}", "daten": "x" * 900} for i in range(3)]
    mistral = FakeMistral(
        [
            '{"referenznummern": ["R0"]}',
            '{"referenznummern": ["R2"]}',
            '{"referenznummern": []}',
        ]
    )
    ergebnis = stufe1_filtern(daten, "prompt", mistral, "modell")
    assert len(mistral.aufrufe) == 3
    assert [d["referenznummer"] for d in ergebnis] == ["R0", "R2"]


def test_stufe2_ausgabe_mit_titel_und_eine_begruendung(
    capsys: pytest.CaptureFixture[str],
) -> None:
    detail_mit_titel = {"referenznummer": "R1", "titel": "Entwickler", "ort": "X"}
    mistral = FakeMistral(
        [
            '{"bewertungen": [{"referenznummer": "R1", "entscheidung": "angenommen", "begruendung": "Passt zum Profil."}]}'
        ]
    )
    stufe2_bewerten([detail_mit_titel], "prompt", mistral, "modell")
    out = capsys.readouterr().out
    assert "[Stufe 2] R1 (Entwickler): angenommen - Passt zum Profil." in out
    assert out.count("Passt zum Profil.") == 1  # Begründung genau einmal


def test_ablauf_ohne_doppelte_begruendung(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mistral = FakeMistral(
        [
            '{"referenznummern": ["R1"]}',
            '{"bewertungen": [{"referenznummer": "R1", "entscheidung": "abgelehnt", "begruendung": "Fachfremd."}]}',
        ]
    )
    schreibe_prompts(tmp_path)
    fuehre_aus(konfig(), FakeArbeitsamt([[ANGEBOT]], {"R1": DETAIL}), mistral, tmp_path)
    out = capsys.readouterr().out
    assert out.count("Fachfremd.") == 1
    assert "[Bewertung]" not in out


def test_stufe2_umlaut_schluessel() -> None:
    mistral = FakeMistral(
        [
            '{"bewertungen": [{"referenznummer": "R1", "entscheidung": "Ja", "begr\u00fcndung": "passt gut"}]}'
        ]
    )
    bewertungen = stufe2_bewerten([DETAIL], "prompt", mistral, "modell")
    assert bewertungen[0].angenommen is True
    assert bewertungen[0].beggruendung == "passt gut"


def test_stufe2_typografische_anfuehrungszeichen() -> None:
    """Antwort mit „ “ statt ASCII-Anführungszeichen wird trotzdem geparst."""
    mistral = FakeMistral(
        [
            '{"bewertungen": [{"referenznummer": "R1", "entscheidung": "abgelehnt", "begruendung": "Fachfremd."}]}'
        ]
    )
    bewertungen = stufe2_bewerten([DETAIL], "prompt", mistral, "modell")
    assert bewertungen[0].angenommen is False
    assert bewertungen[0].beggruendung == "Fachfremd."


def test_stufe2_code_fence_around_json() -> None:
    mistral = FakeMistral(
        [
            '```json\n{"bewertungen": [{"referenznummer": "R1", "entscheidung": "angenommen", "begruendung": "Passt."}]}\n```'
        ]
    )
    bewertungen = stufe2_bewerten([DETAIL], "prompt", mistral, "modell")
    assert bewertungen[0].angenommen is True


def test_stufe1_typografische_anfuehrungszeichen() -> None:
    mistral = FakeMistral(['{"referenznummern": ["R1"]}'])
    ergebnis = stufe1_filtern([ANGEBOT, ANGEBOT2], "prompt", mistral, "modell")
    assert ergebnis == [ANGEBOT]


def test_stufe2_fehlende_bewertung_gilt_als_abgelehnt(
    capsys: pytest.CaptureFixture[str],
) -> None:
    mistral = FakeMistral(['{"bewertungen": []}'])
    bewertungen = stufe2_bewerten([DETAIL], "prompt", mistral, "modell")
    assert bewertungen[0].angenommen is False
    assert bewertungen[0].beggruendung == "vom Modell nicht bewertet"
    assert "nicht bewertet" in capsys.readouterr().out


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
        [
            '{"bewertungen": [{"referenznummer": "R9", "entscheidung": "angenommen", "begruendung": "ok"}]}'
        ]
    )
    bewertungen = stufe2_bewerten([seltsam], "prompt", mistral2, "modell")
    assert bewertungen[0].angenommen is True
    assert json.loads(mistral2.aufrufe[0]["nutzer"]) == [seltsam]


# ---------------------------------------------------------------- Ablauf


def test_ablauf_vollstaendig(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    arbeitsamt = FakeArbeitsamt([[ANGEBOT, ANGEBOT2]], {"R1": DETAIL})
    mistral = FakeMistral(
        [
            '{"referenznummern": ["R1"]}',
            '{"bewertungen": [{"referenznummer": "R1", "entscheidung": "angenommen", "begruendung": "passt"}]}',
            "Das ist der Bericht.",
        ]
    )
    schreibe_prompts(tmp_path)
    code = fuehre_aus(konfig(), arbeitsamt, mistral, tmp_path)
    assert code == 0
    out = capsys.readouterr().out
    assert not any(zeile.startswith(("[API]", "[LLM]")) for zeile in out.splitlines())
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
            '{"bewertungen": [{"referenznummer": "R1", "entscheidung": "angenommen", "begruendung": "ok"}]}',
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
    schreibe_prompts(tmp_path)
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
            '{"bewertungen": [{"referenznummer": "R1", "entscheidung": "abgelehnt", "begruendung": "nein"}]}',
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
            '{"bewertungen": [{"referenznummer": "R1", "entscheidung": "angenommen", "begruendung": "ok"}]}',
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
            '{"bewertungen": [{"referenznummer": "R1", "entscheidung": "angenommen", "begruendung": "ok"}]}',
            "   ",
        ]
    )
    schreibe_prompts(tmp_path)
    with pytest.raises(AgentFehler, match="leere Antwort"):
        fuehre_aus(
            konfig(), FakeArbeitsamt([[ANGEBOT]], {"R1": DETAIL}), mistral, tmp_path
        )


# ------------------------------------------------------------------ Debug


class FakeAntwort:
    def __init__(self, daten: bytes) -> None:
        self._daten = daten

    def read(self) -> bytes:
        return self._daten

    def __enter__(self) -> FakeAntwort:
        return self

    def __exit__(self, *args: object) -> None:
        pass


def _http_fehler(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "url", code, "Fehler", email.message.Message(), io.BytesIO(b"{}")
    )


def test_debug_ohne_schalter_keine_zeilen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    debug.aktiviere(konfig(debug=False))
    klient = ArbeitsamtKlient()
    urls = ["https://rest.arbeitsagentur.de/x"]
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout: FakeAntwort(b"{}")
    )
    klient._get_json(urls[0])
    out = capsys.readouterr().out
    assert not any(z.startswith(("[API]", "[LLM]")) for z in out.splitlines())


def test_debug_api_zeilen_vor_jeder_anfrage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    debug.aktiviere(konfig(debug=True))
    klient = ArbeitsamtKlient()
    klient.PAUSE_SEKUNDEN = 0.0
    seiten = [
        {"stellenangebote": [{"referenznummer": f"R{i}"} for i in range(2)]},
    ]

    def fake_urlopen(req: Any, timeout: float) -> FakeAntwort:
        urls.append(req.full_url)
        return FakeAntwort(json.dumps(seiten.pop(0)).encode("utf-8"))

    urls: list[str] = []
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    klient.hole_angebote("57080", 7, 25)
    out = capsys.readouterr().out
    api_zeilen = [z for z in out.splitlines() if z.startswith("[API]")]
    assert api_zeilen == [f"[API] GET {u}" for u in urls]
    assert urls[0].endswith("size=100&page=1")


def test_debug_api_wiederholungsversuche(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    debug.aktiviere(konfig(debug=True))
    klient = ArbeitsamtKlient()
    klient.BACKOFF_SEKUNDEN = 0.001
    versuche: list[int] = []

    def fake_urlopen(req: Any, timeout: float) -> FakeAntwort:
        versuche.append(1)
        if len(versuche) < 3:
            raise _http_fehler(429)
        return FakeAntwort(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    klient._get_json("https://rest.arbeitsagentur.de/y")
    out = capsys.readouterr().out
    api_zeilen = [z for z in out.splitlines() if z.startswith("[API]")]
    assert len(versuche) == 3
    assert len(api_zeilen) == 3
    assert all(z == "[API] GET https://rest.arbeitsagentur.de/y" for z in api_zeilen)


def test_debug_llm_zeilen(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    debug.aktiviere(konfig(debug=True))
    klient = MistralKlient("schluessel")
    korpus = {"choices": [{"message": {"content": "ok"}}]}
    daten: list[bytes] = []

    def fake_urlopen(req: Any, timeout: float) -> FakeAntwort:
        daten.append(bytes(req.data))
        return FakeAntwort(json.dumps(korpus).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    klient.frage(
        "mistral-medium-latest",
        "system",
        "nutzer",
        json_objekt=True,
        debug_bezeichnung="Stufe 1 JSON",
    )
    out = capsys.readouterr().out
    erwartet = (
        f"[LLM] Sende Stufe 1 JSON an mistral-medium-latest "
        f"({round(len(daten[0]) / 1000)} KB)"
    )
    assert erwartet in out.splitlines()
    assert "nutzer" not in out  # Inhalt erscheint nie in der Debug-Ausgabe


def test_debug_llm_wiederholungsversuche(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    debug.aktiviere(konfig(debug=True))
    klient = MistralKlient("schluessel")
    klient.BACKOFF_SEKUNDEN = 0.001
    versuche: list[int] = []

    def fake_urlopen(req: Any, timeout: float) -> FakeAntwort:
        versuche.append(1)
        if len(versuche) < 2:
            raise _http_fehler(500)
        return FakeAntwort(
            json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    klient.frage("m", "s", "n", debug_bezeichnung="Stufe 2 JSON")
    out = capsys.readouterr().out
    llm_zeilen = [z for z in out.splitlines() if z.startswith("[LLM]")]
    assert len(versuche) == 2
    assert len(llm_zeilen) == 2
    assert all(z.startswith("[LLM] Sende Stufe 2 JSON an m (") for z in llm_zeilen)


def test_debug_bezeichnungen_der_stufen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Im Debug-Modus tragen die drei Aufruforte ihre hartkodierte
    Bezeichnung und das Modell aus der Konfiguration."""
    debug.aktiviere(
        baue_konfiguration(
            [
                "57080",
                "7",
                "25",
                "--debug",
                "--llm1",
                "m1",
                "--llm2",
                "m2",
                "--llm3",
                "m3",
            ]
        )
    )
    mistral = FakeMistral(
        [
            '{"referenznummern": ["R1"]}',
            '{"bewertungen": [{"referenznummer": "R1", "entscheidung": "angenommen", "begruendung": "ok"}]}',
            "Bericht",
        ]
    )
    schreibe_prompts(tmp_path)
    arbeitsamt = FakeArbeitsamt([[ANGEBOT]], {"R1": DETAIL})
    assert (
        fuehre_aus(
            baue_konfiguration(
                [
                    "57080",
                    "7",
                    "25",
                    "--debug",
                    "--llm1",
                    "m1",
                    "--llm2",
                    "m2",
                    "--llm3",
                    "m3",
                ]
            ),
            arbeitsamt,
            mistral,
            tmp_path,
        )
        == 0
    )
    bezeichnungen = [a["debug_bezeichnung"] for a in mistral.aufrufe]
    modelle = [a["modell"] for a in mistral.aufrufe]
    assert bezeichnungen == ["Stufe 1 JSON", "Stufe 2 JSON", "Stufe 3 Berichtsdaten"]
    assert modelle == ["m1", "m2", "m3"]


# ------------------------------------------------- Vorab-Prüfung Prompts


def test_vorpruefung_legt_pflichtdateien_an(tmp_path: Path) -> None:
    with pytest.raises(
        AgentFehler,
        match=r"erstellt: .*stufe1_prompts\.txt, stufe2_prompts\.txt",
    ):
        lade_alle_prompts(tmp_path)
    from a_shell_agent import prompts

    for name, vorlage in (
        (prompts.DATEINAME_STUFE1, prompts.VORLAGE_STUFE1),
        (prompts.DATEINAME_STUFE2, prompts.VORLAGE_STUFE2),
    ):
        assert (tmp_path / name).read_text(encoding="utf-8") == vorlage
    # Berichtsdatei ist optional und wird nicht angelegt
    assert not (tmp_path / prompts.DATEINAME_BERICHT).exists()


def test_vorpruefung_legt_fehlende_ohne_ueberschreiben(tmp_path: Path) -> None:
    schreibe_prompts(tmp_path)
    (tmp_path / DATEINAME_STUFE1).unlink()
    with pytest.raises(AgentFehler, match=r"erstellt: .*stufe1_prompts\.txt"):
        lade_alle_prompts(tmp_path)
    # Stufe 2 bleibt unberührt
    assert (tmp_path / DATEINAME_STUFE2).read_text(encoding="utf-8") == (
        "IT-Anwendungsentwicklung, Linux, IT im Gesundheitswesen.\n"
    )


def test_vorpruefung_sammelt_probleme(tmp_path: Path) -> None:
    schreibe_prompts(tmp_path)
    (tmp_path / DATEINAME_STUFE1).write_text("", encoding="utf-8")
    from a_shell_agent import prompts

    (tmp_path / DATEINAME_STUFE2).write_text(prompts.VORLAGE_STUFE2, encoding="utf-8")
    with pytest.raises(AgentFehler, match=r"leer.*Platzhalter"):
        lade_alle_prompts(tmp_path)
    assert (tmp_path / DATEINAME_STUFE1).read_text(encoding="utf-8") == ""


def test_vorpruefung_ok_liefert_kombinierte_prompts(tmp_path: Path) -> None:
    schreibe_prompts(tmp_path)
    p1, p2, p3 = lade_alle_prompts(tmp_path)
    from a_shell_agent import struktur

    assert p1.startswith(struktur.STRUKTUR_STUFE1)
    assert p1.endswith("IT-Anwendungsentwicklung, Linux, IT im Gesundheitswesen.\n")
    assert p2.startswith(struktur.STRUKTUR_STUFE2)
    # Ohne Berichtsdatei: reine Struktur
    assert p3 == struktur.STRUKTUR_BERICHT


def test_vorpruefung_vor_api_abruf(tmp_path: Path) -> None:
    """Fehlende Prompts brechen den Lauf ab, bevor irgendein API-Abruf
    oder LLM-Aufruf stattfindet (spart Rechenleistung)."""
    mistral = FakeMistral([])
    arbeitsamt = FakeArbeitsamt([[ANGEBOT]], {"R1": DETAIL})
    with pytest.raises(AgentFehler, match="erstellt"):
        fuehre_aus(konfig(), arbeitsamt, mistral, tmp_path)
    assert mistral.aufrufe == []
    assert arbeitsamt.seiten == [[ANGEBOT]]  # nie angefasst


# ---------------------------------------------------------- Prompt-Dateien


def test_prompt_fehlend_wird_erzeugt_und_abbruch(tmp_path: Path) -> None:
    pfad = tmp_path / DATEINAME_STUFE1
    with pytest.raises(AgentFehler, match="ausfüllen"):
        lade_inhalt(pfad, "VORLAGE\n[[HIER: x]]", platzhalter_pflicht=True)
    assert pfad.read_text(encoding="utf-8") == "VORLAGE\n[[HIER: x]]"


def test_prompt_leer_abbruch_ohne_ueberschreiben(tmp_path: Path) -> None:
    pfad = tmp_path / DATEINAME_STUFE1
    pfad.write_text("", encoding="utf-8")
    with pytest.raises(AgentFehler, match="leer"):
        lade_inhalt(pfad, "VORLAGE", platzhalter_pflicht=True)
    assert pfad.read_text(encoding="utf-8") == ""


def test_prompt_platzhalter_unveraendert_abbruch(tmp_path: Path) -> None:
    pfad = tmp_path / DATEINAME_STUFE2
    pfad.write_text(
        "Struktur...\n[[HIER: Deine Jobprofile eintragen]]", encoding="utf-8"
    )
    with pytest.raises(AgentFehler, match="Platzhalter"):
        lade_inhalt(pfad, "VORLAGE", platzhalter_pflicht=True)
    assert pfad.read_text(encoding="utf-8").startswith("Struktur")


def test_prompt_personalisiert_ok(tmp_path: Path) -> None:
    pfad = tmp_path / DATEINAME_STUFE1
    pfad.write_text("Meine Kriterien: alles mit Linux.", encoding="utf-8")
    assert (
        lade_inhalt(pfad, "VORLAGE", platzhalter_pflicht=True)
        == "Meine Kriterien: alles mit Linux."
    )


def test_bericht_inhalt_fehlend_ist_leer(tmp_path: Path) -> None:
    pfad = tmp_path / DATEINAME_BERICHT
    assert lade_inhalt(pfad, "V", platzhalter_pflicht=False) == ""
    assert not pfad.exists()  # optionale Datei wird nicht angelegt


def test_bericht_inhalt_leer_ist_leer(tmp_path: Path) -> None:
    pfad = tmp_path / DATEINAME_BERICHT
    pfad.write_text("  ", encoding="utf-8")
    assert lade_inhalt(pfad, "V", platzhalter_pflicht=False) == ""


def test_bericht_inhalt_platzhalter_ist_leer(tmp_path: Path) -> None:
    pfad = tmp_path / DATEINAME_BERICHT
    pfad.write_text("[[HIER: Wünsche]]", encoding="utf-8")
    assert lade_inhalt(pfad, "V", platzhalter_pflicht=False) == ""


def test_bericht_inhalt_vorhanden(tmp_path: Path) -> None:
    pfad = tmp_path / DATEINAME_BERICHT
    pfad.write_text("Schreibe locker und kurz.", encoding="utf-8")
    assert (
        lade_inhalt(pfad, "V", platzhalter_pflicht=False) == "Schreibe locker und kurz."
    )


def test_bericht_wird_mit_inhalt_kombiniert(tmp_path: Path) -> None:
    schreibe_prompts(tmp_path)
    (tmp_path / DATEINAME_BERICHT).write_text(
        "Schreibe locker und kurz.", encoding="utf-8"
    )
    _p1, _p2, p3 = lade_alle_prompts(tmp_path)
    from a_shell_agent import struktur

    assert p3.startswith(struktur.STRUKTUR_BERICHT)
    assert p3.endswith("Schreibe locker und kurz.")


def test_vorlagen_enthalten_platzhaltermarke() -> None:
    from a_shell_agent import prompts

    for vorlage in (
        prompts.VORLAGE_STUFE1,
        prompts.VORLAGE_STUFE2,
        prompts.VORLAGE_BERICHT,
    ):
        assert PLATZHALTER_MARKE in vorlage


def test_struktur_trennt_form_und_inhalt() -> None:
    """Die Form (Aufgabe, Antwortform, Anführungszeichen-Regel) liegt in
    struktur.py im Paket; die Inhalts-Vorlagen enthalten keine Struktur."""
    from a_shell_agent import prompts, struktur

    for text in (struktur.STRUKTUR_STUFE1, struktur.STRUKTUR_STUFE2):
        assert 'ASCII-Anführungszeichen " als JSON-Begrenzer' in text
        assert "niemals Anführungszeichen" in text
        assert "referenznummern" in struktur.STRUKTUR_STUFE1
        assert "bewertungen" in struktur.STRUKTUR_STUFE2
    for vorlage in (
        prompts.VORLAGE_STUFE1,
        prompts.VORLAGE_STUFE2,
        prompts.VORLAGE_BERICHT,
    ):
        assert "JSON" not in vorlage
        assert "Antworte" not in vorlage


# ------------------------------------------------------------------ .env


def test_env_loader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pfad = tmp_path / ".env"
    pfad.write_text(
        '# Kommentar\nMISTRAL_API_KEY="aus-datei-mit-quotes"\nLEER=\nOHNE\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    assert lade_env(pfad) == {
        "MISTRAL_API_KEY": "aus-datei-mit-quotes",
        "LEER": "",
    }
    monkeypatch.setenv("MISTRAL_API_KEY", "aus-umgebung")
    from a_shell_agent.umgebung import hole_mistral_schluessel

    assert hole_mistral_schluessel(tmp_path) == "aus-umgebung"
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    assert hole_mistral_schluessel(tmp_path) == "aus-datei-mit-quotes"
    (tmp_path / "ohne.env").write_text("", encoding="utf-8")
    with pytest.raises(AgentFehler, match="MISTRAL_API_KEY fehlt"):
        hole_mistral_schluessel(tmp_path / "ohne.env")
