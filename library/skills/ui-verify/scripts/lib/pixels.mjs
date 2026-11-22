// Operacje na obrazach RGBA {width,height,data}: kompozycja, przycinanie, maski, roznice.

function make(width, height, data) {
  return { width, height, data: data || new Uint8ClampedArray(width * height * 4) };
}

export function compositeOnBackground(img, bg = { r: 255, g: 255, b: 255 }) {
  const out = make(img.width, img.height);
  const n = img.width * img.height;
  for (let i = 0; i < n; i++) {
    const o = i * 4;
    const a = img.data[o + 3] / 255;
    out.data[o] = Math.round(img.data[o] * a + bg.r * (1 - a));
    out.data[o + 1] = Math.round(img.data[o + 1] * a + bg.g * (1 - a));
    out.data[o + 2] = Math.round(img.data[o + 2] * a + bg.b * (1 - a));
    out.data[o + 3] = 255;
  }
  return out;
}

function crop(img, w, h) {
  if (img.width === w && img.height === h) return img;
  const out = make(w, h);
  for (let y = 0; y < h; y++) {
    const s = y * img.width * 4;
    out.data.set(img.data.subarray(s, s + w * 4), y * w * 4);
  }
  return out;
}

export function cropToCommon(a, b) {
  const w = Math.min(a.width, b.width);
  const h = Math.min(a.height, b.height);
  const cropped = a.width !== b.width || a.height !== b.height;
  return { a: crop(a, w, h), b: crop(b, w, h), cropped };
}

export function applyMasks(img, masks) {
  const out = make(img.width, img.height, new Uint8ClampedArray(img.data));
  for (const m of masks || []) {
    if (!(m.width >= 0) || !(m.height >= 0)) throw new Error('mask: ujemny lub niepoprawny wymiar');
    const x0 = Math.max(0, Math.floor(m.x));
    const y0 = Math.max(0, Math.floor(m.y));
    const x1 = Math.min(img.width, Math.ceil(m.x + m.width));
    const y1 = Math.min(img.height, Math.ceil(m.y + m.height));
    for (let y = y0; y < y1; y++) {
      for (let x = x0; x < x1; x++) out.data.set([255, 0, 255, 255], (y * img.width + x) * 4);
    }
  }
  return out;
}

export function flagDifferences(a, b, opts = {}) {
  if (a.width !== b.width || a.height !== b.height) throw new Error('flagDifferences: rozne wymiary');
  const tol = opts.channelTolerance || 0;
  const n = a.width * a.height;
  const flags = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    const o = i * 4;
    for (let c = 0; c < 4; c++) {
      if (Math.abs(a.data[o + c] - b.data[o + c]) > tol) { flags[i] = 1; break; }
    }
  }
  return flags;
}

// Kolor piksela (x,y) z `a` wystepuje w otoczeniu 3x3 tego punktu w `b`.
function matchesNearby(a, b, x, y, tol) {
  const ao = (y * a.width + x) * 4;
  for (let dy = -1; dy <= 1; dy++) {
    for (let dx = -1; dx <= 1; dx++) {
      const nx = x + dx;
      const ny = y + dy;
      if (nx < 0 || ny < 0 || nx >= b.width || ny >= b.height) continue;
      const bo = (ny * b.width + nx) * 4;
      let same = true;
      for (let c = 0; c < 4; c++) if (Math.abs(a.data[ao + c] - b.data[bo + c]) > tol) { same = false; break; }
      if (same) return true;
    }
  }
  return false;
}

export function diffImages(imgA, imgB, opts = {}) {
  const maxPct = opts.maxPct ?? 0.5;
  const tol = opts.channelTolerance || 0;
  const tolerate1px = opts.tolerate1px !== false;
  const flagger = opts.flagger || ((x, y) => flagDifferences(x, y, { channelTolerance: tol }));
  let a = imgA;
  let b = imgB;
  let cropped = false;
  if (a.width !== b.width || a.height !== b.height) {
    if (!opts.cropToCommon) {
      return { verdict: 'FAIL', pct: 100, diffCount: 0, total: 0, sizeMismatch: true, cropped: false };
    }
    ({ a, b, cropped } = cropToCommon(a, b));
  }
  a = compositeOnBackground(a, opts.background);
  b = compositeOnBackground(b, opts.background);
  if (opts.masks && opts.masks.length) {
    a = applyMasks(a, opts.masks);
    b = applyMasks(b, opts.masks);
  }
  const flags = Uint8Array.from(flagger(a, b));
  if (tolerate1px) {
    for (let y = 0; y < a.height; y++) {
      for (let x = 0; x < a.width; x++) {
        const i = y * a.width + x;
        if (flags[i] && matchesNearby(a, b, x, y, tol) && matchesNearby(b, a, x, y, tol)) flags[i] = 0;
      }
    }
  }
  const total = a.width * a.height;
  let diffCount = 0;
  for (let i = 0; i < flags.length; i++) if (flags[i]) diffCount++;
  const pct = total === 0 ? 0 : (100 * diffCount) / total;
  return { verdict: pct <= maxPct ? 'PASS' : 'FAIL', pct, diffCount, total, sizeMismatch: false, cropped, flags };
}
