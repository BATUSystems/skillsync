// Czysta logika polecenia `measure`: walidacja konfiguracji, pomiar par (pudelko, style, ksztalt), raport z maskowaniem.
// Bez przegladarki i bez PNG: adapter zwraca pomiary i obrazy RGBA { width, height, data }.
import { pathToFileURL } from 'node:url';
import { applyDeviations, compareBox, compareNumber, compareStyles } from './compare.mjs';
import { checkOrigin, PolicyError, resolveInside } from './policy.mjs';
import { combine, exitFor } from './verdict.mjs';

export const DEFAULTS = Object.freeze({
  viewport: Object.freeze({ width: 400, height: 200, deviceScaleFactor: 1 }),
  tolerancePx: 1,
  sizeTolerancePx: 1,
  colorTolerance: 0,
  maxPct: 0.5,
  styleProps: Object.freeze(['color', 'backgroundColor', 'borderTopColor', 'borderTopLeftRadius', 'fontSize', 'fontFamily']),
});

export class MeasureError extends Error {
  constructor(message, code, cause) {
    super(message);
    this.name = 'MeasureError';
    this.code = code;
    if (cause !== undefined) this.cause = cause;
  }
}

const TOP_KEYS = new Set(['version', 'viewport', 'tolerancePx', 'sizeTolerancePx', 'colorTolerance', 'maxPct', 'shiftPx', 'shapeAlign', 'styleProps', 'secretEnv', 'pairs']);
const PAIR_KEYS = new Set(['id', 'reference', 'candidate', 'deviations', 'masks', 'masksReference', 'masksCandidate', 'acceptShape']);
const SIDE_KEYS = new Set(['url', 'selector']);
const DEVIATION_KEYS = new Set(['prop', 'reason', 'actual', 'expected', 'selector']);
const MASK_KEYS = new Set(['x', 'y', 'width', 'height']);
const ALIGNS = new Set(['center', 'start']);

function unknownKeys(obj, allowed, where, errors) {
  for (const k of Object.keys(obj)) if (!allowed.has(k)) errors.push(`${where}.${k}: unknown key`);
}
const ID_RE = /^[A-Za-z0-9._-]{1,120}$/;
const SCHEME_RE = /^[A-Za-z][A-Za-z0-9+.-]+:/;

const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const isNonNeg = (v) => isNum(v) && v >= 0;
const isText = (v) => typeof v === 'string' && v.trim() !== '';

function resolveUrl(url, { configDir, allowOrigins }, where, errors) {
  if (!isText(url)) {
    errors.push(`${where}.url: required non-empty string`);
    return url;
  }
  let target = url;
  if (!SCHEME_RE.test(url)) {
    try {
      target = pathToFileURL(resolveInside(configDir, url)).href;
    } catch {
      errors.push(`${where}.url: path "${url}" is outside the configuration directory`);
      return url;
    }
  }
  const verdict = checkOrigin(target, { configDir, allow: allowOrigins });
  if (!verdict.ok) {
    errors.push(`${where}.url: origin not allowed (${verdict.reason}); use --allow-origin for external hosts`);
    return url;
  }
  return target;
}

function validateSide(side, ctx, where, errors) {
  if (!isObj(side)) {
    errors.push(`${where}: expected object { url, selector }`);
    return side;
  }
  unknownKeys(side, SIDE_KEYS, where, errors);
  const url = resolveUrl(side.url, ctx, where, errors);
  if (!isText(side.selector)) errors.push(`${where}.selector: required non-empty string`);
  return { url, selector: side.selector };
}

function validateNumberField(cfg, key, errors) {
  if (cfg[key] === undefined) return DEFAULTS[key];
  if (!isNonNeg(cfg[key])) {
    errors.push(`${key}: expected a non-negative number`);
    return DEFAULTS[key];
  }
  return cfg[key];
}

function validateViewport(v, errors) {
  if (v === undefined) return { ...DEFAULTS.viewport };
  if (!isObj(v)) {
    errors.push('viewport: expected object { width, height, deviceScaleFactor }');
    return { ...DEFAULTS.viewport };
  }
  const out = { ...DEFAULTS.viewport };
  for (const k of Object.keys(v)) {
    if (!(k in DEFAULTS.viewport)) errors.push(`viewport.${k}: unknown key`);
    else if (!isNum(v[k]) || v[k] <= 0) errors.push(`viewport.${k}: expected a positive number`);
    else out[k] = v[k];
  }
  return out;
}

function validatePair(pair, index, ctx, seen, errors) {
  const where = `pairs[${index}]`;
  if (!isObj(pair)) {
    errors.push(`${where}: expected object`);
    return pair;
  }
  unknownKeys(pair, PAIR_KEYS, where, errors);
  if (typeof pair.id !== 'string' || !ID_RE.test(pair.id) || pair.id === '.' || pair.id === '..') {
    errors.push(`${where}.id: expected [A-Za-z0-9._-]{1,120}, not "." or ".."`);
  } else if (seen.has(pair.id.toLowerCase())) {
    errors.push(`${where}.id: duplicate id "${pair.id}" (ids are compared case-insensitively)`);
  } else {
    seen.add(pair.id.toLowerCase());
  }
  const reference = validateSide(pair.reference, ctx, `${where}.reference`, errors);
  const candidate = validateSide(pair.candidate, ctx, `${where}.candidate`, errors);

  const deviations = [];
  if (pair.deviations !== undefined) {
    if (!Array.isArray(pair.deviations)) errors.push(`${where}.deviations: expected a list`);
    else {
      pair.deviations.forEach((d, i) => {
        const w = `${where}.deviations[${i}]`;
        if (!isObj(d)) { errors.push(`${w}: expected object`); return; }
        unknownKeys(d, DEVIATION_KEYS, w, errors);
        if (!isText(d.prop)) errors.push(`${w}.prop: required non-empty string`);
        if (!isText(d.reason)) errors.push(`${w}.reason: required non-empty string (every approved deviation needs a reason)`);
        deviations.push({ ...d });
      });
    }
  }
  const rects = (key) => {
    const out = [];
    if (pair[key] === undefined) return out;
    if (!Array.isArray(pair[key])) {
      errors.push(`${where}.${key}: expected a list`);
      return out;
    }
    pair[key].forEach((m, i) => {
      const w = `${where}.${key}[${i}]`;
      if (isObj(m)) unknownKeys(m, MASK_KEYS, w, errors);
      if (!isObj(m) || !isNum(m.x) || !isNum(m.y) || !isNonNeg(m.width) || !isNonNeg(m.height)) {
        errors.push(`${w}: expected { x, y, width >= 0, height >= 0 } as numbers`);
        return;
      }
      out.push({ x: m.x, y: m.y, width: m.width, height: m.height });
    });
    return out;
  };
  const masks = rects('masks');
  const masksReference = rects('masksReference');
  const masksCandidate = rects('masksCandidate');
  let acceptShape = null;
  if (pair.acceptShape !== undefined) {
    const w = `${where}.acceptShape`;
    if (!isObj(pair.acceptShape)) {
      errors.push(`${w}: expected object { reason } with a non-empty reason`);
    } else {
      unknownKeys(pair.acceptShape, new Set(['reason']), w, errors);
      if (!isText(pair.acceptShape.reason)) errors.push(`${w}.reason: required non-empty string (an accepted shape deviation needs a reason)`);
      else acceptShape = { reason: pair.acceptShape.reason };
    }
  }
  return { id: pair.id, reference, candidate, deviations, masks, masksReference, masksCandidate, acceptShape };
}

export function validateConfig(cfg, { configDir, allowOrigins = [] } = {}) {
  const errors = [];
  if (!isObj(cfg)) return { ok: false, errors: ['configuration: expected a JSON object'], config: null };
  for (const k of Object.keys(cfg)) if (!TOP_KEYS.has(k)) errors.push(`unknown key "${k}"`);
  if (cfg.version !== 1) errors.push('version: expected 1');
  const config = {
    version: 1,
    viewport: validateViewport(cfg.viewport, errors),
    tolerancePx: validateNumberField(cfg, 'tolerancePx', errors),
    sizeTolerancePx: validateNumberField(cfg, 'sizeTolerancePx', errors),
    colorTolerance: validateNumberField(cfg, 'colorTolerance', errors),
    maxPct: validateNumberField(cfg, 'maxPct', errors),
    shiftPx: 1,
    shapeAlign: 'center',
    styleProps: [...DEFAULTS.styleProps],
    secretEnv: [],
    pairs: [],
  };
  config.shiftPx = Math.ceil(config.viewport.deviceScaleFactor);
  if (cfg.shiftPx !== undefined) {
    if (Number.isInteger(cfg.shiftPx) && cfg.shiftPx >= 0) config.shiftPx = cfg.shiftPx;
    else errors.push('shiftPx: expected an integer >= 0 (screen pixels)');
  }
  if (cfg.shapeAlign !== undefined) {
    if (typeof cfg.shapeAlign === 'string' && ALIGNS.has(cfg.shapeAlign)) config.shapeAlign = cfg.shapeAlign;
    else errors.push('shapeAlign: expected "center" or "start"');
  }
  if (cfg.styleProps !== undefined) {
    if (Array.isArray(cfg.styleProps) && cfg.styleProps.every(isText)) config.styleProps = [...cfg.styleProps];
    else errors.push('styleProps: expected a list of non-empty strings');
  }
  if (cfg.secretEnv !== undefined) {
    if (Array.isArray(cfg.secretEnv) && cfg.secretEnv.every(isText)) config.secretEnv = [...cfg.secretEnv];
    else errors.push('secretEnv: expected a list of environment variable names');
  }
  if (!Array.isArray(cfg.pairs) || cfg.pairs.length === 0) {
    errors.push('pairs: expected a non-empty list');
  } else {
    const ctx = { configDir, allowOrigins };
    const seen = new Set();
    config.pairs = cfg.pairs.map((p, i) => validatePair(p, i, ctx, seen, errors));
  }
  return { ok: errors.length === 0, errors, config: errors.length === 0 ? config : null };
}

// Odstepstwo z selektorem dotyczy stron pary; findingi pomiaru nie niosa selektora, wiec pasujacy selektor jest zdejmowany.
function normalizeDeviations(deviations, pair) {
  return (deviations || []).map((d) => {
    if (d.selector === undefined) return d;
    if (d.selector === pair.candidate.selector || d.selector === pair.reference.selector) {
      const { selector, ...rest } = d;
      return rest;
    }
    return d;
  });
}

function approve(findings, deviations) {
  let remaining = findings;
  const approved = [];
  for (const dev of deviations) {
    const r = applyDeviations(remaining, [dev]);
    for (const f of r.approved) approved.push({ ...f, reason: dev.reason });
    remaining = r.unapproved;
  }
  return { unapproved: remaining, approved };
}

function hiddenProp(obj, name, value) {
  Object.defineProperty(obj, name, { value, enumerable: false, writable: true, configurable: true });
}

async function viaAdapter(fn) {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof PolicyError) throw e;
    throw new MeasureError(`adapter: ${e && e.message ? e.message : e}`, 'adapter', e);
  }
}

async function measurePair(pair, { config, adapter, diffFn }) {
  const ref = await viaAdapter(() => adapter.measure(pair.reference));
  const cand = await viaAdapter(() => adapter.measure(pair.candidate));
  const missing = [];
  if (!ref || ref.found !== true) missing.push('reference');
  if (!cand || cand.found !== true) missing.push('candidate');
  if (missing.length) {
    const reason = [ref, cand].some((m) => m && m.reason === 'page-state-mismatch') ? 'page-state-mismatch' : 'element-not-found';
    return {
      id: pair.id,
      verdict: 'UNVERIFIED',
      reason,
      missing,
      measure: { verdict: 'UNVERIFIED', reason, unapproved: [], approved: [] },
      shape: { verdict: 'SKIPPED', reason },
    };
  }

  // Wlasciwosc bez wartosci we wzorcu (literowka w styleProps) dawalaby cichy PASS: to blad konfiguracji.
  for (const p of config.styleProps) {
    const v = ref.styles ? ref.styles[p] : undefined;
    if (v === undefined || v === null || String(v).trim() === '') {
      throw new MeasureError(`styleProps: property "${p}" has no value on the reference element (typo or unsupported property)`, 'config');
    }
  }

  const boxCmp = compareBox(cand.box, ref.box, config.tolerancePx);
  const expectedStyles = {};
  for (const p of config.styleProps) expectedStyles[p] = ref.styles[p];
  const styleCmp = compareStyles(cand.styles, expectedStyles, { tolerancePx: config.tolerancePx, colorTolerance: config.colorTolerance });
  const { unapproved, approved } = approve([...boxCmp.findings, ...styleCmp.findings], normalizeDeviations(pair.deviations, pair));
  const measure = { verdict: unapproved.length === 0 ? 'PASS' : 'FAIL', unapproved, approved };

  const sizeOk = ['width', 'height'].every((k) => compareNumber(cand.box[k], ref.box[k], config.sizeTolerancePx).pass);
  let shape;
  let diff = null;
  if (!sizeOk) {
    shape = { verdict: 'SKIPPED', reason: 'box-mismatch' };
  } else {
    const refImg = await viaAdapter(() => adapter.screenshot(pair.reference));
    const candImg = await viaAdapter(() => adapter.screenshot(pair.candidate));
    const masks = [...(pair.masks || []), ...(pair.masksReference || []), ...(pair.masksCandidate || [])];
    const r = diffFn(refImg, candImg, {
      cropToCommon: true,
      maxPct: config.maxPct,
      masks,
      shiftPx: config.shiftPx,
      shiftRadius: config.shiftPx,
      align: config.shapeAlign,
    });
    shape = { verdict: r.verdict, pct: r.pct, diffCount: r.diffCount, total: r.total, cropped: r.cropped === true };
    diff = { reference: refImg, candidate: candImg, flags: r.flags };
  }
  // Odstepstwo ksztaltu jako dane: FAIL albo SKIPPED ksztaltu staje sie ACCEPTED (liczone jak PASS) z powodem w raporcie.
  if (pair.acceptShape && (shape.verdict === 'FAIL' || shape.verdict === 'SKIPPED')) {
    shape = { ...shape, verdict: 'ACCEPTED', acceptedFrom: shape.verdict === 'FAIL' ? 'FAIL' : shape.reason, reason: pair.acceptShape.reason };
  }
  const SHAPE_VERDICT = { PASS: 'PASS', ACCEPTED: 'PASS', FAIL: 'FAIL', SKIPPED: 'UNVERIFIED' };
  const verdict = combine([measure.verdict, SHAPE_VERDICT[shape.verdict] ?? 'UNVERIFIED']);
  const out = { id: pair.id, verdict, measure, shape };
  if (verdict === 'UNVERIFIED' && shape.verdict === 'SKIPPED') out.reason = 'shape-skipped';
  if (diff) hiddenProp(out, 'diff', diff);
  return out;
}

export async function runMeasure({ config, adapter, diffFn }) {
  const pairs = [];
  for (const pair of config.pairs) pairs.push(await measurePair(pair, { config, adapter, diffFn }));
  const verdict = combine(pairs.map((p) => p.verdict));
  return { verdict, pairs, exit: exitFor(verdict) };
}

const fmt = (v) => (typeof v === 'number' ? String(Number(v.toFixed(4))) : String(v));

function findingLine(prefix, f) {
  const delta = f.delta !== undefined && Number.isFinite(f.delta) ? ` delta ${fmt(f.delta)}` : '';
  const reason = f.reason ? ` (reason: ${f.reason})` : '';
  return `  ${prefix} ${f.prop} actual ${fmt(f.actual)} expected ${fmt(f.expected)}${delta}${reason}`;
}

export function formatReport(result, masker) {
  const lines = [];
  for (const p of result.pairs) {
    lines.push(`${p.verdict} ${p.id}${p.reason ? ` ${p.reason}` : ''}`);
    if (p.missing) lines.push(`  missing element: ${p.missing.join(', ')}`);
    for (const f of p.measure.unapproved) lines.push(findingLine('diff', f));
    for (const f of p.measure.approved) lines.push(findingLine('approved', f));
    if (p.shape.verdict === 'SKIPPED') {
      lines.push(`  shape SKIPPED (${p.shape.reason})`);
    } else if (p.shape.verdict === 'ACCEPTED') {
      lines.push(`  shape ACCEPTED (reason: ${p.shape.reason}; was ${p.shape.acceptedFrom})`);
    } else {
      lines.push(`  shape ${p.shape.verdict} ${fmt(p.shape.pct)}%${p.shape.cropped ? ' cropped' : ''}`);
    }
  }
  lines.push(`result ${result.verdict} (exit ${result.exit})`);
  return masker.mask(`${lines.join('\n')}\n`);
}

export function toJson(result, masker) {
  const plain = {
    verdict: result.verdict,
    exit: result.exit,
    pairs: result.pairs.map((p) => ({
      id: p.id,
      verdict: p.verdict,
      ...(p.reason ? { reason: p.reason } : {}),
      ...(p.missing ? { missing: p.missing } : {}),
      measure: p.measure,
      shape: p.shape,
    })),
  };
  return masker.maskValue(JSON.parse(JSON.stringify(plain)));
}
