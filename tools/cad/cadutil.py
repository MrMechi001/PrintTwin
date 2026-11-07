"""Small OCCT helpers for the MK4 model build (run with the CadQuery/OCP Python)."""
from __future__ import annotations

import itertools

import numpy as np
from OCP.Bnd import Bnd_Box
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS


def read_step(path):
    r = STEPControl_Reader()
    if r.ReadFile(str(path)) != IFSelect_RetDone:
        raise IOError(f"cannot read {path}")
    r.TransferRoots()
    return r.OneShape()


def bbox(shape):
    b = Bnd_Box()
    BRepBndLib.Add_s(shape, b, True)
    return np.array(b.Get()).reshape(2, 3)          # [[x0,y0,z0],[x1,y1,z1]]


def props(shape):
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    c = g.CentreOfMass()
    return g.Mass(), np.array([c.X(), c.Y(), c.Z()])


def cylinders(shape, rmin=0.0, rmax=1e9):
    """Unique cylindrical faces: (radius, point on axis, unit axis)."""
    out = {}
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        a = BRepAdaptor_Surface(TopoDS.Face_s(ex.Current()))
        ex.Next()
        if a.GetType() != GeomAbs_Cylinder:
            continue
        c = a.Cylinder()
        if not rmin <= c.Radius() <= rmax:
            continue
        p, d = c.Axis().Location(), c.Axis().Direction()
        d = np.array([d.X(), d.Y(), d.Z()])
        d = d * (1 if d[np.argmax(np.abs(d))] > 0 else -1)
        p = np.array([p.X(), p.Y(), p.Z()])
        p = p - d * np.dot(p, d)                       # canonical point: foot from origin
        key = (round(c.Radius(), 2), *np.round(p, 1), *np.round(d, 3))
        out[key] = (c.Radius(), p, d)
    return list(out.values())


def nema_mounts(shape, pilot=(10.5, 11.6), bolt=(1.4, 1.8), pitch=31.0, tol=0.6):
    """NEMA17-style mounts: a ~22 mm pilot hole with 4 M3 holes on a 31 mm square around it."""
    cyl = cylinders(shape)
    res = []
    for r, p, d in cyl:
        if not pilot[0] <= r <= pilot[1]:
            continue
        holes = [q for rr, q, dd in cyl if bolt[0] <= rr <= bolt[1] and abs(abs(np.dot(dd, d)) - 1) < 1e-3]
        n = 0
        for q in holes:
            off = q - p
            off = off - d * np.dot(off, d)
            if abs(np.linalg.norm(off) - pitch / np.sqrt(2)) < tol:
                n += 1
        if n >= 4:
            res.append((r, p, d, n))
    return res


ROTS = []
for perm in itertools.permutations(range(3)):
    for signs in itertools.product((1, -1), repeat=3):
        m = np.zeros((3, 3))
        for i, (j, s) in enumerate(zip(perm, signs)):
            m[i, j] = s
        if round(np.linalg.det(m)) == 1:
            ROTS.append(m)


def placement(src_shape, dst_shape, tol=0.3):
    """Axis-aligned rigid transform (R, t) mapping a part's own frame onto its placed copy:
    bounding-box extents and centre of mass must agree. Returns (R, t, error) or None."""
    sb, db = bbox(src_shape), bbox(dst_shape)
    _, sc = props(src_shape)
    _, dc = props(dst_shape)
    best = None
    for R in ROTS:
        corners = np.array([[x, y, z] for x in sb[:, 0] for y in sb[:, 1] for z in sb[:, 2]]) @ R.T
        lo = corners.min(axis=0)
        ext = corners.max(axis=0) - lo
        if np.abs(ext - (db[1] - db[0])).max() > 1.0:
            continue
        t = db[0] - lo
        err = np.linalg.norm(R @ sc + t - dc)
        if best is None or err < best[2]:
            best = (R, t, err)
    return best if best and best[2] < tol else None
