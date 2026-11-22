// Roznica "z nakladka" vs "bez nakladki": nic nie moze zniknac ani sie zmienic, a to co doszlo musi
// lezec w zakresie (korzen, prefiks, alias) albo byc dozwolona at-regula. Funkcje czyste.
import { scan, splitSelectors, collapseWhitespace } from './css-parse.mjs';
import { buildRootPattern, evaluatePrepared, prepareScope } from './css-scope-core.mjs';

const KEYFRAMES_RE = /^(-[a-z]+-)?keyframes$/;
const DESCRIPTION_LIMIT = 160;
const EXAMPLES_LIMIT = 10;
const SEP = ' >> ';

function makeMatcher(config) {
  if (!config || typeof config !== 'object') throw new TypeError('config must be an object');
  const rootRe = buildRootPattern(config.root);
  const list = Array.isArray(config.exceptions) ? config.exceptions : [];
  const prefixes = [];
  const aliases = [];
  for (const ex of list) {
    if (ex.kind === 'prefix') prefixes.push(String(ex.value).trim());
    else if (ex.kind === 'root-alias') aliases.push(buildRootPattern(ex.value));
  }
  return (selector) => {
    const sel = String(selector).trim();
    return rootRe.test(sel) || prefixes.some((p) => sel.startsWith(p)) || aliases.some((re) => re.test(sel));
  };
}

export function selectorAllowed(selector, scopeConfig) {
  return makeMatcher(scopeConfig)(selector);
}

const ctxText = (heads) => (heads.length ? heads.join(SEP) + SEP : '');
const atNameOf = (head) => {
  const m = /^@([A-Za-z0-9_-]+)/.exec(head);
  return m ? m[1].toLowerCase() : '';
};

// Spłaszcza drzewo do trojek { ctx, head, body, kind, key }.
function flatten(items, heads, out) {
  for (const item of items) {
    const ctx = ctxText(heads);
    if (item.kind === 'rule') {
      out.push({ ctx, head: collapseWhitespace(item.selector), body: collapseWhitespace(item.body).trim(), kind: 'rule', nested: (item.nested || []).length > 0 });
    } else if (item.kind === 'group') {
      flatten(item.children, heads.concat(item.head), out);
    } else if (item.kind === 'keyframes') {
      const body = item.steps.map((s) => `${collapseWhitespace(s.selector)}{${collapseWhitespace(s.body).trim()}}`).join(' ');
      out.push({ ctx, head: item.head, body, kind: 'keyframes' });
    } else if (item.kind === 'at-block') {
      out.push({ ctx, head: item.head, body: collapseWhitespace(item.body).trim(), kind: 'at-block' });
    } else if (item.kind === 'statement') {
      out.push({ ctx, head: item.text, body: '', kind: 'statement' });
    }
  }
  return out;
}

function toMultiset(cssText) {
  const triples = flatten(scan(cssText).items, [], []);
  const map = new Map();
  for (const t of triples) {
    t.key = JSON.stringify([t.ctx, t.head, t.body]);
    const entry = map.get(t.key);
    if (entry) entry.count++;
    else map.set(t.key, { count: 1, triple: t });
  }
  return { total: triples.length, map };
}

const describe = (t) => {
  const text = `${t.ctx}${t.head}`;
  return text.length > DESCRIPTION_LIMIT ? text.slice(0, DESCRIPTION_LIMIT) : text;
};

// Elementy z nadwyzka w a wzgledem b, z krotnoscia, w kolejnosci wystapienia w a.
function surplus(a, b) {
  const out = [];
  for (const [key, { count, triple }] of a.map) {
    const other = b.map.get(key);
    const extra = count - (other ? other.count : 0);
    for (let i = 0; i < extra; i++) out.push(triple);
  }
  return out;
}

export function diffCss(withCss, withoutCss, scopeConfig, options = {}) {
  const prepared = prepareScope(scopeConfig);
  const allowAt = new Set(Array.isArray(options?.allowAtRules) ? options.allowAtRules : ['font-face']);
  const withSet = toMultiset(withCss);
  const withoutSet = toMultiset(withoutCss);

  const added = surplus(withSet, withoutSet);
  const removedTriples = surplus(withoutSet, withSet);

  const isAllowed = (t) => {
    if (t.kind === 'rule') {
      if (t.nested || splitSelectors(t.head).length === 0) return false;
      return evaluatePrepared(prepared, t.head, t.body).ok;
    }
    if (t.kind === 'keyframes') return allowAt.has('keyframes');
    if (t.kind === 'at-block') {
      const name = atNameOf(t.head);
      if (name === 'font-face') return allowAt.has('font-face');
      if (name === 'page') return allowAt.has('page');
      if (KEYFRAMES_RE.test(name)) return allowAt.has('keyframes');
    }
    return false;
  };

  let addedAllowed = 0;
  const addedOutside = [];
  for (const t of added) {
    if (isAllowed(t)) addedAllowed++;
    else addedOutside.push(describe(t));
  }
  const removed = removedTriples.map(describe);

  const examples = [
    ...removed.map((d) => `REMOVED/CHANGED: ${d}`),
    ...addedOutside.map((d) => `ADDED OUTSIDE SCOPE: ${d}`),
  ].slice(0, EXAMPLES_LIMIT);

  return {
    ok: removed.length === 0 && addedOutside.length === 0,
    counts: {
      with: withSet.total,
      without: withoutSet.total,
      added: added.length,
      removed: removed.length,
      addedAllowed,
    },
    addedOutside,
    removed,
    examples,
  };
}
