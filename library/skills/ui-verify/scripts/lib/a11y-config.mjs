// Validation of the a11y configuration (version 1). Errors name keys and positions, never quote the file content.
import { createHash } from 'node:crypto';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { checkOrigin, resolveInside, safeName, PolicyError } from './policy.mjs';
import { CHECKS, FOCUS_RULE_MODES } from './a11y-schema.mjs';
import { resolveThresholds } from './a11y-contrast.mjs';
import { DEFAULT_MIN_DIFF_PX, validateMouseConfig } from './a11y-focus.mjs';
import { DEFAULT_MAX_STOPS, DEFAULT_ORDER_TOLERANCE_PX } from './a11y-tab.mjs';

export class A11yConfigError extends Error {
  constructor(message, code = 'config') {
    super(message);
    this.name = 'A11yConfigError';
    this.code = code;
  }
}

const TOP_KEYS = new Set(['version', 'viewport', 'secretEnv', 'login', 'pages', 'thresholds', 'focus', 'tab', 'order', 'exceptionsFile']);
const PAGE_KEYS = new Set(['id', 'url', 'assert', 'checks', 'waitFor']);
const VIEWPORT_DEFAULT = Object.freeze({ width: 1280, height: 800, deviceScaleFactor: 1 });
const SCHEME_RE = /^[A-Za-z][A-Za-z0-9+.-]+:/;
const ENV_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const isText = (v) => typeof v === 'string' && v.trim() !== '';

// JSON text to a value; the parser message (which may quote the content) is never propagated.
export function parseConfigText(text) {
  try {
    return JSON.parse(String(text));
  } catch {
    throw new A11yConfigError('configuration is not valid JSON', 'json');
  }
}

export function loginEnvNames(raw) {
  const steps = isObj(raw) && isObj(raw.login) && Array.isArray(raw.login.steps) ? raw.login.steps : [];
  return steps.filter((s) => isObj(s) && isObj(s.fill) && isText(s.fill.valueEnv)).map((s) => s.fill.valueEnv);
}

export function missingLoginEnv(config, env) {
  if (!config.login) return [];
  const names = config.login.steps.filter((s) => s.fill && (typeof env?.[s.fill.valueEnv] !== 'string' || env[s.fill.valueEnv] === '')).map((s) => s.fill.valueEnv);
  return [...new Set(names)];
}

// Digest of the normalised configuration; absolute paths below the configuration directory are made relative.
// With Windows paths the separators of exceptionsFile become "/", so the digest is the same on every system.
export function configDigest(config, configDir = null, { pathImpl = path } = {}) {
  const canon = (v) => (Array.isArray(v) ? v.map(canon) : isObj(v)
    ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, canon(v[k])])) : v);
  const portable = pathImpl.sep === '\\' && typeof config?.exceptionsFile === 'string'
    ? { ...config, exceptionsFile: config.exceptionsFile.replace(/\\/g, '/') } : config;
  let text = JSON.stringify(canon(portable));
  if (configDir) {
    const dir = pathImpl.resolve(configDir);
    const forms = [pathToFileURL(dir).href, JSON.stringify(dir).slice(1, -1), dir.replace(/\\/g, '/')];
    for (const f of [...new Set(forms)].sort((a, b) => b.length - a.length)) text = text.split(f).join('<config-dir>');
  }
  return createHash('sha256').update(text, 'utf8').digest('hex').slice(0, 12);
}

// valueEnv: only names from UI_VERIFY_SECRET_ENV (comma list) or with the UI_VERIFY_ prefix; a configuration from elsewhere
// must not be able to read arbitrary environment variables. The error never quotes the name or value.
export function isAllowedValueEnv(name, env) {
  if (String(name).startsWith('UI_VERIFY_')) return true;
  return String((env && env.UI_VERIFY_SECRET_ENV) || '').split(',').map((n) => n.trim()).filter(Boolean).includes(name);
}

function resolveUrl(url, ctx, where, errors) {
  if (!isText(url)) { errors.push(`${where}.url: required non-empty string`); return url; }
  let target = url;
  if (!SCHEME_RE.test(url)) {
    try {
      target = pathToFileURL(resolveInside(ctx.configDir, url)).href;
    } catch {
      errors.push(`${where}.url: path is outside the configuration directory`);
      return url;
    }
  }
  const v = checkOrigin(target, { configDir: ctx.configDir, allow: ctx.allowOrigins });
  if (!v.ok) errors.push(`${where}.url: origin not allowed (${v.reason}); use --allow-origin for external hosts`);
  return target;
}

function validateViewport(v, errors) {
  const out = { ...VIEWPORT_DEFAULT };
  if (v === undefined) return out;
  if (!isObj(v)) { errors.push('viewport: expected object { width, height, deviceScaleFactor }'); return out; }
  for (const k of Object.keys(v)) {
    if (!Object.hasOwn(VIEWPORT_DEFAULT, k)) errors.push(`viewport.${k}: unknown key`);
    else if (k === 'deviceScaleFactor' && v[k] !== 1) errors.push('viewport.deviceScaleFactor: only 1 is supported (omit it)');
    else if (!isNum(v[k]) || v[k] <= 0) errors.push(`viewport.${k}: expected a positive number`);
    else out[k] = v[k];
  }
  return out;
}

function validateLogin(login, ctx, errors) {
  if (!isObj(login)) { errors.push('login: expected object { url, steps }'); return null; }
  for (const k of Object.keys(login)) if (k !== 'url' && k !== 'steps') errors.push(`login.${k}: unknown key`);
  const url = resolveUrl(login.url, ctx, 'login', errors);
  const steps = [];
  if (!Array.isArray(login.steps) || login.steps.length === 0) {
    errors.push('login.steps: expected a non-empty list');
  } else {
    login.steps.forEach((s, i) => {
      const w = `login.steps[${i}]`;
      if (!isObj(s) || Object.keys(s).length !== 1) { errors.push(`${w}: expected exactly one of { fill } or { click }`); return; }
      if (Object.hasOwn(s, 'click')) {
        if (!isText(s.click)) errors.push(`${w}.click: required non-empty selector`); else steps.push({ click: s.click });
      } else if (Object.hasOwn(s, 'fill')) {
        const f = s.fill;
        if (!isObj(f)) { errors.push(`${w}.fill: expected object { selector, valueEnv }`); return; }
        for (const k of Object.keys(f)) {
          if (k === 'value') errors.push(`${w}.fill.value: literal values are not allowed (a secret must not live in the configuration); use valueEnv`);
          else if (k !== 'selector' && k !== 'valueEnv') errors.push(`${w}.fill: unknown key "${k}"`);
        }
        if (!isText(f.selector)) errors.push(`${w}.fill.selector: required non-empty string`);
        if (!isText(f.valueEnv)) errors.push(`${w}.fill.valueEnv: required name of an environment variable`);
        else if (!isAllowedValueEnv(f.valueEnv, ctx.env)) errors.push(`${w}.fill.valueEnv: this variable is not allowed (only names listed in UI_VERIFY_SECRET_ENV or starting with UI_VERIFY_)`);
        steps.push({ fill: { selector: f.selector, valueEnv: f.valueEnv } });
      } else {
        errors.push(`${w}: unknown step (allowed: fill, click)`);
      }
    });
  }
  return { url, steps };
}

function validatePage(page, index, ctx, seen, errors) {
  const where = `pages[${index}]`;
  if (!isObj(page)) { errors.push(`${where}: expected object`); return page; }
  for (const k of Object.keys(page)) if (!PAGE_KEYS.has(k)) errors.push(`${where}.${k}: unknown key`);
  try {
    safeName(page.id);
    if (seen.has(page.id)) errors.push(`${where}.id: duplicate id`); else seen.add(page.id);
  } catch (e) {
    if (e instanceof PolicyError) errors.push(`${where}.id: expected [A-Za-z0-9._-]{1,120}`); else throw e;
  }
  const url = resolveUrl(page.url, ctx, where, errors);
  if (page.waitFor !== undefined && !isText(page.waitFor)) errors.push(`${where}.waitFor: expected a non-empty selector`);
  let assertion = null;
  if (page.assert !== undefined) {
    if (!isObj(page.assert)) {
      errors.push(`${where}.assert: expected object { selector, text? }`);
    } else {
      for (const k of Object.keys(page.assert)) if (k !== 'selector' && k !== 'text') errors.push(`${where}.assert.${k}: unknown key`);
      if (!isText(page.assert.selector)) errors.push(`${where}.assert.selector: required non-empty string`);
      if (page.assert.text !== undefined && typeof page.assert.text !== 'string') errors.push(`${where}.assert.text: expected a string`);
      assertion = { selector: page.assert.selector, ...(page.assert.text !== undefined ? { text: page.assert.text } : {}) };
    }
  }
  let checks = [...CHECKS];
  if (page.checks !== undefined) {
    if (Array.isArray(page.checks) && page.checks.every((c) => CHECKS.includes(c))) checks = [...new Set(page.checks)];
    else errors.push(`${where}.checks: expected a list drawn from ${CHECKS.join(', ')}`);
  }
  return { id: page.id, url, waitFor: page.waitFor ?? null, assert: assertion, checks };
}

function onlyKeys(obj, allowed, where, errors) {
  for (const k of Object.keys(obj)) if (!allowed.includes(k)) errors.push(`${where}.${k}: unknown key`);
}

export function validateA11yConfig(cfg, { configDir, allowOrigins = [], env = {} } = {}) {
  const errors = [];
  if (!isObj(cfg)) return { ok: false, errors: ['configuration: expected a JSON object'], config: null };
  for (const k of Object.keys(cfg)) if (!TOP_KEYS.has(k)) errors.push(`unknown key "${k}"`);
  if (cfg.version !== 1) errors.push('version: expected 1');
  const ctx = { configDir, allowOrigins, env };
  const config = {
    version: 1,
    viewport: validateViewport(cfg.viewport, errors),
    secretEnv: [],
    login: null,
    pages: [],
    thresholds: { normal: 4.5, large: 3.0 },
    focus: { minDiffPx: DEFAULT_MIN_DIFF_PX, mouse: null },
    tab: { maxStops: DEFAULT_MAX_STOPS, positiveTabindex: 'fail' },
    order: { mode: 'warn', tolerancePx: DEFAULT_ORDER_TOLERANCE_PX },
    exceptionsFile: null,
    warnings: [],
  };
  if (cfg.secretEnv !== undefined) {
    if (Array.isArray(cfg.secretEnv) && cfg.secretEnv.every((n) => isText(n) && ENV_RE.test(n))) config.secretEnv = [...cfg.secretEnv];
    else errors.push('secretEnv: expected a list of environment variable names');
  }
  if (cfg.login !== undefined) config.login = validateLogin(cfg.login, ctx, errors);
  if (!Array.isArray(cfg.pages) || cfg.pages.length === 0) {
    errors.push('pages: expected a non-empty list');
  } else {
    const seen = new Set();
    config.pages = cfg.pages.map((p, i) => validatePage(p, i, ctx, seen, errors));
  }
  if (cfg.thresholds !== undefined) {
    const t = resolveThresholds(cfg.thresholds);
    errors.push(...t.errors);
    config.thresholds = t.thresholds;
    config.warnings.push(...t.warnings);
  }
  if (cfg.focus !== undefined) {
    if (!isObj(cfg.focus)) {
      errors.push('focus: expected object { minDiffPx, mouse, contrast, appearance }');
    } else {
      onlyKeys(cfg.focus, ['minDiffPx', 'mouse', 'contrast', 'appearance'], 'focus', errors);
      if (cfg.focus.minDiffPx !== undefined) {
        if (Number.isInteger(cfg.focus.minDiffPx) && cfg.focus.minDiffPx >= 1) config.focus.minDiffPx = cfg.focus.minDiffPx;
        else errors.push('focus.minDiffPx: expected an integer >= 1');
      }
      const m = validateMouseConfig(cfg.focus.mouse);
      errors.push(...m.errors);
      config.focus.mouse = m.mouse;
      // no defaults here: a configuration without these keys keeps its digest; readers treat a missing key as "off"
      for (const key of ['contrast', 'appearance']) {
        if (cfg.focus[key] === undefined) continue;
        if (FOCUS_RULE_MODES.includes(cfg.focus[key])) config.focus[key] = cfg.focus[key];
        else errors.push(`focus.${key}: expected "off", "report" or "fail"`);
      }
    }
  }
  if (cfg.tab !== undefined) {
    if (!isObj(cfg.tab)) {
      errors.push('tab: expected object { maxStops, positiveTabindex }');
    } else {
      onlyKeys(cfg.tab, ['maxStops', 'positiveTabindex'], 'tab', errors);
      if (cfg.tab.maxStops !== undefined) {
        if (Number.isInteger(cfg.tab.maxStops) && cfg.tab.maxStops >= 1 && cfg.tab.maxStops <= DEFAULT_MAX_STOPS) config.tab.maxStops = cfg.tab.maxStops;
        else errors.push(`tab.maxStops: expected an integer 1..${DEFAULT_MAX_STOPS}`);
      }
      if (cfg.tab.positiveTabindex !== undefined) {
        if (['fail', 'warn'].includes(cfg.tab.positiveTabindex)) config.tab.positiveTabindex = cfg.tab.positiveTabindex;
        else errors.push('tab.positiveTabindex: expected "fail" or "warn"');
      }
    }
  }
  if (cfg.order !== undefined) {
    if (!isObj(cfg.order)) {
      errors.push('order: expected object { mode, tolerancePx }');
    } else {
      onlyKeys(cfg.order, ['mode', 'tolerancePx'], 'order', errors);
      if (cfg.order.mode !== undefined) {
        if (['warn', 'fail'].includes(cfg.order.mode)) config.order.mode = cfg.order.mode;
        else errors.push('order.mode: expected "warn" or "fail"');
      }
      if (cfg.order.tolerancePx !== undefined) {
        if (isNum(cfg.order.tolerancePx) && cfg.order.tolerancePx >= 0) config.order.tolerancePx = cfg.order.tolerancePx;
        else errors.push('order.tolerancePx: expected a number >= 0');
      }
    }
  }
  if (cfg.exceptionsFile !== undefined) {
    if (!isText(cfg.exceptionsFile)) {
      errors.push('exceptionsFile: expected a path relative to the configuration directory');
    } else {
      try {
        config.exceptionsFile = resolveInside(configDir, cfg.exceptionsFile);
      } catch {
        errors.push('exceptionsFile: path is outside the configuration directory');
      }
    }
  }
  return { ok: errors.length === 0, errors, config: errors.length === 0 ? config : null };
}
