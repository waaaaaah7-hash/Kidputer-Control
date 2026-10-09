from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KC_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://kc:kc@localhost:5432/kidputercontrol"

    # Built dashboard (web/dist). When the folder exists it is served at "/", same origin as the API.
    static_dir: str = "static"

    # Windows agent files bundled into the one-click installer download (the repo's agent/ folder).
    agent_dir: str = str(Path(__file__).resolve().parents[2] / "agent")

    # Public https address of this service, written into installer downloads. Empty = derive from the
    # request (correct behind Render's proxy, which forwards the original scheme and host).
    public_url: str = ""

    # One-time bootstrap for hosts without a shell (e.g. Render free tier): if no admin exists yet at
    # startup, create this one. Remove KC_BOOTSTRAP_ADMIN_PASSWORD from the environment afterwards.
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""

    # Admin sessions
    session_cookie_name: str = "kc_session"
    session_ttl_hours: int = 12

    # "Remember this device": password sign-ins on a remembered browser skip the TOTP code.
    device_cookie_name: str = "kc_device"
    remember_device_days: int = 30

    # Risky actions (delete a PC, change its PIN, download an installer, manage passkeys) need a
    # second-factor check (TOTP code or passkey) within this many minutes.
    reverify_minutes: int = 10

    # How long a link inviting another parent stays valid.
    invite_ttl_hours: int = 48

    # Passkeys (WebAuthn). The relying-party ID is the host the dashboard is opened on.
    webauthn_rp_name: str = "Kidputer Control"
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

    # Parent notifications by email, through any SMTP service (Gmail with an app password, Resend,
    # Postmark, ...). Email is off while smtp_host is empty; push notifications need no setup.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    # "starttls" (port 587), "ssl" (port 465) or "none".
    smtp_security: str = "starttls"

    # Background jobs: "PC went silent" checks, sending notifications, daily digests. Tests turn this
    # off and run the jobs directly.
    background_jobs: bool = True

    # Render sets RENDER_EXTERNAL_URL; used for links in notifications when public_url is empty.
    render_external_url: str = Field(default="", validation_alias="RENDER_EXTERNAL_URL")

    @property
    def dashboard_url(self) -> str:
        return (self.public_url or self.render_external_url).rstrip("/")

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

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
