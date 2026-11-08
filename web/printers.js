// Printer models for the Printer view. The bridge reports a model key (state.model, see
// printtwin/models.py); each entry says how to build it and what to fall back to while a
// true-to-size model doesn't exist yet.
//   glb:        a CAD model in web/models/ (printer_glb.js), optional recolor {node: material}
//   procedural: 'bedslinger' (printer3d.js) or 'corexy' (printer_corexy.js), sized by `volume`
const Z_PARTS = ['z-top-left', 'z-top-right', 'z-bottom-left', 'z-bottom-right'];
const paint = (names, mat) => Object.fromEntries(names.map((n) => [n, mat]));

export const MODELS = {
  mk4:   { label: 'Original Prusa MK4', glb: 'models/mk4.glb' },
  // MK4S: MK4 frame and axes, new Nextruder (front print-fan case, 360-degree shroud, fan-door
  // cover); Z parts black, only the X-ends stay orange. Older bridges without mk4s.glb: MK4 recoloured.
  mk4s:  { label: 'Original Prusa MK4S', glb: 'models/mk4s.glb', fallback: 'mk4s_interim' },
  mk4s_interim: { label: 'Original Prusa MK4S', glb: 'models/mk4.glb', recolor: paint(Z_PARTS, 'petg'), approx: true },
  // MK3.9 / MK3.9S: an MK3S+ upgraded with the MK4 / MK4S Nextruder and electronics. Shown as the
  // MK4 / MK4S with the MK3S+'s orange Z parts until an MK3S+ frame model exists.
  mk39:  { label: 'Original Prusa MK3.9', glb: 'models/mk39.glb', fallback: 'mk39_interim' },
  mk39_interim: { label: 'Original Prusa MK3.9', glb: 'models/mk4.glb', approx: true },
  mk39s: { label: 'Original Prusa MK3.9S', glb: 'models/mk39s.glb', fallback: 'mk39s_interim' },
  mk39s_interim: { label: 'Original Prusa MK3.9S', glb: 'models/mk4s.glb', recolor: paint(Z_PARTS, 'orange'),
                   approx: true, fallback: 'mk4s_interim' },
  coreone:  { label: 'Prusa CORE One', glb: 'models/coreone.glb', fallback: 'corexy', volume: { bed: [250, 220], height: 270 } },
  coreonel: { label: 'Prusa CORE One L', glb: 'models/coreonel.glb', fallback: 'corexy', volume: { bed: [300, 300], height: 330 } },
  // XL: Prusa's printed parts, frame estimated (no published XL assembly yet) -> marked approximate
  xl:    { label: 'Original Prusa XL', glb: 'models/xl.glb', approx: true, fallback: 'corexy', volume: { bed: [360, 360], height: 360 } },
  mini:  { label: 'Original Prusa MINI+', glb: 'models/mini.glb', fallback: 'bedslinger', volume: { bed: [180, 180], height: 180 } },
  bedslinger: { label: 'Generic bed-slinger', procedural: 'bedslinger', generic: true },
  corexy:     { label: 'Generic CoreXY', procedural: 'corexy', generic: true },
  // flying gantry (bed fixed, the XY gantry moves in Z): Voron 2.4, Sovol SV08
  corexy_gantry: { label: 'Generic CoreXY, flying gantry', procedural: 'corexy', options: { zMotion: 'gantry' }, generic: true },
};

// which models/*.glb the server has (one request, cached); if the server can't say
// (older bridge), just try loading - a missing file falls back like any other failure
let available = null;
async function hasModel(path) {
  available ??= fetch('/api/models').then((r) => (r.ok ? r.json() : null)).then((j) => (j ? new Set(j.glb) : null))
    .catch(() => null);
  const set = await available;
  return set ? set.has(path.replace(/^models\//, '')) : true;
}

// -> { printer, key (the entry actually used), requested, label, approx }
export async function loadPrinter(requested, { volume } = {}) {
  let key = MODELS[requested] ? requested : 'bedslinger';
  const seen = new Set();
  const want = MODELS[requested];
  while (!seen.has(key)) {
    seen.add(key);
    const m = MODELS[key];
    try {
      if (m.glb && await hasModel(m.glb)) {
        const { buildPrinter } = await import('./printer_glb.js');
        const printer = await buildPrinter(new URL(m.glb, import.meta.url).href, { recolor: m.recolor });
        return { printer, key, requested, label: m.label, approx: Boolean(m.approx) };
      }
      if (m.procedural) {
        const vol = volume || want?.volume || {};
        const mod = await import(m.procedural === 'corexy' ? './printer_corexy.js' : './printer3d.js');
        const printer = mod.buildPrinter(m.procedural === 'corexy' ? { ...vol, ...(m.options || {}) } : undefined);
        return { printer, key, requested, label: want?.label || m.label, approx: true };
      }
    } catch (e) {
      console.warn(`printer model ${key} failed`, e);
    }
    key = m.fallback || 'bedslinger';
  }
  throw new Error(`no printer model for ${requested}`);
}
