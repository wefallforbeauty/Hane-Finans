"""Where personal data and settings live.

Personal data never goes into the repository. By default it lives under the
XDG data directory (``~/.local/share/hane-finans/``):

- ``defter.sqlite``: the ledger, prices and CPI
- ``yedek/``: backups
- ``cikti/``: charts and reports

Settings (the EVDS API key) are read from the ``EVDS_API_KEY`` environment
variable or from ``~/.config/hane-finans/ayarlar.toml``.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from hane_finans.errors import FinansError

APP_DIR_NAME = "hane-finans"
DB_FILE_NAME = "defter.sqlite"
ENV_DATA_DIR = "HANE_FINANS_DIZIN"
ENV_DB = "HANE_FINANS_VT"
ENV_EVDS_KEY = "EVDS_API_KEY"


def data_dir() -> Path:
    if custom := os.environ.get(ENV_DATA_DIR):
        return Path(custom).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / APP_DIR_NAME


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / APP_DIR_NAME


def default_db_path() -> Path:
    if custom := os.environ.get(ENV_DB):
        return Path(custom).expanduser()
    return data_dir() / DB_FILE_NAME


def settings_path() -> Path:
    return config_dir() / "ayarlar.toml"


def load_settings() -> dict:
    path = settings_path()
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise FinansError(f"Ayar dosyası okunamadı ({path}): {exc}") from exc


def evds_api_key() -> str | None:
    """EVDS key from the environment, else from the settings file."""
    if key := os.environ.get(ENV_EVDS_KEY, "").strip():
        return key
    key = load_settings().get("evds", {}).get("api_key", "")
    return key.strip() or None


def save_evds_api_key(key: str) -> Path:
    """Write the key to the settings file, readable only by the user."""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    settings.setdefault("evds", {})["api_key"] = key.strip()
    lines: list[str] = []
    for section, values in settings.items():
        if not isinstance(values, dict):
            continue
        lines.append(f"[{section}]")
        for k, v in values.items():
            escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{k} = "{escaped}"')
        lines.append("")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    os.chmod(path, 0o600)
    return path
