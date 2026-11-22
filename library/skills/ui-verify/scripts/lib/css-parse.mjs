// Parser CSS dla kontroli zakresu: jednoprzebiegowy tokenizer (komentarze, napisy, escape,
// nawiasy), drzewo regul i pomocnicze funkcje czyste. Brak efektow przy imporcie i zaleznosci.

export class CssParseError extends Error {
  constructor(code, message, line, column) {
    super(message);
    this.name = 'CssParseError';
    this.code = code;
    this.line = line;
    this.column = column;
  }
}

const GROUP_NAMES = new Set(['media', 'supports', 'document', '-moz-document', 'layer', 'container', 'scope', 'starting-style']);
const KEYFRAMES_RE = /^(-[a-z]+-)?keyframes$/;

const isWs = (c) => c === 32 || c === 9 || c === 10 || c === 13 || c === 12;
const isWordCode = (c) => (c >= 48 && c <= 57) || (c >= 65 && c <= 90) || (c >= 97 && c <= 122) || c === 95 || c === 45;

// Pozycja (linia, kolumna od 1) indeksu; tylko sciezka bledu, wiec prosty przebieg.
function locate(text, idx) {
  let line = 1;
  let lineStart = 0;
  for (let i = 0; i < idx; i++) {
    if (text.charCodeAt(i) === 10) {
      line++;
      lineStart = i + 1;
    }
  }
  return { line, column: idx - lineStart + 1 };
}

function fail(text, idx, code, what) {
  const { line, column } = locate(text, idx);
  return new CssParseError(code, `${what} at line ${line}, column ${column}`, line, column);
}

// Koniec napisu otwartego w indeksie i (indeks za zamykajacym cudzyslowem) albo -1.
function skipString(text, i) {
  const quote = text.charCodeAt(i);
  const n = text.length;
  for (let j = i + 1; j < n; j++) {
    const ch = text.charCodeAt(j);
    if (ch === 92) j++;
    else if (ch === quote) return j + 1;
  }
  return -1;
}

function blank(chunk) {
  if (chunk.indexOf('\n') < 0 && chunk.indexOf('\r') < 0) return ' '.repeat(chunk.length);
  const out = new Array(chunk.length);
  for (let i = 0; i < chunk.length; i++) {
    const ch = chunk.charAt(i);
    out[i] = ch === '\n' || ch === '\r' ? ch : ' ';
  }
  return out.join('');
}

// Zastepuje komentarze spacjami (zachowuje znaki nowej linii), waliduje napisy i komentarze.
function stripComments(text) {
  const n = text.length;
  const parts = [];
  let last = 0;
  for (let i = 0; i < n; ) {
    const c = text.charCodeAt(i);
    if (c === 92) {
      i += 2;
    } else if (c === 34 || c === 39) {
      const end = skipString(text, i);
      if (end < 0) throw fail(text, i, 'unterminated-string', 'Unterminated string');
      i = end;
    } else if (c === 47 && text.charCodeAt(i + 1) === 42) {
      const end = text.indexOf('*/', i + 2);
      if (end < 0) throw fail(text, i, 'unterminated-comment', 'Unterminated comment');
      parts.push(text.slice(last, i), blank(text.slice(i, end + 2)));
      last = end + 2;
      i = last;
    } else {
      i++;
    }
  }
  if (parts.length === 0) return text;
  parts.push(text.slice(last));
  return parts.join('');
}

// Zwija biale znaki do jednej spacji poza napisami i przycina.
function collapse(s) {
  const n = s.length;
  const out = [];
  let pendingSpace = false;
  let last = 0;
  for (let i = 0; i < n; ) {
    const c = s.charCodeAt(i);
    if (isWs(c)) {
      if (i > last) {
        if (pendingSpace && out.length) out.push(' ');
        pendingSpace = false;
        out.push(s.slice(last, i));
      }
      pendingSpace = true;
      i++;
      last = i;
    } else if (c === 34 || c === 39) {
      let end = skipString(s, i);
      if (end < 0) end = n;
      i = end;
    } else if (c === 92) {
      i += 2;
    } else {
      i++;
    }
  }
  if (last < n) {
    if (pendingSpace && out.length) out.push(' ');
    out.push(s.slice(last, n));
  }
  return out.join('');
}

// Dzieli po znaku separatora na najwyzszym poziomie (poza (), [], napisami i escape).
function splitTop(text, sepCode) {
  const n = text.length;
  const out = [];
  let depth = 0;
  let start = 0;
  const push = (end) => {
    const part = text.slice(start, end).trim();
    if (part) out.push(part);
  };
  for (let i = 0; i < n; ) {
    const c = text.charCodeAt(i);
    if (c === 92) {
      i += 2;
    } else if (c === 34 || c === 39) {
      const end = skipString(text, i);
      i = end < 0 ? n : end;
    } else {
      if (c === 40 || c === 91) depth++;
      else if ((c === 41 || c === 93) && depth > 0) depth--;
      else if (c === sepCode && depth === 0) {
        push(i);
        start = i + 1;
      }
      i++;
    }
  }
  push(n);
  return out;
}

export function splitSelectors(selector) {
  return splitTop(selector, 44);
}

export function splitDeclarations(body) {
  return splitTop(body, 59);
}

export function normalizeDeclaration(decl) {
  const n = decl.length;
  const colon = decl.indexOf(':');
  const out = [];
  let from = 0;
  if (colon >= 0) {
    out.push(decl.slice(0, colon).replace(/\s+/g, '').toLowerCase(), ':');
    from = colon + 1;
  }
  let last = from;
  for (let i = from; i < n; ) {
    const c = decl.charCodeAt(i);
    if (isWs(c)) {
      if (i > last) out.push(decl.slice(last, i));
      i++;
      last = i;
    } else if (c === 34 || c === 39) {
      const end = skipString(decl, i);
      i = end < 0 ? n : end;
    } else if (c === 92) {
      i += 2;
    } else {
      i++;
    }
  }
  if (last < n) out.push(decl.slice(last, n));
  return out.join('');
}

export function countBraces(text) {
  const clean = stripComments(text);
  const n = clean.length;
  let count = 0;
  for (let i = 0; i < n; ) {
    const c = clean.charCodeAt(i);
    if (c === 92) {
      i += 2;
    } else if (c === 34 || c === 39) {
      const end = skipString(clean, i);
      i = end < 0 ? n : end;
    } else {
      if (c === 123) count++;
      i++;
    }
  }
  return count;
}

export function countImportant(text) {
  const clean = stripComments(text);
  const n = clean.length;
  let count = 0;
  for (let i = 0; i < n; ) {
    const c = clean.charCodeAt(i);
    if (c === 92) {
      i += 2;
    } else if (c === 34 || c === 39) {
      const end = skipString(clean, i);
      i = end < 0 ? n : end;
    } else {
      i++;
      if (c === 33) {
        let j = i;
        while (j < n && isWs(clean.charCodeAt(j))) j++;
        if (clean.slice(j, j + 9).toLowerCase() === 'important' && !isWordCode(clean.charCodeAt(j + 9))) {
          count++;
          i = j + 9;
        }
      }
    }
  }
  return count;
}

// Indeks pasujacej `}` dla `{` w openIdx; brak => blad na najglebiej otwartej `{`.
function findClose(text, openIdx) {
  const n = text.length;
  const opens = [openIdx];
  for (let i = openIdx + 1; i < n; ) {
    const c = text.charCodeAt(i);
    if (c === 92) {
      i += 2;
    } else if (c === 34 || c === 39) {
      const end = skipString(text, i);
      i = end < 0 ? n : end;
    } else {
      if (c === 123) {
        opens.push(i);
      } else if (c === 125) {
        opens.pop();
        if (opens.length === 0) return i;
      }
      i++;
    }
  }
  throw fail(text, opens[opens.length - 1], 'unbalanced-brace', 'Unbalanced brace: missing closing "}" for "{"');
}

function atName(head) {
  let end = 1;
  while (end < head.length && isWordCode(head.charCodeAt(end))) end++;
  return head.slice(1, end).toLowerCase();
}

export function scan(text) {
  const clean = stripComments(String(text));
  const n = clean.length;

  const lineStarts = [0];
  for (let i = 0; i < n; i++) if (clean.charCodeAt(i) === 10) lineStarts.push(i + 1);
  const lineOf = (idx) => {
    let lo = 0;
    let hi = lineStarts.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (lineStarts[mid] <= idx) lo = mid;
      else hi = mid - 1;
    }
    return lo + 1;
  };
  const firstNonWs = (from, to) => {
    let k = from;
    while (k < to && isWs(clean.charCodeAt(k))) k++;
    return k;
  };

  const rules = [];
  const root = { items: [], heads: [], openIdx: -1 };
  const stack = [root];
  let preStart = 0;
  let depth = 0;

  for (let i = 0; i < n; ) {
    const c = clean.charCodeAt(i);
    if (c === 92) {
      i += 2;
    } else if (c === 34 || c === 39) {
      const end = skipString(clean, i);
      i = end < 0 ? n : end;
    } else if (c === 40 || c === 91) {
      depth++;
      i++;
    } else if ((c === 41 || c === 93) && depth > 0) {
      depth--;
      i++;
    } else if (c === 59 && depth === 0) {
      const prelude = collapse(clean.slice(preStart, i));
      if (prelude.charCodeAt(0) === 64) {
        stack[stack.length - 1].items.push({ kind: 'statement', text: prelude, line: lineOf(firstNonWs(preStart, i)) });
      }
      i++;
      preStart = i;
    } else if (c === 123) {
      const top = stack[stack.length - 1];
      const prelude = collapse(clean.slice(preStart, i));
      const line = lineOf(firstNonWs(preStart, i));
      depth = 0;
      if (top.kind === 'keyframes') {
        const close = findClose(clean, i);
        top.node.steps.push({ selector: prelude, body: clean.slice(i + 1, close), line });
        i = close + 1;
      } else if (prelude.charCodeAt(0) === 64) {
        const name = atName(prelude);
        if (KEYFRAMES_RE.test(name)) {
          const node = { kind: 'keyframes', head: prelude, steps: [], line };
          top.items.push(node);
          stack.push({ kind: 'keyframes', node, items: null, heads: top.heads, openIdx: i });
          i++;
        } else if (GROUP_NAMES.has(name)) {
          const node = { kind: 'group', head: prelude, children: [], line };
          top.items.push(node);
          stack.push({ kind: 'group', node, items: node.children, heads: top.heads.concat(prelude), openIdx: i });
          i++;
        } else {
          const close = findClose(clean, i);
          top.items.push({ kind: 'at-block', head: prelude, body: clean.slice(i + 1, close), line });
          i = close + 1;
        }
      } else {
        const close = findClose(clean, i);
        const rule = {
          kind: 'rule',
          selector: prelude,
          body: clean.slice(i + 1, close),
          line,
          context: top.heads.slice(),
          inKeyframes: false,
        };
        top.items.push(rule);
        rules.push(rule);
        i = close + 1;
      }
      preStart = i;
    } else if (c === 125) {
      if (stack.length === 1) throw fail(clean, i, 'unexpected-close', 'Unexpected closing brace "}"');
      stack.pop();
      depth = 0;
      i++;
      preStart = i;
    } else {
      i++;
    }
  }
  if (stack.length > 1) {
    throw fail(clean, stack[stack.length - 1].openIdx, 'unbalanced-brace', 'Unbalanced brace: missing closing "}" for "{"');
  }
  return { clean, rules, items: root.items };
}

export function walkRules(items, visitor) {
  for (const item of items) {
    if (item.kind === 'rule') visitor(item);
    else if (item.kind === 'group') walkRules(item.children, visitor);
  }
}

export { collapse as collapseWhitespace };
