// A printer built from real CAD (models/*.glb, see tools/cad/), animated by the live position.
//
// GLB layout (written in PrintTwin's G-code frame: mm, Z up), extras.printtwin.kinematics:
//   bedslinger (MK4, MK3.9, MINI): static | bed (G-code bed coords, slides in Y) |
//              xaxis (moves in Z) > carriage (moves in X); the nozzle moves in the plane y = nozzle_y
//   corexy     (CORE One, XL):     static | bed (moves in Z) | gantry (moves in Y) > carriage (X);
//              the nozzle tip stays at height z = nozzle_z
// Optional anchors in extras.printtwin: lcd_screen (+ lcd_portrait), spool, filament_inlet_carriage
// or filament_inlet_xaxis, cable_start_carriage + cable_end_static, bowden (start_xaxis, dir_start,
// end_carriage, dir_end, length, radius).
//
// Printer interface (also printer3d.js / printer_corexy.js): { root, bedParts, setPose(x, y, z),
// bedOffset(x, y, z, out), setFans, setFilament, setHeat, setLcd, tick, nozzleWorld, bounds, floorZ }
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { makeCable, makeLcd, makeSpool, makeStrand, placeCyl, spoolTurnsFor } from './printer_common.js';

export async function buildPrinter(url, { recolor = {} } = {}) {
  const gltf = await new GLTFLoader().loadAsync(url);
  const meta = gltf.parser.json.extras?.printtwin;
  if (!meta) throw new Error(`${url} has no PrintTwin metadata`);
  const scene = gltf.scene;
  const node = (n) => { const o = scene.getObjectByName(n); if (!o) throw new Error(`${url}: no node ${n}`); return o; };
  const corexy = meta.kinematics === 'corexy';
  const staticG = node('static'), bedParts = node('bed'), carriage = node('carriage');
  const axis = node(corexy ? 'gantry' : 'xaxis');           // the group that carries the carriage
  const NOZZLE_Y = meta.nozzle_y ?? 105, NOZZLE_Z = meta.nozzle_z ?? 0;

  const root = new THREE.Group();
  root.add(staticG, axis);
  bedParts.removeFromParent();
  bedParts.position.set(0, 0, 0);
  // the groups now live under root / bedParts (no longer under gltf.scene): search those
  const find = (n) => root.getObjectByName(n) || bedParts.getObjectByName(n);

  const mats = {};
  for (const g of [root, bedParts]) {
    g.traverse((o) => {
      if (o.isMesh) { o.castShadow = o.receiveShadow = false; mats[o.material.name] = o.material; }
    });
  }
  // variants share one GLB: e.g. MK3.9S = MK4S with orange Z parts
  for (const [name, matName] of Object.entries(recolor)) {
    const o = find(name);
    if (o?.isMesh && mats[matName]) o.material = mats[matName];
  }
  const sock = mats.sock;                                    // silicone sock glows with the hotend temperature
  if (sock) { sock.emissive = new THREE.Color(0xff3c00); sock.emissiveIntensity = 0; }

  // perforated PSU cover (outer side), if the model has a PSU body
  const psu = find('psu');
  if (psu) {
    const b = new THREE.Box3().setFromObject(psu);
    const c = document.createElement('canvas');
    c.width = 256; c.height = 512;
    const g = c.getContext('2d');
    g.fillStyle = '#16171a'; g.fillRect(0, 0, 256, 512);
    g.fillStyle = '#050506';
    for (let y = 10; y < 512; y += 10) for (let x = (y / 10) % 2 ? 5 : 10; x < 256; x += 10) {
      g.beginPath(); g.arc(x, y, 3.1, 0, Math.PI * 2); g.fill();
    }
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.anisotropy = 4;
    const plane = new THREE.Mesh(new THREE.PlaneGeometry(b.max.y - b.min.y - 6, b.max.z - b.min.z - 6),
      new THREE.MeshStandardMaterial({ map: tex, metalness: 0.55, roughness: 0.55 }));
    plane.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(       // face +X
      new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1), new THREE.Vector3(1, 0, 0)));
    plane.position.set(b.max.x + 0.15, (b.min.y + b.max.y) / 2, (b.min.z + b.max.z) / 2);
    root.add(plane);
  }

  const lcd = meta.lcd_screen ? makeLcd(meta.lcd_screen, { portrait: !!meta.lcd_portrait }) : null;
  if (lcd) root.add(lcd.mesh);

  const filMat = new THREE.MeshStandardMaterial({ color: 0xff8000, roughness: 0.45 });
  const sp = meta.spool;
  const spool = sp ? makeSpool(sp, filMat) : null;
  if (spool) { spool.position.set(...sp.center); root.add(spool); }
  const cable = meta.cable_start_carriage ? makeCable() : null;
  // filament inlet: on the print head (Nextruder) or on the Z carriage (MINI: extruder moves with Z only)
  const inletCarriage = !!meta.filament_inlet_carriage;
  const inletAt = meta.filament_inlet_carriage || meta.filament_inlet_xaxis;
  const strand = inletAt && sp && !meta.filament_path_static ? makeStrand(filMat) : null;
  if (cable) root.add(cable.mesh);
  if (strand) root.add(strand.mesh);
  const cableStart = cable && new THREE.Vector3(...meta.cable_start_carriage);
  const cableEnd = cable && new THREE.Vector3(...meta.cable_end_static);
  const inlet = strand && new THREE.Vector3(...inletAt);
  // PTFE bowden (MINI: from the extruder on the Z carriage) or cable bundle (XL: from the frame's rear top)
  // to the top of the print head
  const bw = meta.bowden;
  const bowden = bw ? makeCable(bw.radius ?? 2, 40, bw.color ? new THREE.Color(bw.color) : 0xe9eae4) : null;
  if (bowden) { bowden.mesh.material.roughness = bw.color ? 0.9 : 0.35; root.add(bowden.mesh); }
  const bwFixed = !!bw?.start_static;                        // start does not move with Z
  const bwStart = bw && new THREE.Vector3(...(bw.start_static || bw.start_xaxis)), bwEnd = bw && new THREE.Vector3(...bw.end_carriage);
  // fixed paths drawn once: filament from the spool to the entry sensor, PTFE from there to the bundle.
  // Smooth tubes (centripetal Catmull-Rom through the points) - a PTFE tube has no sharp bends.
  const polyline = (pts, r, mat) => {
    const v = pts.map((p) => new THREE.Vector3(...p));
    if (v.length < 3) {
      const m = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 1, 10), mat);
      placeCyl(m, v[0], v[1], r);
      return m;
    }
    const curve = new THREE.CatmullRomCurve3(v, false, 'centripetal');
    return new THREE.Mesh(new THREE.TubeGeometry(curve, Math.max(32, v.length * 12), r, 10, false), mat);
  };
  // points along a cubic Bezier that leaves `a` along `da` and enters `b` along `db` (gentle bends)
  const bezierPts = (a, da, b, db, k, n = 14) => {
    const A = new THREE.Vector3(...a), B = new THREE.Vector3(...b);
    const c = new THREE.CubicBezierCurve3(A, A.clone().addScaledVector(new THREE.Vector3(...da).normalize(), k),
      B.clone().addScaledVector(new THREE.Vector3(...db).normalize(), -k), B);
    return c.getPoints(n).map((p) => p.toArray());
  };
  const ptfeMat = new THREE.MeshStandardMaterial({ color: 0xe9eae4, roughness: 0.35 });
  const fixedPaths = new THREE.Group();
  root.add(fixedPaths);
  if (meta.filament_path_static) fixedPaths.add(polyline(meta.filament_path_static, 0.875, filMat));
  if (meta.ptfe_static) fixedPaths.add(polyline(meta.ptfe_static, 2, ptfeMat));

  // ---------------------------------------------------------------- tool changer (XL): docks, parked tools,
  // one spool / filament path / cable bundle per tool; the job's T<n> changes pick the active tool
  const mt = meta.tools;
  const toolParts = mt ? mt.tool_nodes.map((n) => find(n)).filter(Boolean) : [];
  // nodes only a multi-tool XL has (docks, left sensor block); the spool holder that is cloned for every spool
  const multiNodes = (mt?.multi_nodes || (mt?.dock_node ? [mt.dock_node] : [])).map((n) => find(n)).filter(Boolean);
  multiNodes.forEach((o) => { o.visible = false; });
  const holderNodes = (mt?.holder_nodes || []).map((n) => find(n)).filter(Boolean);
  const multi = { n: 1, active: 0, cols: '', parked: [], bundles: [], mats: [], group: new THREE.Group() };
  root.add(multi.group);
  const dockV = new THREE.Vector3();

  // cols: filament colour per tool; used: tools the job uses; count: tools the printer has (XL5IS -> 5)
  function setTools(cols, used, count = 1) {
    cols = cols?.length ? cols : ['#ff8000'];
    filMat.color.set(cols[0]);
    if (!mt) return;
    const need = Math.max(count || 1, ...(used?.length ? used : [0]).map((t) => t + 1));
    const n = Math.min(mt.docks.length, need);
    if (n === multi.n && multi.cols === String(cols)) return;
    multi.group.clear();
    Object.assign(multi, { n, cols: String(cols), parked: [], bundles: [], mats: [] });
    const one = n === 1;
    if (spool) spool.visible = one;
    fixedPaths.visible = one;
    if (bowden) bowden.mesh.visible = one;
    multiNodes.forEach((o) => { o.visible = !one; });
    holderNodes.forEach((o) => { o.visible = one; });
    if (!one) {
      const wr = (sp?.radius ?? 100) - 22;
      for (let i = 0; i < n; i++) {
        const m = new THREE.MeshStandardMaterial({ color: cols[Math.min(i, cols.length - 1)], roughness: 0.45 });
        multi.mats.push(m);
        const c = mt.spools[i], e = mt.entries[i], st = mt.starts[i];
        const s = makeSpool({ ...sp, center: c }, m);
        s.position.set(...c);
        multi.group.add(s);
        if (holderNodes.length && mt.holder_center) {      // telescopic holder for this spool (mirrored on the left)
          const hc = mt.holder_center, hg = new THREE.Group();
          for (const o of holderNodes) { const k = o.clone(); k.visible = true; hg.add(k); }
          const left = c[0] < (mt.mirror_x ?? 0) / 2;
          if (left) { hg.scale.x = -1; hg.position.x = mt.mirror_x; }
          hg.position.y = c[1] - hc[1];
          hg.position.z = c[2] - hc[2];
          multi.group.add(hg);
        }
        multi.group.add(polyline([[c[0], c[1] + wr * 0.75, c[2] + wr * 0.66], e], 0.875, m));
        // PTFE: backwards out of the entry sensor, a wide loop, up into the tool's cable bundle
        multi.group.add(polyline(bezierPts(e, [0, 1, 0.25], st, bw?.dir_start || [0, 0, 1], 110), 2, ptfeMat));
        const g = new THREE.Group();                       // the tool parked in its dock (cold: no sock glow)
        for (const o of toolParts) {
          const c2 = o.clone();
          if (c2.material === sock) c2.material = sock.clone();
          if (c2.material !== sock && c2.material?.emissive) c2.material.emissiveIntensity = 0;
          g.add(c2);
        }
        g.position.set(mt.docks[i][0], mt.docks[i][1], 0);
        multi.group.add(g);
        multi.parked.push(g);
        const b = makeCable(6, 40, 0x111214);
        b.mesh.material.roughness = 0.9;
        multi.group.add(b.mesh);
        multi.bundles.push(b);
      }
    }
    setActiveTool(multi.active, true);
  }

  function setActiveTool(t, force) {
    if (!mt || (t === multi.active && !force)) return;
    multi.active = t;
    multi.parked.forEach((g, i) => { g.visible = i !== t; });
    if (multi.n > 1) filMat.color.copy(multi.mats[Math.min(t, multi.n - 1)].color);
    setPose(pose.x, pose.y, pose.z);
  }
  const bwDirS = bw && new THREE.Vector3(...bw.dir_start), bwDirE = bw && new THREE.Vector3(...bw.dir_end);
  const b0 = new THREE.Vector3(), b1 = new THREE.Vector3(), b2 = new THREE.Vector3(), b3 = new THREE.Vector3();

  // ---------------------------------------------------------------- pose
  const pose = { x: meta.pose?.x ?? 125, y: meta.pose?.y ?? 105, z: meta.pose?.z ?? 50 };
  const cs = new THREE.Vector3(), p1 = new THREE.Vector3(), p2 = new THREE.Vector3();
  const filFrom = new THREE.Vector3(), filTo = new THREE.Vector3(), shift = new THREE.Vector3();
  let spoolTurns = 0;

  function setPose(x, y, z) {
    pose.x = x; pose.y = y; pose.z = z;
    carriage.position.x = x;
    if (corexy) { axis.position.y = y; shift.set(x, y, 0); } else { axis.position.z = z; shift.set(x, 0, z); }
    if (cable) {
      cs.copy(cableStart).add(shift);
      // sagging loop: out to the side of the head, down, then into the electronics
      p1.set(cs.x - 70, cs.y - 25, cs.z - 40);
      p2.set(cableEnd.x + 40, cableEnd.y - 70, Math.min(cs.z, cableEnd.z) - 30);
      cable.set(cs, p1, p2, cableEnd);
    }
    if (bowden) {
      b0.copy(bwStart); if (!bwFixed && !corexy) b0.z += z;
      b3.copy(bwEnd).add(shift);
      // tangents out of both fittings; longer handles when the ends are close (tube length is fixed)
      const k = Math.max(30, ((bw.length ?? 275) - b0.distanceTo(b3)) * 0.55);
      b1.copy(bwDirS).multiplyScalar(k).add(b0);
      b2.copy(bwDirE).multiplyScalar(k).add(b3);
      bowden.set(b0, b1, b2, b3);
    }
    if (multi.n > 1) {                     // every tool's bundle: to the print head or to its dock
      for (let i = 0; i < multi.n; i++) {
        b0.set(...mt.starts[i]);
        b3.copy(bwEnd).add(i === multi.active ? shift : dockV.set(mt.docks[i][0], mt.docks[i][1], 0));
        const k = Math.max(30, ((bw?.length ?? 900) - b0.distanceTo(b3)) * 0.55);
        b1.copy(bwDirS).multiplyScalar(k).add(b0);
        b2.copy(bwDirE).multiplyScalar(k).add(b3);
        multi.bundles[i].set(b0, b1, b2, b3);
      }
    }
    if (strand) {
      filTo.copy(inlet);
      if (inletCarriage) filTo.add(shift); else filTo.z += corexy ? 0 : z;
      const wr = spool.userData.windingRadius;
      filFrom.set(Math.min(Math.max(filTo.x, sp.center[0] - sp.width / 2 + 6), sp.center[0] + sp.width / 2 - 6),
        sp.center[1] - wr * 0.7, sp.center[2] - wr * 0.7);
      strand.set(filFrom, filTo);
    }
  }
  setPose(pose.x, pose.y, pose.z);

  const [lo, hi] = meta.bounds;
  return {
    root, bedParts, setPose, setTools, setActiveTool, kinematics: corexy ? 'corexy' : 'bedslinger',
    bedOffset: (x, y, z, out) => (corexy ? out.set(0, 0, NOZZLE_Z - z) : out.set(0, NOZZLE_Y - y, 0)),
    setFans: () => {},
    setFilament(color, pathMm) {
      if (color) filMat.color.set(color);
      if (spool) spoolTurns = spoolTurnsFor(pathMm, spool.userData.windingRadius);
    },
    setHeat(c) { if (sock) sock.emissiveIntensity = Math.max(0, Math.min(1, ((c || 0) - 60) / 180)) * 0.9; },
    setLcd(s) { lcd?.update(s); },
    tick() { if (spool) spool.rotation.x = spoolTurns * Math.PI * 2; },
    nozzleWorld: (out) => (corexy ? out.set(pose.x, pose.y, NOZZLE_Z) : out.set(pose.x, NOZZLE_Y, pose.z)),
    bounds: { min: lo, max: [hi[0], hi[1], Math.max(hi[2], sp ? sp.center[2] + sp.radius : hi[2])] },
    floorZ: lo[2] - 1.5,
    meta,
  };
}
