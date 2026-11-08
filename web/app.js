import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// Feature colours, index = toolpath type id (see printtwin/toolpath.py TYPES)
const TYPE_COLORS = [
  '#808080', '#ffe14d', '#ff7a33', '#3a4dff', '#b8322a', '#9a58d6', '#f04a4a', '#4f86c2',
  '#f2f2f2', '#14937a', '#3ccf4e', '#1f7a2e', '#b3e3ab', '#ff9a78', '#5ed194', '#bdbdbd',
];
const PLAYBACK_DELAY_MS = 450;   // UDP metrics arrive in ~150 ms bursts; play back slightly delayed
const SPARSE_MS = 120;           // samples further apart (Klipper: ~250 ms): move along the toolpath
const LIVE_TIMEOUT_MS = 2500;

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const prefs = (() => { try { return JSON.parse(localStorage.getItem('printtwin') || '{}'); } catch { return {}; } })();
const savePrefs = () => { try { localStorage.setItem('printtwin', JSON.stringify(prefs)); } catch { /* private mode */ } };

// ------------------------------------------------------------------ scene
const canvas = $('view');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, matchMedia('(pointer: coarse)').matches ? 1.5 : 2));
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0d0f13);
const camera = new THREE.PerspectiveCamera(38, 1, 0.5, 5000);
camera.up.set(0, 0, 1);
camera.position.set(125, -260, 240);
const controls = new OrbitControls(camera, canvas);
controls.target.set(125, 105, 0);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.update();
controls.addEventListener('start', () => { userMoved = true; });
scene.add(new THREE.HemisphereLight(0xffffff, 0x2a2e36, 1.1));
const sun = new THREE.DirectionalLight(0xffffff, 1.8);
sun.position.set(-80, -140, 300);
scene.add(sun);

function resize() {
  const w = window.innerWidth, h = window.innerHeight;
  if (!w || !h) return;                 // hidden (background tab, slicer tab not shown yet): keep the last size
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  // framed while there was no size (0/0 aspect): the camera is lost - frame again now
  if (![camera.position.x, camera.position.y, camera.position.z].every(Number.isFinite)) fit();
}
window.addEventListener('resize', resize);
resize();

// Everything that sits on the print sheet (bed plate, toolpath, printer bed parts).
// In printer mode this group slides in Y underneath the nozzle like the real bed.
const bedGroup = new THREE.Group();
scene.add(bedGroup);
let printerMode = false;

// ------------------------------------------------------------------ bed
let bed = null;
let bedBox = { x0: 0, x1: 250, y0: 0, y1: 210 };
function buildBed(shape) {
  if (bed) {
    bedGroup.remove(bed);
    bed.traverse((o) => o.geometry && o.geometry.dispose());
  }
  let pts = [[0, 0], [250, 0], [250, 210], [0, 210]];
  if (shape) {
    const p = shape.split(',').map((s) => s.split('x').map(Number));
    if (p.length >= 3 && p.every((q) => q.length === 2 && q.every(Number.isFinite))) pts = p;
  }
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  bedBox = { x0: Math.min(...xs), x1: Math.max(...xs), y0: Math.min(...ys), y1: Math.max(...ys) };
  bed = new THREE.Group();
  const plate = new THREE.Mesh(
    new THREE.ShapeGeometry(new THREE.Shape(pts.map(([x, y]) => new THREE.Vector2(x, y)))),
    new THREE.MeshBasicMaterial({ color: 0x181c23 }));
  plate.position.z = -0.08;
  bed.add(plate);
  const g = [];
  for (let x = Math.ceil(bedBox.x0 / 10) * 10; x <= bedBox.x1; x += 10) g.push(x, bedBox.y0, 0, x, bedBox.y1, 0);
  for (let y = Math.ceil(bedBox.y0 / 10) * 10; y <= bedBox.y1; y += 10) g.push(bedBox.x0, y, 0, bedBox.x1, y, 0);
  const lines = new THREE.LineSegments(lineGeo(g), new THREE.LineBasicMaterial({ color: 0x252a33 }));
  lines.position.z = -0.05;
  bed.add(lines);
  const o = [];
  pts.forEach((p, i) => { const q = pts[(i + 1) % pts.length]; o.push(p[0], p[1], 0, q[0], q[1], 0); });
  const outline = new THREE.LineSegments(lineGeo(o), new THREE.LineBasicMaterial({ color: 0x3b4352 }));
  outline.position.z = -0.04;
  bed.add(outline);
  bed.visible = !printerMode;
  bedGroup.add(bed);
}
function lineGeo(arr) {
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(arr, 3));
  return g;
}
buildBed(null);

// ------------------------------------------------------------------ nozzle
const nozzle = new THREE.Group();
{
  const tipH = 6;
  const brass = new THREE.MeshStandardMaterial({ color: 0xd8a446, metalness: 0.75, roughness: 0.3 });
  const cone = new THREE.ConeGeometry(2.3, tipH, 28);
  cone.translate(0, -tipH / 2, 0);
  cone.rotateX(-Math.PI / 2);                     // tip at origin, pointing -Z
  nozzle.add(new THREE.Mesh(cone, brass));
  const hex = new THREE.CylinderGeometry(4.2, 4.2, 3, 6);
  hex.rotateX(Math.PI / 2);
  hex.translate(0, 0, tipH + 1.5);
  nozzle.add(new THREE.Mesh(hex, brass));
  const block = new THREE.BoxGeometry(18, 14, 9);
  block.translate(-2, 0, tipH + 3 + 4.5);
  nozzle.add(new THREE.Mesh(block, new THREE.MeshStandardMaterial({
    color: 0x2b3038, metalness: 0.3, roughness: 0.6, transparent: true, opacity: 0.5 })));
  const glow = new THREE.PointLight(0xff9a40, 0, 28, 2);
  glow.position.set(0, 0, 0.6);
  nozzle.add(glow);
  nozzle.userData.glow = glow;
}
nozzle.visible = false;
scene.add(nozzle);

// ------------------------------------------------------------------ toolpath
const box = new THREE.BoxGeometry(1, 1, 1);
box.translate(0.5, 0, -0.5);        // x: 0..1 along segment, y: across, z: -1..0 below nozzle

const uCount = { value: 0 };
const uCum = { value: 0 };
const uniforms = {
  uCount,
  uCum,
  uColors: { value: TYPE_COLORS.map((c) => new THREE.Color(c)) },
  uFil: { value: new THREE.Color('#ff8000') },
  uTools: { value: Array.from({ length: 8 }, () => new THREE.Color('#ff8000')) },   // filament colour per tool
  uColorMode: { value: 0 },
};

const VERT = /* glsl */`
attribute vec3 aStart;
attribute vec3 aEnd;
attribute vec2 aWH;
attribute float aType;
attribute float aCum;       // extruded path length (mm) at segment start
attribute float aTool;      // tool / filament (T<n>) that prints the segment
uniform float uCount;
uniform float uCum;         // extruded path length at the nozzle
uniform float uGhost;
uniform vec3 uColors[16];
uniform vec3 uTools[8];
uniform float uColorMode;
varying vec3 vN;
varying vec3 vW;
varying vec3 vP;
varying vec3 vCol;
varying float vHot;
void main() {
  float id = float(gl_InstanceID);
  float full = floor(uCount);
  float frac = uCount - full;
  vec3 s = aStart;
  vec3 e = aEnd;
  if (uGhost < 0.5) {
    if (id > full || (id == full && frac <= 0.0)) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); return; }
    if (id == full) e = mix(s, e, frac);
  } else if (id < full) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); return; }
  vec3 d = e - s;
  float L = length(d);
  vec3 dir = L > 1e-5 ? d / L : vec3(1.0, 0.0, 0.0);
  vec3 side = cross(vec3(0.0, 0.0, 1.0), dir);
  float sl = length(side);
  side = sl > 1e-4 ? side / sl : vec3(0.0, 1.0, 0.0);
  vec3 up = cross(dir, side);
  float w = aWH.x;
  vec3 p = s + dir * (position.x * (L + w) - 0.5 * w) + side * (position.y * w) + up * (position.z * aWH.y);
  vec3 n = dir * normal.x + side * normal.y + up * normal.z;
  vW = n;
  vP = p;
  vN = normalize(normalMatrix * n);
  vCol = mix(uColors[int(aType + 0.5)], uTools[int(min(aTool, 7.0) + 0.5)], uColorMode);
  // freshly extruded plastic glows over the last ~80 mm of path behind the nozzle
  vHot = uGhost < 0.5 ? clamp(1.0 - (uCum - (aCum + position.x * L)) / 80.0, 0.0, 1.0) : 0.0;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
}`;

const FRAG = /* glsl */`
uniform float uGhost;
varying vec3 vN;
varying vec3 vW;
varying vec3 vP;
varying vec3 vCol;
varying float vHot;
void main() {
  // Far away a single extrusion line is smaller than a pixel and the infill
  // walls alias into moire bands - fade to flat top-face shading there.
  float flatten = smoothstep(0.1, 0.45, length(fwidth(vP)));
  vec3 N = normalize(mix(normalize(vN), normalize((viewMatrix * vec4(0.0, 0.0, 1.0, 0.0)).xyz), flatten));
  vec3 W = normalize(mix(normalize(vW), vec3(0.0, 0.0, 1.0), flatten));
  float key = max(dot(W, normalize(vec3(-0.35, -0.55, 0.76))), 0.0);
  float head = max(N.z, 0.0);
  if (uGhost > 0.5) {
    gl_FragColor = vec4(vec3(0.62, 0.68, 0.8) * (0.4 + 0.6 * head), 0.028);
  } else {
    vec3 col = vCol * (0.2 + 0.55 * key + 0.35 * head) * (0.88 + 0.12 * W.z);
    col = mix(col, vec3(1.0, 0.8, 0.55), vHot * vHot * 0.55);
    gl_FragColor = vec4(col, 1.0);
  }
  #include <colorspace_fragment>
}`;

const matPrinted = new THREE.ShaderMaterial({ uniforms: { ...uniforms, uGhost: { value: 0 } }, vertexShader: VERT, fragmentShader: FRAG });
const matGhost = new THREE.ShaderMaterial({
  uniforms: { ...uniforms, uGhost: { value: 1 } }, vertexShader: VERT, fragmentShader: FRAG,
  transparent: true, depthWrite: false,
});

// The toolpath can still be growing (job file arriving from the printer): segments live in
// over-allocated typed arrays; new ones are copied in place and only that range is re-uploaded.
let tp = null;   // { key, info, complete, n, cap, geo, printed, ghost, arr:{s,e,wh,t,c}, cum, segLen, layerStarts }
let loadingKey = null, growing = false, lastInfoAt = 0, userMoved = false;
let pid = params.get('p');
let state = null;
const apiBase = () => `/api/p/${encodeURIComponent(pid)}`;

async function fetchJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

async function fetchSegments(from) {
  const r = await fetch(`${apiBase()}/toolpath.bin?from=${from}`);
  if (!r.ok) throw new Error(`toolpath.bin: ${r.status}`);
  const key = r.headers.get('X-Toolpath-Key');
  const f0 = Number(r.headers.get('X-Toolpath-From') || 0);
  const count = Number(r.headers.get('X-Toolpath-Count') || 0);
  const buf = await r.arrayBuffer();
  const M = Math.max(0, count - f0);
  return {
    key, from: f0, count, M,
    s: new Float32Array(buf, 0, 3 * M), e: new Float32Array(buf, 12 * M, 3 * M),
    wh: new Float32Array(buf, 24 * M, 2 * M), t: new Uint8Array(buf, 32 * M, M),
  };
}

function makeGeometry(cap) {
  const arr = {
    s: new Float32Array(3 * cap), e: new Float32Array(3 * cap), wh: new Float32Array(2 * cap),
    t: new Uint8Array(cap), c: new Float32Array(cap), tool: new Uint8Array(cap),
  };
  const geo = new THREE.InstancedBufferGeometry();
  geo.index = box.index;
  geo.setAttribute('position', box.attributes.position);
  geo.setAttribute('normal', box.attributes.normal);
  geo.setAttribute('aStart', new THREE.InstancedBufferAttribute(arr.s, 3));
  geo.setAttribute('aEnd', new THREE.InstancedBufferAttribute(arr.e, 3));
  geo.setAttribute('aWH', new THREE.InstancedBufferAttribute(arr.wh, 2));
  geo.setAttribute('aType', new THREE.InstancedBufferAttribute(arr.t, 1));
  geo.setAttribute('aCum', new THREE.InstancedBufferAttribute(arr.c, 1));   // path length at segment start (glow trail)
  geo.setAttribute('aTool', new THREE.InstancedBufferAttribute(arr.tool, 1));
  geo.instanceCount = 0;
  return { geo, arr };
}

function appendSegments(chunk) {
  const n = tp.n, M = chunk.M;
  if (!M) return;
  if (n + M > tp.cap) {                       // grow: copy into bigger arrays, swap the geometry
    const cap = tp.complete ? n + M : Math.max(2 * tp.cap, n + M + 65536);
    const g = makeGeometry(cap);
    g.arr.s.set(tp.arr.s.subarray(0, 3 * n)); g.arr.e.set(tp.arr.e.subarray(0, 3 * n));
    g.arr.wh.set(tp.arr.wh.subarray(0, 2 * n)); g.arr.t.set(tp.arr.t.subarray(0, n)); g.arr.c.set(tp.arr.c.subarray(0, n));
    g.arr.tool.set(tp.arr.tool.subarray(0, n));
    const cum = new Float64Array(cap + 1); cum.set(tp.cum.subarray(0, n + 1));
    const segLen = new Float32Array(cap); segLen.set(tp.segLen.subarray(0, n));
    tp.geo.dispose();
    tp.printed.geometry = tp.ghost.geometry = g.geo;
    Object.assign(tp, { geo: g.geo, arr: g.arr, cap, cum, segLen });
  } else {
    const a = tp.geo.attributes;
    for (const [name, k] of [['aStart', 3], ['aEnd', 3], ['aWH', 2], ['aType', 1], ['aCum', 1]]) {
      a[name].addUpdateRange(k * n, k * M);
    }
  }
  const { arr, cum, segLen } = tp;
  arr.s.set(chunk.s, 3 * n); arr.e.set(chunk.e, 3 * n); arr.wh.set(chunk.wh, 2 * n); arr.t.set(chunk.t, n);
  for (let i = n; i < n + M; i++) {
    const j = 3 * i;
    segLen[i] = Math.hypot(arr.e[j] - arr.s[j], arr.e[j + 1] - arr.s[j + 1], arr.e[j + 2] - arr.s[j + 2]);
    arr.c[i] = cum[i];
    cum[i + 1] = cum[i] + segLen[i];
  }
  for (const name of ['aStart', 'aEnd', 'aWH', 'aType', 'aCum']) tp.geo.attributes[name].needsUpdate = true;
  tp.n = n + M;
  tp.geo.instanceCount = tp.n;
  fillTools(n);
}

// tools the printer has, from the slicer's printer model: XL5IS / XLP5T -> 5, XL2IS -> 2, XLIS -> 1
function printerTools(info) {
  const m = /^XLP?(\d)?(?:IS|T)?/i.exec(info?.printer_model || '');
  return m ? Number(m[1] || 1) : 1;
}

// tools (XL) / filaments (MMU) the job uses
function toolsInJob(info) {
  const t = [...new Set((info?.tool_changes || []).map((c) => c[1]))].sort((a, b) => a - b);
  return t.length ? t : [0];
}

// tool / filament per segment from the T<n> changes in the toolpath info
function toolAt(i) {
  const ch = tp?.info?.tool_changes;
  if (!ch?.length) return 0;
  let lo = 0, hi = ch.length - 1, t = ch[0][0] <= i ? ch[0][1] : 0;
  while (lo <= hi) {
    const m = (lo + hi) >> 1;
    if (ch[m][0] <= i) { t = ch[m][1]; lo = m + 1; } else hi = m - 1;
  }
  return t;
}

function fillTools(from = 0) {
  const ch = tp?.info?.tool_changes || [];
  const a = tp.arr.tool, n = tp.n;
  if (from >= n) return;
  let t = toolAt(from), j = ch.findIndex((c) => c[0] > from);
  if (j < 0) j = ch.length;
  for (let i = from; i < n;) {
    const next = j < ch.length ? Math.min(ch[j][0], n) : n;
    a.fill(Math.min(t, 7), i, next);
    i = next;
    if (j < ch.length) t = ch[j++][1];
  }
  const attr = tp.geo.attributes.aTool;
  attr.addUpdateRange(from, n - from);
  attr.needsUpdate = true;
}

function applyInfo(info) {
  const prev = tp.info;
  tp.info = info;
  tp.complete = Boolean(info.complete);
  tp.layerStarts = info.layers.map((l) => l[1]);
  if (info.filament_colour && info.filament_colour !== prev?.filament_colour) {
    uniforms.uFil.value.set(info.filament_colour);
    if (printer) printer.setFilament(info.filament_colour, 0);
  }
  const cols = info.filament_colours || (info.filament_colour ? [info.filament_colour] : null);
  const prevCols = prev?.filament_colours || (prev?.filament_colour ? [prev.filament_colour] : null);
  if (cols && String(cols) !== String(prevCols)) {
    uniforms.uTools.value.forEach((c, i) => c.set(cols[Math.min(i, cols.length - 1)]));
    printer?.setTools?.(cols, toolsInJob(info), printerTools(info));
  }
  if (String(info.tool_changes) !== String(prev?.tool_changes)) {
    fillTools(0);
    printer?.setTools?.(cols || ['#ff8000'], toolsInJob(info), printerTools(info));
  }
  if (info.bed_shape !== prev?.bed_shape) buildBed(info.bed_shape);
  if (String(info.types_present) !== String(prev?.types_present)) buildLegend();
}

async function loadToolpath(key) {
  loadingKey = key;
  try {
    const [info, chunk] = await Promise.all([fetchJson(`${apiBase()}/toolpath.json`), fetchSegments(0)]);
    if (info.key !== key || chunk.key !== key) throw new Error('toolpath changed while loading');
    const cap = info.complete ? Math.max(1, chunk.M) : Math.max(65536, 2 * chunk.M);
    const g = makeGeometry(cap);
    if (tp) {
      bedGroup.remove(tp.printed, tp.ghost);
      tp.geo.dispose();
    }
    const printed = new THREE.Mesh(g.geo, matPrinted);
    const ghost = new THREE.Mesh(g.geo, matGhost);
    printed.frustumCulled = ghost.frustumCulled = false;
    ghost.renderOrder = 1;
    ghost.visible = prefs.ghost !== false;
    bedGroup.add(printed, ghost);
    tp = { key, info: null, complete: false, n: 0, cap, geo: g.geo, arr: g.arr, printed, ghost,
      cum: new Float64Array(cap + 1), segLen: new Float32Array(cap), layerStarts: [] };
    appendSegments(chunk);
    applyInfo(info);
    lastInfoAt = performance.now();
    buf_.length = 0;
    if (!userMoved) fit();          // keep the view the user chose (Fit frames it again)
  } catch (e) {
    console.warn('toolpath load failed', e);
    setTimeout(() => { if (loadingKey === key) loadingKey = null; }, 3000);
    return;
  }
  loadingKey = null;
}

// More of a still-arriving job file is ready: fetch just the new segments (and fresh layer info).
async function growToolpath() {
  if (!tp || growing) return;
  growing = true;
  const key = tp.key;
  try {
    const chunk = await fetchSegments(tp.n);
    if (!tp || tp.key !== key || chunk.key !== key) return;
    if (chunk.from !== tp.n) { loadToolpath(key); return; }
    appendSegments(chunk);
    const now = performance.now();
    if (now - lastInfoAt > 1500 || state?.toolpath_complete) {
      lastInfoAt = now;
      const info = await fetchJson(`${apiBase()}/toolpath.json`);
      if (!tp || tp.key !== key || info.key !== key) return;
      const wasComplete = tp.complete;
      applyInfo(info);
      if (info.complete && info.count !== tp.n) { await growToolpathNow(key); }
      if (tp.complete && !wasComplete) {
        if (info.rewritten) { loadToolpath(key); return; }   // layers/heights were derived at the end
        if (!userMoved) fit();
      }
    }
  } catch (e) {
    console.warn('toolpath update failed', e);
  } finally {
    growing = false;
  }
}
async function growToolpathNow(key) {
  const chunk = await fetchSegments(tp.n);
  if (tp && tp.key === key && chunk.key === key && chunk.from === tp.n) appendSegments(chunk);
}

function buildLegend() {
  const el = $('legend');
  if (!tp || uniforms.uColorMode.value === 1) { el.hidden = true; return; }
  el.innerHTML = tp.info.types_present
    .map((t) => `<span><i style="background:${TYPE_COLORS[t]}"></i>${tp.info.types[t]}</span>`).join('');
  el.hidden = false;
}

function fit() {
  let box6;
  userMoved = false;
  if (printerMode && printer) box6 = [...printer.bounds.min, ...printer.bounds.max];
  else box6 = tp?.complete && tp.n ? tp.info.bbox : [bedBox.x0, bedBox.y0, 0, bedBox.x1, bedBox.y1, 0];
  const [x0, y0, z0, x1, y1, z1] = box6;
  const c = new THREE.Vector3((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2);
  const radius = Math.max(20, 0.5 * Math.hypot(x1 - x0, y1 - y0, z1 - z0));
  const halfV = THREE.MathUtils.degToRad(camera.fov / 2);
  const aspect = camera.aspect > 0 && Number.isFinite(camera.aspect) ? camera.aspect : 1;   // no size yet
  const halfH = Math.atan(Math.tan(halfV) * aspect);
  const dist = (radius / Math.sin(Math.min(halfV, halfH))) * (printerMode ? 0.84 : 0.9);
  const dir = printerMode ? new THREE.Vector3(0.5, -0.76, 0.42) : new THREE.Vector3(0, -0.74, 0.67);
  if (printerMode && camera.aspect > 0.8) c.add(new THREE.Vector3(-45, 0, 30));   // keep the HUD (top left) clear
  controls.target.copy(c);
  camera.position.copy(c).addScaledVector(dir.normalize(), dist);
  controls.update();
}

// ------------------------------------------------------------------ printer mode
let printer = null, printerLoading = null, envTex = null;

function makeFloor(z = -111.6, center = [125, 100]) {
  const g = new THREE.Group();
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const ctx = c.getContext('2d');
  const grd = ctx.createRadialGradient(128, 128, 8, 128, 128, 128);
  grd.addColorStop(0, 'rgba(0,0,0,0.6)');
  grd.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = grd;
  ctx.fillRect(0, 0, 256, 256);
  const shadow = new THREE.Mesh(new THREE.PlaneGeometry(760, 820),
    new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(c), transparent: true, depthWrite: false }));
  shadow.position.set(center[0], center[1], z);
  const floor = new THREE.Mesh(new THREE.CircleGeometry(2400, 64), new THREE.MeshBasicMaterial({ color: 0x14161b }));
  floor.position.set(center[0], center[1], z - 0.6);
  g.add(floor, shadow);
  return g;
}

// The model follows what the bridge detected (state.model: from the job file, the printer's
// type code or the config); web/printers.js maps it to a CAD model or a generic fallback.
let printerKey = null, printerInfo = null, firstStateAt = 0;
const wantedModel = () => state?.model || 'bedslinger';
// right after the bridge starts it hasn't asked the printer for its type yet: wait a few
// seconds before falling back to a generic model, instead of flashing it first
const detectionPending = () => Boolean(state && !state.model && state.online && state.printer_type == null
  && performance.now() - firstStateAt < 5000);

// size of a generic printer model: the bed from the job (slicer bed_shape), the height - and the
// bed, without a job - from the travel limits the printer reports (Klipper axis_minimum/maximum)
function jobVolume() {
  const v = state?.volume, h = v ? Math.round(v.max[2]) : 250;
  const s = tp?.info?.bed_shape;
  const p = s ? s.split(',').map((q) => q.split('x').map(Number)) : [];
  if (p.length && p.every((q) => q.length === 2 && q.every(Number.isFinite))) {
    const xs = p.map((q) => q[0]), ys = p.map((q) => q[1]);
    return { bed: [Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)], height: h };
  }
  return v ? { bed: [Math.round(v.max[0] - Math.max(0, v.min[0])), Math.round(v.max[1] - Math.max(0, v.min[1]))], height: h }
    : undefined;
}

function ensurePrinter(key = wantedModel()) {
  if (printer && printerKey === key) return Promise.resolve(printer);
  if (printerLoading && printerLoading.key === key) return printerLoading;
  const job = (async () => {
    const [{ RoomEnvironment }, { loadPrinter, MODELS }] = await Promise.all([
      import('three/addons/environments/RoomEnvironment.js'), import('./printers.js')]);
    const res = await loadPrinter(key, { volume: MODELS[key]?.volume ? undefined : jobVolume() });
    if (printerLoading !== job) return printer;              // superseded by another model meanwhile
    const p = res.printer;
    const b = p.bounds;
    p.root.add(makeFloor(p.floorZ ?? -111.6, [(b.min[0] + b.max[0]) / 2, (b.min[1] + b.max[1]) / 2]));
    if (!envTex) {
      const pmrem = new THREE.PMREMGenerator(renderer);
      envTex = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
      pmrem.dispose();
    }
    if (printer) {                                          // replace the previous model
      scene.remove(printer.root);
      bedGroup.remove(printer.bedParts);
    }
    scene.add(p.root);
    bedGroup.add(p.bedParts);
    p.root.visible = p.bedParts.visible = printerMode;
    if (tp?.info.filament_colour) p.setFilament(tp.info.filament_colour, 0);
    if (tp?.info) p.setTools?.(tp.info.filament_colours || [tp.info.filament_colour || '#ff8000'], toolsInJob(tp.info), printerTools(tp.info));
    printer = p;
    printerKey = key;
    printerInfo = res;
    showModelLabel();
    if (printerMode) fit();
    return p;
  })();
  job.key = key;
  printerLoading = job;
  job.catch(() => {}).then(() => { if (printerLoading === job) printerLoading = null; });
  return job;
}

function showModelLabel() {
  const el = $('model');
  if (!el) return;
  el.hidden = !(printerMode && printerInfo);
  if (printerInfo) {
    el.textContent = `${printerInfo.label}${printerInfo.approx ? ' · approximate model' : ''}`;
    el.title = printerInfo.approx ? 'A true-to-size model of this printer is in the works' : 'True-to-size model';
  }
}

async function setPrinterMode(on) {
  printerMode = on;
  bPrinter.setAttribute('aria-pressed', String(on));
  if (on && state && !detectionPending()) {                  // otherwise onState loads it
    try { await ensurePrinter(); } catch (e) { console.warn('printer model failed', e); }
  }
  if (printer) {
    printer.root.visible = printerMode;
    printer.bedParts.visible = printerMode;
  }
  scene.environment = printerMode ? envTex : null;
  scene.environmentIntensity = 0.75;
  if (bed) bed.visible = !printerMode;
  if (!printerMode) bedGroup.position.set(0, 0, 0);
  showModelLabel();
  fit();
}

// ------------------------------------------------------------------ live data
const buf_ = [];            // [t_ms, x, y, z, printed]
let tLast = 0, wallLast = -1e9, playT = null, sampleGap = 20;   // ms between position samples (smoothed)

// path length at toolpath position `count` (segments + fraction) and back
function cumAt(count) {
  const n = tp.n;
  if (!n) return 0;
  const i = Math.max(0, Math.min(Math.floor(count), n - 1));
  return tp.cum[i] + Math.max(0, Math.min(1, count - i)) * tp.segLen[i];
}
function countAtCum(c) {
  let lo = 0, hi = tp.n;                       // largest i with cum[i] <= c
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (tp.cum[mid] <= c) lo = mid; else hi = mid - 1;
  }
  const i = Math.min(lo, tp.n - 1);
  return i + (tp.segLen[i] > 0 ? Math.max(0, Math.min(1, (c - tp.cum[i]) / tp.segLen[i])) : 0);
}
// a live sample [t, x, y, z, printed] that sits on the toolpath at its printed position (not a
// travel move, park position or pause); 2 mm leaves room for the bed-mesh Z in the live position
function onPath(s) {
  const q = pointAt(s[4]);
  return Math.hypot(q[0] - s[1], q[1] - s[2]) < 2 && Math.abs(q[2] - s[3]) < 2;
}
function pointAt(count) {
  const i = Math.max(0, Math.min(Math.floor(count), tp.n - 1)), u = Math.max(0, Math.min(1, count - i));
  const s = tp.arr.s, e = tp.arr.e, j = 3 * i;
  return [s[j] + (e[j] - s[j]) * u, s[j + 1] + (e[j + 1] - s[j + 1]) * u, s[j + 2] + (e[j + 2] - s[j + 2]) * u];
}

function onPos(d) {
  if (!tp || d.key !== tp.key) return;
  for (const s of d.s) {
    const last = buf_[buf_.length - 1];
    if (last && s[0] < last[0] - 1000) { buf_.length = 0; playT = null; }   // printer / Klipper host restarted
    else if (last && s[0] > last[0]) sampleGap += (Math.min(2000, s[0] - last[0]) - sampleGap) * 0.1;
    buf_.push(s);
  }
  if (buf_.length > 3000) buf_.splice(0, buf_.length - 3000);
  tLast = buf_[buf_.length - 1][0];
  wallLast = performance.now();
}

const fmt1 = (v) => (v == null ? '–' : v.toFixed(1));
function fmtDur(s) {
  if (s == null) return '–';
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? `${h} h ${String(m).padStart(2, '0')} min` : `${m} min`;
}

function onState(s) {
  state = s;
  const st = s.online ? (s.state || '–') : 'OFFLINE';
  const chip = $('state');
  chip.textContent = st;
  chip.className = `chip ${st}`;
  $('pname').textContent = s.name;
  const file = s.job?.name || (s.online ? 'No job' : 'Printer not reachable');
  $('file').textContent = file;
  $('file').title = file;
  const active = s.state === 'PRINTING' || s.state === 'PAUSED';
  $('pct').textContent = s.progress == null ? '–' : `${Math.round(s.progress)} %`;
  $('bar').style.width = `${s.progress || 0}%`;
  $('eta').textContent = active ? `${fmtDur(s.time_remaining)} left`
    : s.state === 'FINISHED' ? `printed in ${fmtDur(s.time_printing)}` : '';
  const t = s.temps || {};
  $('tn').textContent = `${fmt1(t.nozzle)} / ${t.nozzle_target ?? '–'} °C`;
  $('tb').textContent = `${fmt1(t.bed)} / ${t.bed_target ?? '–'} °C`;

  const m = s.metrics || {};
  const dot = $('live');
  const fmt = s.format ? ` · ${{ bgcode: '.bgcode', '3mf': '.gcode.3mf' }[s.format] || '.gcode'}` : '';
  if (m.sync && m.sync.n >= 20 && m.sync.ok < 0.95) {
    dot.className = 'dot partial';
    $('livetxt').textContent = `Live, but file position off (${Math.round(m.sync.ok * 100)} % in sync)${fmt}`;
  } else if (m.estimated && active) {
    dot.className = 'dot partial';               // Bambu: the printer reports layer and percent, no position
    $('livetxt').textContent = `Estimated position (printer reports layer and %)${fmt}`;
  } else if (m.pos_age != null && m.pos_age < 3) {
    dot.className = 'dot on';
    $('livetxt').textContent = `Live nozzle position · ${Math.round(m.pos_rate)} Hz${fmt}`;
  } else if (m.sdpos_age != null && m.sdpos_age < 3) {
    dot.className = 'dot partial';
    $('livetxt').textContent = 'Live file position (enable pos_x/y/z for the nozzle)';
  } else {
    dot.className = 'dot';
    $('livetxt').textContent = active ? 'Layer only – no metrics stream' : 'No live data';
  }

  const ld = $('loading');
  if (s.loading) {
    ld.hidden = false;
    const L = s.loading;
    if (L.phase === 'download') {
      const pct = L.total ? Math.round((100 * L.done) / L.total) : 0;
      const kb = `${Math.round(L.done / 1024)} of ${Math.round((L.total || 0) / 1024)} KB`;
      ld.textContent = L.progressive
        ? `Job file ${pct} % (${kb}) – the model builds as it arrives${s.ahead ? ' · printer is ahead, catching up' : ''}`
        : `Fetching job file from printer… ${pct} % (${kb})`;
    } else if (L.phase === 'parse') {
      ld.textContent = L.source === 'slicer' ? 'Using the copy the slicer handed over…' : 'Building toolpath…';
    } else if (L.phase === 'search') {
      ld.textContent = L.message ? L.message.charAt(0).toUpperCase() + L.message.slice(1) : 'Looking for the job file…';
    } else ld.textContent = `Toolpath failed: ${L.message || 'unknown error'}`;
  } else ld.hidden = true;

  if (!s.toolpath_key && tp && !loadingKey) {   // a new print whose file isn't there yet: not the old model
    bedGroup.remove(tp.printed, tp.ghost);
    tp.geo.dispose();
    tp = null;
    buf_.length = 0;
    $('layer').textContent = 'Layer –';
    $('z').textContent = 'Z –';
  } else if (s.toolpath_key && (!tp || tp.key !== s.toolpath_key)) {
    if (loadingKey !== s.toolpath_key) loadToolpath(s.toolpath_key);
  } else if (tp && !loadingKey && (s.toolpath_count > tp.n || (s.toolpath_complete && !tp.complete))) {
    growToolpath();
  }

  // printer model: load it once the first state arrives, swap it when the detection changes
  if (!firstStateAt) firstStateAt = performance.now();
  if (printerMode && !detectionPending() && wantedModel() !== printerKey && printerLoading?.key !== wantedModel()) {
    ensurePrinter().catch((e) => console.warn('printer model failed', e));
  }
}

// ------------------------------------------------------------------ controls UI
let follow = false;
const bColor = $('bColor'), bGhost = $('bGhost'), bFollow = $('bFollow');
function applyPrefs() {
  uniforms.uColorMode.value = prefs.color === 'features' ? 0 : 1;
  bColor.textContent = prefs.color === 'features' ? 'Features' : 'Filament';
  bGhost.setAttribute('aria-pressed', String(prefs.ghost !== false));
  if (tp) tp.ghost.visible = prefs.ghost !== false;
  buildLegend();
}
bColor.removeAttribute('aria-pressed');
bColor.addEventListener('click', () => { prefs.color = prefs.color === 'features' ? 'filament' : 'features'; savePrefs(); applyPrefs(); });
bGhost.addEventListener('click', () => { prefs.ghost = prefs.ghost === false; savePrefs(); applyPrefs(); });
bFollow.addEventListener('click', () => { follow = !follow; bFollow.setAttribute('aria-pressed', String(follow)); });
$('bFit').addEventListener('click', fit);
const bPrinter = $('bPrinter');
bPrinter.addEventListener('click', () => { prefs.printer = !printerMode; savePrefs(); setPrinterMode(prefs.printer); });
// URL overrides (shareable views): ?printer=1|0, ?color=features|filament
if (params.has('color')) prefs.color = params.get('color') === 'features' ? 'features' : 'filament';
applyPrefs();
if (params.has('printer') ? params.get('printer') !== '0' : prefs.printer) setPrinterMode(true);

// ------------------------------------------------------------------ open on phone (QR code)
let qrLib = null;
function loadQrLib() {
  if (window.qrcode) return Promise.resolve();
  qrLib ??= new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = 'https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.js';
    s.onload = resolve;
    s.onerror = () => { qrLib = null; reject(new Error('QR library not reachable')); };
    document.head.appendChild(s);
  });
  return qrLib;
}
function qrSvg(text) {
  const qr = window.qrcode(0, 'M');
  qr.addData(text);
  qr.make();
  const n = qr.getModuleCount(), q = 4;
  let d = '';
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (qr.isDark(r, c)) d += `M${c + q} ${r + q}h1v1h-1z`;
  return `<svg viewBox="0 0 ${n + 2 * q} ${n + 2 * q}" xmlns="http://www.w3.org/2000/svg" shape-rendering="crispEdges"`
    + ` role="img" aria-label="QR code"><rect width="100%" height="100%" fill="#fff"/><path d="${d}" fill="#000"/></svg>`;
}
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
$('bPhone').addEventListener('click', async () => {
  const dlg = $('phone');
  dlg.showModal();
  $('qr').textContent = 'Loading…';
  try {
    const info = await fetch('/api/info').then((r) => r.json());
    const q = pid ? `?p=${encodeURIComponent(pid)}` : '';
    const urls = (info.urls.length ? info.urls : [{ kind: 'lan', url: `${location.origin}/` }]).map((u) => ({ ...u, url: u.url + q }));
    $('urls').innerHTML = urls.map((u) => `<li><span class="muted">${u.kind === 'tailscale' ? 'Tailscale' : 'Wi-Fi / LAN'}</span>`
      + `<a href="${esc(u.url)}">${esc(u.url)}</a></li>`).join('');
    await loadQrLib();
    $('qr').innerHTML = qrSvg(urls[0].url);
  } catch (e) {
    $('qr').textContent = 'QR code unavailable – type the address below on your phone.';
  }
});
$('phoneClose').addEventListener('click', () => $('phone').close());

// ------------------------------------------------------------------ frame loop
let lastFrame = performance.now(), lastHud = 0;
const tmpV = new THREE.Vector3();

function layerAt(count) {
  const ls = tp.layerStarts;
  let lo = 0, hi = ls.length - 1, i = -1;
  while (lo <= hi) { const mid = (lo + hi) >> 1; if (ls[mid] <= count) { i = mid; lo = mid + 1; } else hi = mid - 1; }
  return i;
}

function frame(now) {
  requestAnimationFrame(frame);
  const dt = Math.min(100, now - lastFrame);
  lastFrame = now;
  let count = null, nz = null;
  if (tp && buf_.length && now - wallLast < LIVE_TIMEOUT_MS) {
    const sparse = sampleGap > SPARSE_MS;
    const delay = sparse ? Math.max(PLAYBACK_DELAY_MS, 2.2 * sampleGap) : PLAYBACK_DELAY_MS;
    const target = tLast + (now - wallLast) - delay;
    if (playT === null || Math.abs(target - playT) > Math.max(800, 3 * sampleGap)) playT = target;
    else playT += dt + (target - playT) * 0.05;
    while (buf_.length > 2 && buf_[1][0] <= playT) buf_.shift();
    const a = buf_[0], b = buf_[1];
    if (b && playT > a[0]) {
      const u = Math.min(1, (playT - a[0]) / Math.max(1, b[0] - a[0]));
      if (sparse && tp.n && b[4] > a[4] && b[4] - a[4] < 5000 && onPath(a) && onPath(b)) {
        // few samples (Klipper): follow the toolpath between them at constant speed along it,
        // instead of cutting straight across the part
        const ca = cumAt(a[4]), cb = cumAt(b[4]);
        count = countAtCum(ca + (cb - ca) * u);
        nz = pointAt(count);
      } else {
        nz = [a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u, a[3] + (b[3] - a[3]) * u];
        count = a[4] + (b[4] - a[4]) * u;
      }
    } else {
      nz = [a[1], a[2], a[3]];
      count = a[4];
    }
  } else if (tp && state) {
    playT = null;
    count = state.printed;
    nz = state.nozzle;
  }
  if (tp && count != null) {
    uCount.value = count;
    const n = tp.n;
    const full = Math.min(Math.floor(count), n);
    uCum.value = full >= n ? tp.cum[n] + (tp.complete ? 1e6 : 0) : tp.cum[full] + (count - full) * tp.segLen[full];
  }

  const printing = state && (state.state === 'PRINTING' || state.state === 'PAUSED');
  if (printerMode && printer) {
    nozzle.visible = false;
    // pose: live nozzle while printing, else the idle axes PrusaLink reports
    let pose = printing && nz ? nz : null;
    if (!pose && printing && tp?.n && count != null) {
      // no nozzle metrics (layer-only mode): put the head where the toolpath says it is
      const j = 3 * Math.min(Math.max(Math.floor(count), 0), tp.n - 1);
      pose = [tp.arr.s[j], tp.arr.s[j + 1], tp.arr.s[j + 2]];
    }
    if (!pose && state && [state.axis_x, state.axis_y, state.axis_z].every((v) => typeof v === 'number')) {
      pose = [state.axis_x, state.axis_y, state.axis_z];
    }
    if (!pose && nz) pose = nz;
    if (pose) {
      printer.setPose(pose[0], pose[1], pose[2]);
      printer.bedOffset(pose[0], pose[1], pose[2], bedGroup.position);   // bed-slinger: Y, CoreXY: Z
    }
    printer.setFans(state?.fan_hotend || 0);
    printer.setHeat(state?.temps?.nozzle);
    printer.setLcd(state);
    if (tp) {
      printer.setFilament(null, Math.min(uCum.value, tp.cum[tp.n]));
      printer.setActiveTool?.(toolAt(Math.min(Math.floor(uCount.value), Math.max(tp.n - 1, 0))));
    }
    printer.tick(dt / 1000);
    if (follow && pose) {
      printer.nozzleWorld(tmpV).sub(controls.target).multiplyScalar(0.06);
      controls.target.add(tmpV);
      camera.position.add(tmpV);
    }
  } else {
    nozzle.visible = Boolean(printing && nz);
    if (nozzle.visible) {
      nozzle.position.set(nz[0], nz[1], nz[2]);
      nozzle.userData.glow.intensity = state.state === 'PRINTING' ? 900 : 0;
      if (follow) {
        tmpV.set(nz[0], nz[1], nz[2]).sub(controls.target).multiplyScalar(0.06);
        controls.target.add(tmpV);
        camera.position.add(tmpV);
      }
    }
  }

  if (tp && now - lastHud > 250) {
    lastHud = now;
    const li = layerAt(uCount.value);
    const nL = `${tp.info.layers.length}${tp.complete ? '' : '+'}`;
    $('layer').textContent = li >= 0 ? `Layer ${li + 1} / ${nL}` : `Layer – / ${nL}`;
    $('z').textContent = li >= 0 ? `Z ${tp.info.layers[li][0].toFixed(2)} mm` : 'Z –';
  }
  controls.update();
  renderer.render(scene, camera);
}
requestAnimationFrame(frame);
// debugging handle for the browser console
window.printtwin = { THREE, renderer, scene, camera, controls, uniforms, get tp() { return tp; }, get state() { return state; }, get printer() { return printer; } };

// ------------------------------------------------------------------ connect
async function init() {
  try {
    const printers = await fetch('/api/printers').then((r) => r.json());
    if (!pid) pid = printers[0]?.id;
    if (!pid) { $('file').textContent = 'No printer configured'; return; }
  } catch (e) {
    $('file').textContent = 'PrintTwin server not reachable';
    setTimeout(init, 3000);
    return;
  }
  const es = new EventSource(`/api/p/${encodeURIComponent(pid)}/events`);
  es.addEventListener('state', (e) => onState(JSON.parse(e.data)));
  es.addEventListener('pos', (e) => onPos(JSON.parse(e.data)));
  es.onerror = () => {
    const chip = $('state');
    chip.textContent = 'RECONNECTING';
    chip.className = 'chip PAUSED';
  };
}
init();
