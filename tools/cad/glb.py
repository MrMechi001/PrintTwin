"""Tessellate OCCT shapes and write a binary glTF (GLB) - no dependencies beyond OCP + numpy.

Each part becomes one mesh node (float32 positions/normals, uint32 indices) that can sit
under a named group node; materials are plain metallic-roughness PBR.
"""
from __future__ import annotations

import json
import struct

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepLib import BRepLib_ToolTriangulatedShape
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS


def tessellate(shape, lin_defl: float = 0.08, ang_defl: float = 0.35):
    """-> (positions Nx3 float32, normals Nx3 float32, indices Mx3 uint32) in the shape's frame."""
    BRepMesh_IncrementalMesh(shape, lin_defl, False, ang_defl, True)
    pos, nor, idx = [], [], []
    base = 0
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        face = TopoDS.Face_s(ex.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        ex.Next()
        if tri is None or tri.NbTriangles() == 0:
            continue
        if not tri.HasNormals():
            BRepLib_ToolTriangulatedShape.ComputeNormals_s(face, tri)
        trsf = loc.Transformation()
        n = tri.NbNodes()
        p = np.empty((n, 3))
        q = np.empty((n, 3))
        for i in range(1, n + 1):
            pt = tri.Node(i).Transformed(trsf)
            p[i - 1] = (pt.X(), pt.Y(), pt.Z())
            v = tri.Normal(i)
            q[i - 1] = (v.X(), v.Y(), v.Z())
        # rotate normals by the location (no scaling in STEP locations)
        if not loc.IsIdentity():
            m = np.array([[trsf.Value(r, c) for c in (1, 2, 3)] for r in (1, 2, 3)])
            q = q @ m.T
        rev = face.Orientation() == TopAbs_REVERSED
        if rev:
            q = -q
        t = np.empty((tri.NbTriangles(), 3), dtype=np.int64)
        for i in range(1, tri.NbTriangles() + 1):
            a, b, c = tri.Triangle(i).Get()
            t[i - 1] = (a, c, b) if rev else (a, b, c)
        pos.append(p)
        nor.append(q)
        idx.append(t - 1 + base)
        base += n
    if not pos:
        return None
    P = np.vstack(pos).astype(np.float32)
    N = np.vstack(nor)
    N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)
    return P, N.astype(np.float32), np.vstack(idx).astype(np.uint32)


class GlbWriter:
    def __init__(self):
        self.bin = bytearray()
        self.gltf = {"asset": {"version": "2.0", "generator": "PrintTwin tools/cad"}, "scene": 0,
                     "scenes": [{"nodes": []}], "nodes": [], "meshes": [], "materials": [],
                     "accessors": [], "bufferViews": [], "buffers": []}
        self._mat = {}

    def material(self, name: str, color, metallic=0.0, roughness=0.6, emissive=None, double=False) -> int:
        if name in self._mat:
            return self._mat[name]
        r, g, b = color
        m = {"name": name, "pbrMetallicRoughness": {"baseColorFactor": [r, g, b, 1.0],
                                                     "metallicFactor": metallic, "roughnessFactor": roughness}}
        if emissive:
            m["emissiveFactor"] = list(emissive)
        if double:
            m["doubleSided"] = True
        self.gltf["materials"].append(m)
        self._mat[name] = len(self.gltf["materials"]) - 1
        return self._mat[name]

    def _view(self, data: bytes, target: int | None) -> int:
        while len(self.bin) % 4:
            self.bin.append(0)
        v = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            v["target"] = target
        self.bin += data
        self.gltf["bufferViews"].append(v)
        return len(self.gltf["bufferViews"]) - 1

    def _acc(self, arr: np.ndarray, kind: str, ctype: int, target: int, minmax=False) -> int:
        a = {"bufferView": self._view(arr.tobytes(), target), "componentType": ctype,
             "count": int(arr.shape[0] if arr.ndim > 1 else arr.size), "type": kind}
        if minmax:
            a["min"] = arr.min(axis=0).tolist()
            a["max"] = arr.max(axis=0).tolist()
        self.gltf["accessors"].append(a)
        return len(self.gltf["accessors"]) - 1

    def mesh(self, name: str, P, N, I, material: int) -> int:
        prim = {"attributes": {"POSITION": self._acc(P, "VEC3", 5126, 34962, True),
                               "NORMAL": self._acc(N, "VEC3", 5126, 34962)},
                "indices": self._acc(I.reshape(-1), "SCALAR", 5125, 34963), "material": material}
        self.gltf["meshes"].append({"name": name, "primitives": [prim]})
        return len(self.gltf["meshes"]) - 1

    def node(self, name: str, mesh: int | None = None, children=None, translation=None, parent: int | None = None,
             extras: dict | None = None) -> int:
        n = {"name": name}
        if mesh is not None:
            n["mesh"] = mesh
        if children:
            n["children"] = list(children)
        if translation is not None:
            n["translation"] = [float(v) for v in translation]
        if extras:
            n["extras"] = extras
        self.gltf["nodes"].append(n)
        i = len(self.gltf["nodes"]) - 1
        if parent is None:
            self.gltf["scenes"][0]["nodes"].append(i)
        else:
            self.gltf["nodes"][parent].setdefault("children", []).append(i)
        return i

    def write(self, path, extras: dict | None = None):
        while len(self.bin) % 4:
            self.bin.append(0)
        self.gltf["buffers"] = [{"byteLength": len(self.bin)}]
        if extras:
            self.gltf["extras"] = extras
        js = json.dumps(self.gltf, separators=(",", ":")).encode()
        js += b" " * (-len(js) % 4)
        total = 12 + 8 + len(js) + 8 + len(self.bin)
        with open(path, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, total))
            f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
            f.write(struct.pack("<II", len(self.bin), 0x004E4942) + bytes(self.bin))
        return total
