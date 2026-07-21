"""Konfiguration des mailarc-servers (aus Umgebung / .env).

Der Server ist die zentrale Gegenstelle zum ``RestStorage``/``accounts``-Client
des imap-archivers. Die Defaults machen ihn sofort lauffähig (SQLite); im Betrieb
wird ``DATABASE_URL`` auf Postgres gezeigt und ``API_TOKEN``/``SECRET_KEY`` gesetzt.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Ziel-Datenbank. Default SQLite (sofort lauffähig, keine Infrastruktur nötig);
    # im Betrieb Postgres, z. B. postgresql+psycopg://user:pass@host:5432/mailarc
    DATABASE_URL: str = "sqlite:///./mailarc_server.db"

    # Bearer-Token, das der Client als REST_API_TOKEN mitschickt. Ohne gesetztes
    # Token verweigert der Server jede Anfrage (500) — bewusst kein offener Default.
    API_TOKEN: str = ""

    # Fernet-Schlüssel (urlsafe base64, 32 Byte) zum Verschlüsseln der IMAP-Passwörter.
    # Erzeugen: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    SECRET_KEY: str = ""

    # Sync-Ordner-Lock als Lease mit TTL (Vorschlag Abschnitt 5.3, Stale-Locks).
    LOCK_TTL_SECONDS: int = 300

    # Obergrenze für die Seitengröße von GET /emails.
    MAX_PAGE_LIMIT: int = 1000

    # Fortschritt eines Sync-Jobs alle N verarbeiteten Mails persistieren (Live-Poll).
    JOB_COMMIT_EVERY: int = 200


settings = Settings()
