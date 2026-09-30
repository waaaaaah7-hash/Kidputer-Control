from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KC_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://kc:kc@localhost:5432/kidputercontrol"

    # Built dashboard (web/dist). When the folder exists it is served at "/", same origin as the API.
    static_dir: str = "static"

    # One-time bootstrap for hosts without a shell (e.g. Render free tier): if no admin exists yet at
    # startup, create this one. Remove KC_BOOTSTRAP_ADMIN_PASSWORD from the environment afterwards.
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""

    # Admin sessions
    session_cookie_name: str = "kc_session"
    session_ttl_hours: int = 12
    cookie_secure: bool = True  # set False only for local http dev

    # When true, an admin without TOTP enabled can only reach the TOTP setup endpoints.
    require_totp: bool = True
    totp_issuer: str = "Kidputer Control"
    mfa_pending_ttl_minutes: int = 5

    # Brute-force protection for dashboard login
    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    # Device enrollment
    enrollment_code_ttl_hours: int = 24

    # Lock-screen PIN hashing (PBKDF2-SHA256, verifiable offline by the agent via .NET Rfc2898DeriveBytes)
    pin_pbkdf2_iterations: int = 200_000

    # Allowed browser origins for the dashboard (comma separated)
    cors_origins: str = ""

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, v: str) -> str:
        # Hosts (Render, Heroku-style) hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the driver.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix) :]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
