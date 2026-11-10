// Building blocks shared by all printer models: live LCD, spool, cable / filament tubes.
import * as THREE from 'three';

// Character LCD (MK3S+): white characters on a blue backlight, like the stock 20x4 display
export const CHAR_LCD_BACKLIGHT = '#1f46d2';
export const CHAR_LCD_PIXEL = '#eef3ff';

// a quad between the four corners (bottom-left, bottom-right, top-right, top-left) showing `canvas`
function screenMesh(corners, canvas) {
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  const [p0, p1, p2, p3] = corners.map((p) => new THREE.Vector3(...p));
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute([...p0.toArray(), ...p1.toArray(), ...p2.toArray(), ...p3.toArray()], 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, 1, 0, 1, 1, 0, 1], 2));
  geo.setIndex([0, 1, 2, 0, 2, 3]);
  geo.computeVertexNormals();
  const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ map: tex, toneMapped: false, side: THREE.DoubleSide }));
  return { mesh, tex };
}

// ---------------------------------------------------------------- LCD with the live status
// style 'char20x4': the MK3S+'s character LCD (see makeCharLcd); otherwise the graphic screen
export function makeLcd(corners, { portrait = false, style, name } = {}) {
  if (style === 'char20x4') return makeCharLcd(corners, { name });
  const W = portrait ? 320 : 480, H = portrait ? 480 : 320;   // MINI: 240 x 320 portrait screen
  const canvas = document.createElement('canvas');
  canvas.width = W; canvas.height = H;
  const { mesh, tex } = screenMesh(corners, canvas);
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

// ---------------------------------------------------------------- character LCD: 20x4, HD44780, 5x8 dot cells
// Character ROM A00 (the HD44780's standard font), 0x20-0x7F: 7 rows of 5 dots per glyph, top row first,
// one base-32 digit per row (bit 4 = left column); row 8 is the cursor line and stays dark. A00 has ¥ at
// 0x5C ('\') and arrows at 0x7E ('~') / 0x7F, like the real display.
const ROM_A00 = [
  '0000000', '4444004', 'aaa0000', 'aavavaa', '4fke5u4', 'op248j3', 'cik8lid', 'c480000',   // space ! " # $ % & '
  '2488842', '8422248', '04lel40', '044v440', '0000c48', '000v000', '00000cc', '01248g0',   // ( ) * + , - . /
  'ehjlphe', '4c4444e', 'eh1248v', 'v2421he', '26aiv22', 'vgu11he', '68guhhe', 'v124888',   // 0-7
  'ehhehhe', 'ehhf12c', '0cc0cc0', '0cc0c48', '248g842', '00v0v00', '8421248', 'eh12404',   // 8 9 : ; < = > ?
  'eh1dlle', 'ehhhvhh', 'uhhuhhu', 'ehggghe', 'sihhhis', 'vgguggv', 'vgguggg', 'ehgnhhf',   // @ A-G
  'hhhvhhh', 'e44444e', '72222ic', 'hikokih', 'ggggggv', 'hrllhhh', 'hhpljhh', 'ehhhhhe',   // H-O
  'uhhuggg', 'ehhhlid', 'uhhukih', 'fgge11u', 'v444444', 'hhhhhhe', 'hhhhha4', 'hhhllla',   // P-W
  'hha4ahh', 'hhha444', 'v1248gv', 'e88888e', 'hav4v44', 'e22222e', '4ah0000', '000000v',   // X Y Z [ ¥ ] ^ _
  '8420000', '00e1fhf', 'ggmphhu', '00egghe', '11djhhf', '00ehvge', '698s888', '0fhhf1e',   // ` a-g
  'ggmphhh', '40c444e', '20622ic', 'ggikoki', 'c44444e', '00qllhh', '00mphhh', '00ehhhe',   // h-o
  '00uhugg', '00djf11', '00mpggg', '00ege1u', '88s8896', '00hhhjd', '00hhha4', '00hhlla',   // p-w
  '00ha4ah', '00hhf1e', '00v248v', '2448442', '4444444', '8442448', '042v240', '048v840',   // x y z { | } → ←
];
// Prusa firmware 3.x custom characters (FontTable.h / lcd.h LCD_STR_*), 8 rows each
const LCD_ICONS = {
  0x80: '0vlhlv00',   // LCD_STR_BEDTEMP
  0x81: 'ciic0000',   // LCD_STR_DEGREE
  0x82: '4aaaahhe',   // LCD_STR_THERMOMETER
  0x86: '04i9i400',   // LCD_STR_FEEDRATE (»)
  0x87: '0ejlhe00',   // LCD_STR_CLOCK
};
const BED = '\x80', DEG = '\x81', THERMO = '\x82', FEED = '\x86', CLOCK = '\x87';
const rowsOf = (g) => [...g.padEnd(8, '0')].map((d) => parseInt(d, 32));
const GLYPHS = new Map([...ROM_A00.map((g, i) => [0x20 + i, rowsOf(g)]),
  ...Object.entries(LCD_ICONS).map(([c, g]) => [Number(c), rowsOf(g)])]);

// text from outside (file names) as ROM characters: accents dropped, anything else '?'
const romText = (str) => [...String(str ?? '')].map((ch) => {
  const a = ch.normalize('NFD')[0], c = a.charCodeAt(0);
  return c >= 0x20 && c < 0x80 ? a : '?';
}).join('');
const fit = (str, n) => str.padEnd(n).slice(0, n);
const num = (v, w) => (Number.isFinite(v) ? String(Math.round(v)).padStart(w) : '-'.repeat(w));

// The status screen of Prusa firmware 3.x (ultralcd.cpp, lcdui_print_status_screen), 4 rows of 20:
//   |N000/000D   Z000.00 |   N thermometer, D degree; Z with ' ' (homed)
//   |B000/000D   F100%   |   B bed, F feedrate (») = speed %
//   | HO 99%     t00:17R |   " HO" host print (fw 3.13+; " SD" from the printer's card - the state doesn't say,
//                              PrusaLink on a Pi streams over serial); t clock, R remaining ('?': speed not 100 %)
//   |Status line.........|   file name while printing (scrolls), "<machine> ready." when idle
// `tick` (seconds) drives the scrolling file name and the heating dots, as the firmware's 1 s refresh.
export function charLcdRows(s, { name = 'Prusa i3 MK3S', tick = 0 } = {}) {
  const v = s?.online ? s : null;                            // no live values: dashes, not stale numbers
  const t = v?.temps || {};
  const active = v?.state === 'PRINTING' || v?.state === 'PAUSED';
  const temp = (icon, cur, target) => fit(`${icon}${num(cur, 3)}/${Number.isFinite(target) ? Math.round(target) : '---'}${DEG}`, 9);
  const z = Number.isFinite(v?.axis_z) ? v.axis_z : v?.nozzle?.[2];
  const zs = Number.isFinite(z) ? `Z${z.toFixed(2).padStart(6)} ` : 'Z   --- ';
  // progress: " HO" = print from the host, as PrusaLink on a Pi runs it; "---%" when not printing
  const pct = active && Number.isFinite(v.progress) ? ` HO${String(Math.floor(v.progress)).padStart(3)}%` : '   ---%';
  let time = `${CLOCK}--:--  `;
  if (active) {
    let m = null, suff = ' ', doubt = ' ';
    if (Number.isFinite(v.time_remaining)) {
      m = Math.floor(v.time_remaining / 60); suff = 'R';
      if (Number.isFinite(v.speed) && v.speed > 0 && v.speed !== 100) { doubt = '?'; m = Math.floor((100 * m) / v.speed); }
    } else if (Number.isFinite(v.time_printing)) m = Math.floor(v.time_printing / 60);   // elapsed
    if (m != null) {
      time = m < 6000 ? `${CLOCK}${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}${suff}${doubt}`
        : `${CLOCK}${String(Math.floor(m / 60)).padStart(3)}h ${suff}${doubt}`;
    }
  }
  return [
    `${temp(THERMO, t.nozzle, t.nozzle_target)}   ${zs}`,
    `${temp(BED, t.bed, t.bed_target)}   ${fit(`${FEED}${num(v?.speed, 3)}%`, 8)}`,
    `${pct}     ${time}`,
    fit(charLcdStatus(s, name, tick), 20),
  ].map((r) => fit(r, 20));
}

function charLcdStatus(s, name, tick) {
  if (!s) return '';
  if (!s.online) return 'OFFLINE';
  const t = s.temps || {};
  const below = (cur, target, d) => target > 0 && Number.isFinite(cur) && cur < target - d;
  if (s.state === 'PRINTING' && (s.progress ?? 0) < 1) {        // M190 / M109 at the start: "Heating" + dots
    const msg = below(t.bed, t.bed_target, 2) ? 'Bed Heating' : below(t.nozzle, t.nozzle_target, 3) ? 'Heating' : null;
    if (msg) return msg + `${' '.repeat(7)}${'.'.repeat(tick % 14)}`.slice(msg.length);
  }
  const file = romText(s.job?.name);
  switch (s.state) {
    case 'PAUSED': return 'Print paused';
    case 'STOPPED': return 'Print aborted';
    case 'ERROR': return 'Printer error';
    case 'ATTENTION': return 'Attention needed';
    case 'BUSY': return 'Please wait';
    case 'PRINTING': case 'FINISHED':
      // long names scroll one character per refresh, then start over
      if (file) return file.length > 20 ? file.slice(tick % (file.length - 19)) : file;
      if (s.state === 'PRINTING') return '';
  }
  return `${romText(name)} ready.`;
}

// HD44780 2004 module geometry in units of 0.05 mm: dot 0.55 at 0.60 pitch, character pitch 3.55 x 5.35,
// viewing area 76.0 x 25.2 (the lcd_screen corners should span it)
const DOT = 11, DOT_PITCH = 12, CELL_X = 71, CELL_Y = 107, VIEW_W = 1520, VIEW_H = 504;
const ORIGIN_X = (VIEW_W - 19 * CELL_X - 5 * DOT_PITCH + 1) / 2, ORIGIN_Y = (VIEW_H - 3 * CELL_Y - 8 * DOT_PITCH + 1) / 2;

function makeCharLcd(corners, { name } = {}) {
  const canvas = document.createElement('canvas');
  canvas.width = VIEW_W; canvas.height = VIEW_H;
  const { mesh, tex } = screenMesh(corners, canvas);
  tex.anisotropy = 4;
  // backlight with the faint grid of unlit dots, drawn once
  const base = document.createElement('canvas');
  base.width = VIEW_W; base.height = VIEW_H;
  const b = base.getContext('2d');
  b.fillStyle = CHAR_LCD_BACKLIGHT; b.fillRect(0, 0, VIEW_W, VIEW_H);
  const glow = b.createRadialGradient(VIEW_W / 2, VIEW_H / 2, VIEW_H * 0.2, VIEW_W / 2, VIEW_H / 2, VIEW_W * 0.62);
  glow.addColorStop(0, 'rgba(255,255,255,0.07)'); glow.addColorStop(1, 'rgba(0,0,24,0.22)');
  b.fillStyle = glow; b.fillRect(0, 0, VIEW_W, VIEW_H);
  b.fillStyle = CHAR_LCD_PIXEL; b.globalAlpha = 0.075;
  for (let r = 0; r < 4; r++) for (let c = 0; c < 20; c++) {
    for (let y = 0; y < 8; y++) for (let x = 0; x < 5; x++) {
      b.fillRect(ORIGIN_X + c * CELL_X + x * DOT_PITCH, ORIGIN_Y + r * CELL_Y + y * DOT_PITCH, DOT, DOT);
    }
  }
  const ctx = canvas.getContext('2d');
  const dots = (rows, grow) => {
    for (let r = 0; r < 4; r++) for (let c = 0; c < 20; c++) {
      const g = GLYPHS.get(rows[r].charCodeAt(c)) || GLYPHS.get(0x3f);
      for (let y = 0; y < 8; y++) for (let x = 0; x < 5; x++) {
        if (g[y] & (16 >> x)) {
          ctx.fillRect(ORIGIN_X + c * CELL_X + x * DOT_PITCH - grow, ORIGIN_Y + r * CELL_Y + y * DOT_PITCH - grow, DOT + 2 * grow, DOT + 2 * grow);
        }
      }
    }
  };
  let key = null;
  function update(s) {
    const rows = charLcdRows(s, { name, tick: Math.floor(performance.now() / 1000) });
    const k = rows.join('\n');
    if (k === key) return;
    key = k;
    ctx.globalAlpha = 1;
    ctx.drawImage(base, 0, 0);
    ctx.fillStyle = CHAR_LCD_PIXEL;
    ctx.globalAlpha = 0.16; dots(rows, 2);                   // a little light bleeding around lit dots
    ctx.globalAlpha = 0.97; dots(rows, 0);
    ctx.globalAlpha = 1;
    tex.needsUpdate = true;
  }
  update(null);
  return { mesh, update };
}

// ---------------------------------------------------------------- spool (axis along X unless `axis`), Prusament-style windows
// `axis` (unit vector, printer frame): the spool is turned so that its local X lies along it; turn it
// with spinSpool. Without one (or [1, 0, 0]) the group stays unrotated and spins with rotation.x.
const X_AXIS = new THREE.Vector3(1, 0, 0);
export function makeSpool({ radius = 100, width = 68, hub_radius = 27, axis } = {}, filMat) {
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
  const ax = axis ? new THREE.Vector3(...axis).normalize() : X_AXIS;
  if (ax.x < 1 - 1e-9) {                                    // any other axis: turn the spool onto it
    g.quaternion.setFromUnitVectors(X_AXIS, ax);
    g.userData.axis = ax.toArray();
    g.userData.orient = g.quaternion.toArray();
  }
  return g;
}

// turn a spool about its own axis to `angle` (radians)
const _spinAx = new THREE.Vector3(), _spinQ = new THREE.Quaternion();
export function spinSpool(spool, angle) {
  const { axis, orient } = spool.userData;
  if (!axis) { spool.rotation.x = angle; return; }
  spool.quaternion.setFromAxisAngle(_spinAx.fromArray(axis), angle).multiply(_spinQ.fromArray(orient));
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
// Which way a spool turns about its local X axis (its own axis, also when makeSpool turned it onto
// another one) while filament is pulled off it: the rim where the filament leaves (`from`) moves
// towards where the filament goes (`to`). Points in `root` coordinates; +1 or -1 for spinSpool.
const _m = new THREE.Matrix4(), _c = new THREE.Vector3(), _ax = new THREE.Vector3(), _d = new THREE.Vector3();
export function spoolDirection(spool, from, to, root) {
  root.updateWorldMatrix(true, true);
  _m.copy(root.matrixWorld).invert().multiply(spool.matrixWorld);      // spool -> root
  _c.setFromMatrixPosition(_m);
  _ax.set(1, 0, 0).transformDirection(_m).cross(_d.subVectors(from, _c));   // rim velocity for +rotation
  return _ax.dot(_d.subVectors(to, from)) < 0 ? -1 : 1;
}

export const spoolTurnsFor = (pathMm, windingRadius) => ((pathMm * 0.09) / 2.405) / (2 * Math.PI * windingRadius);
