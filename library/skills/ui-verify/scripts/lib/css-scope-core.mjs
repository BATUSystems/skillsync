// Kontrola zakresu CSS: kazdy selektor musi zaczynac sie od klasy korzenia albo od jawnego wyjatku
// (prefix, root-alias, exact-rule). Funkcje czyste; parser w css-parse.mjs.
import {
  scan,
  walkRules,
  splitSelectors,
  splitDeclarations,
  normalizeDeclaration,
  countBraces,
  countImportant,
  collapseWhitespace,
} from './css-parse.mjs';

const ROOT_CLASS_RE = /^\.[A-Za-z_][A-Za-z0-9_-]*$/;

export function buildRootPattern(rootClass) {
  if (typeof rootClass !== 'string') throw new TypeError('root class must be a string');
  const cls = rootClass.trim();
  if (!ROOT_CLASS_RE.test(cls)) throw new TypeError(`invalid root class: ${JSON.stringify(rootClass)}`);
  const escaped = cls.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`^${escaped}(?![\\w\\-\\\\\\x00]|[^\\x00-\\x7F])`);
}

function prepare(config) {
  if (!config || typeof config !== 'object') throw new TypeError('config must be an object');
  const rootRe = buildRootPattern(config.root);
  const list = Array.isArray(config.exceptions) ? config.exceptions : [];
  const exceptions = list.map((ex) => {
    const base = { id: ex.id, kind: ex.kind, reason: ex.reason, hits: 0 };
    if (ex.kind === 'prefix') return { ...base, value: String(ex.value).trim() };
    if (ex.kind === 'root-alias') {
      return { ...base, re: buildRootPattern(ex.value), onlyPropertyPrefix: ex.onlyPropertyPrefix ?? null };
    }
    if (ex.kind === 'exact-rule') {
      const decls = (ex.declarations || []).map(normalizeDeclaration);
      return { ...base, selector: collapseWhitespace(String(ex.selector)), decls: new Set(decls), declCount: decls.length };
    }
    throw new TypeError(`unknown exception kind: ${ex.kind}`);
  });
  return { rootRe, exceptions };
}

const propName = (decl) => {
  const i = decl.indexOf(':');
  return (i < 0 ? decl : decl.slice(0, i)).trim();
};

function describe(selector, decls, reason) {
  return `${selector} { ${decls.join('; ')} } (${reason})`;
}

// Ocena jednej reguly (lista selektorow + cialo) wzgledem przygotowanej konfiguracji; wspolna dla check i diff.
export function evaluatePrepared(prepared, selectorList, body) {
  const { rootRe, exceptions } = prepared;
  const selectors = splitSelectors(selectorList);
  let decls = null;
  const declsOf = () => (decls ??= splitDeclarations(body));

  if (selectors.length === 1) {
    const exact = exceptions.find((ex) => ex.kind === 'exact-rule' && ex.selector === selectors[0]);
    if (exact) {
      const norm = declsOf().map(normalizeDeclaration);
      const ok = norm.length === exact.declCount && norm.every((d) => exact.decls.has(d)) && new Set(norm).size === exact.decls.size;
      if (ok) return { ok: true, problems: [], exceptionIds: [exact.id] };
      return { ok: false, problems: [describe(selectors[0], declsOf(), exact.reason)], exceptionIds: [] };
    }
  }

  const problems = [];
  const hit = new Set();
  for (const sel of selectors) {
    if (rootRe.test(sel)) continue;
    let accepted = null;
    let failure = null;
    for (const ex of exceptions) {
      if (ex.kind === 'prefix') {
        if (sel.startsWith(ex.value)) {
          accepted = ex;
          break;
        }
      } else if (ex.kind === 'root-alias' && ex.re.test(sel)) {
        if (ex.onlyPropertyPrefix === null || declsOf().every((d) => propName(d).startsWith(ex.onlyPropertyPrefix))) {
          accepted = ex;
          break;
        }
        failure ??= describe(sel, declsOf(), ex.reason);
      }
    }
    if (accepted) hit.add(accepted.id);
    else problems.push(failure ?? sel);
  }
  return { ok: problems.length === 0, problems, exceptionIds: [...hit] };
}

export function evaluateRule(selectorList, body, scopeConfig) {
  return evaluatePrepared(prepare(scopeConfig), selectorList, body);
}

export { prepare as prepareScope };

export function checkScope(cssText, config) {
  const prepared = prepare(config);
  const { exceptions } = prepared;
  const { items } = scan(cssText);
  const rules = countBraces(cssText);
  const important = countImportant(cssText);
  const bad = [];

  walkRules(items, (rule) => {
    const r = evaluatePrepared(prepared, rule.selector, rule.body);
    bad.push(...r.problems);
    for (const id of r.exceptionIds) {
      const ex = exceptions.find((e) => e.id === id);
      if (ex) ex.hits++;
    }
    for (const inner of rule.nested || []) bad.push(`${rule.selector} { ${inner} { ... } } (nested rule is not supported)`);
  });

  const warnings = [];
  for (const ex of exceptions) if (ex.hits === 0) warnings.push(`exception ${ex.id} was not used`);
  return {
    ok: bad.length === 0,
    bad,
    rules,
    important,
    exceptions: exceptions.map((ex) => ({ id: ex.id, hits: ex.hits })),
    warnings,
  };
}
