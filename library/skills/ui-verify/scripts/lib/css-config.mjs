// Konfiguracja kontroli zakresu CSS jako dane: walidacja schematu, parsowanie JSON, wbudowany zestaw
// przypadkow silnika i ich uruchamianie. Funkcje czyste; zero efektow przy imporcie.
import { resolveInside } from './policy.mjs';
import { CssParseError } from './css-parse.mjs';
import { checkScope } from './css-scope-core.mjs';

const MAX_CONFIG_CHARS = 1_000_000;
const MAX_CSS_CHARS = 20000;
const ROOT_RE = /^\.[A-Za-z_][A-Za-z0-9_-]*$/;
const SCSS_NAME_RE = /^[A-Za-z0-9._-]+\.scss$/;
const ID_RE = /^[a-z0-9-]{1,40}$/;
const CONTROL_RE = /[\u0000-\u001f\u007f]/;
const AT_RULES = ['font-face', 'keyframes', 'page'];
const TOP_KEYS = ['version', 'root', 'scssDir', 'entry', 'without', 'out', 'exceptions', 'selftest', 'important', 'diff'];
const KINDS = ['prefix', 'root-alias', 'exact-rule'];
const KIND_KEYS = {
  prefix: ['value'],
  'root-alias': ['value', 'onlyPropertyPrefix'],
  'exact-rule': ['selector', 'declarations'],
};

const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const isText = (v) => typeof v === 'string' && v.trim().length > 0;

function unknownKeys(obj, allowed, path, errors) {
  for (const k of Object.keys(obj)) {
    if (!allowed.includes(k)) errors.push(`${path}.${k}: unknown key "${k}"`);
  }
}

function checkInside(configDir, value, path, errors) {
  try {
    resolveInside(configDir, value);
    return true;
  } catch {
    errors.push(`${path}: must be a relative path inside the config directory`);
    return false;
  }
}

function validateException(ex, i, root, seenIds, errors) {
  const path = `exceptions[${i}]`;
  if (!isObj(ex)) {
    errors.push(`${path}: must be an object`);
    return null;
  }
  const before = errors.length;
  const rec = {};

  if (!has(ex, 'id')) errors.push(`${path}.id: required`);
  else if (typeof ex.id !== 'string' || !ID_RE.test(ex.id)) errors.push(`${path}.id: must match [a-z0-9-]{1,40}`);
  else if (seenIds.has(ex.id)) errors.push(`${path}.id: duplicate id "${ex.id}"`);
  else {
    seenIds.add(ex.id);
    rec.id = ex.id;
  }

  const kindOk = has(ex, 'kind') && KINDS.includes(ex.kind);
  if (!has(ex, 'kind')) errors.push(`${path}.kind: required`);
  else if (!kindOk) errors.push(`${path}.kind: must be one of ${KINDS.join(', ')}`);
  else rec.kind = ex.kind;

  if (!has(ex, 'reason')) errors.push(`${path}.reason: required`);
  else if (!isText(ex.reason)) errors.push(`${path}.reason: must be a non-empty string`);
  else rec.reason = ex.reason;

  if (kindOk) {
    unknownKeys(ex, ['id', 'kind', 'reason', ...KIND_KEYS[ex.kind]], path, errors);
    if (ex.kind === 'prefix') {
      if (!has(ex, 'value')) errors.push(`${path}.value: required`);
      else if (!isText(ex.value) || CONTROL_RE.test(ex.value)) errors.push(`${path}.value: must be a non-empty string without control characters`);
      else rec.value = ex.value;
    } else if (ex.kind === 'root-alias') {
      if (!has(ex, 'value')) errors.push(`${path}.value: required`);
      else if (typeof ex.value !== 'string' || !ROOT_RE.test(ex.value)) errors.push(`${path}.value: must be a class like ".name"`);
      else if (ex.value === root) errors.push(`${path}.value: must differ from root`);
      else rec.value = ex.value;
      if (has(ex, 'onlyPropertyPrefix')) {
        if (typeof ex.onlyPropertyPrefix !== 'string' || !ex.onlyPropertyPrefix.startsWith('--')) {
          errors.push(`${path}.onlyPropertyPrefix: must be a string starting with "--"`);
        } else rec.onlyPropertyPrefix = ex.onlyPropertyPrefix;
      }
    } else {
      if (!has(ex, 'selector')) errors.push(`${path}.selector: required`);
      else if (!isText(ex.selector)) errors.push(`${path}.selector: must be a non-empty string`);
      else rec.selector = ex.selector;
      if (!has(ex, 'declarations')) errors.push(`${path}.declarations: required`);
      else if (!Array.isArray(ex.declarations) || ex.declarations.length === 0) {
        errors.push(`${path}.declarations: must be a non-empty array`);
      } else {
        let good = true;
        ex.declarations.forEach((d, j) => {
          if (!isText(d) || !d.includes(':')) {
            errors.push(`${path}.declarations[${j}]: must be a non-empty string containing ":"`);
            good = false;
          }
        });
        if (good) rec.declarations = ex.declarations.slice();
      }
    }
  }
  return errors.length === before ? rec : null;
}

function validateSelftest(list, errors) {
  const out = [];
  const names = new Set();
  list.forEach((c, i) => {
    const path = `selftest[${i}]`;
    if (!isObj(c)) {
      errors.push(`${path}: must be an object`);
      return;
    }
    const before = errors.length;
    unknownKeys(c, ['name', 'css', 'expect'], path, errors);
    if (typeof c.name !== 'string' || c.name.length < 1 || c.name.length > 120) errors.push(`${path}.name: must be a string of 1..120 characters`);
    else if (names.has(c.name)) errors.push(`${path}.name: duplicate name "${c.name}"`);
    else names.add(c.name);
    if (typeof c.css !== 'string' || c.css.length > MAX_CSS_CHARS) errors.push(`${path}.css: must be a string of at most ${MAX_CSS_CHARS} characters`);
    if (c.expect !== 'accepted' && c.expect !== 'rejected') errors.push(`${path}.expect: must be "accepted" or "rejected"`);
    if (errors.length === before) out.push({ name: c.name, css: c.css, expect: c.expect });
  });
  return out;
}

export function validateConfig(obj, { configDir } = {}) {
  const errors = [];
  if (!isObj(obj)) return { ok: false, errors: ['config: must be an object'], config: null };
  const dir = typeof configDir === 'string' && configDir ? configDir : process.cwd();
  const config = {};

  unknownKeys(obj, TOP_KEYS, '', errors);
  for (let i = 0; i < errors.length; i++) errors[i] = errors[i].replace(/^\./, '');

  if (!has(obj, 'version')) errors.push('version: required');
  else if (obj.version !== 1) errors.push('version: must be 1');
  else config.version = 1;

  let root = null;
  if (!has(obj, 'root')) errors.push('root: required');
  else if (typeof obj.root !== 'string' || !ROOT_RE.test(obj.root)) errors.push('root: must be a class like ".name"');
  else {
    root = obj.root;
    config.root = root;
  }

  if (has(obj, 'scssDir')) {
    if (typeof obj.scssDir !== 'string' || obj.scssDir === '') errors.push('scssDir: must be a non-empty relative path');
    else if (checkInside(dir, obj.scssDir, 'scssDir', errors)) config.scssDir = obj.scssDir;
  }
  for (const key of ['entry', 'without']) {
    if (!has(obj, key)) continue;
    if (typeof obj[key] !== 'string' || !SCSS_NAME_RE.test(obj[key])) errors.push(`${key}: must be a file name ending in .scss`);
    else config[key] = obj[key];
  }
  if (has(obj, 'out')) {
    if (typeof obj.out !== 'string' || !obj.out.endsWith('.css')) errors.push('out: must be a relative path ending in .css');
    else if (checkInside(dir, obj.out, 'out', errors)) config.out = obj.out;
  }

  config.exceptions = [];
  if (has(obj, 'exceptions')) {
    if (!Array.isArray(obj.exceptions)) errors.push('exceptions: must be an array');
    else {
      const seen = new Set();
      obj.exceptions.forEach((ex, i) => {
        const rec = validateException(ex, i, root, seen, errors);
        if (rec) config.exceptions.push(rec);
      });
    }
  }

  config.selftest = [];
  if (has(obj, 'selftest')) {
    if (!Array.isArray(obj.selftest)) errors.push('selftest: must be an array');
    else config.selftest = validateSelftest(obj.selftest, errors);
  }

  if (has(obj, 'important')) {
    if (!isObj(obj.important)) errors.push('important: must be an object');
    else {
      unknownKeys(obj.important, ['max'], 'important', errors);
      if (has(obj.important, 'max')) {
        const m = obj.important.max;
        if (typeof m !== 'number' || !Number.isInteger(m) || m < 0) errors.push('important.max: must be an integer >= 0');
        else config.important = { max: m };
      } else config.important = {};
    }
  }

  config.diff = { allowAtRules: ['font-face'] };
  if (has(obj, 'diff')) {
    if (!isObj(obj.diff)) errors.push('diff: must be an object');
    else {
      unknownKeys(obj.diff, ['allowAtRules'], 'diff', errors);
      if (has(obj.diff, 'allowAtRules')) {
        const a = obj.diff.allowAtRules;
        if (!Array.isArray(a) || a.some((x) => typeof x !== 'string' || !AT_RULES.includes(x))) {
          errors.push(`diff.allowAtRules: must be an array with values from ${AT_RULES.join(', ')}`);
        } else config.diff = { allowAtRules: a.slice() };
      }
    }
  }

  if (errors.length > 0) return { ok: false, errors, config: null };
  return { ok: true, errors: [], config };
}

export function parseConfig(text, { configDir } = {}) {
  if (typeof text !== 'string') return { ok: false, errors: ['config: must be text'], config: null };
  if (text.length > MAX_CONFIG_CHARS) {
    return { ok: false, errors: [`config: larger than ${MAX_CONFIG_CHARS} characters`], config: null };
  }
  let obj;
  try {
    obj = JSON.parse(text);
  } catch {
    return { ok: false, errors: ['invalid JSON: syntax error'], config: null };
  }
  return validateConfig(obj, { configDir });
}

export function toScopeConfig(config) {
  return {
    root: config.root,
    exceptions: (config.exceptions || []).map((ex) => {
      const rec = { id: ex.id, kind: ex.kind, reason: ex.reason };
      if (ex.kind === 'prefix') rec.value = ex.value;
      else if (ex.kind === 'root-alias') {
        rec.value = ex.value;
        if (ex.onlyPropertyPrefix !== undefined) rec.onlyPropertyPrefix = ex.onlyPropertyPrefix;
      } else {
        rec.selector = ex.selector;
        rec.declarations = ex.declarations.slice();
      }
      return rec;
    }),
  };
}

const EXACT_SELECTOR = '.root-theme main[tabindex="-1"]:focus:not(:focus-visible)';

export function builtinConfig() {
  return {
    version: 1,
    root: '.root',
    exceptions: [
      { id: 'menu-host', kind: 'prefix', value: '.menu-host > .item', reason: 'menu rendered outside the root' },
      { id: 'theme-hook', kind: 'root-alias', value: '.root-theme', onlyPropertyPrefix: '--tok-', reason: 'design tokens only' },
      {
        id: 'skip-link',
        kind: 'exact-rule',
        selector: EXACT_SELECTOR,
        declarations: ['outline:0!important'],
        reason: 'programmatic focus target without outline',
      },
    ],
  };
}

export function builtinCases() {
  const s = EXACT_SELECTOR;
  const mk = (name, css, expect) => ({ name, css, expect });
  return [
    mk('root-element', '.root a { color: red }', 'accepted'),
    mk('root-compound', '.root.is-open > .x:hover { margin: 0 }', 'accepted'),
    mk('root-in-media', '@media print { .root a { color: red } }', 'accepted'),
    mk('element-outside-root', 'a { color: red }', 'rejected'),
    mk('body-outside-root', 'body { margin: 0 }', 'rejected'),
    mk('outside-root-in-media', '@media print { a { color: red } }', 'rejected'),
    mk('prefix-exception', '.menu-host > .item a { color: red }', 'accepted'),
    mk('prefix-other-child', '.menu-host > .other { color: red }', 'rejected'),
    mk('alias-token-properties', '.root-theme { --tok-a: 1; --tok-b: 2 }', 'accepted'),
    mk('alias-other-property', '.root-theme { color: red }', 'rejected'),
    mk('alias-mixed-properties', '.root-theme { --tok-a: 1; color: red }', 'rejected'),
    mk('exact-rule-expanded', `${s} { outline: 0 !important }`, 'accepted'),
    mk('exact-rule-compressed', `${s}{outline:0!important}`, 'accepted'),
    mk('exact-rule-extra-declaration', `${s} { outline: 0 !important; color: red }`, 'rejected'),
    mk('exact-rule-none-instead-of-zero', `${s} { outline: none !important }`, 'rejected'),
    mk('exact-rule-grouped', `${s}, a { outline: 0 !important }`, 'rejected'),
    mk('exact-rule-other-alias-element', '.root-theme main:focus:not(:focus-visible) { outline: 0 !important }', 'rejected'),
    mk('exact-rule-other-attribute-value', '.root-theme main[tabindex="0"]:focus:not(:focus-visible) { outline: 0 !important }', 'rejected'),
    mk('exact-rule-focus-visible', '.root-theme main[tabindex="-1"]:focus-visible { outline: 0 !important }', 'rejected'),
  ];
}

export function runCases(cases, scopeConfig) {
  return cases.map((c) => {
    let got;
    let error;
    try {
      got = checkScope(c.css, scopeConfig).bad.length === 0 ? 'accepted' : 'rejected';
    } catch (e) {
      if (!(e instanceof CssParseError)) throw e;
      got = 'rejected';
      error = String(e.message);
    }
    const res = { name: c.name, expect: c.expect, got, pass: got === c.expect };
    if (error !== undefined) res.error = error;
    return res;
  });
}
