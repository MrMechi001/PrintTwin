// Procedural generic CoreXY printer (enclosed box frame): the gantry moves in Y, the print head
// along the gantry in X, and in Z either the bed moves down (zMotion 'bed': CORE One, XL,
// Voron Trident / V0, Creality K1, ...) or the whole XY gantry rides up (zMotion 'gantry': a
// flying gantry, Voron 2.4, Sovol SV08). Fallback for CoreXY printers without a CAD model;
// sized from the print volume. Interface: see printer_glb.js.
//
// World = G-code frame (mm, Z up). 'bed': the nozzle tip stays at height NOZZLE_Z and the bed
// group (G-code bed coordinates) sits at z = NOZZLE_Z - Z. 'gantry': the bed stays at BED_Z and
// the gantry (built for Z = 0) is lifted by Z.
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { makeCable, makeLcd, makeSpool, makeStrand, spoolDirection, spoolTurnsFor } from './printer_common.js';

const mat = (color, roughness, metalness = 0, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness, metalness, ...extra });

function box(m, x0, x1, y0, y1, z0, z1, r = 0) {
  const w = x1 - x0, d = y1 - y0, h = z1 - z0;
  const rr = Math.min(r, w / 2 - 0.01, d / 2 - 0.01, h / 2 - 0.01);
  const g = rr > 0.05 ? new RoundedBoxGeometry(w, d, h, 2, rr) : new THREE.BoxGeometry(w, d, h);
  const mesh = new THREE.Mesh(g, m);
  mesh.position.set((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2);
  return mesh;
}

function cyl(m, axis, a0, a1, u, v, r, seg = 20) {
  const g = new THREE.CylinderGeometry(r, r, Math.abs(a1 - a0), seg);
  const c = (a0 + a1) / 2;
  if (axis === 'z') g.rotateX(Math.PI / 2);
  else if (axis === 'x') g.rotateZ(-Math.PI / 2);
  const mesh = new THREE.Mesh(g, m);
  if (axis === 'z') mesh.position.set(u, v, c);
  else if (axis === 'x') mesh.position.set(c, u, v);
  else mesh.position.set(u, c, v);
  return mesh;
}

const add = (p, ...o) => { o.forEach((x) => p.add(x)); return p; };

export function buildPrinter({ bed = [250, 220], height = 270, accent = 0xf0561f, zMotion = 'bed' } = {}) {
  const [W, D] = bed, H = height;
  const flying = zMotion === 'gantry';
  const M = {
    frame: mat(0x1d1f23, 0.6, 0.55), panel: mat(0x2a3038, 0.2, 0.1, { transparent: true, opacity: 0.16, depthWrite: false, side: THREE.DoubleSide }),
    black: mat(0x17181b, 0.55, 0.1), accent: mat(accent, 0.45), rail: mat(0xb8bcc2, 0.25, 0.9),
    rod: mat(0x9a9fa6, 0.35, 0.9), motor: mat(0x0e0f11, 0.5, 0.3), sheet: mat(0x2b2c30, 0.6, 0.5),
    pcb: mat(0x0c0d0f, 0.55, 0.2), alu: mat(0x6d7279, 0.35, 0.85), brass: mat(0xd8a446, 0.28, 0.9),
    sock: mat(0x1a1a1c, 0.8, 0, { emissive: new THREE.Color(0xff3c00), emissiveIntensity: 0 }), belt: mat(0x111111, 0.9),
  };
  const BED_Z = 130;                                 // flying gantry: fixed bed, electronics below it
  // bed mode: bed top at its lowest (Z = H) stays 80 mm above the base; gantry mode: the nozzle
  // geometry is built for Z = 0 and lifted with Z
  const NOZZLE_Z = flying ? BED_Z : H + 80;
  const X0 = -75, X1 = W + 75, Y0 = -70, Y1 = D + 95, Z0 = 0, Z1 = (flying ? BED_Z + H : NOZZLE_Z) + 95;
  const root = new THREE.Group(), bedParts = new THREE.Group();

  // ---------------------------------------------------------------- frame + panels (front open)
  const e = 20;
  for (const [x, y] of [[X0, Y0], [X1 - e, Y0], [X0, Y1 - e], [X1 - e, Y1 - e]]) add(root, box(M.frame, x, x + e, y, y + e, Z0, Z1));
  for (const z of [Z0, Z1 - e, NOZZLE_Z - 40]) {
    add(root, box(M.frame, X0, X1, Y0, Y0 + e, z, z + e), box(M.frame, X0, X1, Y1 - e, Y1, z, z + e),
      box(M.frame, X0, X0 + e, Y0, Y1, z, z + e), box(M.frame, X1 - e, X1, Y0, Y1, z, z + e));
  }
  add(root, box(M.panel, X0 - 2, X0, Y0, Y1, Z0, Z1), box(M.panel, X1, X1 + 2, Y0, Y1, Z0, Z1),
    box(M.panel, X0, X1, Y1, Y1 + 2, Z0, Z1), box(M.panel, X0, X1, Y0, Y1, Z1, Z1 + 2));
  add(root, box(M.black, X0 - 4, X1 + 4, Y0 - 4, Y1 + 4, Z0 - 14, Z0, 4));     // base
  for (const [x, y] of [[X0 + 20, Y0 + 20], [X1 - 20, Y0 + 20], [X0 + 20, Y1 - 20], [X1 - 20, Y1 - 20]]) add(root, cyl(M.black, 'z', Z0 - 22, Z0 - 14, x, y, 12));

  // ---------------------------------------------------------------- Z: 4 drives + motors; the bed (or the gantry) hangs on them
  const ZP = [[-35, -20], [W + 35, -20], [-35, D + 35], [W + 35, D + 35]];
  for (const [x, y] of ZP) {
    add(root, box(M.motor, x - 21, x + 21, y - 21, y + 21, Z0 + 5, Z0 + 45, 3));
    if (flying) add(root, box(M.rail, x - 5, x + 5, y - 5, y + 5, BED_Z - 20, Z1 - e));        // Z rails up the corners
    else add(root, cyl(M.rod, 'z', Z0 + 45, NOZZLE_Z - 40, x, y, 4, 14));                       // lead screws
  }

  // bed (G-code bed coordinates, sheet surface z = 0)
  const sheetShape = new THREE.Shape();
  {
    const [x0, x1, y0, y1, r] = [-3, W + 3, -6, D + 6, 6];
    sheetShape.moveTo(x0 + r, y0); sheetShape.lineTo(x1 - r, y0); sheetShape.quadraticCurveTo(x1, y0, x1, y0 + r);
    sheetShape.lineTo(x1, y1 - r); sheetShape.quadraticCurveTo(x1, y1, x1 - r, y1); sheetShape.lineTo(x0 + r, y1);
    sheetShape.quadraticCurveTo(x0, y1, x0, y1 - r); sheetShape.lineTo(x0, y0 + r); sheetShape.quadraticCurveTo(x0, y0, x0 + r, y0);
  }
  const sheet = new THREE.Mesh(new THREE.ExtrudeGeometry(sheetShape, { depth: 0.8, bevelEnabled: false }), M.sheet);
  sheet.position.z = -0.8;
  add(bedParts, sheet, box(M.pcb, -6, W + 6, -10, D + 12, -4.4, -0.8, 2), box(M.alu, -10, W + 10, -14, D + 16, -16, -4.4, 3));
  if (flying) {                                       // fixed bed on two cross beams to the frame
    for (const y of [D * 0.2, D * 0.8]) add(bedParts, box(M.frame, X0 + e, X1 - e, y - 10, y + 10, -36, -16));
  } else {
    for (const [x, y] of ZP) {                        // arms from the bed frame to the lead-screw nuts
      const xi = Math.min(Math.max(x, 0), W), yi = Math.min(Math.max(y, 0), D);
      add(bedParts, box(M.alu, Math.min(x, xi) - 9, Math.max(x, xi) + 9, Math.min(y, yi) - 9, Math.max(y, yi) + 9, -24, -16, 2),
        cyl(M.black, 'z', -30, -14, x, y, 9));
    }
  }

  // ---------------------------------------------------------------- CoreXY: Y rails (static), gantry (Y), head (X)
  const ZR = NOZZLE_Z + 52;                          // height of the rails / gantry beam
  const flyer = new THREE.Group();                   // XY motion system: static, or lifted with Z (flying gantry)
  root.add(flyer);
  add(flyer, box(M.rail, -48, -36, Y0 + e, Y1 - e, ZR - 6, ZR + 2), box(M.rail, W + 36, W + 48, Y0 + e, Y1 - e, ZR - 6, ZR + 2));
  for (const [x, y] of [[X0 + 26, Y1 - 30], [X1 - 26, Y1 - 30]]) add(flyer, box(M.motor, x - 21, x + 21, y - 21, y + 21, ZR + 4, ZR + 44, 3));
  for (const x of [-30, W + 30]) add(flyer, box(M.belt, x - 3, x + 3, Y0 + e, Y1 - 30, ZR + 8, ZR + 9.5));
  if (flying) {                                      // gantry frame: front and back beams, carriages on the Z rails
    add(flyer, box(M.frame, X0 + e, X1 - e, Y0 + e, Y0 + 2 * e, ZR - 10, ZR + 10), box(M.frame, X0 + e, X1 - e, Y1 - 2 * e, Y1 - e, ZR - 10, ZR + 10));
    for (const [x, y] of ZP) add(flyer, box(M.black, x - 14, x + 14, y - 14, y + 14, ZR - 18, ZR + 18, 3));
  }

  const gantry = new THREE.Group();                  // local y relative to the nozzle
  add(gantry, box(M.frame, -50, W + 50, 14, 34, ZR - 8, ZR + 12, 2), box(M.black, -54, -30, 6, 42, ZR - 12, ZR + 16, 4),
    box(M.black, W + 30, W + 54, 6, 42, ZR - 12, ZR + 16, 4), box(M.belt, -45, W + 45, 10, 12, ZR + 2, ZR + 8));
  const carriage = new THREE.Group();                // local x relative to the nozzle
  const tip = new THREE.ConeGeometry(2.4, 5, 24);
  tip.translate(0, -2.5, 0); tip.rotateX(-Math.PI / 2);
  const nozzle = new THREE.Mesh(tip, M.brass);
  nozzle.position.z = NOZZLE_Z;
  add(carriage, nozzle, cyl(M.brass, 'z', NOZZLE_Z + 5, NOZZLE_Z + 8, 0, 0, 4, 6),
    box(M.sock, -11, 9, -9, 9, NOZZLE_Z + 8, NOZZLE_Z + 19, 3),
    box(M.black, -26, 26, -30, 20, NOZZLE_Z + 21, NOZZLE_Z + 74, 6),           // print head body
    box(M.accent, -20, 20, -33, -29, NOZZLE_Z + 40, NOZZLE_Z + 70, 3),         // front cover
    cyl(M.black, 'y', -34, -31, 0, NOZZLE_Z + 54, 15, 32),                      // hotend fan
    box(M.black, -30, -18, -24, 0, NOZZLE_Z + 10, NOZZLE_Z + 30, 3),          // part-cooling duct
    box(M.black, -24, 24, 20, 34, ZR - 14, ZR + 14, 3));                        // rides on the beam
  gantry.add(carriage);
  flyer.add(gantry);

  // ---------------------------------------------------------------- LCD, spool, filament, cable
  const lcdZ = Z0 + 26, ly = Y0 - 6;
  add(root, box(M.black, W / 2 - 70, W / 2 + 70, ly - 8, ly + 6, lcdZ - 8, lcdZ + 62, 5));
  const lcd = makeLcd([[W / 2 - 58, ly - 8.5, lcdZ], [W / 2 + 46, ly - 8.5, lcdZ], [W / 2 + 46, ly - 8.5, lcdZ + 52], [W / 2 - 58, ly - 8.5, lcdZ + 52]]);
  root.add(lcd.mesh);
  const filMat = mat(0xff8000, 0.45);
  const holder = new THREE.Group();
  const holderZ = flying ? BED_Z + H * 0.55 : NOZZLE_Z - 60;
  holder.position.set(X1 + 104, (Y0 + Y1) / 2, holderZ);          // flanges clear of the side panel
  holder.rotation.z = Math.PI / 2;                   // spool axis along Y, outside the right panel
  const spool = makeSpool({ radius: 100, width: 68, hub_radius: 27 }, filMat);
  holder.add(spool);
  add(root, holder, box(M.black, X1 + 2, X1 + 80, (Y0 + Y1) / 2 - 12, (Y0 + Y1) / 2 + 12, holderZ - 10, holderZ + 10, 3));
  const strand = makeStrand(filMat), cable = makeCable(4.5);
  root.add(strand.mesh, cable.mesh);
  const inlet = new THREE.Vector3(0, -2, NOZZLE_Z + 74), cableStart = new THREE.Vector3(10, 12, NOZZLE_Z + 74);
  const spoolOut = new THREE.Vector3(X1 + 30, (Y0 + Y1) / 2, holderZ + 70);
  const cableEnd = new THREE.Vector3(W / 2, Y1 - 5, Z1 - 30);

  const pose = { x: W / 2, y: D / 2, z: 10 };
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c1 = new THREE.Vector3(), c2 = new THREE.Vector3();
  let spoolTurns = 0, spoolDir = 0;
  const strandTo = new THREE.Vector3();
  function setPose(x, y, z) {
    pose.x = x; pose.y = y; pose.z = z;
    const lift = flying ? z : 0;
    flyer.position.z = lift;
    gantry.position.y = y;
    carriage.position.x = x;
    a.copy(inlet).add(b.set(x, y, lift));
    strand.set(spoolOut, a);
    strandTo.copy(a);
    a.copy(cableStart).add(b.set(x, y, lift));
    c1.set(a.x, a.y + 40, a.z + 60); c2.set(cableEnd.x, cableEnd.y - 80, cableEnd.z + 40);
    cable.set(a, c1, c2, cableEnd);
  }
  setPose(pose.x, pose.y, pose.z);

  return {
    root, bedParts, setPose, kinematics: 'corexy',
    bedOffset: (x, y, z, out) => out.set(0, 0, flying ? BED_Z : NOZZLE_Z - z),
    setFans: () => {},
    setFilament(color, pathMm) { if (color) filMat.color.set(color); spoolTurns = spoolTurnsFor(pathMm, spool.userData.windingRadius); },
    setHeat(cC) { M.sock.emissiveIntensity = Math.max(0, Math.min(1, ((cC || 0) - 60) / 180)) * 0.9; },
    setLcd: (s) => lcd.update(s),
    tick() {
      spoolDir ||= spoolDirection(spool, spoolOut, strandTo, root);   // unwinds towards the print head
      spool.rotation.x = spoolDir * spoolTurns * Math.PI * 2;
    },
    nozzleWorld: (out) => out.set(pose.x, pose.y, NOZZLE_Z + (flying ? pose.z : 0)),
    bounds: { min: [X0 - 10, Y0 - 20, Z0 - 22], max: [X1 + 215, Y1 + 10, Z1 + 5] },
    floorZ: Z0 - 23,
  };
}
