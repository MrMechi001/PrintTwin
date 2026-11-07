"""Lab: every solid / surface body of a STEP file as its own numbered GLB node (original frame).

usage: <cadquery python> -I tools/cad/raw_glb.py IN.stp OUT.glb
Used to identify FokieH's merged CATPart bodies (see web/lab/model.html).
"""
import colorsys
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from glb import GlbWriter, tessellate  # noqa: E402
from OCP.IFSelect import IFSelect_RetDone  # noqa: E402
from OCP.STEPControl import STEPControl_Reader  # noqa: E402
from OCP.TopAbs import TopAbs_SHELL, TopAbs_SOLID  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402

src, out = sys.argv[1], sys.argv[2]
r = STEPControl_Reader()
assert r.ReadFile(src) == IFSelect_RetDone
r.TransferRoots()
shape = r.OneShape()
w = GlbWriter()
for kind, top, avoid in (("s", TopAbs_SOLID, None), ("sh", TopAbs_SHELL, TopAbs_SOLID)):
    ex = TopExp_Explorer(shape, top, avoid) if avoid else TopExp_Explorer(shape, top)
    i = 0
    while ex.More():
        t = tessellate(ex.Current(), 0.1, 0.4)
        if t:
            hue = (i * 0.618) % 1.0
            mat = w.material(f"{kind}{i}", colorsys.hsv_to_rgb(hue, 0.45 if kind == "s" else 0.9, 0.9),
                             0.1, 0.6, double=kind == "sh")
            w.node(f"{kind}{i}", w.mesh(f"{kind}{i}", *t, mat))
        i += 1
        ex.Next()
print(f"wrote {out}: {w.write(out) / 1e6:.1f} MB, {len(w.gltf['nodes'])} nodes")
