// Czysta logika polecenia `capture`: walidacja konfiguracji, macierz stron x szerokosci x trybow, asercja pozytywna, manifest i raport.
// Bez przegladarki i bez PNG: adapter zwraca bajty zrzutu i informacje o asercji; zapis plikow robi powloka.
import { pathToFileURL } from 'node:url';
import { checkOrigin, PolicyError, resolveInside, safeName } from './policy.mjs';
import { combine, exitFor } from './verdict.mjs';

export const DEFAULTS = Object.freeze({
  viewport: Object.freeze({ width: 1280, height: 800, deviceScaleFactor: 1 }),
  modes: Object.freeze(['light']),
  minWidth: 200,
  maxWidth: 4000,
});

export class CaptureError extends Error {
  constructor(message, code, cause) {
    super(message);
    this.name = 'CaptureError';
    this.code = code;
    if (cause !== undefined) this.cause = cause;
  }
}

const TOP_KEYS = new Set(['version', 'viewport', 'widths', 'modes', 'secretEnv', 'assertTimeoutMs', 'login', 'pages']);
const PAGE_KEYS = new Set(['id', 'url', 'waitFor', 'assert', 'assertTimeoutMs', 'masks', 'fullPage']);
const REASON_PAGE_STATE = 'page-state-mismatch';
const isTimeout = (v) => Number.isInteger(v) && v >= 0 && v <= 60000;
const MODES = new Set(['light', 'dark']);
const ID_RE = /^[A-Za-z0-9._-]{1,120}$/;
const SCHEME_RE = /^[A-Za-z][A-Za-z0-9+.-]+:/;
const SHOT_NAME_RE = /^[A-Za-z0-9._-]{1,120}__\d{1,5}-(?:light|dark)\.png$/;
const ASSERTIONS = new Set(['PASS', 'FAIL', 'NONE']);

const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const isText = (v) => typeof v === 'string' && v.trim() !== '';

export function shotName(pageId, width, mode) {
  return `${safeName(pageId)}__${width}-${mode}.png`;
}

export function isShotName(name) {
  return typeof name === 'string' && SHOT_NAME_RE.test(name);
}

// Nazwy zmiennych srodowiska z `login.steps[].fill.valueEnv` (tolerancyjnie, na surowej konfiguracji przed walidacja).
export function loginEnvNames(raw) {
  const steps = raw && isObj(raw) && isObj(raw.login) && Array.isArray(raw.login.steps) ? raw.login.steps : [];
  const out = [];
  for (const s of steps) {
    if (isObj(s) && isObj(s.fill) && isText(s.fill.valueEnv)) out.push(s.fill.valueEnv);
  }
  return out;
}

// Zmienne valueEnv, ktorych brak w srodowisku (brak = blad konfiguracji, nie UNVERIFIED).
export function missingLoginEnv(config, env) {
  if (!config.login) return [];
  const names = [];
  for (const s of config.login.steps) {
    if (s.fill && (typeof env?.[s.fill.valueEnv] !== 'string' || env[s.fill.valueEnv] === '')) names.push(s.fill.valueEnv);
  }
  return [...new Set(names)];
}

// valueEnv: tylko nazwy z UI_VERIFY_SECRET_ENV (lista po przecinku) albo z prefiksem UI_VERIFY_; konfiguracja z obcego repo
// nie moze wyczytac dowolnych zmiennych srodowiska.
function isAllowedValueEnv(name, env) {
  if (name.startsWith('UI_VERIFY_')) return true;
  return String((env && env.UI_VERIFY_SECRET_ENV) || '').split(',').map((n) => n.trim()).filter(Boolean).includes(name);
}

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

function validateLogin(login, ctx, errors) {
  if (!isObj(login)) {
    errors.push('login: expected object { url, steps }');
    return null;
  }
  for (const k of Object.keys(login)) if (k !== 'url' && k !== 'steps') errors.push(`login.${k}: unknown key`);
  const url = resolveUrl(login.url, ctx, 'login', errors);
  const steps = [];
  if (!Array.isArray(login.steps) || login.steps.length === 0) {
    errors.push('login.steps: expected a non-empty list');
  } else {
    login.steps.forEach((s, i) => {
      const w = `login.steps[${i}]`;
      if (!isObj(s)) {
        errors.push(`${w}: expected exactly one of { fill } or { click }`);
        return;
      }
      for (const k of Object.keys(s)) if (k !== 'fill' && k !== 'click') errors.push(`${w}.${k}: unknown key`);
      if (['fill', 'click'].filter((k) => k in s).length !== 1) {
        if (!Object.keys(s).some((k) => k !== 'fill' && k !== 'click')) errors.push(`${w}: expected exactly one of { fill } or { click }`);
        return;
      }
      if ('click' in s) {
        if (!isText(s.click)) errors.push(`${w}.click: required non-empty selector`);
        else steps.push({ click: s.click });
      } else if ('fill' in s) {
        const f = s.fill;
        if (!isObj(f)) {
          errors.push(`${w}.fill: expected object { selector, valueEnv }`);
          return;
        }
        for (const k of Object.keys(f)) {
          if (k === 'value') errors.push(`${w}.fill.value: literal values are not allowed (a secret must not live in the configuration); use valueEnv`);
          else if (k !== 'selector' && k !== 'valueEnv') errors.push(`${w}.fill: unknown key "${k}"`);
        }
        if (!isText(f.selector)) errors.push(`${w}.fill.selector: required non-empty string`);
        if (!isText(f.valueEnv)) errors.push(`${w}.fill.valueEnv: required name of an environment variable`);
        else if (!isAllowedValueEnv(f.valueEnv, ctx.env)) {
          errors.push(`${w}.fill.valueEnv: variable "${f.valueEnv}" is not allowed (only names listed in UI_VERIFY_SECRET_ENV or starting with UI_VERIFY_)`);
        }
        steps.push({ fill: { selector: f.selector, valueEnv: f.valueEnv } });
      } else {
        errors.push(`${w}: unknown step (allowed: fill, click)`);
      }
    });
  }
  return { url, steps };
}

function validateMask(m, where, errors) {
  if (!isObj(m)) {
    errors.push(`${where}: expected { selector } or { x, y, width, height }`);
    return null;
  }
  if ('selector' in m) {
    for (const k of Object.keys(m)) if (k !== 'selector') errors.push(`${where}.${k}: unknown key (a selector mask has only "selector")`);
    if (!isText(m.selector)) {
      errors.push(`${where}.selector: required non-empty string`);
      return null;
    }
    return { selector: m.selector };
  }
  for (const k of Object.keys(m)) if (!['x', 'y', 'width', 'height'].includes(k)) errors.push(`${where}.${k}: unknown key`);
  if (!isNum(m.x) || !isNum(m.y) || !isNum(m.width) || !isNum(m.height) || m.width <= 0 || m.height <= 0) {
    errors.push(`${where}: expected { selector } or { x, y, width > 0, height > 0 } as numbers`);
    return null;
  }
  return { x: m.x, y: m.y, width: m.width, height: m.height };
}

function validatePage(page, index, ctx, seen, errors) {
  const where = `pages[${index}]`;
  if (!isObj(page)) {
    errors.push(`${where}: expected object`);
    return page;
  }
  for (const k of Object.keys(page)) if (!PAGE_KEYS.has(k)) errors.push(`${where}.${k}: unknown key`);
  if (typeof page.id !== 'string' || !ID_RE.test(page.id) || page.id === '.' || page.id === '..') {
    errors.push(`${where}.id: expected [A-Za-z0-9._-]{1,120}`);
  } else if (seen.has(page.id.toLowerCase())) {
    errors.push(`${where}.id: duplicate id "${page.id}" (ids are compared case-insensitively)`);
  } else {
    seen.add(page.id.toLowerCase());
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
  const masks = [];
  if (page.masks !== undefined) {
    if (!Array.isArray(page.masks)) errors.push(`${where}.masks: expected a list`);
    else page.masks.forEach((m, i) => { const v = validateMask(m, `${where}.masks[${i}]`, errors); if (v) masks.push(v); });
  }
  if (page.fullPage !== undefined && typeof page.fullPage !== 'boolean') errors.push(`${where}.fullPage: expected true or false`);
  if (page.assertTimeoutMs !== undefined && !isTimeout(page.assertTimeoutMs)) errors.push(`${where}.assertTimeoutMs: expected an integer 0..60000 (milliseconds)`);
  return {
    id: page.id,
    url,
    waitFor: page.waitFor ?? null,
    assert: assertion,
    masks,
    fullPage: page.fullPage === true,
    assertTimeoutMs: isTimeout(page.assertTimeoutMs) ? page.assertTimeoutMs : null,
  };
}

export function validateCaptureConfig(cfg, { configDir, allowOrigins = [], env = {} } = {}) {
  const errors = [];
  if (!isObj(cfg)) return { ok: false, errors: ['configuration: expected a JSON object'], config: null };
  for (const k of Object.keys(cfg)) if (!TOP_KEYS.has(k)) errors.push(`unknown key "${k}"`);
  if (cfg.version !== 1) errors.push('version: expected 1');
  const viewport = validateViewport(cfg.viewport, errors);
  const config = { version: 1, viewport, widths: [viewport.width], modes: [...DEFAULTS.modes], secretEnv: [], assertTimeoutMs: null, login: null, pages: [] };
  if (cfg.widths !== undefined) {
    const ok = Array.isArray(cfg.widths) && cfg.widths.length > 0
      && cfg.widths.every((w) => Number.isInteger(w) && w >= DEFAULTS.minWidth && w <= DEFAULTS.maxWidth);
    if (ok) config.widths = [...new Set(cfg.widths)];
    else errors.push(`widths: expected a non-empty list of integers ${DEFAULTS.minWidth}..${DEFAULTS.maxWidth}`);
  }
  if (cfg.modes !== undefined) {
    if (Array.isArray(cfg.modes) && cfg.modes.length > 0 && cfg.modes.every((m) => MODES.has(m))) config.modes = [...new Set(cfg.modes)];
    else errors.push('modes: expected a non-empty list of "light" or "dark"');
  }
  if (cfg.secretEnv !== undefined) {
    if (Array.isArray(cfg.secretEnv) && cfg.secretEnv.every(isText)) config.secretEnv = [...cfg.secretEnv];
    else errors.push('secretEnv: expected a list of environment variable names');
  }
  if (cfg.assertTimeoutMs !== undefined) {
    if (isTimeout(cfg.assertTimeoutMs)) config.assertTimeoutMs = cfg.assertTimeoutMs;
    else errors.push('assertTimeoutMs: expected an integer 0..60000 (milliseconds)');
  }
  const ctx = { configDir, allowOrigins, env };
  if (cfg.login !== undefined) config.login = validateLogin(cfg.login, ctx, errors);
  if (!Array.isArray(cfg.pages) || cfg.pages.length === 0) {
    errors.push('pages: expected a non-empty list');
  } else {
    const seen = new Set();
    config.pages = cfg.pages.map((p, i) => validatePage(p, i, ctx, seen, errors));
  }
  return { ok: errors.length === 0, errors, config: errors.length === 0 ? config : null };
}

function hiddenProp(obj, name, value) {
  Object.defineProperty(obj, name, { value, enumerable: false, writable: true, configurable: true });
}

async function viaAdapter(fn) {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof PolicyError) throw e;
    throw new CaptureError(`adapter: ${e && e.message ? e.message : e}`, 'adapter', e);
  }
}

const SHOT_VERDICT = { PASS: 'PASS', FAIL: 'FAIL', NONE: 'UNVERIFIED' };
const SHOT_REASON = { FAIL: 'assertion-failed', NONE: 'no-assertion' };

export async function runCapture({ config, adapter, env = {} }) {
  if (config.login && typeof adapter.login === 'function') {
    await viaAdapter(() => adapter.login({ login: config.login, env }));
  }
  const shots = [];
  for (const page of config.pages) {
    for (const width of config.widths) {
      for (const mode of config.modes) {
        const r = await viaAdapter(() => adapter.capture({ page, width, mode }));
        if (!r || !(r.png instanceof Uint8Array)) throw new CaptureError('adapter: capture returned no PNG bytes', 'adapter');
        const mismatch = r.stateMismatch === true;
        const assertion = mismatch ? 'NONE' : page.assert ? (r.assertionFound === true ? 'PASS' : 'FAIL') : 'NONE';
        const name = shotName(page.id, width, mode);
        const shot = { name, pageId: page.id, width, mode, assertion, file: name };
        if (mismatch) shot.reason = REASON_PAGE_STATE;
        else if (SHOT_REASON[assertion]) shot.reason = SHOT_REASON[assertion];
        hiddenProp(shot, 'png', r.png);
        shots.push(shot);
      }
    }
  }
  const verdict = combine(shots.map((s) => SHOT_VERDICT[s.assertion]));
  return { verdict, shots, exit: exitFor(verdict) };
}

export function manifest(result) {
  return {
    version: 1,
    shots: result.shots.map((s) => ({ name: s.name, pageId: s.pageId, width: s.width, mode: s.mode, assertion: s.assertion })),
  };
}

// Odczyt manifestu z zewnatrz (diff): kazdy wpis musi miec bezpieczna nazwe i znana asercje.
export function parseManifest(obj) {
  const errors = [];
  if (!isObj(obj) || obj.version !== 1 || !Array.isArray(obj.shots)) return { ok: false, errors: ['manifest: expected { version: 1, shots: [...] }'], manifest: null };
  const seen = new Set();
  const shots = [];
  obj.shots.forEach((s, i) => {
    if (!isObj(s) || !isShotName(s.name) || !ASSERTIONS.has(s.assertion)) {
      errors.push(`manifest.shots[${i}]: expected a shot name (<id>__<width>-<light|dark>.png) and assertion PASS|FAIL|NONE`);
    } else if (seen.has(s.name)) {
      errors.push(`manifest.shots[${i}]: duplicate name`);
    } else {
      seen.add(s.name);
      shots.push({ name: s.name, pageId: s.pageId, width: s.width, mode: s.mode, assertion: s.assertion });
    }
  });
  return { ok: errors.length === 0, errors, manifest: errors.length === 0 ? { version: 1, shots } : null };
}

export function formatCaptureReport(result, masker) {
  const lines = [];
  for (const s of result.shots) {
    lines.push(`${SHOT_VERDICT[s.assertion]} ${s.name}${s.reason ? ` ${s.reason}` : ''} (assertion ${s.assertion})`);
  }
  lines.push(`result ${result.verdict} (exit ${result.exit})`);
  return masker.mask(`${lines.join('\n')}\n`);
}

export function toJson(result, masker) {
  const plain = {
    verdict: result.verdict,
    exit: result.exit,
    shots: result.shots.map((s) => ({ name: s.name, pageId: s.pageId, width: s.width, mode: s.mode, assertion: s.assertion, ...(s.reason ? { reason: s.reason } : {}) })),
  };
  return masker.maskValue(JSON.parse(JSON.stringify(plain)));
}
