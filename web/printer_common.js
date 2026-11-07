// Building blocks shared by all printer models: live LCD, spool, cable / filament tubes.
import * as THREE from 'three';

// ---------------------------------------------------------------- LCD with the live status
export function makeLcd(corners, { portrait = false } = {}) {
  const W = portrait ? 320 : 480, H = portrait ? 480 : 320;   // MINI: 240 x 320 portrait screen
  const canvas = document.createElement('canvas');
  canvas.width = W; canvas.height = H;
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  const [p0, p1, p2, p3] = corners.map((p) => new THREE.Vector3(...p));
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute([...p0.toArray(), ...p1.toArray(), ...p2.toArray(), ...p3.toArray()], 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, 1, 0, 1, 1, 0, 1], 2));
  geo.setIndex([0, 1, 2, 0, 2, 3]);
  geo.computeVertexNormals();
  const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ map: tex, toneMapped: false, side: THREE.DoubleSide }));
  let key = null;
  function update(s) {
    const k = s ? `${s.state}|${Math.round(s.progress ?? -1)}|${Math.round(s.temps?.nozzle ?? 0)}|${Math.round(s.temps?.bed ?? 0)}|${s.job?.name}` : '';
    if (k === key) return;
    key = k;
    const c = canvas.getContext('2d');
    c.fillStyle = '#0a0b0e'; c.fillRect(0, 0, W, H);
    c.fillStyle = '#1b1d22'; c.fillRect(0, 0, W, 34);
    c.fillStyle = '#e8eaee'; c.font = '600 18px system-ui, sans-serif';
    const t = s?.temps || {};
    c.fillText(`${Math.round(t.nozzle ?? 0)}/${t.nozzle_target ?? 0}°C`, 14, 24);
    c.fillText(`${Math.round(t.bed ?? 0)}/${t.bed_target ?? 0}°C`, portrait ? 170 : 150, 24);
    if (s) {
      const dy = portrait ? 40 : 0;                         // portrait: same layout, more air
      c.fillStyle = '#ff7a2a'; c.font = '700 26px system-ui, sans-serif';
      c.fillText(s.online ? (s.state || '') : 'OFFLINE', 18, 78 + dy);
      c.fillStyle = '#c9ced8'; c.font = '20px system-ui, sans-serif';
      const name = (s.job?.name || '').replace(/\.b?gcode$/i, '');
      const max = portrait ? 24 : 36;
      c.fillText(name.length > max ? name.slice(0, max - 1) + '…' : name, 18, 114 + dy);
      const p = Math.max(0, Math.min(100, s.progress ?? 0));
      c.fillStyle = '#262a31'; c.fillRect(18, 140 + dy, W - 36, 18);
      c.fillStyle = '#ff7a2a'; c.fillRect(18, 140 + dy, ((W - 36) / 100) * p, 18);
      c.fillStyle = '#ffffff'; c.font = '700 54px system-ui, sans-serif';
      c.fillText(`${Math.round(p)}%`, 18, 230 + dy * 1.5);
      c.font = '20px system-ui, sans-serif'; c.fillStyle = '#c9ced8';
      if (s.time_remaining != null) {
        c.fillText(`${Math.round(s.time_remaining / 60)} min left`, portrait ? 18 : 220, portrait ? 340 : 225);
      }
    }
    tex.needsUpdate = true;
  }
  update(null);
  return { mesh, update };
}

// ---------------------------------------------------------------- spool (axis along X), Prusament-style windows
export function makeSpool({ radius = 100, width = 68, hub_radius = 27 } = {}, filMat) {
  const spoolMat = new THREE.MeshStandardMaterial({ color: 0x17181b, roughness: 0.55 });
  const g = new THREE.Group();
  const along = (geo) => { geo.rotateZ(Math.PI / 2); return geo; };   // cylinder axis Y -> X
  const half = width / 2;
  for (const s of [-1, 1]) {
    const ring = new THREE.Mesh(along(new THREE.CylinderGeometry(radius, radius, 3.5, 72)), spoolMat);
    ring.position.x = s * (half - 1.75);
    g.add(ring);
  }
  g.add(new THREE.Mesh(along(new THREE.CylinderGeometry(radius - 22, radius - 22, width - 7, 72)), filMat));
  g.add(new THREE.Mesh(along(new THREE.CylinderGeometry(hub_radius, hub_radius, width + 1, 40, 1, true)), spoolMat));
  const win = new THREE.MeshBasicMaterial({ color: 0x050506 });
  for (let r = 0; r < 2; r++) {
    const rad = (r ? 0.74 : 0.5) * radius, n = r ? 12 : 8;
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2 + r * 0.26;
      for (const s of [-1, 1]) {
        const hex = new THREE.Mesh(along(new THREE.CylinderGeometry(r ? 10 : 9, r ? 10 : 9, 0.6, 6)), win);
        hex.position.set(s * (half + 0.05), Math.cos(a) * rad, Math.sin(a) * rad);
        g.add(hex);
      }
    }
  }
  g.userData.windingRadius = radius - 22;
  return g;
}

// ---------------------------------------------------------------- tubes: a cable along a Bezier, a straight strand
const UP = new THREE.Vector3(0, 1, 0);
const tmpM = new THREE.Matrix4(), tmpQ = new THREE.Quaternion(), tmpS = new THREE.Vector3(), tmpD = new THREE.Vector3(), tmpP = new THREE.Vector3();

export function placeCyl(obj, a, b, r, index) {
  tmpD.subVectors(b, a);
  const len = tmpD.length();
  tmpQ.setFromUnitVectors(UP, tmpD.normalize());
  tmpP.addVectors(a, b).multiplyScalar(0.5);
  tmpS.set(r, len, r);
  if (index === undefined) { obj.position.copy(tmpP); obj.quaternion.copy(tmpQ); obj.scale.copy(tmpS); }
  else obj.setMatrixAt(index, tmpM.compose(tmpP, tmpQ, tmpS));
}

export function makeCable(radius = 4.2, segments = 28, color = 0x141518) {
  const mesh = new THREE.InstancedMesh(new THREE.CylinderGeometry(1, 1, 1, 10),
    new THREE.MeshStandardMaterial({ color, roughness: 0.92 }), segments);
  mesh.frustumCulled = false;
  const bez = new THREE.CubicBezierCurve3(new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3());
  const pts = Array.from({ length: segments + 1 }, () => new THREE.Vector3());
  return {
    mesh,
    set(p0, p1, p2, p3) {
      bez.v0.copy(p0); bez.v1.copy(p1); bez.v2.copy(p2); bez.v3.copy(p3);
      for (let i = 0; i <= segments; i++) bez.getPoint(i / segments, pts[i]);
      for (let i = 0; i < segments; i++) placeCyl(mesh, pts[i], pts[i + 1], radius, i);
      mesh.instanceMatrix.needsUpdate = true;
    },
  };
}

export function makeStrand(filMat, radius = 0.875) {
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 1, 8), filMat);
  mesh.frustumCulled = false;
  return { mesh, set: (a, b) => placeCyl(mesh, a, b, radius) };
}

// filament used for a path length: ~0.45 x 0.2 mm bead per mm of path, 1.75 mm filament
export const spoolTurnsFor = (pathMm, windingRadius) => ((pathMm * 0.09) / 2.405) / (2 * Math.PI * windingRadius);
