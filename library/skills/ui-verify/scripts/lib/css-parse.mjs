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
// At-reguly z blokiem bez regul podlegajacych kontroli (deklaracje, marginesy, ...). Kazda inna `@`-regula z blokiem to grupa.
const LEAF_NAMES = new Set([
  'font-face', 'page', 'property', 'counter-style', 'font-feature-values', 'font-palette-values',
  'position-try', 'view-transition', 'viewport', '-ms-viewport', '-webkit-viewport',
]);

const isWs = (c) => c === 32 || c === 9 || c === 10 || c === 13 || c === 12;
const isWordCode = (c) => (c >= 48 && c <= 57) || (c >= 65 && c <= 90) || (c >= 97 && c <= 122) || c === 95 || c === 45;

const isNameCode = (c) => isWordCode(c) || c > 127 || c === 0;
// Poczatek jednostki w petlach znakowych: identyfikator, cyfra/slowo, `#nazwa`, `@nazwa` albo `\`.
const isWordStart = (c) => isNameCode(c) || c === 92 || c === 35 || c === 64;
const isHex = (c) => (c >= 48 && c <= 57) || (c >= 65 && c <= 70) || (c >= 97 && c <= 102);

// Jednostka zaczynajaca sie w i (litera, cyfra, `_`, `-`, znak spoza ASCII, `\`, `#`, `@`): konsumuje nazwe z escape CSS
// (`\` + 1-6 cyfr hex + opcjonalna jedna spacja albo `\` + dowolny znak poza nowa linia). Zwraca indeks za jednostka;
// `url(` (nazwa po zdekodowaniu bez wielkosci liter == url, od razu `(`) to token: niecytowany do pierwszego
// nieescapowanego `)`. -2 = brak `)` do konca wejscia; url z napisem tokenizuje sie jak zwykla funkcja.
function skipWord(text, i) {
  const n = text.length;
  const c0 = text.charCodeAt(i);
  let j = c0 === 35 || c0 === 64 ? i + 1 : i;
  const from = j;
  let dec = '';
  let len = 0;
  const add = (cp) => {
    if (len < 4) dec += cp >= 65 && cp <= 90 ? String.fromCharCode(cp + 32) : cp > 0 && cp < 0x10000 ? String.fromCharCode(cp) : '?';
    len++;
  };
  while (j < n) {
    const ch = text.charCodeAt(j);
    if (isNameCode(ch)) {
      add(ch);
      j++;
    } else if (ch === 92) {
      const nx = text.charCodeAt(j + 1);
      if (j + 1 >= n || nx === 10 || nx === 13 || nx === 12) break;
      if (isHex(nx)) {
        let k = j + 1;
        while (k < n && k - j <= 6 && isHex(text.charCodeAt(k))) k++;
        add(parseInt(text.slice(j + 1, k), 16));
        const w = text.charCodeAt(k);
        if (w === 13 && text.charCodeAt(k + 1) === 10) k += 2;
        else if (isWs(w)) k++;
        j = k;
      } else {
        add(nx);
        j += 2;
      }
    } else break;
  }
  if (j === from) return c0 === 92 ? i + 2 : from;
  if (c0 === 35 || c0 === 64 || len !== 3 || dec !== 'url' || text.charCodeAt(j) !== 40) return j;
  const paren = j;
  j++;
  while (j < n && isWs(text.charCodeAt(j))) j++;
  const q = text.charCodeAt(j);
  // url z napisem: zwracamy indeks `(`, wiec wolajacy widzi zwykly nawias (stos zamykaczy zostaje spojny).
  if (q === 34 || q === 39) return paren;
  for (; j < n; j++) {
    const ch = text.charCodeAt(j);
    if (ch === 92) j++;
    else if (ch === 41) return j + 1;
  }
  return -2;
}

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
    if (ch === 92) {
      // `\` + nowa linia to kontynuacja napisu (CRLF liczy sie jako jedna nowa linia).
      if (text.charCodeAt(j + 1) === 13 && text.charCodeAt(j + 2) === 10) j += 2;
      else j++;
    } else if (ch === quote) return j + 1;
    else if (ch === 10 || ch === 13 || ch === 12) return -1;
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

const COMMENT_MARK = '\u0001';

// Zastepuje komentarze spacjami (mark: pojedynczym znacznikiem U+0001, bez zachowania pozycji) (zachowuje znaki nowej linii), waliduje napisy i komentarze.
function stripComments(text, mark = false) {
  // Literalny U+0001 w wejsciu nie moze udawac znacznika komentarza (ta sama dlugosc, wiec pozycje bez zmian).
  if (mark && text.indexOf(COMMENT_MARK) >= 0) text = text.replaceAll(COMMENT_MARK, '\uFFFD');
  const n = text.length;
  const parts = [];
  let last = 0;
  for (let i = 0; i < n; ) {
    const c = text.charCodeAt(i);
    if (c === 34 || c === 39) {
      const end = skipString(text, i);
      if (end < 0) throw fail(text, i, 'unterminated-string', 'Unterminated string');
      i = end;
    } else if (isWordStart(c)) {
      const e = skipWord(text, i);
      if (e === -2) throw fail(text, i, 'unterminated-url', 'Unterminated url(');
      else i = e;
    } else if (c === 47 && text.charCodeAt(i + 1) === 42) {
      const end = text.indexOf('*/', i + 2);
      if (end < 0) throw fail(text, i, 'unterminated-comment', 'Unterminated comment');
      parts.push(text.slice(last, i), mark ? COMMENT_MARK : blank(text.slice(i, end + 2)));
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

// Zwija biale znaki do jednej spacji poza napisami i przycina. Tryb marked (diff): ciag zlozony z bialych znakow i
// znacznikow komentarza U+0001 (poza slowem: terminator escape zjety przez skipWord nie jest bialym znakiem) daje
// jedna spacje, gdy zawiera bialy znak; sam znacznik zostaje (`a/**/b` != `a b`); na krawedziach tekstu znika.
function collapse(s, marked = false) {
  const n = s.length;
  const out = [];
  const isSep = marked ? (c) => isWs(c) || c === 1 : isWs;
  let last = 0;
  for (let i = 0; i < n; ) {
    const c = s.charCodeAt(i);
    if (isSep(c)) {
      if (i > last) out.push(s.slice(last, i));
      let k = i;
      let ws = false;
      while (k < n && isSep(s.charCodeAt(k))) {
        if (isWs(s.charCodeAt(k))) ws = true;
        k++;
      }
      if (out.length && k < n) out.push(ws ? ' ' : s.slice(i, k));
      i = k;
      last = k;
    } else if (c === 34 || c === 39) {
      let end = skipString(s, i);
      if (end < 0) end = n;
      i = end;
    } else if (isWordStart(c)) {
      const e = skipWord(s, i);
      i = e === -2 ? n : e;
    } else {
      i++;
    }
  }
  if (last < n) out.push(s.slice(last, n));
  return out.join('');
}

export function collapseMarked(s) {
  return collapse(s, true);
}

// Dzieli po znaku separatora na najwyzszym poziomie (poza (), [], napisami i escape).
function splitTop(text, sepCode) {
  const n = text.length;
  const out = [];
  const closers = [];
  let start = 0;
  const push = (end) => {
    const part = text.slice(start, end).trim();
    if (part) out.push(part);
  };
  for (let i = 0; i < n; ) {
    const c = text.charCodeAt(i);
    if (c === 34 || c === 39) {
      const end = skipString(text, i);
      i = end < 0 ? n : end;
    } else if (isWordStart(c)) {
      const e = skipWord(text, i);
      i = e === -2 ? n : e;
    } else {
      if (c === 40) closers.push(41);
      else if (c === 91) closers.push(93);
      else if ((c === 41 || c === 93) && closers.length > 0 && closers[closers.length - 1] === c) closers.pop();
      else if (c === sepCode && closers.length === 0) {
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
    } else if (isWordStart(c)) {
      const e = skipWord(decl, i);
      i = e === -2 ? n : e;
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
    if (c === 34 || c === 39) {
      const end = skipString(clean, i);
      i = end < 0 ? n : end;
    } else if (isWordStart(c)) {
      const e = skipWord(clean, i);
      i = e === -2 ? n : e;
    } else {
      if (c === 123) count++;
      i++;
    }
  }
  return count;
}

// Identyfikator z [from, to) po zdekodowaniu escape CSS, malymi literami.
function decodeIdent(text, from, to) {
  let out = '';
  for (let j = from; j < to; ) {
    if (text.charCodeAt(j) !== 92) {
      out += text.charAt(j);
      j++;
      continue;
    }
    const nx = text.charCodeAt(j + 1);
    if (j + 1 >= to) break;
    if (isHex(nx)) {
      let k = j + 1;
      while (k < to && k - j <= 6 && isHex(text.charCodeAt(k))) k++;
      const cp = parseInt(text.slice(j + 1, k), 16);
      out += cp === 0 || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff) ? '\ufffd' : String.fromCodePoint(cp);
      const w = text.charCodeAt(k);
      if (w === 13 && text.charCodeAt(k + 1) === 10) k += 2;
      else if (k < to && isWs(w)) k++;
      j = k;
    } else {
      const cp = text.codePointAt(j + 1);
      out += String.fromCodePoint(cp);
      j += 1 + (cp > 0xffff ? 2 : 1);
    }
  }
  return out.toLowerCase();
}

export function countImportant(text) {
  const clean = stripComments(text);
  const n = clean.length;
  let count = 0;
  for (let i = 0; i < n; ) {
    const c = clean.charCodeAt(i);
    if (c === 34 || c === 39) {
      const end = skipString(clean, i);
      i = end < 0 ? n : end;
    } else if (isWordStart(c)) {
      const e = skipWord(clean, i);
      i = e === -2 ? n : e;
    } else {
      i++;
      if (c === 33) {
        let j = i;
        while (j < n && isWs(clean.charCodeAt(j))) j++;
        const cj = clean.charCodeAt(j);
        if (isNameCode(cj) || cj === 92) {
          const e = skipWord(clean, j);
          if (e > j && e - j <= 100 && decodeIdent(clean, j, e) === 'important') {
            count++;
            i = e;
          }
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
    if (c === 34 || c === 39) {
      const end = skipString(text, i);
      i = end < 0 ? n : end;
    } else if (isWordStart(c)) {
      const e = skipWord(text, i);
      i = e === -2 ? n : e;
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

const isHexCode = (c) => (c >= 48 && c <= 57) || (c >= 65 && c <= 70) || (c >= 97 && c <= 102);

// Nazwa at-reguly z dekodowaniem escape CSS (`@\6d edia` == `@media`), malymi literami.
function atName(head) {
  let out = '';
  let i = 1;
  while (i < head.length) {
    const c = head.charCodeAt(i);
    if (c === 92) {
      let j = i + 1;
      if (j >= head.length || isWs(head.charCodeAt(j))) break;
      if (isHexCode(head.charCodeAt(j))) {
        let k = j;
        while (k < head.length && k - j < 6 && isHexCode(head.charCodeAt(k))) k++;
        const cp = parseInt(head.slice(j, k), 16);
        out += cp === 0 || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff) ? '\ufffd' : String.fromCodePoint(cp);
        j = k;
        if (isWs(head.charCodeAt(j))) j++;
        i = j;
      } else {
        const cp = head.codePointAt(j);
        out += String.fromCodePoint(cp);
        i = j + (cp > 0xffff ? 2 : 1);
      }
    } else if (isWordCode(c)) {
      out += head.charAt(i);
      i++;
    } else {
      break;
    }
  }
  return out.toLowerCase();
}

// Preludia regul zagniezdzonych w ciele reguly (komentarze juz usuniete); pusta lista, gdy ciala bez `{`.
function nestedPreludes(body) {
  if (body.indexOf('{') < 0) return [];
  const n = body.length;
  const out = [];
  const closers = [];
  let preStart = 0;
  for (let i = 0; i < n; ) {
    const c = body.charCodeAt(i);
    if (c === 34 || c === 39) {
      const end = skipString(body, i);
      i = end < 0 ? n : end;
    } else if (isWordStart(c)) {
      const e = skipWord(body, i);
      i = e === -2 ? n : e;
    } else {
      if (c === 40) closers.push(41);
      else if (c === 91) closers.push(93);
      else if ((c === 41 || c === 93) && closers.length > 0 && closers[closers.length - 1] === c) closers.pop();
      else if (c === 123) {
        out.push(collapse(body.slice(preStart, i)));
        closers.length = 0;
        preStart = i + 1;
      } else if (c === 125 || (c === 59 && closers.length === 0)) {
        closers.length = 0;
        preStart = i + 1;
      }
      i++;
    }
  }
  return out;
}

const MAX_DEPTH = 256;

export function scan(text, options = {}) {
  const mark = options?.markComments === true;
  const clean = stripComments(String(text), mark);
  const col = mark ? collapseMarked : collapse;
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
  const closers = [];
  const openedAt = [];
  const bracketError = (idx) => fail(clean, idx, 'unbalanced-bracket', 'Unbalanced bracket in prelude');
  const tooDeep = (idx) => fail(clean, idx, 'too-deep', `Nesting deeper than ${MAX_DEPTH} levels`);

  for (let i = 0; i < n; ) {
    const c = clean.charCodeAt(i);
    if (
      stack.length === 1 &&
      closers.length === 0 &&
      ((c === 60 && clean.startsWith('<!--', i)) || (c === 45 && clean.startsWith('-->', i))) &&
      firstNonWs(preStart, i) === i
    ) {
      // CDO/CDC na poziomie arkusza to biale znaki (poza preludium).
      i += c === 60 ? 4 : 3;
      preStart = i;
    } else if (c === 34 || c === 39) {
      const end = skipString(clean, i);
      i = end < 0 ? n : end;
    } else if (isWordStart(c)) {
      const e = skipWord(clean, i);
      i = e === -2 ? n : e;
    } else if (c === 40 || c === 91) {
      closers.push(c === 40 ? 41 : 93);
      openedAt.push(i);
      i++;
    } else if (c === 41 || c === 93) {
      if (closers.length > 0) {
        if (closers[closers.length - 1] !== c) throw bracketError(i);
        closers.pop();
        openedAt.pop();
      }
      i++;
    } else if ((c === 59 || c === 123 || c === 125) && closers.length > 0) {
      throw bracketError(i);
    } else if (c === 59) {
      const prelude = col(clean.slice(preStart, i));
      if (prelude.charCodeAt(0) === 64 && stack[stack.length - 1].items !== null) {
        stack[stack.length - 1].items.push({ kind: 'statement', text: prelude, line: lineOf(firstNonWs(preStart, i)) });
      }
      i++;
      preStart = i;
    } else if (c === 123) {
      const top = stack[stack.length - 1];
      const prelude = col(clean.slice(preStart, i));
      const line = lineOf(firstNonWs(preStart, i));
      if (top.kind === 'keyframes') {
        const close = findClose(clean, i);
        top.node.steps.push({ selector: prelude, body: clean.slice(i + 1, close), line });
        i = close + 1;
      } else if (prelude.charCodeAt(0) === 64) {
        const name = atName(prelude);
        if (KEYFRAMES_RE.test(name)) {
          const node = { kind: 'keyframes', head: prelude, steps: [], line };
          if (stack.length > MAX_DEPTH) throw tooDeep(i);
          top.items.push(node);
          stack.push({ kind: 'keyframes', node, items: null, heads: top.heads, openIdx: i });
          i++;
        } else if (GROUP_NAMES.has(name) || !LEAF_NAMES.has(name)) {
          const node = { kind: 'group', head: prelude, children: [], line };
          if (stack.length > MAX_DEPTH) throw tooDeep(i);
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
        const ruleBody = clean.slice(i + 1, close);
        const rule = {
          kind: 'rule',
          selector: prelude,
          body: ruleBody,
          nested: nestedPreludes(ruleBody),
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
      i++;
      preStart = i;
    } else {
      i++;
    }
  }
  if (closers.length > 0) throw bracketError(openedAt[openedAt.length - 1]);
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
