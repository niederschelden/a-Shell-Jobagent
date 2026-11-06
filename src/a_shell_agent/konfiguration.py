"""CLI-Parser und das vollständig validierte, unveränderliche Konfigurationsobjekt."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from a_shell_agent.fehler import AgentFehler

ZEITRAUM_WERTE: tuple[int, ...] = (1, 3, 7, 14, 30)
DEFAULT_MODELL_STUFE1 = "mistral-medium-latest"
DEFAULT_MODELL_STUFE2 = "mistral-medium-latest"
DEFAULT_MODELL_BERICHT = "mistral-medium-latest"


class Konfigurationsfehler(AgentFehler):
    """Ungültiger CLI-Parameter."""


@dataclass(frozen=True)
class Konfiguration:
    """Vollständig validierte, unveränderliche Aufrufkonfiguration."""

    plz: str
    zeitraum: int
    entfernung: int
    debug: bool
    modell_stufe1: str
    modell_stufe2: str
    modell_bericht: str


def _parse(argv: list[str]) -> argparse.Namespace:
    # a-Shell/iPad-Tastaturen ersetzen Doppelbindestriche gern durch
    # Geviertstriche; wir normalisieren führende „—" zu „--".
    argv = [("--" + a[1:]) if a.startswith("—") else a for a in argv]
    parser = argparse.ArgumentParser(
        prog="python -m a_shell_agent",
        description=(
            "Stellenangebote der Bundesagentur für Arbeit abrufen, per "
            "Mistral filtern und einen Bericht erstellen."
        ),
    )
    parser.add_argument("plz", help="Postleitzahl, genau 5 Ziffern")
    parser.add_argument(
        "zeitraum",
        help=(
            f"veröffentlicht seit N Tagen; erlaubt: "
            f"{', '.join(map(str, ZEITRAUM_WERTE))}"
        ),
    )
    parser.add_argument("entfernung", help="Suchradius in km, ganze Zahl von 1 bis 200")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="stellen.json und details.json am Ende nicht löschen",
    )
    parser.add_argument(
        "--llm1",
        default=DEFAULT_MODELL_STUFE1,
        help="Mistral-Modell Stufe 1 (Aussortieren)",
    )
    parser.add_argument(
        "--llm2",
        default=DEFAULT_MODELL_STUFE2,
        help="Mistral-Modell Stufe 2 (Positivauswahl)",
    )
    parser.add_argument(
        "--llm3",
        default=DEFAULT_MODELL_BERICHT,
        help="Mistral-Modell Stufe 3 (Bericht)",
    )
    return parser.parse_args(argv)


def baue_konfiguration(argv: list[str]) -> Konfiguration:
    """Prüft alle Parameter und baut das unveränderliche Konfigurationsobjekt."""
    args = _parse(argv)

    plz = args.plz
    if not (len(plz) == 5 and plz.isdigit()):
        raise Konfigurationsfehler(
            f"Ungültige Postleitzahl: {plz!r}. Regel: genau 5 Ziffern (z. B. 57080)."
        )

    try:
        zeitraum = int(args.zeitraum)
    except ValueError:
        zeitraum = -1
    if zeitraum not in ZEITRAUM_WERTE:
        raise Konfigurationsfehler(
            f"Ungültiger Zeitraum: {args.zeitraum!r}. Regel: veröffentlicht "
            f"seit N Tagen, erlaubte Werte: {', '.join(map(str, ZEITRAUM_WERTE))}."
        )

    try:
        entfernung = int(args.entfernung)
    except ValueError:
        entfernung = -1
    if not 1 <= entfernung <= 200:
        raise Konfigurationsfehler(
            f"Ungültige Entfernung: {args.entfernung!r}. Regel: Suchradius in "
            f"km als ganze Zahl im Bereich 1 bis 200."
        )

    return Konfiguration(
        plz=plz,
        zeitraum=zeitraum,
        entfernung=entfernung,
        debug=bool(args.debug),
        modell_stufe1=args.llm1,
        modell_stufe2=args.llm2,
        modell_bericht=args.llm3,
    )
