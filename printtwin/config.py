from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.local.toml"


def load(path: Path | None = None) -> dict:
    with open(path or DEFAULT_CONFIG, "rb") as f:
        cfg = tomllib.load(f)
    cfg.setdefault("server", {})
    cfg["server"].setdefault("listen_host", "0.0.0.0")
    cfg["server"].setdefault("port", 8790)
    cfg.setdefault("metrics", {})
    cfg["metrics"].setdefault("listen_host", "0.0.0.0")
    cfg["metrics"].setdefault("listen_port", 8514)
    return cfg
