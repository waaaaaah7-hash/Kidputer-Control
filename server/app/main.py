from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .bootstrap import bootstrap_admin
from .config import get_settings
from .deps import CSRF_HEADER
from .routers import agent, auth, devices, passkeys

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    bootstrap_admin(settings)
    yield


app = FastAPI(title="Kidputer Control API", version="1.0.0", lifespan=lifespan)

_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", CSRF_HEADER],
    )

# The dashboard is a static SPA with no inline scripts; QR codes are data: URLs.
_SPA_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "no-referrer")
    h.setdefault("X-Frame-Options", "DENY")
    if settings.cookie_secure:
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    if request.url.path.startswith("/api/"):
        h.setdefault("Cache-Control", "no-store")
    elif not request.url.path.startswith("/docs"):
        h.setdefault("Content-Security-Policy", _SPA_CSP)
    return response


app.include_router(auth.router, prefix="/api/v1")
app.include_router(passkeys.router, prefix="/api/v1")
app.include_router(devices.router, prefix="/api/v1")
app.include_router(agent.router, prefix="/api/v1")


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}


_static = Path(settings.static_dir)
if (_static / "index.html").is_file():
    app.mount("/assets", StaticFiles(directory=_static / "assets"), name="assets")
    _static_root = _static.resolve()

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = (_static_root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(_static_root):
            return FileResponse(candidate)
        # Client-side routes (/devices/..., /login) all load the app shell.
        return FileResponse(_static_root / "index.html", headers={"Cache-Control": "no-cache"})
