// WCAG 2.x contrast arithmetic in floating point (no channel rounding) and the per-element contrast verdict.
// The background comes from sampled screenshot pixels; the text colour is composited over each pixel.
import { SchemaError, validateContrastSample } from './a11y-schema.mjs';

export const DEFAULT_THRESHOLDS = Object.freeze({ normal: 4.5, large: 3.0 });
export const LARGE_TEXT_PX = 24;
export const LARGE_BOLD_PX = 56 / 3; // 14 pt = 18.6667 px, compared with >=
export const SOLID_SPREAD = 1;
// Minimum of thresholds.nonText (WCAG 1.4.11). Not part of DEFAULT_THRESHOLDS: the key exists only when it is configured.
export const NON_TEXT_MIN_RATIO = 3;

// sRGB channel (0..255, may be fractional) to linear light; threshold 0.04045 (WCAG 2.2).
export function srgbToLinear(c) {
  const v = c / 255;
  return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
}

export function relativeLuminance({ r, g, b }) {
  return 0.2126 * srgbToLinear(r) + 0.7152 * srgbToLinear(g) + 0.0722 * srgbToLinear(b);
}

export function contrastRatio(rgbA, rgbB) {
  const a = relativeLuminance(rgbA);
  const b = relativeLuminance(rgbB);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

// Foreground with alpha composited "over" an opaque background; channels stay fractional.
export function compositeOver(fg, bg) {
  const a = fg.a === undefined ? 1 : fg.a;
  return { r: fg.r * a + bg.r * (1 - a), g: fg.g * a + bg.g * (1 - a), b: fg.b * a + bg.b * (1 - a) };
}

export function isLargeText({ fontSizePx, fontWeight, bold }) {
  const isBold = bold === true || (bold === undefined && Number(fontWeight) >= 700);
  return fontSizePx >= LARGE_TEXT_PX || (isBold && fontSizePx >= LARGE_BOLD_PX);
}

// thresholds: { normal, large, nonText?, reason? }. Lowering below 4.5 / 3.0 (nonText: 3.0) needs a reason (error) and
// yields a warning. Text measurement ignores nonText.
export function resolveThresholds(input) {
  const errors = [];
  const warnings = [];
  const out = { ...DEFAULT_THRESHOLDS };
  if (input !== undefined && input !== null && typeof input.reason === 'string' && input.reason.trim() !== '') out.reason = input.reason.trim();
  if (input !== undefined && input !== null) {
    if (typeof input !== 'object' || Array.isArray(input)) {
      errors.push('thresholds: expected object { normal, large, nonText?, reason? }');
    } else {
      for (const k of Object.keys(input)) if (!['normal', 'large', 'nonText', 'reason'].includes(k)) errors.push(`thresholds.${k}: unknown key`);
      for (const k of ['normal', 'large', 'nonText']) {
        if (input[k] === undefined) continue;
        if (typeof input[k] !== 'number' || !Number.isFinite(input[k]) || input[k] < 1 || input[k] > 21) errors.push(`thresholds.${k}: expected a number 1..21`);
        else out[k] = input[k];
      }
      if (input.reason !== undefined && (typeof input.reason !== 'string' || input.reason.trim() === '')) errors.push('thresholds.reason: expected a non-empty string');
      const minimum = { ...DEFAULT_THRESHOLDS, nonText: NON_TEXT_MIN_RATIO };
      const lowered = Object.keys(minimum).filter((k) => out[k] < minimum[k]); // an absent nonText never compares lower
      if (lowered.length) {
        if (typeof input.reason !== 'string' || input.reason.trim() === '') {
          errors.push(`thresholds.${lowered.join(', ')}: lowering below the WCAG minimum (4.5 / 3.0) requires a "reason"`);
        } else {
          warnings.push({ code: 'threshold-lowered', level: 'warning', message: `threshold lowered (${lowered.map((k) => `${k}=${out[k]}`).join(', ')}): ${input.reason.trim()}` });
        }
      }
    }
  }
  return { ok: errors.length === 0, errors, warnings, thresholds: out };
}

const keyOf = (p) => `${p[0]},${p[1]},${p[2]}`;

// Most frequent colour of the samples (ties: the one seen first).
export function dominantColor(pixels) {
  const counts = new Map();
  for (const p of pixels) {
    const k = keyOf(p);
    const e = counts.get(k);
    if (e) e.n++; else counts.set(k, { n: 1, rgb: { r: p[0], g: p[1], b: p[2] } });
  }
  let best = null;
  for (const e of counts.values()) if (best === null || e.n > best.n) best = e;
  return best ? best.rgb : null;
}

export function backgroundKindOf(pixels) {
  if (pixels.length === 0) return 'solid';
  for (let c = 0; c < 3; c++) {
    let lo = 255;
    let hi = 0;
    for (const p of pixels) { if (p[c] < lo) lo = p[c]; if (p[c] > hi) hi = p[c]; }
    if (hi - lo > SOLID_SPREAD) return 'varying';
  }
  return 'solid';
}

const round3 = (n) => Math.round(n * 1000) / 1000;

// The ratio that decides: the minimum, or the 1st percentile on a varying background with at least 100 samples.
export function worstRatio(ratios, kind) {
  if (kind === 'varying' && ratios.length >= 100) {
    const sorted = [...ratios].sort((a, b) => a - b);
    return sorted[Math.floor(sorted.length * 0.01)];
  }
  return ratios.reduce((lo, r) => Math.min(lo, r), Infinity);
}

// Returns { verdict, reason?, ratio, required, ... }. Precedence: EXEMPT, then UNVERIFIED, then the measurement.
export function measureSample(sample, thresholdsInput) {
  const v = validateContrastSample(sample);
  if (!v.ok) throw new SchemaError(`invalid contrast sample: ${v.errors.join('; ')}`, v.errors);
  const base = { selector: sample.selector, fontSizePx: sample.fontSizePx };
  if (sample.exempt) return { ...base, verdict: 'EXEMPT', reason: sample.exempt };
  if (sample.unverified) return { ...base, verdict: 'UNVERIFIED', reason: sample.unverified };
  const rt = resolveThresholds(thresholdsInput);
  if (!rt.ok) throw new SchemaError(`invalid thresholds: ${rt.errors.join('; ')}`, rt.errors);
  const th = rt.thresholds;
  const pixels = sample.pixels || [];
  if (pixels.length === 0) return { ...base, verdict: 'UNVERIFIED', reason: 'no-samples' };

  const bold = sample.bold === undefined ? Number(sample.fontWeight) >= 700 : sample.bold === true;
  const large = isLargeText({ fontSizePx: sample.fontSizePx, fontWeight: sample.fontWeight, bold });
  const required = large ? th.large : th.normal;
  const alpha = sample.foreground.a * (sample.opacity === undefined ? 1 : sample.opacity);
  const fg = { r: sample.foreground.r, g: sample.foreground.g, b: sample.foreground.b, a: alpha };

  const ratios = pixels.map((p) => contrastRatio(compositeOver(fg, { r: p[0], g: p[1], b: p[2] }), { r: p[0], g: p[1], b: p[2] }));
  const kind = backgroundKindOf(pixels);
  const exact = worstRatio(ratios, kind);
  const notes = [];
  if (kind === 'varying') notes.push('sampled');
  return {
    ...base,
    verdict: exact >= required ? 'PASS' : 'FAIL',
    ratio: round3(exact),
    ratioExact: exact,
    required,
    large,
    bold,
    backgroundKind: kind,
    foreground: { r: sample.foreground.r, g: sample.foreground.g, b: sample.foreground.b, a: sample.foreground.a },
    effectiveOpacity: sample.opacity === undefined ? 1 : sample.opacity,
    effectiveBackground: dominantColor(pixels),
    notes,
  };
}

// What a naive reader sees: the colour of a semi-transparent layer alone, as if it were opaque.
export function naiveLayerRatio(layerRgb, foregroundRgb) {
  return contrastRatio(foregroundRgb, layerRgb);
}
