"""Client für die Jobsuche-API der Bundesagentur für Arbeit (urllib, stdlib-only)."""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from a_shell_agent.fehler import AgentFehler

SUCHE_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs"
DETAIL_URL = (
    "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobdetails"
)
API_HEADER = {"X-API-Key": "jobboerse-jobsuche"}


class ArbeitsamtKlient:
    """Suche mit Pagination und Detailabruf, jeweils mit exponentiellem Backoff."""

    SEITENGROESSE = 100
    PAUSE_SEKUNDEN = 6.0
    MAX_VERSUCHE = 5
    BACKOFF_SEKUNDEN = 2.0
    TIMEOUT_SEKUNDEN = 30.0

    def hole_angebote(
        self, plz: str, zeitraum: int, entfernung: int
    ) -> list[dict[str, Any]]:
        """Blättert durch alle Ergebnisseiten des Zeitraums (nur Überschriften)."""
        angebote: list[dict[str, Any]] = []
        seite = 1  # API erwartet 1-basierte Seiten, page=0 liefert HTTP 400
        while True:
            query = urllib.parse.urlencode(
                {
                    "angebotsart": "1",
                    "wo": plz,
                    "umkreis": entfernung,
                    "veroeffentlichtseit": zeitraum,
                    "size": self.SEITENGROESSE,
                    "page": seite,
                }
            )
            roh = self._get_json(f"{SUCHE_URL}?{query}")
            eintraege = self._ergebnisliste(roh)
            angebote.extend(eintraege)
            if len(eintraege) < self.SEITENGROESSE:
                return angebote
            seite += 1
            time.sleep(self.PAUSE_SEKUNDEN)

    def hole_detail(self, referenznummer: str) -> dict[str, Any]:
        """Ruft die Details eines Angebots ab (base64-codierte Referenznummer)."""
        code = base64.b64encode(referenznummer.encode("utf-8")).decode("ascii")
        url = f"{DETAIL_URL}/{urllib.parse.quote(code)}"
        detail = self._get_json(url)
        if not isinstance(detail, dict):
            raise AgentFehler(f"Detailantwort für {referenznummer} ist kein Objekt.")
        return detail

    def _ergebnisliste(self, roh: Any) -> list[dict[str, Any]]:
        if isinstance(roh, dict):
            for wert in roh.values():
                if isinstance(wert, list):
                    return [e for e in wert if isinstance(e, dict)]
            return []
        raise AgentFehler("Antwort der Jobsuche ist kein JSON-Objekt.")

    def _get_json(self, url: str) -> Any:
        fehler: BaseException | None = None
        for versuch in range(1, self.MAX_VERSUCHE + 1):
            try:
                anfrage = urllib.request.Request(url, headers=API_HEADER)
                with urllib.request.urlopen(
                    anfrage, timeout=self.TIMEOUT_SEKUNDEN
                ) as antwort:
                    return json.loads(antwort.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                fehler = e
                if e.code == 429 or 500 <= e.code < 600:
                    self._warte(versuch, f"HTTP {e.code}")
                    continue
                detail = self._fehlerdetail(e)
                raise AgentFehler(f"API lehnt ab: HTTP {e.code} ({url}){detail}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                fehler = e
                self._warte(versuch, f"Netzwerkfehler: {e}")
        raise AgentFehler(
            f"{url} nach {self.MAX_VERSUCHE} Versuchen unerreichbar: {fehler}"
        )

    def _fehlerdetail(self, e: urllib.error.HTTPError) -> str:
        try:
            meldung = e.read().decode("utf-8", errors="replace")
        except OSError:
            return ""
        kompakt = " ".join(meldung.split())
        return f": {kompakt[:200]}" if kompakt else ""

    def _warte(self, versuch: int, grund: str) -> None:
        sekunden = self.BACKOFF_SEKUNDEN**versuch
        print(
            f"[Arbeitsamt] {grund}, warte {sekunden:.0f} s "
            f"(Versuch {versuch}/{self.MAX_VERSUCHE})",
            flush=True,
        )
        time.sleep(sekunden)
