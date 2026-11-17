from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.local.toml"
PLACEHOLDERS = {"192.168.x.y", "PRUSALINK-API-KEY", "...", ""}   # the examples' values, not filled in yet


def filled(v) -> bool:
    """A config value that is set: not empty, not one of the examples' placeholders ("01P00A...")."""
    s = str(v if v is not None else "").strip()
    return s not in PLACEHOLDERS and not s.endswith("...")


KIND_NAMES = {"prusalink": "Prusa (PrusaLink)", "moonraker": "Klipper (Moonraker)", "bambu": "Bambu Lab"}
NEEDS = {      # kind -> [(key, what it is)]
    "prusalink": [("host", "the printer's IP address"), ("api_key", "its PrusaLink API key "
                  "(printer: Settings > Network > PrusaLink)")],
    "moonraker": [("host", "the address of Mainsail / Fluidd / Moonraker")],
    "bambu": [("host", "the printer's IP address"), ("serial", "its serial number"),
              ("access_code", "its LAN access code (printer screen, network settings)")],
}


def prepare_printers(cfg: dict) -> tuple[list[str], list[str]]:
    """Make the [[printer]] blocks usable and say what is missing, per printer.
    * serial + access_code but no kind (or the example's "prusalink" with no API key): Bambu Lab
    * no id: one from the name (or the kind), unique
    * a block that still holds only the example's values is left out when another printer is filled in
    Changes cfg["printer"]; returns (notes, problems) - notes: what was assumed, problems: one
    line per printer that cannot work yet (empty: all fine)."""
    notes, problems = [], []
    blocks = [pc for pc in cfg.get("printer", []) if isinstance(pc, dict)]
    for n, pc in enumerate(blocks, 1):
        kind = str(pc.get("kind") or "").strip().lower()
        if (kind in ("", "prusalink") and (filled(pc.get("serial")) or filled(pc.get("access_code")))
                and not filled(pc.get("api_key")) and not filled(pc.get("user"))):
            notes.append(f"printer {pc.get('id') or n}: serial number + access code given - "
                         f"taken as a Bambu Lab printer (kind = \"bambu\")")
            kind = "bambu"
        pc["kind"] = kind or "prusalink"

    def untouched(pc):                  # still the example: nothing of the printer's own filled in
        return not any(filled(pc.get(k)) for k in ("host", "api_key", "user", "password", "serial", "access_code"))

    def missing(pc):
        if pc["kind"] not in NEEDS:
            return [f'kind = "{pc["kind"]}" is unknown - use "prusalink", "moonraker" or "bambu"']
        out = [f"{k} ({what})" for k, what in NEEDS[pc["kind"]] if not filled(pc.get(k))]
        if (pc["kind"] == "prusalink" and out and out[-1].startswith("api_key")
                and filled(pc.get("user")) and filled(pc.get("password"))):
            out.pop()                   # MK3 + PrusaLink on a Pi: user + password instead of the key
        return out

    ready = [pc for pc in blocks if not missing(pc)]
    if ready:
        for pc in [pc for pc in blocks if untouched(pc)]:
            notes.append(f"printer {pc.get('id') or '?'}: still the example's values - left out")
            blocks.remove(pc)
    used = set()
    for n, pc in enumerate(blocks, 1):
        pid = str(pc.get("id") or "").strip()
        if not pid:
            base = "".join(ch if ch.isalnum() else "-" for ch in str(pc.get("name") or pc["kind"]).lower()).strip("-")
            base = "-".join(filter(None, base.split("-"))) or "printer"
            pid, i = base, 2
            while pid in used or any(str(o.get("id") or "") == pid for o in blocks if o is not pc):
                pid, i = f"{base}{i}", i + 1
            notes.append(f"printer {n}: no id - using id = \"{pid}\" (address: /?p={pid})")
            pc["id"] = pid
        elif pid in used:
            problems.append(f'printer {n} ("{pid}"): the id "{pid}" is used twice - give each printer its own id')
        used.add(pid)
        miss = missing(pc)
        if miss:
            hint = ""
            if pc["kind"] == "prusalink":
                hint = ('\n      (a Bambu Lab printer? write  kind = "bambu"  and its serial and access_code; '
                        'a Klipper printer:  kind = "moonraker")')
            problems.append(f'printer {n} ("{pid}", {KIND_NAMES.get(pc["kind"], pc["kind"])}) - missing: '
                            + "; ".join(miss) + hint)
    cfg["printer"] = blocks
    if not blocks:
        problems.append("no [[printer]] block - copy one from config.example.toml")
    return notes, problems


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
    cfg.setdefault("connect", {})                       # Prusa Connect: off unless an explicit yes
    v = cfg["connect"].get("enabled", False)
    cfg["connect"]["enabled"] = v is True or str(v).strip().lower() in ("true", "yes", "on", "1")
    tokens = Path(cfg["connect"].get("tokens") or "connect.local.json")
    cfg["connect"]["tokens"] = tokens if tokens.is_absolute() else Path(path or DEFAULT_CONFIG).resolve().parent / tokens
    return cfg
