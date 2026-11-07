// A printer built from real CAD (models/*.glb, see tools/cad/), animated by the live position.
//
// GLB layout (written in PrintTwin's G-code frame: mm, Z up), extras.printtwin.kinematics:
//   bedslinger (MK4, MK3.9, MINI): static | bed (G-code bed coords, slides in Y) |
//              xaxis (moves in Z) > carriage (moves in X); the nozzle moves in the plane y = nozzle_y
//   corexy     (CORE One, XL):     static | bed (moves in Z) | gantry (moves in Y) > carriage (X);
//              the nozzle tip stays at height z = nozzle_z
// Optional anchors in extras.printtwin: lcd_screen, spool, filament_inlet_carriage,
// cable_start_carriage, cable_end_static.
//
// Printer interface (also printer3d.js / printer_corexy.js): { root, bedParts, setPose(x, y, z),
// bedOffset(x, y, z, out), setFans, setFilament, setHeat, setLcd, tick, nozzleWorld, bounds, floorZ }
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { makeCable, makeLcd, makeSpool, makeStrand, spoolTurnsFor } from './printer_common.js';

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

  const lcd = meta.lcd_screen ? makeLcd(meta.lcd_screen) : null;
  if (lcd) root.add(lcd.mesh);

  const filMat = new THREE.MeshStandardMaterial({ color: 0xff8000, roughness: 0.45 });
  const sp = meta.spool;
  const spool = sp ? makeSpool(sp, filMat) : null;
  if (spool) { spool.position.set(...sp.center); root.add(spool); }
  const cable = meta.cable_start_carriage ? makeCable() : null;
  const strand = meta.filament_inlet_carriage && sp ? makeStrand(filMat) : null;
  if (cable) root.add(cable.mesh);
  if (strand) root.add(strand.mesh);
  const cableStart = cable && new THREE.Vector3(...meta.cable_start_carriage);
  const cableEnd = cable && new THREE.Vector3(...meta.cable_end_static);
  const inlet = strand && new THREE.Vector3(...meta.filament_inlet_carriage);

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
    if (strand) {
      filTo.copy(inlet).add(shift);
      const wr = spool.userData.windingRadius;
      filFrom.set(Math.min(Math.max(filTo.x, sp.center[0] - sp.width / 2 + 6), sp.center[0] + sp.width / 2 - 6),
        sp.center[1] - wr * 0.7, sp.center[2] - wr * 0.7);
      strand.set(filFrom, filTo);
    }
  }
  setPose(pose.x, pose.y, pose.z);

  const [lo, hi] = meta.bounds;
  return {
    root, bedParts, setPose, kinematics: corexy ? 'corexy' : 'bedslinger',
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
