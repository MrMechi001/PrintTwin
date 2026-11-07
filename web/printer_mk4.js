// Original Prusa MK4 from real CAD (models/mk4.glb, built by tools/cad/build_mk4.py from
// FokieH's GPL-2.0 assembly + Prusa's GPL-2.0 printed parts), animated by the live position.
// Same interface as printer3d.js: { root, bedParts, setPose, setFans, setFilament, setHeat,
// setLcd, tick, nozzleWorld, bounds, NOZZLE_Y, floorZ }.
// World = G-code frame (mm, Z up); the nozzle moves in the plane y = NOZZLE_Y, the bed group
// (G-code bed coordinates) slides in Y underneath it.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

export async function buildPrinter(url = new URL('models/mk4.glb', import.meta.url).href) {
  const gltf = await new GLTFLoader().loadAsync(url);
  const meta = gltf.parser.json.extras?.printtwin;
  if (!meta) throw new Error('mk4.glb has no PrintTwin metadata');
  const scene = gltf.scene;
  const node = (n) => { const o = scene.getObjectByName(n); if (!o) throw new Error(`mk4.glb: no node ${n}`); return o; };
  const staticG = node('static'), bedParts = node('bed'), xAxis = node('xaxis'), carriage = node('carriage');
  const NOZZLE_Y = meta.nozzle_y;

  const root = new THREE.Group();
  root.add(staticG, xAxis);
  bedParts.removeFromParent();
  bedParts.position.set(0, 0, 0);

  const mats = {};
  scene.traverse((o) => {
    if (o.isMesh) {
      o.castShadow = o.receiveShadow = false;
      mats[o.material.name] = o.material;
    }
  });
  // silicone sock glows with the hotend temperature
  const sock = mats.sock;
  if (sock) { sock.emissive = new THREE.Color(0xff3c00); sock.emissiveIntensity = 0; }

  // ---------------------------------------------------------------- perforated PSU cover (outer side)
  const psu = scene.getObjectByName('psu');
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
    const w = b.max.y - b.min.y, h = b.max.z - b.min.z;
    const plane = new THREE.Mesh(new THREE.PlaneGeometry(w - 6, h - 6),
      new THREE.MeshStandardMaterial({ map: tex, metalness: 0.55, roughness: 0.55 }));
    // plane x -> world +Y (depth), plane y -> +Z (up), normal -> +X (outer side)
    plane.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(
      new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1), new THREE.Vector3(1, 0, 0)));
    plane.position.set(b.max.x + 0.15, (b.min.y + b.max.y) / 2, (b.min.z + b.max.z) / 2);
    root.add(plane);
  }

  // ---------------------------------------------------------------- LCD with live status
  const lcdCanvas = document.createElement('canvas');
  lcdCanvas.width = 480; lcdCanvas.height = 320;
  const lcdTex = new THREE.CanvasTexture(lcdCanvas);
  lcdTex.colorSpace = THREE.SRGBColorSpace;
  {
    const [p0, p1, p2, p3] = meta.lcd_screen.map((p) => new THREE.Vector3(...p));
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute([...p0.toArray(), ...p1.toArray(), ...p2.toArray(), ...p3.toArray()], 3));
    geo.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, 1, 0, 1, 1, 0, 1], 2));
    geo.setIndex([0, 1, 2, 0, 2, 3]);
    geo.computeVertexNormals();
    root.add(new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ map: lcdTex, toneMapped: false })));
  }

  // ---------------------------------------------------------------- spool + filament + cable (procedural)
  const filMat = new THREE.MeshStandardMaterial({ color: 0xff8000, roughness: 0.45 });
  const spoolMat = new THREE.MeshStandardMaterial({ color: 0x17181b, roughness: 0.55 });
  const sp = meta.spool;
  const spool = new THREE.Group();
  spool.position.set(...sp.center);
  {
    const along = (geo) => { geo.rotateZ(Math.PI / 2); return geo; };   // cylinder axis Y -> X
    const half = sp.width / 2;
    for (const s of [-1, 1]) {
      const ring = new THREE.Mesh(along(new THREE.CylinderGeometry(sp.radius, sp.radius, 3.5, 72, 1, false)), spoolMat);
      ring.position.x = s * (half - 1.75);
      spool.add(ring);
    }
    spool.add(new THREE.Mesh(along(new THREE.CylinderGeometry(sp.radius - 22, sp.radius - 22, sp.width - 7, 72)), filMat));
    spool.add(new THREE.Mesh(along(new THREE.CylinderGeometry(sp.hub_radius, sp.hub_radius, sp.width + 1, 40, 1, true)), spoolMat));
    // hexagon windows in the flanges make the rotation visible (Prusament-style spool)
    const win = new THREE.MeshBasicMaterial({ color: 0x050506 });
    for (let r = 0; r < 2; r++) {
      const rad = r ? 74 : 50, n = r ? 12 : 8;
      for (let i = 0; i < n; i++) {
        const a = (i / n) * Math.PI * 2 + r * 0.26;
        for (const s of [-1, 1]) {
          const hex = new THREE.Mesh(along(new THREE.CylinderGeometry(r ? 10 : 9, r ? 10 : 9, 0.6, 6)), win);
          hex.position.set(s * (half + 0.05), Math.cos(a) * rad, Math.sin(a) * rad);
          spool.add(hex);
        }
      }
    }
  }
  root.add(spool);

  const SEG = 28;
  const cable = new THREE.InstancedMesh(new THREE.CylinderGeometry(1, 1, 1, 10), new THREE.MeshStandardMaterial({ color: 0x141518, roughness: 0.92 }), SEG);
  cable.frustumCulled = false;
  const strand = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 1, 8), filMat);
  strand.frustumCulled = false;
  root.add(cable, strand);
  const cableStart = new THREE.Vector3(...meta.cable_start_carriage);
  const cableEnd = new THREE.Vector3(...meta.cable_end_static);
  const inlet = new THREE.Vector3(...meta.filament_inlet_carriage);
  const bez = new THREE.CubicBezierCurve3(new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3(), cableEnd.clone());
  const pts = Array.from({ length: SEG + 1 }, () => new THREE.Vector3());
  const UP = new THREE.Vector3(0, 1, 0);
  const tmpM = new THREE.Matrix4(), tmpQ = new THREE.Quaternion(), tmpS = new THREE.Vector3(), tmpD = new THREE.Vector3(), tmpP = new THREE.Vector3();
  function placeCyl(obj, a, b, r, index) {
    tmpD.subVectors(b, a);
    const len = tmpD.length();
    tmpQ.setFromUnitVectors(UP, tmpD.normalize());
    tmpP.addVectors(a, b).multiplyScalar(0.5);
    tmpS.set(r, len, r);
    if (index === undefined) { obj.position.copy(tmpP); obj.quaternion.copy(tmpQ); obj.scale.copy(tmpS); }
    else obj.setMatrixAt(index, tmpM.compose(tmpP, tmpQ, tmpS));
  }

  // ---------------------------------------------------------------- API
  const pose = { x: meta.pose.x, z: meta.pose.z };
  let spoolTurns = 0;
  const filFrom = new THREE.Vector3(), filTo = new THREE.Vector3(), cs = new THREE.Vector3();

  function setPose(x, z) {
    pose.x = x; pose.z = z;
    carriage.position.x = x;
    xAxis.position.z = z;
    // carriage-local anchors -> world (group transforms are pure translations)
    cs.copy(cableStart).add(new THREE.Vector3(x, 0, z));
    // sagging loop: out to the left/front of the extruder, down in front of the frame window,
    // then back through the window into the electronics box
    bez.v0.copy(cs);
    bez.v1.set(cs.x - 70, cs.y - 25, cs.z - 40);
    bez.v2.set(cableEnd.x + 40, cableEnd.y - 70, Math.min(cs.z, cableEnd.z) - 30);
    for (let i = 0; i <= SEG; i++) bez.getPoint(i / SEG, pts[i]);
    for (let i = 0; i < SEG; i++) placeCyl(cable, pts[i], pts[i + 1], 4.2, i);
    cable.instanceMatrix.needsUpdate = true;
    filTo.copy(inlet).add(new THREE.Vector3(x, 0, z));
    // filament leaves the spool at the front-bottom of the winding
    filFrom.set(Math.min(Math.max(filTo.x, sp.center[0] - sp.width / 2 + 6), sp.center[0] + sp.width / 2 - 6),
      sp.center[1] - (sp.radius - 22) * 0.7, sp.center[2] - (sp.radius - 22) * 0.7);
    placeCyl(strand, filFrom, filTo, 0.875);
  }
  setPose(pose.x, pose.z);

  function setFilament(color, pathMm) {
    if (color) filMat.color.set(color);
    const filMm = (pathMm * 0.09) / 2.405;             // ~0.45 x 0.2 mm bead per mm of path, 1.75 mm filament
    spoolTurns = filMm / (2 * Math.PI * (sp.radius - 22));
  }

  function setHeat(nozzleC) {
    if (sock) sock.emissiveIntensity = Math.max(0, Math.min(1, ((nozzleC || 0) - 60) / 180)) * 0.9;
  }

  let lcdKey = '';
  function setLcd(s) {
    const key = s ? `${s.state}|${Math.round(s.progress ?? -1)}|${Math.round(s.temps?.nozzle ?? 0)}|${Math.round(s.temps?.bed ?? 0)}|${s.job?.name}` : '';
    if (key === lcdKey) return;
    lcdKey = key;
    const c = lcdCanvas.getContext('2d');
    c.fillStyle = '#0a0b0e'; c.fillRect(0, 0, 480, 320);
    c.fillStyle = '#1b1d22'; c.fillRect(0, 0, 480, 34);
    c.fillStyle = '#e8eaee'; c.font = '600 18px system-ui, sans-serif';
    const t = s?.temps || {};
    c.fillText(`${Math.round(t.nozzle ?? 0)}/${t.nozzle_target ?? 0}°C`, 14, 24);
    c.fillText(`${Math.round(t.bed ?? 0)}/${t.bed_target ?? 0}°C`, 150, 24);
    if (!s) { lcdTex.needsUpdate = true; return; }
    c.fillStyle = '#ff7a2a'; c.font = '700 26px system-ui, sans-serif';
    c.fillText(s.online ? (s.state || '') : 'OFFLINE', 18, 78);
    c.fillStyle = '#c9ced8'; c.font = '20px system-ui, sans-serif';
    const name = (s.job?.name || '').replace(/\.b?gcode$/i, '');
    c.fillText(name.length > 36 ? name.slice(0, 35) + '…' : name, 18, 114);
    const p = Math.max(0, Math.min(100, s.progress ?? 0));
    c.fillStyle = '#262a31'; c.fillRect(18, 140, 444, 18);
    c.fillStyle = '#ff7a2a'; c.fillRect(18, 140, 4.44 * p, 18);
    c.fillStyle = '#ffffff'; c.font = '700 54px system-ui, sans-serif';
    c.fillText(`${Math.round(p)}%`, 18, 230);
    c.font = '20px system-ui, sans-serif'; c.fillStyle = '#c9ced8';
    if (s.time_remaining != null) c.fillText(`${Math.round(s.time_remaining / 60)} min left`, 220, 225);
    lcdTex.needsUpdate = true;
  }
  setLcd(null);

  function tick() {
    spool.rotation.x = spoolTurns * Math.PI * 2;
  }

  const [lo, hi] = meta.bounds;
  return {
    root, bedParts, setPose, setFans: () => {}, setFilament, setHeat, setLcd, tick, NOZZLE_Y,
    nozzleWorld: (out) => out.set(pose.x, NOZZLE_Y, pose.z),
    bounds: { min: lo, max: [hi[0], hi[1], Math.max(hi[2], sp.center[2] + sp.radius)] },
    floorZ: lo[2] - 1.5,
    meta,
  };
}
