"""config.local.toml as people fill it in: printer blocks checked and explained (config.prepare_printers).

usage: python -I tests/test_config.py
The blocks first-time users write (a Bambu printer typed into the Prusa example block, no id, the
example left in next to the real printer, ...) and what PrintTwin says about them; run.py with a
broken TOML file and with the untouched example.
"""
import copy
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from printtwin import config  # noqa: E402

failures = 0
EXAMPLE = tomllib.loads((ROOT / "config.example.toml").read_text(encoding="utf-8"))


def check(name: str, ok: bool, detail=""):
    global failures
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    failures += not ok


def prep(*blocks):
    cfg = {"printer": [copy.deepcopy(b) for b in blocks]}
    notes, problems = config.prepare_printers(cfg)
    return cfg["printer"], notes, problems


def main():
    bambu = {"host": "192.168.1.50", "serial": "01P00A123456789", "access_code": "12345678"}
    example_prusa = EXAMPLE["printer"][0]

    ps, notes, probs = prep(bambu)
    check("Bambu block without id and kind: works as Bambu", not probs and ps[0]["kind"] == "bambu", probs)
    check("... gets an id", ps[0]["id"] == "bambu" and any("no id" in n for n in notes), (ps, notes))

    typed_into_prusa = {**example_prusa, "host": "192.168.1.50", "serial": "01P00A123456789",
                        "access_code": "12345678"}
    del typed_into_prusa["api_key"]
    ps, notes, probs = prep(typed_into_prusa)
    check("serial + access code typed into the Prusa example block: Bambu", not probs
          and ps[0]["kind"] == "bambu" and ps[0]["id"] == "mk4", probs)

    ps, notes, probs = prep(example_prusa, {"id": "p1s", "name": "Bambu Lab P1S", "kind": "bambu", **bambu})
    check("example Prusa block left in next to a filled Bambu block: left out", not probs
          and [p["id"] for p in ps] == ["p1s"] and any("example" in n for n in notes), (ps, probs))

    ps, notes, probs = prep(example_prusa)
    check("only the example: says what is missing", len(probs) == 1 and "host" in probs[0] and "api_key" in probs[0]
          and 'kind = "bambu"' in probs[0], probs)

    ps, notes, probs = prep({"id": "x1", "kind": "bambu", "host": "192.168.1.51", "serial": "00M09A1234"})
    check("Bambu without access code: names exactly that", len(probs) == 1 and "access_code" in probs[0]
          and "serial" not in probs[0] and "api_key" not in probs[0], probs)

    ps, notes, probs = prep({"id": "p", "kind": "bambu", "host": "192.168.x.y", "serial": "01P00A...",
                             "access_code": "87654321"})
    check("example serial 01P00A... counts as not filled in", len(probs) == 1 and "serial" in probs[0]
          and "host" in probs[0], probs)

    ps, notes, probs = prep({"id": "mk3", "host": "192.168.1.9:8080", "user": "maker", "password": "secret"})
    check("MK3 with user + password: fine", not probs and ps[0]["kind"] == "prusalink", probs)

    ps, notes, probs = prep({"id": "a", "kind": "moonraker", "host": "voron.local"},
                            {"id": "a", "kind": "moonraker", "host": "voron2.local"})
    check("an id used twice is reported", any("used twice" in p for p in probs), probs)

    ps, notes, probs = prep({"id": "q", "kind": "Bambu", **bambu})
    check("kind is not case sensitive", not probs and ps[0]["kind"] == "bambu", probs)

    ps, notes, probs = prep({"id": "q", "kind": "creality", "host": "192.168.1.5"})
    check("unknown kind is reported", len(probs) == 1 and "unknown" in probs[0], probs)

    ps, notes, probs = prep({"name": "Bambu Lab P1S", **bambu}, {"name": "Bambu Lab P1S", **bambu})
    check("two printers without id get different ids", not probs and len({p["id"] for p in ps}) == 2,
          [p["id"] for p in ps])

    check("no printer at all is reported", prep()[2] != [])

    ps, notes, probs = prep({"ID": "x", "Kind": "bambu", "IP": "192.168.1.50", "Seriennummer": "01P00A123456789",
                             "Access_Code": "12345678", "colour": "red"})
    check("other spellings (IP, Seriennummer, Access_Code, Kind): understood", not probs and ps[0]["host"]
          == "192.168.1.50" and ps[0]["serial"] == "01P00A123456789" and ps[0]["access_code"] == "12345678", probs)
    check("... and an unknown setting is named", any("colour" in n and "not a setting" in n for n in notes), notes)

    # the friend's file: the Prusa block emptied (its [[printer]] line kept), the Bambu block filled in
    # but every line still commented out
    ex_text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    lines = ex_text.splitlines()
    first = lines.index("[[printer]]")
    end = next(i for i in range(first + 1, len(lines)) if lines[i].startswith("# A Klipper"))
    friend = lines[:first + 1] + [""] + lines[end:]
    friend = "\n".join(friend).replace('# host = "192.168.x.y"\n# serial', '# host = "192.168.1.50"\n# serial') \
        .replace('# serial = "01P00A..."', '# serial = "01P00A123456789"') \
        .replace('# access_code = "12345678"', '# access_code = "87654321"')
    cfg = tomllib.loads(friend)
    notes, probs = config.prepare_printers(cfg, friend)
    hint = next((p for p in probs if "start with #" in p), "")
    check("friend's file: the commented-out Bambu lines are named", "(serial)" in hint and "(access_code)" in hint
          and "(host)" in hint and "(api_key)" not in hint, probs)
    check("... with their line numbers", all(f"{n} (" in hint for n, l in enumerate(friend.splitlines(), 1)
                                             if l.startswith(("# serial", "# access_code"))), hint)
    notes, probs = config.prepare_printers(tomllib.loads(ex_text), ex_text)
    check("untouched example: no # hint (its comments are the example's own)",
          not any("start with #" in p for p in probs), probs)

    # run.py itself (always with --config: never the user's own config)
    tmp = Path(tempfile.mkdtemp(prefix="printtwin-config-"))
    bad = tmp / "bad.toml"
    bad.write_text('[[printer]]\nkind = "bambu"\nhost = "192.168.1.50"\nserial = 01P00A123456789\n', encoding="utf-8")
    r = subprocess.run([sys.executable, "-I", str(ROOT / "run.py"), "--config", str(bad)], capture_output=True,
                       text=True, timeout=60)
    check("TOML error: no traceback, a hint about quotes", r.returncode != 0 and "Traceback" not in r.stderr
          and "quotes" in r.stderr, r.stderr[-300:])
    ex = tmp / "example.toml"
    ex.write_text((ROOT / "config.example.toml").read_text(encoding="utf-8"), encoding="utf-8")
    r = subprocess.run([sys.executable, "-I", str(ROOT / "run.py"), "--config", str(ex)], capture_output=True,
                       text=True, timeout=60)
    check("untouched example: exits with the per-printer message", r.returncode == 1 and 'printer 1 ("mk4"'
          in r.stdout and "api_key" in r.stdout, r.stdout[-400:] + r.stderr[-300:])
    for f in tmp.iterdir():
        f.unlink()
    tmp.rmdir()

    print(f"\n{'all passed' if not failures else f'{failures} FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
