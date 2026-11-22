// Czysta logika polecenia `measure`: walidacja konfiguracji, pomiar par (pudelko, style, ksztalt), raport z maskowaniem.
// Bez przegladarki i bez PNG: adapter zwraca pomiary i obrazy RGBA { width, height, data }.
import { pathToFileURL } from 'node:url';
import { applyDeviations, compareBox, compareNumber, compareStyles } from './compare.mjs';
import { checkOrigin, resolveInside } from './policy.mjs';
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

const TOP_KEYS = new Set(['version', 'viewport', 'tolerancePx', 'sizeTolerancePx', 'colorTolerance', 'maxPct', 'styleProps', 'secretEnv', 'pairs']);
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
  if (typeof pair.id !== 'string' || !ID_RE.test(pair.id)) {
    errors.push(`${where}.id: expected [A-Za-z0-9._-]{1,120}`);
  } else if (seen.has(pair.id)) {
    errors.push(`${where}.id: duplicate id "${pair.id}"`);
  } else {
    seen.add(pair.id);
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
        if (!isText(d.prop)) errors.push(`${w}.prop: required non-empty string`);
        if (!isText(d.reason)) errors.push(`${w}.reason: required non-empty string (every approved deviation needs a reason)`);
        deviations.push({ ...d });
      });
    }
  }
  const masks = [];
  if (pair.masks !== undefined) {
    if (!Array.isArray(pair.masks)) errors.push(`${where}.masks: expected a list`);
    else {
      pair.masks.forEach((m, i) => {
        const w = `${where}.masks[${i}]`;
        if (!isObj(m) || !isNum(m.x) || !isNum(m.y) || !isNonNeg(m.width) || !isNonNeg(m.height)) {
          errors.push(`${w}: expected { x, y, width >= 0, height >= 0 } as numbers`);
          return;
        }
        masks.push({ x: m.x, y: m.y, width: m.width, height: m.height });
      });
    }
  }
  return { id: pair.id, reference, candidate, deviations, masks };
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
    styleProps: [...DEFAULTS.styleProps],
    secretEnv: [],
    pairs: [],
  };
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
    return {
      id: pair.id,
      verdict: 'UNVERIFIED',
      reason: 'element-not-found',
      missing,
      measure: { verdict: 'UNVERIFIED', reason: 'element-not-found', unapproved: [], approved: [] },
      shape: { verdict: 'SKIPPED', reason: 'element-not-found' },
    };
  }

  const boxCmp = compareBox(cand.box, ref.box, config.tolerancePx);
  const expectedStyles = {};
  for (const p of config.styleProps) if (ref.styles && p in ref.styles) expectedStyles[p] = ref.styles[p];
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
    const r = diffFn(refImg, candImg, { cropToCommon: true, maxPct: config.maxPct, masks: pair.masks });
    shape = { verdict: r.verdict, pct: r.pct, diffCount: r.diffCount, total: r.total, cropped: r.cropped === true };
    diff = { reference: refImg, candidate: candImg, flags: r.flags };
  }
  const out = {
    id: pair.id,
    verdict: combine([measure.verdict, shape.verdict].filter((v) => v !== 'SKIPPED')),
    measure,
    shape,
  };
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
