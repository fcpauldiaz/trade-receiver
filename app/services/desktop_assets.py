from __future__ import annotations

import re
from pathlib import Path

from app.config import settings

ALLOWED_SUFFIXES = {".dmg", ".exe", ".zip", ".xml"}
FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
MAX_ASSET_BYTES = 200 * 1024 * 1024

LATEST_MAC = "TradeDeskyWatcher.dmg"
LATEST_WIN_SETUP = "TradeDeskyWatcher-setup.exe"
LATEST_WIN_ZIP = "TradeDeskyWatcher-win.zip"

LATEST_NT_WIN_SETUP = "TradeDeskyNinjaTraderReceiver-setup.exe"
LATEST_NT_WIN_ZIP = "TradeDeskyNinjaTraderReceiver-win.zip"
LATEST_NT_APPCAST = "TradeDeskyNinjaTraderReceiver-appcast.xml"

_SHORT_CACHE_NAMES = {
    "appcast.xml",
    LATEST_MAC,
    LATEST_WIN_SETUP,
    LATEST_WIN_ZIP,
    LATEST_NT_WIN_SETUP,
    LATEST_NT_WIN_ZIP,
    LATEST_NT_APPCAST,
}

_MEDIA_TYPES = {
    ".xml": "application/xml",
    ".dmg": "application/x-apple-diskimage",
    ".exe": "application/octet-stream",
    ".zip": "application/zip",
}


def assets_dir() -> Path:
    path = Path(settings.desktop_assets_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_filename(name: str) -> str:
    if not name or not FILENAME_RE.fullmatch(name):
        raise ValueError("Invalid filename")
    if Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError("Unsupported file type")
    return name


def latest_alias(name: str) -> str | None:
    lower = name.lower()
    if lower.endswith(".dmg"):
        return LATEST_MAC
    if lower.endswith(".exe") and "setup" in lower:
        if "ninjatraderreceiver" in lower:
            return LATEST_NT_WIN_SETUP
        return LATEST_WIN_SETUP
    if lower.endswith("-win.zip"):
        if "ninjatraderreceiver" in lower:
            return LATEST_NT_WIN_ZIP
        return LATEST_WIN_ZIP
    if lower == LATEST_NT_APPCAST.lower():
        return None
    if lower.endswith(".xml") and "ninjatraderreceiver" in lower and "appcast" in lower:
        return LATEST_NT_APPCAST
    return None


def save_asset(filename: str, data: bytes) -> list[str]:
    if len(data) > MAX_ASSET_BYTES:
        raise ValueError("File too large")
    name = validate_filename(filename)
    directory = assets_dir()
    dest = directory / name
    dest.write_bytes(data)
    written = [name]
    alias = latest_alias(name)
    if alias and alias != name:
        (directory / alias).write_bytes(data)
        written.append(alias)
    return written


def asset_path(filename: str) -> Path | None:
    name = validate_filename(filename)
    directory = assets_dir().resolve()
    path = (directory / name).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        return None
    return path


def media_type(filename: str) -> str:
    return _MEDIA_TYPES.get(Path(filename).suffix.lower(), "application/octet-stream")


def cache_control(filename: str) -> str:
    name = Path(filename).name
    if name in _SHORT_CACHE_NAMES:
        return "public, max-age=300"
    if latest_alias(name) is not None:
        return "public, max-age=31536000, immutable"
    return "public, max-age=300"
