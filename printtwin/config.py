from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.local.toml"
PLACEHOLDERS = {"192.168.x.y", "PRUSALINK-API-KEY", "...", ""}   # the examples' values, not filled in yet


def filled(v) -> bool:
    """A config value that is set: not empty, not one of the examples' placeholders."""
    return str(v if v is not None else "").strip() not in PLACEHOLDERS


def load(path: Path | None = None) -> dict:
    with open(path or DEFAULT_CONFIG, "rb") as f:
        cfg = tomllib.load(f)
    cfg.setdefault("server", {})
    cfg["server"].setdefault("listen_host", "0.0.0.0")
    cfg["server"].setdefault("port", 8790)
    cfg.setdefault("metrics", {})
    cfg["metrics"].setdefault("listen_host", "0.0.0.0")
    cfg["metrics"].setdefault("listen_port", 8514)
    cfg.setdefault("slicer", {})
    v = cfg["slicer"].get("print_host", False)          # only an explicit yes turns it on
    cfg["slicer"]["print_host"] = v is True or str(v).strip().lower() in ("true", "yes", "on", "1")
    return cfg
