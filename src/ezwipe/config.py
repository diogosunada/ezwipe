"""Where EZWipe keeps its data, and the operator settings file.

Data location, first match wins:
  1. $EZWIPE_DATA
  2. /run/ezwipe-data  (the EZWIPE-DATA partition on the boot USB, mounted at boot)
  3. %LOCALAPPDATA%\\EZWipe or ~/.local/share/EZWipe

Settings live in ezwipe.toml in that folder. Every key is optional:

    operator = "Jane Doe"
    organization = "Sunada Recycling"
    location = "Bench 2"
    report_logo = "logo.png"           # relative to the data folder
    auto_profile = "auto-purge"        # set to enable auto mode (wipe on boot)
    auto_delay_s = 60                  # countdown before auto mode starts
    custom_fields = ["Asset tag", "PO number"]
    dark_mode = true
"""

from __future__ import annotations

import hashlib
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import APP_NAME

BOOT_DATA_MOUNT = "/run/ezwipe-data"


def data_dir() -> Path:
    env = os.environ.get("EZWIPE_DATA")
    if env:
        p = Path(env)
    elif os.path.ismount(BOOT_DATA_MOUNT):
        p = Path(BOOT_DATA_MOUNT)
    else:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
        p = Path(base) / APP_NAME
    (p / "reports").mkdir(parents=True, exist_ok=True)
    return p


def data_is_persistent(p: Path) -> bool:
    """False when reports would be lost at shutdown (live system without a data partition)."""
    if os.environ.get("EZWIPE_DATA"):
        return True
    if os.path.isdir("/run/live") and not str(p).startswith(BOOT_DATA_MOUNT):
        return False
    return True


@dataclass
class Settings:
    operator: str = ""
    organization: str = ""
    location: str = ""
    report_logo: str = ""
    auto_profile: str = ""
    auto_delay_s: int = 60
    custom_fields: list[str] = field(default_factory=list)
    dark_mode: bool = True
    source: str = ""              # path of the file read, "" when defaults
    sha256: str = ""              # recorded when auto mode uses this file
    errors: list[str] = field(default_factory=list)

    @property
    def auto_enabled(self) -> bool:
        return bool(self.auto_profile)


def load_settings(folder: Path) -> Settings:
    path = folder / "ezwipe.toml"
    s = Settings()
    if not path.is_file():
        return s
    raw = path.read_bytes()
    s.source, s.sha256 = str(path), hashlib.sha256(raw).hexdigest()
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        s.errors.append(f"ezwipe.toml could not be read: {e}. Using defaults; auto mode is off.")
        return s
    for key in ("operator", "organization", "location", "report_logo", "auto_profile"):
        if isinstance(data.get(key), str):
            setattr(s, key, data[key].strip())
    if isinstance(data.get("auto_delay_s"), int):
        s.auto_delay_s = max(15, min(600, data["auto_delay_s"]))
    if isinstance(data.get("custom_fields"), list):
        s.custom_fields = [str(x) for x in data["custom_fields"]][:8]
    if isinstance(data.get("dark_mode"), bool):
        s.dark_mode = data["dark_mode"]
    return s
