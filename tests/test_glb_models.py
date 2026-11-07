"""Printer models in web/models: valid GLB, the groups and metadata the viewer needs, gzip copy.

usage: python -I tests/test_glb_models.py
"""
import gzip
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "web/models"
failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    failures += 0 if ok else 1


def gltf_json(data: bytes):
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    assert magic == b"glTF" and version == 2 and length == len(data), "not a glTF 2.0 binary"
    clen, ctype = struct.unpack_from("<I4s", data, 12)
    assert ctype == b"JSON"
    return json.loads(data[20:20 + clen])


def material_of(g, node_name):
    for n in g["nodes"]:
        if n.get("name") == node_name and "mesh" in n:
            return g["materials"][g["meshes"][n["mesh"]]["primitives"][0]["material"]]["name"]
    return None


glbs = sorted(MODELS.glob("*.glb"))
check("models present", len(glbs) >= 2, ", ".join(p.name for p in glbs))
for path in glbs:
    data = path.read_bytes()
    try:
        g = gltf_json(data)
    except Exception as e:      # noqa: BLE001
        check(f"{path.name}: glTF", False, str(e))
        continue
    meta = g.get("extras", {}).get("printtwin", {})
    names = {n.get("name") for n in g["nodes"]}
    groups = {"static", "bed", "carriage"} | ({"gantry"} if meta.get("kinematics") == "corexy" else {"xaxis"})
    check(f"{path.name}: groups {sorted(groups)}", groups <= names)
    for key in ("version", "nozzle_y", "pose", "lcd_screen", "spool", "bounds", "sources"):
        check(f"{path.name}: extras.printtwin.{key}", key in meta)
    gz = path.with_name(path.name + ".gz")
    check(f"{path.name}: .gz copy identical", gz.exists() and gzip.decompress(gz.read_bytes()) == data)

# model-specific looks
mk4s = MODELS / "mk4s.glb"
if mk4s.exists():
    g = gltf_json(mk4s.read_bytes())
    check("mk4s: black Z parts", all(material_of(g, f"z-{p}") == "petg" for p in ("top-left", "top-right", "bottom-left", "bottom-right")))
    check("mk4s: orange X-ends", material_of(g, "x-end-motor") == "orange" and material_of(g, "x-end-idler") == "orange")
    names = {n.get("name") for n in g["nodes"]}
    check("mk4s: new Nextruder parts", {"fan-case", "fan-case-cover", "fan-shroud", "fan-door-cover", "print-fan"} <= names)
    shrouds = [n for n in g["nodes"] if n.get("name") == "fan-shroud"]
    check("mk4s: only the MK4S fan shroud", len(shrouds) == 1, f"{len(shrouds)} nodes")
mini = MODELS / "mini.glb"
if mini.exists():
    g = gltf_json(mini.read_bytes())
    meta = g["extras"]["printtwin"]
    names = {n.get("name") for n in g["nodes"]}
    check("mini: Prusa's parts", {"z-top", "z-bottom", "x-carriage", "heatsink", "display-box", "extruder-front",
                                  "superpinda-holder", "sheet"} <= names)
    check("mini: orange printed parts", material_of(g, "z-top") == "orange" and material_of(g, "x-end") == "orange")
    check("mini: portrait LCD, extruder on the Z carriage, bowden",
          meta.get("lcd_portrait") is True and "filament_inlet_xaxis" in meta
          and {"start_xaxis", "end_carriage", "dir_start", "dir_end"} <= set(meta.get("bowden", {})))
mk4 = MODELS / "mk4.glb"
if mk4.exists():
    g = gltf_json(mk4.read_bytes())
    check("mk4: orange Z parts", material_of(g, "z-top-left") == "orange")
    check("mk4: no MK4S parts", "fan-case" not in {n.get("name") for n in g["nodes"]})

print("\nall passed" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
