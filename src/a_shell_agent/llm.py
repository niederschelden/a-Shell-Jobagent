"""Client für die Mistral-Chat-API (urllib, stdlib-only)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from a_shell_agent.fehler import AgentFehler

ENDPOINT = "https://api.mistral.ai/v1/chat/completions"


class MistralKlient:
    """Ein Chat-Completion-Aufruf pro Stufe, mit Backoff bei 429/5xx/Netzwerk."""

    TIMEOUT_BASIS_SEKUNDEN = 120.0
    TIMEOUT_ZUWACHS_SEKUNDEN = 60.0
    ZUWACHS_AB_BYTES = 50_000
    MAX_VERSUCHE = 3
    BACKOFF_SEKUNDEN = 2.0

    def __init__(self, schluessel: str) -> None:
        self._schluessel = schluessel

    def frage(
        self, modell: str, system: str, nutzer: str, json_objekt: bool = True
    ) -> str:
        """Sendet System- und Nutzerinhalt; gibt den Antworttext zurück.

        json_objekt=True fordert die Antwort als JSON-Objekt an (Stufen 1/2);
        Stufe 3 (Bericht) ruft ohne response_format auf.
        """
        body: dict[str, Any] = {
            "model": modell,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": nutzer},
            ],
            "temperature": 0.2,
        }
        if json_objekt:
            body["response_format"] = {"type": "json_object"}
        daten = json.dumps(body, ensure_ascii=False).encode("utf-8")
        timeout = self._timeout_fuer(len(daten))
        fehler: BaseException | None = None
        for versuch in range(1, self.MAX_VERSUCHE + 1):
            try:
                anfrage = urllib.request.Request(
                    ENDPOINT,
                    data=daten,
                    headers={
                        "Authorization": f"Bearer {self._schluessel}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(anfrage, timeout=timeout) as antwort:
                    roh = json.loads(antwort.read().decode("utf-8"))
                return self._text(roh)
            except urllib.error.HTTPError as e:
                fehler = e
                if e.code == 429 or 500 <= e.code < 600:
                    self._warte(versuch, f"HTTP {e.code}")
                    continue
                raise AgentFehler(f"Mistral lehnt ab: HTTP {e.code}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                fehler = e
                self._warte(versuch, f"Netzwerkfehler: {e}")
        raise AgentFehler(
            f"Mistral nach {self.MAX_VERSUCHE} Versuchen unerreichbar: {fehler}"
        )

    def _text(self, roh: Any) -> str:
        try:
            inhalt = roh["choices"][0]["message"]["content"]
        except (TypeError, KeyError, IndexError) as e:
            raise AgentFehler(f"Unerwartete Mistral-Antwortstruktur: {roh!r}") from e
        if not isinstance(inhalt, str):
            raise AgentFehler("Mistral-Antwort enthält keinen Text.")
        return inhalt

    def _timeout_fuer(self, groesse: int) -> float:
        return (
            self.TIMEOUT_BASIS_SEKUNDEN
            + (groesse // self.ZUWACHS_AB_BYTES) * self.TIMEOUT_ZUWACHS_SEKUNDEN
        )

    def _warte(self, versuch: int, grund: str) -> None:
        sekunden = self.BACKOFF_SEKUNDEN**versuch
        print(
            f"[Mistral] {grund}, warte {sekunden:.0f} s "
            f"(Versuch {versuch}/{self.MAX_VERSUCHE})",
            flush=True,
        )
        time.sleep(sekunden)
