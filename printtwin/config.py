from __future__ import annotations

import re
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


PRINTER_KEYS = {"id", "name", "kind", "host", "api_key", "user", "password", "model", "serial", "access_code",
                "cafile", "mqtt_port", "ftps_port", "ftps_plain_data"}
ALIASES = {"ip": "host", "ip_address": "host", "ipaddress": "host", "address": "host", "hostname": "host",
           "url": "host", "apikey": "api_key", "api-key": "api_key", "key": "api_key", "serial_number": "serial",
           "serialnumber": "serial", "seriennummer": "serial", "sn": "serial", "accesscode": "access_code",
           "access-code": "access_code", "lan_access_code": "access_code", "code": "access_code", "type": "kind",
           "username": "user", "pass": "password"}
_COMMENTED = re.compile(r"^\s*#+\s*(\[\[printer\]\]|([A-Za-z_-]+)\s*=\s*(.*?))\s*$")


def _commented_settings(text: str) -> list[tuple[int, str]]:
    """(line number, key) of printer settings that are commented out (# in front) although they
    hold the user's own values - i.e. differ from every commented line of config.example.toml."""
    def norm(line):
        m = _COMMENTED.match(line)
        if not m or not m.group(2):
            return None
        key = m.group(2).lower()
        val = re.sub(r'\s+#.*$', "", m.group(3)) if not m.group(3).startswith(("'", '"')) else \
            re.sub(r'^(".*?"|\'.*?\').*$', r"\1", m.group(3))
        return key, val.strip()
    try:
        example = {norm(l) for l in (ROOT / "config.example.toml").read_text(encoding="utf-8").splitlines()}
    except OSError:
        example = set()
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        kv = norm(line)
        if kv and ALIASES.get(kv[0], kv[0]) in PRINTER_KEYS and kv not in example and kv[1]:
            out.append((n, ALIASES.get(kv[0], kv[0])))
    return out


def prepare_printers(cfg: dict, text: str | None = None) -> tuple[list[str], list[str]]:
    """Make the [[printer]] blocks usable and say what is missing, per printer.
    * keys in other spellings (Host, IP, serial_number, ...): taken as the right key
    * serial + access_code but no kind (or the example's "prusalink" with no API key): Bambu Lab
    * no id: one from the name (or the kind), unique
    * a block that still holds only the example's values is left out when another printer is filled in
    * text (the config file): if something is missing, name the lines that are commented out (#)
      although they hold the user's own values
    Changes cfg["printer"]; returns (notes, problems) - notes: what was assumed, problems: one
    line per printer that cannot work yet (empty: all fine)."""
    notes, problems = [], []
    blocks = [pc for pc in cfg.get("printer", []) if isinstance(pc, dict)]
    for n, pc in enumerate(blocks, 1):
        for k in list(pc):
            right = ALIASES.get(k.lower().strip(), k.lower().strip())
            if right != k and right in PRINTER_KEYS and right not in pc:
                pc[right] = pc.pop(k)
                notes.append(f"printer {n}: {k} = ... taken as {right}")
            elif right not in PRINTER_KEYS:
                notes.append(f"printer {n}: {k} = ... is not a setting PrintTwin knows - ignored")
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
    if problems and text:
        hidden = _commented_settings(text)
        if hidden:
            lines = ", ".join(f"{n} ({k})" for n, k in hidden)
            problems.append(f"these lines start with # and are therefore ignored: line {lines} - delete the # "
                            f"at the start of each of them (and in front of the [[printer]] line above them)")
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
