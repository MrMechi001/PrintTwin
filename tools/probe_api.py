"""Dump what PrusaLink reports right now (a printer from config.local.toml).

usage: python -I tools/probe_api.py [printer id]       (default: the first printer)
Logs in with api_key, or user + password (PrusaLink on a Raspberry Pi: HTTP digest).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from printtwin import config  # noqa: E402
from printtwin.prusalink import PrusaLink  # noqa: E402

printers = config.load()["printer"]
want = sys.argv[1] if len(sys.argv) > 1 else None
p = next((q for q in printers if want is None or q.get("id") == want), None)
if p is None:
    sys.exit(f"no printer {want!r} in the config ({', '.join(q.get('id', '?') for q in printers)})")
pl = PrusaLink(p["host"], p.get("api_key"), user=p.get("user"), password=p.get("password"))
for path in ["/api/version", "/api/v1/info", "/api/v1/status", "/api/v1/job",
             "/api/v1/storage", "/api/v1/files/usb/", "/api/v1/files/local/", "/api/printer", "/api/job"]:
    status, data = pl.get_json(path)
    print(f"=== {path}  HTTP {status}")
    print(json.dumps(data, indent=2, ensure_ascii=False))
