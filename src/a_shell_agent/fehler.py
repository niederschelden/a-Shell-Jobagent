"""Zentrale Fehlerklasse: jeder Abbruch läuft über AgentFehler."""


class AgentFehler(Exception):
    """Kontrollierter Abbruch mit klarer deutscher Fehlermeldung."""
