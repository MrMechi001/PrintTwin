"""Dump what PrusaLink reports right now (first printer in config.local.toml)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from printtwin import config  # noqa: E402
from printtwin.prusalink import PrusaLink  # noqa: E402

p = config.load()["printer"][0]
pl = PrusaLink(p["host"], p["api_key"])
for path in ["/api/version", "/api/v1/info", "/api/v1/status", "/api/v1/job",
             "/api/v1/storage", "/api/v1/files/usb/"]:
    status, data = pl.get_json(path)
    print(f"=== {path}  HTTP {status}")
    print(json.dumps(data, indent=2, ensure_ascii=False))
