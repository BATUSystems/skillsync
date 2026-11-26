// Contrast and area of the focus indicator, computed from the screenshots of the same clip without and with focus
// (WCAG 1.4.11 and 2.4.13). Pure logic: no browser, no files; verdicts and modes are not decided here.
import { compositeOver, contrastRatio } from './a11y-contrast.mjs';

export const FOCUS_MIN_RATIO = 3;
const WHITE = Object.freeze({ r: 255, g: 255, b: 255 });
const CORNER_TOLERANCE_PX = 16;
const AREA_TOLERANCE = 0.05;

// Required area in CSS px for a box { width, height, borderRadius? } measured without focus: twice the perimeter of the
// rounded rectangle, with the radius cut to half of the shorter side. null when there is no usable rectangle or radius.
export function requiredArea(box) {
  if (!box) return null;
  const { width, height, borderRadius = 0 } = box;
  if (![width, height, borderRadius].every(Number.isFinite) || width <= 0 || height <= 0 || borderRadius < 0) return null;
  const r = Math.min(borderRadius, Math.min(width, height) / 2);
  return 2 * (2 * (width + height) - (8 - 2 * Math.PI) * r);
}

// The area that passes: the required one less a fixed allowance for corners and 5% for fractional sizes.
export function effectiveThreshold(area) {
  if (area === null) return null;
  return Math.max(0, area - CORNER_TOLERANCE_PX - AREA_TOLERANCE * area);
}

// Screenshots are opaque; a pixel with alpha is still placed on an opaque white background.
const pixelAt = (img, i) => {
  const o = i * 4;
  return compositeOver({ r: img.data[o], g: img.data[o + 1], b: img.data[o + 2], a: img.data[o + 3] / 255 }, WHITE);
};

// Contrast ratios (before against after) of the pixels a flagger marks as changed.
function changedRatios(flagger, before, after) {
  const ratios = [];
  const flags = flagger(before, after);
  for (let i = 0; i < flags.length; i++) if (flags[i]) ratios.push(contrastRatio(pixelAt(before, i), pixelAt(after, i)));
  return ratios;
}

const countQualifying = (ratios) => ratios.reduce((n, ratio) => n + (ratio >= FOCUS_MIN_RATIO ? 1 : 0), 0);

function median(sorted) {
  if (sorted.length === 0) return null;
  const half = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[half] : (sorted[half - 1] + sorted[half]) / 2;
}

// flaggerStrict ignores antialiasing (diffPx, qualifyingStrict, ratioMedian, ratioMin); flaggerAA counts it
// (qualifyingAA). box is the element without focus. null when the images differ in size.
export function focusMetrics({ flaggerStrict, flaggerAA }, before, after, box) {
  if (before.width !== after.width || before.height !== after.height) return null;
  const strict = changedRatios(flaggerStrict, before, after).sort((a, b) => a - b);
  const area = requiredArea(box);
  return {
    diffPx: strict.length,
    qualifyingStrict: countQualifying(strict),
    qualifyingAA: countQualifying(changedRatios(flaggerAA, before, after)),
    ratioMedian: median(strict),
    ratioMin: strict.length ? strict[0] : null,
    requiredArea: area,
    threshold: effectiveThreshold(area),
  };
}
