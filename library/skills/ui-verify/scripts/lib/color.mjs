// Parsowanie i porownywanie kolorow CSS (hex, rgb/rgba, transparent); czysta logika.

const clamp01 = (n) => Number.isFinite(n) && n >= 0 && n <= 1;
const isChan = (n) => Number.isFinite(n) && n >= 0 && n <= 255;

export function parseColor(text) {
  if (typeof text !== 'string') return null;
  const s = text.trim().toLowerCase();
  if (s === 'transparent') return { r: 0, g: 0, b: 0, a: 0 };
  let m = /^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/.exec(s);
  if (m) {
    let h = m[1];
    if (h.length === 3) h = [...h].map((c) => c + c).join('');
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    const a = h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1;
    return { r, g, b, a };
  }
  m = /^rgba?\(\s*([^)]*)\)$/.exec(s);
  if (!m) return null;
  const parts = m[1].split(/[\s,/]+/).filter(Boolean);
  if (parts.length < 3 || parts.length > 4) return null;
  if (!parts.every((p) => /^[+-]?(\d+\.?\d*|\.\d+)$/.test(p))) return null;
  const [r, g, b, a = 1] = parts.map(Number);
  if (!isChan(r) || !isChan(g) || !isChan(b) || !clamp01(a)) return null;
  return { r, g, b, a };
}

// Kompozycja alfa fg nad bg; kanaly zaokraglone do calkowitych.
export function blend(fg, bg) {
  const fa = fg.a ?? 1;
  const ba = bg.a ?? 1;
  const a = fa + ba * (1 - fa);
  if (a === 0) return { r: 0, g: 0, b: 0, a: 0 };
  const ch = (f, b2) => Math.round((f * fa + b2 * ba * (1 - fa)) / a);
  return { r: ch(fg.r, bg.r), g: ch(fg.g, bg.g), b: ch(fg.b, bg.b), a };
}

export function colorDistance(a, b) {
  return Math.max(Math.abs(a.r - b.r), Math.abs(a.g - b.g), Math.abs(a.b - b.b));
}

export function sameColor(a, b, tolerance = 0) {
  return colorDistance(a, b) <= tolerance;
}
