"""Einmalige interaktive Garmin-Anmeldung (für Konten mit Zwei-Faktor-Code).

    python -m app.garmin_login

Legt die Token in DATA_DIR/garmin_tokens ab. Danach läuft der Sync ohne Passwort-Eingabe.
"""
import getpass

from . import config
from .garmin import client


def main() -> None:
    if not config.GARMIN_EMAIL:
        config.GARMIN_EMAIL = input("Garmin-E-Mail: ").strip()
    if not config.GARMIN_PASSWORD:
        config.GARMIN_PASSWORD = getpass.getpass("Garmin-Passwort: ")
    client(prompt_mfa=lambda: input("Zwei-Faktor-Code: ").strip())
    print(f"Anmeldung erfolgreich. Token liegen in {config.GARMIN_TOKEN_DIR}")


if __name__ == "__main__":
    main()
