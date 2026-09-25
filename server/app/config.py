from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SM_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://sm:sm@localhost:5432/sessionmonitor"

    # Admin sessions
    session_cookie_name: str = "sm_session"
    session_ttl_hours: int = 12
    cookie_secure: bool = True  # set False only for local http dev

    # When true, an admin without TOTP enabled can only reach the TOTP setup endpoints.
    require_totp: bool = True
    totp_issuer: str = "Session Monitor"
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
