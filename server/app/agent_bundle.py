"""Agent auto-update (roadmap #13): the agent files this server ships, and their fingerprints.

Updates come only from the agent folder baked into the server's deployed image (KC_AGENT_DIR). There
is no way to upload agent code through the API, so someone who steals a parent's password can't push
code to the PC. The agent downloads the manifest and each file over HTTPS and checks every file's
SHA-256, and the bundle fingerprint as a whole, before replacing anything.
"""

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class BundleFile:
    path: str  # relative to the KidputerControl folder, with Windows separators ("Modules\\X.psm1")
    sha256: str  # upper-case hex, as the agent's Get-FileHash style output
    size: int
    source: Path


@dataclass(frozen=True)
class Bundle:
    version: str
    fingerprint: str
    files: tuple[BundleFile, ...]

    def file(self, path: str) -> BundleFile | None:
        for f in self.files:
            if f.path.lower() == path.lower():
                return f
        return None


def fingerprint(files: list[tuple[str, str]]) -> str:
    """SHA-256 over "path:sha256" lines sorted by lower-cased path. The agent computes the same."""
    lines = "".join(f"{p.lower()}:{h.upper()}\n" for p, h in sorted(files, key=lambda x: x[0].lower()))
    return hashlib.sha256(lines.encode()).hexdigest().upper()


@lru_cache
def load_bundle(agent_dir: str) -> Bundle | None:
    root = Path(agent_dir) / "KidputerControl"
    if not root.is_dir():
        return None
    files = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        data = p.read_bytes()
        rel = p.relative_to(root).as_posix().replace("/", "\\")
        files.append(BundleFile(rel, hashlib.sha256(data).hexdigest().upper(), len(data), p))
    common = root / "Modules" / "KidputerControl.Common.psm1"
    m = re.search(r"\$script:AgentVersion = '([^']+)'", common.read_text()) if common.is_file() else None
    return Bundle(m.group(1) if m else "unknown", fingerprint([(f.path, f.sha256) for f in files]), tuple(files))
