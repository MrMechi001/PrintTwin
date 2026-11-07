// Procedural MK4-style bed-slinger, driven by the live nozzle position.
// World = G-code frame (mm, Z up). The nozzle stays in the plane y = NOZZLE_Y;
// the bed (and the print on it) slides in Y underneath it, the X axis rides in Z,
// the extruder carriage moves along X.
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';

export const NOZZLE_Y = 105;

const mat = (color, roughness, metalness = 0, extra = {}) =>
  new THREE.MeshStandardMaterial({ color, roughness, metalness, ...extra });

function box(m, x0, x1, y0, y1, z0, z1, r = 0) {
  const w = x1 - x0, d = y1 - y0, h = z1 - z0;
  const rr = Math.min(r, w / 2 - 0.01, d / 2 - 0.01, h / 2 - 0.01);
  const g = rr > 0.05 ? new RoundedBoxGeometry(w, d, h, 2, rr) : new THREE.BoxGeometry(w, d, h);
  const mesh = new THREE.Mesh(g, m);
  mesh.position.set((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2);
  return mesh;
}

// cylinder along an axis: a0..a1 on that axis, (u, v) = the remaining two coordinates in xyz order
function cyl(m, axis, a0, a1, u, v, r, seg = 24, rTop = r) {
  const g = new THREE.CylinderGeometry(rTop, r, Math.abs(a1 - a0), seg);
  const c = (a0 + a1) / 2;
  if (axis === 'z') g.rotateX(Math.PI / 2);
  else if (axis === 'x') g.rotateZ(-Math.PI / 2);
  const mesh = new THREE.Mesh(g, m);
  if (axis === 'z') mesh.position.set(u, v, c);
  else if (axis === 'x') mesh.position.set(c, u, v);
  else mesh.position.set(u, c, v);
  return mesh;
}

function add(parent, ...objs) { objs.forEach((o) => parent.add(o)); return parent; }

export function buildPrinter() {
  const M = {
    black: mat(0x1c1e22, 0.62, 0.1),
    orange: mat(0xee6a24, 0.55),
    alu: mat(0xa3a9b1, 0.36, 0.85),
    chrome: mat(0xe3e6ea, 0.12, 1.0),
    screw: mat(0x8e9298, 0.42, 0.9),
    motor: mat(0x25272b, 0.42, 0.65),
    brass: mat(0xd8a446, 0.3, 0.8),
    sheet: mat(0x2c2d31, 0.9, 0.2),
    pcb: mat(0x121316, 0.5, 0.2),
    belt: mat(0x111111, 0.9),
    rubber: mat(0x0b0b0c, 0.95),
    sock: mat(0x2a2a2c, 0.8, 0, { emissive: new THREE.Color(0xff3c00), emissiveIntensity: 0 }),
    ptfe: mat(0xf2f2f0, 0.4),
    cable: mat(0x1e2023, 0.95),
    filament: mat(0xff8000, 0.45),
  };

  const root = new THREE.Group();        // static frame + moving axes
  const bedParts = new THREE.Group();    // rides on the bed (caller adds it to its bed group)

  // ---------------------------------------------------------------- Y frame (static)
  add(root,
    box(M.alu, 28, 48, -150, 362, -100, -60, 1.5),
    box(M.alu, 202, 222, -150, 362, -100, -60, 1.5),
    box(M.black, 10, 240, -162, -150, -110, -38, 3),
    box(M.black, 10, 240, 362, 374, -110, -38, 3),
    cyl(M.chrome, 'y', -150, 362, 70, -50, 4),
    cyl(M.chrome, 'y', -150, 362, 180, -50, 4),
    box(M.belt, 122, 128, -148, 360, -47.5, -46),
    box(M.motor, 104, 146, 374, 414, -96, -54, 2),
  );
  for (const x of [70, 180]) for (const [y0, y1] of [[-150, -136], [348, 362]]) add(root, box(M.orange, x - 12, x + 12, y0, y1, -64, -36, 3));
  for (const [x, y] of [[38, -146], [212, -146], [38, 358], [212, 358]]) add(root, cyl(M.rubber, 'z', -112, -100, x, y, 9));

  // ---------------------------------------------------------------- Z frame (static)
  const FY0 = NOZZLE_Y + 50, FY1 = NOZZLE_Y + 80, ZY = NOZZLE_Y + 34;
  add(root,
    box(M.alu, -105, -75, FY0, FY1, -100, 330, 2),
    box(M.alu, 325, 355, FY0, FY1, -100, 330, 2),
    box(M.alu, -105, 355, FY0, FY1, 300, 330, 2),
    box(M.alu, -105, 355, FY0, FY1, -100, -76, 2),
    box(M.motor, -64, -22, ZY - 21, ZY + 21, -98, -56, 2),
    box(M.motor, 272, 314, ZY - 21, ZY + 21, -98, -56, 2),
    cyl(M.screw, 'z', -56, 292, -43, ZY, 4, 16),
    cyl(M.screw, 'z', -56, 292, 293, ZY, 4, 16),
    cyl(M.chrome, 'z', -76, 300, -65, ZY, 4),
    cyl(M.chrome, 'z', -76, 300, 315, ZY, 4),
    box(M.orange, -78, -30, ZY - 12, FY0, 282, 300, 3),
    box(M.orange, 280, 328, ZY - 12, FY0, 282, 300, 3),
    box(M.black, -100, -40, FY1, FY1 + 30, -95, 40, 3),        // electronics
    box(M.black, 270, 340, FY1, FY1 + 40, -95, 120, 3),        // PSU
  );

  // ---------------------------------------------------------------- LCD with live status
  const lcdCanvas = document.createElement('canvas');
  lcdCanvas.width = 320; lcdCanvas.height = 200;
  const lcdTex = new THREE.CanvasTexture(lcdCanvas);
  lcdTex.colorSpace = THREE.SRGBColorSpace;
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(80, 50), new THREE.MeshBasicMaterial({ map: lcdTex }));
  screen.geometry.rotateX(Math.PI / 2);
  screen.position.set(45, -190.7, -78);
  add(root,
    box(M.black, 0, 112, -190, -162, -112, -46, 4),
    screen,
    cyl(M.black, 'y', -199, -190, 99, -78, 8),
    cyl(M.orange, 'y', -197, -191, 99, -78, 9.5, 24, 9.5),
  );

  // ---------------------------------------------------------------- spool on top
  const SPOOL = new THREE.Vector3(125, NOZZLE_Y + 30, 440);
  const spool = new THREE.Group();
  spool.position.copy(SPOOL);
  add(spool,
    cyl(M.black, 'y', -33, -29, 0, 0, 100, 56),
    cyl(M.black, 'y', 29, 33, 0, 0, 100, 56),
    cyl(M.filament, 'y', -29, 29, 0, 0, 80, 56),
    cyl(mat(0x3a3d42, 0.6), 'y', -34, 34, 0, 0, 27, 32),
  );
  for (let i = 0; i < 6; i++) {          // flange windows so the rotation is visible
    const a = (i / 6) * Math.PI * 2;
    for (const y of [-33.2, 33.2]) {
      const w = box(mat(0x0d0e10, 0.9), -9, 9, -0.3, 0.3, -22, 22, 2);
      w.position.set(Math.cos(a) * 55, y, Math.sin(a) * 55);
      w.rotation.y = -a;
      spool.add(w);
    }
  }
  add(root, spool,
    box(M.orange, 115, 135, FY0 + 13, FY0 + 27, 330, 448, 3),
    cyl(M.alu, 'y', NOZZLE_Y - 8, FY0 + 27, 125, 440, 6),
  );

  // ---------------------------------------------------------------- X axis (moves in Z)
  const xAxis = new THREE.Group();
  add(xAxis,
    box(M.orange, -78, -28, ZY - 17, ZY + 13, 25, 110, 4),
    box(M.orange, 278, 328, ZY - 17, ZY + 13, 25, 110, 4),
    box(M.motor, -122, -80, ZY - 38, ZY + 4, 45, 87, 2),
    cyl(M.chrome, 'x', -40, 290, ZY, 48, 4),
    cyl(M.chrome, 'x', -40, 290, ZY, 95, 4),
    box(M.belt, -60, 300, ZY - 11, ZY - 9.5, 68, 75),
  );
  root.add(xAxis);

  // ---------------------------------------------------------------- extruder carriage (moves in X)
  const carriage = new THREE.Group();
  carriage.position.y = NOZZLE_Y;
  const tip = new THREE.ConeGeometry(2.4, 6, 28);
  tip.translate(0, -3, 0);
  tip.rotateX(-Math.PI / 2);                 // tip at origin, pointing down
  const heater = box(M.sock, -11, 9, -8, 8, 9, 20, 3);
  add(carriage,
    new THREE.Mesh(tip, M.brass),
    cyl(M.brass, 'z', 6, 9, 0, 0, 4.2, 6),
    heater,
    cyl(M.alu, 'z', 20, 34, 0, 0, 7, 24),
    box(M.black, -29, 29, -34, 18, 30, 128, 6),
    box(M.orange, -24, 24, -39, -32, 54, 122, 4),
    box(M.orange, -7, 7, -30, -12, 126, 138, 3),
    box(M.black, -44, -29, -30, 12, 40, 92, 5),               // part-cooling blower
    box(M.black, -40, -26, -12, 2, 16, 44, 3),
    box(M.black, -30, -12, -12, 2, 8, 22, 3),                 // duct towards the nozzle
    box(M.black, -30, 30, 18, 27, 36, 118, 3),                // carriage plate
    box(M.orange, -26, 26, 26, 42, 38, 58, 4),                // bearing housings around the X rods
    box(M.orange, -26, 26, 26, 42, 85, 105, 4),
    cyl(M.ptfe, 'z', 128, 144, 0, -6, 2.6),
    cyl(M.black, 'x', 29, 33, -8, 72, 20, 32),                // hotend fan housing (right side)
  );
  const fan = new THREE.Group();
  fan.position.set(33.6, -8, 72);
  for (let i = 0; i < 7; i++) {
    const blade = box(mat(0x2e3136, 0.6), -0.5, 0.5, -2.5, 2.5, 3, 17, 0);
    const pivot = new THREE.Group();
    pivot.rotation.x = (i / 7) * Math.PI * 2;
    blade.rotation.y = 0.5;
    pivot.add(blade);
    fan.add(pivot);
  }
  fan.add(cyl(M.black, 'x', -0.6, 1.2, 0, 0, 5));
  const ring = new THREE.Mesh(new THREE.TorusGeometry(18.5, 1.4, 8, 40), M.black);
  ring.rotation.y = Math.PI / 2;
  ring.position.set(34.4, -8, 72);
  add(carriage, fan, ring);
  xAxis.add(carriage);

  // ---------------------------------------------------------------- bed assembly (bed coords)
  const sheetShape = new THREE.Shape();
  {
    const [x0, x1, y0, y1, r] = [-2, 252, -14, 227, 6];
    sheetShape.moveTo(x0 + r, y0);
    sheetShape.lineTo(x1 - r, y0); sheetShape.quadraticCurveTo(x1, y0, x1, y0 + r);
    sheetShape.lineTo(x1, y1 - r); sheetShape.quadraticCurveTo(x1, y1, x1 - r, y1);
    sheetShape.lineTo(x0 + r, y1); sheetShape.quadraticCurveTo(x0, y1, x0, y1 - r);
    sheetShape.lineTo(x0, y0 + r); sheetShape.quadraticCurveTo(x0, y0, x0 + r, y0);
  }
  const sheet = new THREE.Mesh(new THREE.ExtrudeGeometry(sheetShape, { depth: 0.8, bevelEnabled: false }), M.sheet);
  sheet.position.z = -0.82;
  add(bedParts,
    sheet,
    box(M.pcb, -5, 255, -16, 231, -6, -0.82, 2),
    box(M.alu, 15, 235, -5, 215, -40, -36, 2),
    box(M.orange, 117, 133, 90, 120, -48, -40, 2),
  );
  for (const x of [70, 180]) for (const y of [20, 160]) add(bedParts, box(M.black, x - 10, x + 10, y, y + 30, -58, -40, 2));
  for (const [x, y] of [[10, 0], [240, 0], [10, 205], [240, 205], [125, 103]]) add(bedParts, cyl(M.alu, 'z', -36, -6, x, y, 3, 12));

  // ---------------------------------------------------------------- cable bundle + filament strand (world space)
  const SEG = 26;
  const cable = new THREE.InstancedMesh(new THREE.CylinderGeometry(1, 1, 1, 8), M.cable, SEG);
  cable.frustumCulled = false;
  const strand = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 1, 8), M.filament);
  strand.frustumCulled = false;
  add(root, cable, strand);
  const CABLE_END = new THREE.Vector3(-70, FY0 - 5, 318);
  const bez = new THREE.CubicBezierCurve3(new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3(), CABLE_END.clone());
  const pts = Array.from({ length: SEG + 1 }, () => new THREE.Vector3());
  const UP = new THREE.Vector3(0, 1, 0);
  const tmpM = new THREE.Matrix4(), tmpQ = new THREE.Quaternion(), tmpS = new THREE.Vector3(), tmpD = new THREE.Vector3(), tmpP = new THREE.Vector3();

  function place(obj, a, b, r, index) {
    tmpD.subVectors(b, a);
    const len = tmpD.length();
    tmpQ.setFromUnitVectors(UP, tmpD.normalize());
    tmpP.addVectors(a, b).multiplyScalar(0.5);
    tmpS.set(r, len, r);
    if (index === undefined) { obj.position.copy(tmpP); obj.quaternion.copy(tmpQ); obj.scale.copy(tmpS); }
    else obj.setMatrixAt(index, tmpM.compose(tmpP, tmpQ, tmpS));
  }

  const filTop = new THREE.Vector3(), filSpool = new THREE.Vector3(SPOOL.x, SPOOL.y, SPOOL.z - 80);

  // ---------------------------------------------------------------- API
  let fanAngle = 0, fanRps = 0, spoolTurns = 0;
  const pose = { x: 125, z: 50 };

  function setPose(x, z) {
    pose.x = x; pose.z = z;
    xAxis.position.z = z;
    carriage.position.x = x;
    // cable: from the extruder top, up and back to the frame
    bez.v0.set(x + 4, NOZZLE_Y + 12, z + 128);
    bez.v1.set(x + 4, NOZZLE_Y + 40, z + 230);
    bez.v2.set((x + CABLE_END.x) / 2, FY0 - 30, Math.max(z + 200, 360));
    for (let i = 0; i <= SEG; i++) bez.getPoint(i / SEG, pts[i]);
    for (let i = 0; i < SEG; i++) place(cable, pts[i], pts[i + 1], 4.5, i);
    cable.instanceMatrix.needsUpdate = true;
    filTop.set(x, NOZZLE_Y - 6, z + 144);
    place(strand, filSpool, filTop, 0.9);
  }
  setPose(pose.x, pose.z);

  function setFans(hotendRpm) {
    fanRps = hotendRpm > 0 ? 2 + Math.min(hotendRpm, 9000) / 1500 : 0;
  }

  function setFilament(color, pathMm) {
    if (color) M.filament.color.set(color);
    // ~0.45 x 0.2 mm bead per mm of path, 1.75 mm filament, 80 mm winding radius
    const filMm = (pathMm * 0.09) / 2.405;
    spoolTurns = filMm / (2 * Math.PI * 80);
  }

  function setHeat(nozzleC) {
    M.sock.emissiveIntensity = Math.max(0, Math.min(1, ((nozzleC || 0) - 60) / 180)) * 0.9;
  }

  let lcdKey = '';
  function setLcd(s) {
    const key = s ? `${s.state}|${Math.round(s.progress ?? -1)}|${Math.round(s.temps?.nozzle ?? 0)}|${Math.round(s.temps?.bed ?? 0)}|${s.job?.name}` : '';
    if (key === lcdKey) return;
    lcdKey = key;
    const c = lcdCanvas.getContext('2d');
    c.fillStyle = '#0b0c0f'; c.fillRect(0, 0, 320, 200);
    if (!s) { lcdTex.needsUpdate = true; return; }
    c.fillStyle = '#ff8a2a'; c.font = '700 22px system-ui, sans-serif';
    c.fillText(s.online ? (s.state || '') : 'OFFLINE', 16, 34);
    c.fillStyle = '#c9ced8'; c.font = '15px system-ui, sans-serif';
    const name = (s.job?.name || '').replace(/\.b?gcode$/i, '');
    c.fillText(name.length > 34 ? name.slice(0, 33) + '…' : name, 16, 62);
    const p = Math.max(0, Math.min(100, s.progress ?? 0));
    c.fillStyle = '#262a31'; c.fillRect(16, 82, 288, 14);
    c.fillStyle = '#ff8a2a'; c.fillRect(16, 82, 2.88 * p, 14);
    c.fillStyle = '#ffffff'; c.font = '700 34px system-ui, sans-serif';
    c.fillText(`${Math.round(p)}%`, 16, 140);
    c.font = '16px system-ui, sans-serif'; c.fillStyle = '#c9ced8';
    const t = s.temps || {};
    c.fillText(`Nozzle ${Math.round(t.nozzle ?? 0)}/${t.nozzle_target ?? 0}°`, 16, 178);
    c.fillText(`Bed ${Math.round(t.bed ?? 0)}/${t.bed_target ?? 0}°`, 190, 178);
    lcdTex.needsUpdate = true;
  }
  setLcd(null);

  function tick(dt) {
    fanAngle += dt * fanRps * Math.PI * 2;
    fan.rotation.x = fanAngle;
    spool.rotation.y = -spoolTurns * Math.PI * 2;
  }

  return {
    root, bedParts, setPose, setFans, setFilament, setHeat, setLcd, tick,
    nozzleWorld: (out) => out.set(pose.x, NOZZLE_Y, pose.z),
    bounds: { min: [-125, -200, -112], max: [415, 300, 540] },
  };
}
