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

    # --- Elasticsearch (für die client-API /api/search) ---------------------
    # Der Server spricht ES direkt, damit Web-/Rich-Clients suchen können, ohne ES
    # je an den Browser zu exponieren. Gleiche Instanz/Index wie der CLI-Indexlauf.
    ES_HOST: str = "http://localhost:9200"
    ES_USER: str = "elastic"
    ES_PASSWORD: str = ""
    ES_INDEX: str = "emails"
    ES_VERIFY_CERTS: bool = True

    # --- client-API-Auth (Web-UI / Rich-Client) -----------------------------
    # Leichtgewichtige interne Auth: ein Benutzer, signiertes Session-Cookie
    # (HMAC über SECRET_KEY). Für Mehrbenutzer/Rollen später erweiterbar.
    WEB_USERNAME: str = "admin"
    WEB_PASSWORD: str = ""            # leer = client-API-Login deaktiviert (500)
    WEB_SESSION_TTL: int = 43200      # Gültigkeit des Session-Cookies in Sekunden (12 h)
    # Erlaubte Frontend-Origins (CORS, Komma-getrennt). Default: Vite-Dev-Server.
    WEB_ORIGINS: str = "http://localhost:5173"
    WEB_COOKIE_SECURE: bool = False   # in Produktion (HTTPS) auf true
    WEB_COOKIE_SAMESITE: str = "lax"  # bei fremder Domain + HTTPS: "none"

    @property
    def web_origins(self) -> list[str]:
        return [o.strip() for o in self.WEB_ORIGINS.split(",") if o.strip()]


settings = Settings()
