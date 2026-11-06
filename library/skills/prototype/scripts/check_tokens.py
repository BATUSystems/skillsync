#!/usr/bin/env python3
"""Kontrola tokenów i szablonów skilla prototypów (Python 3, tylko biblioteka standardowa).

Sprawdza wyłącznie pliki CSS, HTML i JSON (nie ładuje silnika ani żadnego pliku JS):

1. Tokeny motywu: każdy token z `required-tokens.txt` jest zdefiniowany w jasnym bloku `:root` motywu,
   token zdefiniowany tylko w bloku ciemnym jest błędem, a lista wymaganych tokenów musi być równa zbiorowi
   tokenów używanych przez zasoby (`var(--nazwa)` w plikach CSS/HTML katalogu `--assets`; pomijane są
   zmienne zdefiniowane lokalnie w tych samych plikach oraz zmienne `--proto-*` ustawiane przez silnik).
2. Szablony v2: `comments.json` (pusty dziennik z `{{SLUG}}`), `.gitignore`, `.gitattributes`, brak
   `comments.js`, `index.html` bez odwołania do `comments.js` i z blokadą motywu
   (`meta color-scheme` z `light` i `dark` oraz `meta darkreader-lock` w `<head>`).
3. Fikstury: fold v1 (eksporty `comments.js`) i niezależny fold v2 z etykietami osób, porównane z plikami
   `*.expected.json`.

Kody wyjścia: 0 = wszystko zgodne, 1 = wykryto rozjazd (komunikaty na stderr), 2 = błąd wywołania.
"""
import argparse
import html.parser
import json
import re
import sys
from pathlib import Path

COMMENTS_HEADER = '{"format":"skillsync-prototype-comments","version":2,"prototypeId":"{{SLUG}}","operations":['
V1_PREFIX = 'window.__SKILLSYNC_PROTOTYPE_COMMENTS__ = '
TOKEN_RE = re.compile(r'--[A-Za-z0-9_-]+')
ENGINE_PREFIX = '--proto-'


def utf16_key(s):
    """Klucz porównania zgodny z operatorem `<` w JS (jednostki UTF-16)."""
    return s.encode('utf-16-be', 'surrogatepass')


# ---------------------------------------------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------------------------------------------

def strip_css_comments(text):
    return re.sub(r'/\*.*?\*/', '', text, flags=re.S)


def css_declarations(text, with_values=False):
    """Zwraca listę (stos_selektorów, nazwa_tokenu) dla deklaracji niestandardowych właściwości;
    z `with_values` krotki mają trzeci element: wartość ze znormalizowanymi białymi znakami."""
    text = strip_css_comments(text)
    out = []
    stack = []
    buf = []
    paren = 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in '"\'':
            j = i + 1
            while j < n and text[j] != ch:
                j += 2 if text[j] == '\\' else 1
            buf.append(text[i:j + 1])
            i = j + 1
            continue
        if ch == '(':
            paren += 1
        elif ch == ')':
            paren = max(0, paren - 1)
        if paren == 0 and ch == '{':
            stack.append(''.join(buf).strip())
            buf = []
        elif paren == 0 and ch in ';}':
            decl = ''.join(buf)
            m = re.match(r'\s*(--[A-Za-z0-9_-]+)\s*:', decl)
            if m and stack:
                if with_values:
                    out.append((tuple(stack), m.group(1), ' '.join(decl[m.end():].split())))
                else:
                    out.append((tuple(stack), m.group(1)))
            buf = []
            if ch == '}' and stack:
                stack.pop()
        else:
            buf.append(ch)
        i += 1
    return out


def is_dark(stack):
    return any(re.search(r'\bdark\b|\.dark\b|data-theme\s*=\s*["\']?dark', sel) for sel in stack)


def is_root_block(stack):
    parts = [p.strip() for p in stack[-1].split(',')]
    return any(re.match(r':root\b', p) for p in parts)


def theme_tokens(css, blocks=None):
    """Zwraca (jasne, ciemne). Gdy podano listę `blocks`, trafia do niej słownik token -> znormalizowana wartość
    każdego bloku ciemnego osobno (klucz: stos selektorów bloku), w kolejności pierwszego wystąpienia."""
    light, dark = set(), set()
    per_block = {}
    for stack, name, value in css_declarations(css, with_values=True):
        if is_dark(stack):
            dark.add(name)
            per_block.setdefault(' / '.join(stack), {})[name] = value
        elif is_root_block(stack):
            light.add(name)
    if blocks is not None:
        blocks.extend(per_block.items())
    return light, dark


def used_tokens(assets_dir):
    """Tokeny używane przez zasoby szablonu: var(--x) w *.css i *.html, bez zmiennych definiowanych lokalnie."""
    used, local = set(), set()
    for path in sorted(Path(assets_dir).rglob('*')):
        if not path.is_file() or path.suffix.lower() not in ('.css', '.html'):
            continue
        text = path.read_text(encoding='utf-8-sig')
        text = strip_css_comments(text)
        used.update(re.findall(r'var\(\s*(--[A-Za-z0-9_-]+)', text))
        local.update(name for _, name in css_declarations(text))
        local.update(re.findall(r'(?<![\w-])(--[A-Za-z0-9_-]+)\s*:', text))
    return {t for t in used - local if not t.startswith(ENGINE_PREFIX)}


def read_required(path):
    return [ln.strip() for ln in Path(path).read_text(encoding='utf-8-sig').splitlines()
            if ln.strip() and not ln.strip().startswith('#')]


def check_theme(theme, required_path, assets, errors):
    theme, required_path, assets = Path(theme), Path(required_path), Path(assets)
    if not theme.is_file():
        errors.append('brak pliku motywu: %s' % theme)
        return None
    if not required_path.is_file():
        errors.append('brak listy wymaganych tokenów: %s' % required_path)
        return None
    try:
        required = read_required(required_path)
    except (OSError, ValueError) as e:
        errors.append('%s: nie można odczytać listy wymaganych tokenów: %s' % (required_path.name, e))
        return None
    try:
        theme_text = theme.read_text(encoding='utf-8-sig')
    except (OSError, ValueError) as e:
        errors.append('%s: nie można odczytać pliku motywu: %s' % (theme.name, e))
        return None
    dark_blocks = []
    light, dark = theme_tokens(theme_text, dark_blocks)
    if not light:
        errors.append('%s: brak jasnego bloku :root z tokenami' % theme.name)
    for tok in required:
        if tok not in light:
            errors.append('%s: brak definicji wymaganego tokenu %s w jasnym bloku :root' % (theme.name, tok))
    for tok in sorted(dark - light):
        errors.append('%s: token %s zdefiniowany tylko w bloku ciemnym' % (theme.name, tok))
    # Dwa bloki ciemne (np. `prefers-color-scheme` i `data-theme="dark"`) muszą definiować te same tokeny.
    for i in range(1, len(dark_blocks)):
        (name_a, vals_a), (name_b, vals_b) = dark_blocks[0], dark_blocks[i]
        for tok in sorted(set(vals_a) ^ set(vals_b)):
            only = name_a if tok in vals_a else name_b
            errors.append('%s: token %s jest tylko w jednym z bloków ciemnych (%s)' % (theme.name, tok, only))
        # Wspólne tokeny muszą mieć te same wartości (po normalizacji białych znaków).
        for tok in sorted(set(vals_a) & set(vals_b)):
            if vals_a[tok] != vals_b[tok]:
                errors.append('%s: token %s ma różne wartości w blokach ciemnych: %s = "%s" vs %s = "%s"'
                              % (theme.name, tok, name_a, vals_a[tok], name_b, vals_b[tok]))
    if assets.is_dir():
        used = used_tokens(assets)
        for tok in sorted(set(required) - used):
            errors.append('%s: wymagany token %s nie jest używany przez zasoby (%s)'
                          % (required_path.name, tok, assets.name))
        for tok in sorted(used - set(required)):
            errors.append('%s: zasoby używają tokenu %s, którego nie ma na liście' % (required_path.name, tok))
    return len(required)


# ---------------------------------------------------------------------------------------------------------------
# Szablony
# ---------------------------------------------------------------------------------------------------------------

class MetaCollector(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.metas = []  # (attrs, w <head>)
        self.refs = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'meta':
            self.metas.append((a, 'head' in self.stack))
        for key in ('src', 'href'):
            if a.get(key):
                self.refs.append(a[key])
        if tag not in ('meta', 'link', 'br', 'img', 'input', 'hr', 'source', 'area', 'base', 'col', 'embed',
                       'track', 'wbr'):
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass


def check_index(index, errors):
    if not index.is_file():
        errors.append('brak pliku %s' % index)
        return
    text = index.read_text(encoding='utf-8-sig')
    c = MetaCollector()
    c.feed(text)
    c.close()
    scheme = [(a, h) for a, h in c.metas if a.get('name') == 'color-scheme']
    lock = [(a, h) for a, h in c.metas if a.get('name') == 'darkreader-lock']
    if not scheme:
        errors.append('%s: brak meta color-scheme' % index.name)
    else:
        content = ' '.join((a.get('content') or '') for a, _ in scheme).split()
        if 'light' not in content or 'dark' not in content:
            errors.append('%s: meta color-scheme bez light i dark' % index.name)
        if not any(h for _, h in scheme):
            errors.append('%s: meta color-scheme poza <head>' % index.name)
    if not lock:
        errors.append('%s: brak meta darkreader-lock' % index.name)
    elif not any(h for _, h in lock):
        errors.append('%s: meta darkreader-lock poza <head>' % index.name)
    if any(r.split('?')[0].split('#')[0].endswith('comments.js') for r in c.refs) or 'comments.js' in text:
        errors.append('%s: odwołanie do comments.js (szablon v2 używa comments.json)' % index.name)


def check_comments_template(path, errors):
    path = Path(path)
    if not path.is_file():
        errors.append('brak szablonu komentarzy: %s' % path)
        return
    text = path.read_text(encoding='utf-8-sig')
    if text not in (COMMENTS_HEADER + '\n]}\n', COMMENTS_HEADER + '\n]}'):
        errors.append('%s: szablon nie jest pustym dziennikiem v2 (nagłówek, `]}`, '
                      '"prototypeId":"{{SLUG}}")' % path.name)
        return
    try:
        doc = json.loads(text.replace('{{SLUG}}', 'x'))
    except ValueError as e:
        errors.append('%s: niepoprawny JSON: %s' % (path.name, e))
        return
    if doc.get('operations') != []:
        errors.append('%s: dziennik szablonu nie jest pusty' % path.name)


def check_repo_templates(assets, errors):
    gi = assets / '.gitignore'
    if not gi.is_file():
        errors.append('brak pliku %s' % gi)
    else:
        lines = {ln.strip() for ln in gi.read_text(encoding='utf-8-sig').splitlines()}
        for need in ('comments.json.lock', '*.tmp'):
            if need not in lines:
                errors.append('.gitignore: brak wpisu %s' % need)
    ga = assets / '.gitattributes'
    if not ga.is_file():
        errors.append('brak pliku %s' % ga)
    else:
        lines = {' '.join(ln.split()) for ln in ga.read_text(encoding='utf-8-sig').splitlines()}
        if 'comments.json merge=skillsync-comments' not in lines:
            errors.append('.gitattributes: brak wpisu `comments.json merge=skillsync-comments`')
    if (assets / 'comments.js').exists():
        errors.append('%s: szablon v2 nie może zawierać comments.js' % assets)


# ---------------------------------------------------------------------------------------------------------------
# Fold v1
# ---------------------------------------------------------------------------------------------------------------

def load_v1_export(path):
    text = Path(path).read_text(encoding='utf-8-sig')
    i = text.index(V1_PREFIX)
    j = text.rindex(';')
    return json.loads(text[i + len(V1_PREFIX):j])


def fold_v1(doc):
    ops, seen = [], set()
    for op in doc.get('operations') or []:
        if not isinstance(op, dict):
            continue
        if not all(isinstance(op.get(k), str) and op.get(k) for k in ('id', 'type', 'threadId', 'at')):
            continue
        if op['type'] not in ('create', 'reply', 'reanchor', 'resolve', 'reopen', 'delete') or op['id'] in seen:
            continue
        seen.add(op['id'])
        if not isinstance(op.get('payload'), dict):
            op = dict(op, payload={})
        ops.append(op)
    ops.sort(key=lambda o: (utf16_key(o['at']), utf16_key(o['id'])))
    deleted = {o['threadId'] for o in ops if o['type'] == 'delete'}
    threads, order = {}, []
    for op in ops:
        tid, p, t = op['threadId'], op['payload'], op['type']
        if tid in deleted:
            continue
        th = threads.get(tid)
        if t == 'create':
            msg = p.get('message')
            if th or not msg or not isinstance(p.get('screen'), str):
                continue
            th = {'id': tid, 'screen': p['screen'], 'anchor': p.get('anchor'), 'label': p.get('label'),
                  'resolved': False, 'messages': [msg.get('id')]}
            threads[tid] = th
            order.append(tid)
        elif th is None:
            continue
        elif t == 'reply':
            msg = p.get('message')
            if msg and isinstance(msg.get('id'), str) and msg['id'] not in th['messages']:
                th['messages'].append(msg['id'])
        elif t == 'resolve':
            th['resolved'] = True
        elif t == 'reopen':
            th['resolved'] = False
        elif t == 'reanchor':
            for k in ('screen', 'anchor', 'label'):
                if k in p:
                    th[k] = p[k]
    result = [threads[t] for t in order]
    counts = {}
    for th in result:
        if not th['resolved']:
            counts[th['screen']] = counts.get(th['screen'], 0) + 1
    return {'threads': result, 'openCountByScreen': counts}


# ---------------------------------------------------------------------------------------------------------------
# Fold v2
# ---------------------------------------------------------------------------------------------------------------

THREAD_TYPES = {'create', 'reply', 'edit', 'retract', 'vote', 'resolve', 'reopen', 'reanchor', 'delete', 'triage',
                'decision'}
TRIAGE_CLASSES = ('clear', 'disputed', 'out_of_scope')
GLOBAL_TYPES = {'iteration', 'restore', 'accept', 'unaccept'}


def normalize_v2(doc):
    ops, seen = [], set()
    for op in doc.get('operations') or []:
        if not isinstance(op, dict):
            continue
        t = op.get('type')
        if t not in THREAD_TYPES and t not in GLOBAL_TYPES:
            continue
        need = ('id', 'at', 'threadId') if t in THREAD_TYPES else ('id', 'at')
        if not all(isinstance(op.get(k), str) and op.get(k) for k in need):
            continue
        if op['id'] in seen:
            continue
        seen.add(op['id'])
        op = dict(op)
        op['by'] = op['by'] if isinstance(op.get('by'), str) and op['by'] else None
        if not isinstance(op.get('payload'), dict):
            op['payload'] = {}
        ops.append(op)
    ops.sort(key=lambda o: (utf16_key(o['at']), utf16_key(o['id'])))
    return ops


def display_name(login):
    local = login.split('@')[0]
    words = [w for w in re.split(r'[.\s]+', local) if w]
    return [w[0].upper() + w[1:] for w in words] or [login]


def compute_labels(logins):
    names = {l: display_name(l) for l in logins}
    groups = {}
    for l, w in names.items():
        groups.setdefault(w[0], []).append(l)
    labels = {}
    for first, members in groups.items():
        if len(members) == 1:
            labels[members[0]] = first
            continue
        short = {}
        for l in members:
            w = names[l]
            short[l] = first + (' ' + w[1][0].upper() + '.' if len(w) > 1 else '')
        for l in members:
            dup = sum(1 for m in members if short[m] == short[l]) > 1
            labels[l] = ' '.join(names[l]) if dup else short[l]
    return labels


def fold_v2(doc):
    ops = normalize_v2(doc)
    deleted = {o['threadId'] for o in ops if o['type'] == 'delete'}
    threads, order, removed = {}, [], {}
    number = 0
    iterations = []
    for op in ops:
        t, p = op['type'], op['payload']
        if t == 'iteration':
            n = p.get('number')
            if isinstance(n, int) and not isinstance(n, bool) and n >= 1:
                iterations.append(n)
            continue
        if t in GLOBAL_TYPES:
            continue
        tid = op['threadId']
        th = threads.get(tid)
        if t == 'create':
            if tid in threads or tid in removed:
                continue
            msg = p.get('message')
            if not msg or not isinstance(p.get('screen'), str):
                continue
            number += 1
            anchor = p.get('anchor') if isinstance(p.get('anchor'), dict) else {}
            kind = anchor.get('kind') if anchor.get('kind') in ('element', 'screen', 'lost') else 'lost'
            if tid in deleted:
                removed[tid] = 'deleted'
                continue
            threads[tid] = th = {
                'id': tid, 'number': number, 'screen': p['screen'], 'anchorKind': kind,
                'protoId': anchor.get('protoId') if kind == 'element' else None,
                'label': p.get('label'), 'by': op['by'], 'status': 'open', 'statusReason': None,
                'triage': None, 'decisions': [], 'messages': [],
                'view': 'mobile' if p.get('view') == 'mobile' else 'desktop'}
            order.append(tid)
            th['messages'].append(new_message(msg, op['by']))
            continue
        if t == 'delete':
            if th is not None:
                del threads[tid]
                order.remove(tid)
                removed[tid] = 'deleted'
            continue
        if th is None:
            continue
        msgs = {m['id']: m for m in th['messages']}
        if t == 'reply':
            msg = p.get('message')
            if msg and isinstance(msg.get('id'), str) and msg['id'] not in msgs:
                th['messages'].append(new_message(msg, op['by']))
        elif t == 'edit':
            m = msgs.get(p.get('messageId'))
            # `by` None (brak autora) nigdy nie pasuje do autora wiadomości, tak jak `by !== null` w silniku.
            if m and op['by'] is not None and m['by'] == op['by'] and not m['retracted'] and isinstance(p.get('text'), str):
                m['text'], m['edited'] = p['text'], True
        elif t == 'retract':
            m = msgs.get(p.get('messageId'))
            if m and op['by'] is not None and m['by'] == op['by'] and not m['retracted']:
                m['retracted'] = True
                if m is th['messages'][0]:
                    others = [x for x in th['messages'][1:] if not x['retracted'] and x['by'] != op['by']]
                    if not others:
                        del threads[tid]
                        order.remove(tid)
                        removed[tid] = 'retracted'
        elif t == 'vote':
            m = msgs.get(p.get('messageId'))
            by = op['by']
            if m and not m['retracted'] and by:
                for side in ('up', 'down'):
                    if by in m[side]:
                        m[side].remove(by)
                if p.get('value') in ('up', 'down'):
                    m[p['value']].append(by)
        elif t == 'resolve':
            th['status'] = 'closed'
            th['statusReason'] = p.get('reason') if isinstance(p.get('reason'), str) else None
        elif t == 'reopen':
            th['status'], th['statusReason'] = 'open', None
        elif t == 'reanchor':
            if isinstance(p.get('screen'), str):
                th['screen'] = p['screen']
            anchor = p.get('anchor')
            if isinstance(anchor, dict):
                kind = anchor.get('kind') if anchor.get('kind') in ('element', 'screen', 'lost') else 'lost'
                th['anchorKind'] = kind
                th['protoId'] = anchor.get('protoId') if kind == 'element' else None
            if 'label' in p:
                th['label'] = p['label']
        elif t == 'triage':
            # Nieznana klasa jest pomijana w całości, jak SKIPPED w silniku (`TRIAGE_CLASSES`).
            if p.get('class') not in TRIAGE_CLASSES:
                continue
            th['triage'] = p.get('class')
            if p.get('class') == 'disputed':
                th['status'], th['statusReason'] = 'awaiting', None
        elif t == 'decision':
            outcome = p.get('outcome')
            th['decisions'].append('%s:%s' % (p.get('kind'), outcome))
            if outcome in ('apply', 'variant'):
                th['status'], th['statusReason'] = 'decided', None
            elif outcome in ('keep', 'out_of_scope'):
                th['status'] = 'closed'
                th['statusReason'] = 'kept' if outcome == 'keep' else 'out_of_scope'
            elif outcome == 'defer':
                th['status'], th['statusReason'] = 'deferred', None
    result = [threads[t] for t in sorted(order, key=lambda x: threads[x]['number'])]
    counts = {}
    for th in result:
        if th['status'] in ('open', 'awaiting', 'decided'):
            counts[th['screen']] = counts.get(th['screen'], 0) + 1
    logins = sorted({o['by'] for o in ops if o['by']})
    labels = compute_labels(logins)
    return {'threads': result, 'removed': removed, 'currentIteration': max(iterations) + 1 if iterations else 1,
            'openCountByScreen': counts, 'people': labels}


def new_message(msg, by):
    return {'id': msg.get('id'), 'by': by, 'text': msg.get('text'), 'edited': False, 'retracted': False,
            'up': [], 'down': []}


# ---------------------------------------------------------------------------------------------------------------
# Porównanie z oczekiwaniem
# ---------------------------------------------------------------------------------------------------------------

def diff_values(a, b, path):
    """Różnice (oczekiwane a, otrzymane b) jako lista napisów; listy wątków dopasowywane po `id`."""
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a:
            if k not in b:
                out.append('%s: brak pola %s' % (path, k))
            else:
                out += diff_values(a[k], b[k], '%s.%s' % (path, k) if path else k)
        for k in b:
            if k not in a:
                out.append('%s: nadmiarowe pole %s' % (path, k))
    elif isinstance(a, list) and isinstance(b, list) and a and b and all(isinstance(x, dict) and 'id' in x
                                                                           for x in a + b):
        ia = {x['id']: x for x in a}
        ib = {x['id']: x for x in b}
        for k in ia:
            if k not in ib:
                out.append('%s: brak elementu %s' % (path, k))
            else:
                out += diff_values(ia[k], ib[k], '%s[%s]' % (path, k))
        for k in ib:
            if k not in ia:
                out.append('%s: nadmiarowy element %s' % (path, k))
        if [x['id'] for x in a] != [x['id'] for x in b] and set(ia) == set(ib):
            out.append('%s: inna kolejność elementów' % path)
    elif a != b:
        out.append('%s: oczekiwano %s, jest %s' % (path, json.dumps(a, ensure_ascii=False),
                                                   json.dumps(b, ensure_ascii=False)))
    return out


def compare_fold(label, expected_path, got, errors):
    expected_path = Path(expected_path)
    if not expected_path.is_file():
        errors.append('brak pliku oczekiwań %s' % expected_path)
        return False
    try:
        expected = json.loads(expected_path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as e:
        errors.append('%s: nie można wczytać pliku oczekiwań: %s' % (expected_path.name, e))
        return False
    problems = diff_values(expected, got, '')
    for pr in problems:
        errors.append('%s (%s): %s' % (expected_path.name, label, pr))
    return not problems


def check_fixtures(fixtures, export, errors):
    notes = []
    v1 = fixtures / 'v1'
    v2 = fixtures / 'v2'
    if export:
        exports = [Path(export)]
    else:
        exports = sorted(v1.glob('export-comments*.js'))
    if not exports:
        errors.append('%s: brak eksportów v1 (export-comments*.js)' % v1)
    for exp in exports:
        try:
            got = fold_v1(load_v1_export(exp))
        except (ValueError, OSError) as e:
            errors.append('%s: nie można wczytać eksportu v1: %s' % (exp.name, e))
            continue
        compare_fold('fold v1', exp.with_name(exp.stem + '.expected.json'), got, errors)
    notes.append('eksporty v1: %d' % len(exports))
    log = v2 / 'comments-v2.json'
    if not log.is_file():
        errors.append('brak pliku %s' % log)
    else:
        try:
            doc = json.loads(log.read_text(encoding='utf-8-sig'))
        except ValueError as e:
            errors.append('%s: niepoprawny JSON: %s' % (log.name, e))
        else:
            compare_fold('fold v2', v2 / 'comments-v2.expected.json', fold_v2(doc), errors)
            notes.append('dziennik v2: %d operacji' % len(doc.get('operations') or []))
    return notes


# ---------------------------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(
        prog='check_tokens.py',
        description='Kontrola tokenów motywu, szablonów v2 i foldu komentarzy skilla prototypów.')
    ap.add_argument('skill_dir', nargs='?', help='katalog skilla prototype (domyślnie nadrzędny wobec scripts/)')
    ap.add_argument('--assets', help='katalog szablonów repozytorium prototypu (domyślnie <skill>/assets/repo)')
    ap.add_argument('--theme', help='plik motywu (domyślnie <skill>/assets/theme/default.css)')
    ap.add_argument('--required', help='lista wymaganych tokenów (domyślnie <skill>/assets/theme/required-tokens.txt)')
    ap.add_argument('--comments', help='szablon comments.json (domyślnie <assets>/comments.json)')
    ap.add_argument('--export', help='ogranicza kontrolę v1 do jednego eksportu comments.js '
                                     '(oczekiwanie: obok, <nazwa>.expected.json)')
    ap.add_argument('--fixtures', help='katalog z v1/ i v2/ (domyślnie <skill>/scripts/fixtures)')
    ap.add_argument('--print-used', action='store_true',
                    help='wypisz tokeny używane przez zasoby (posortowane) i zakończ')
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    skill = Path(args.skill_dir).resolve() if args.skill_dir else Path(__file__).resolve().parent.parent
    if not skill.is_dir():
        ap.error('katalog skilla nie istnieje: %s' % (args.skill_dir or skill))
    assets = Path(args.assets) if args.assets else skill / 'assets' / 'repo'
    theme = Path(args.theme) if args.theme else skill / 'assets' / 'theme' / 'default.css'
    required = Path(args.required) if args.required else skill / 'assets' / 'theme' / 'required-tokens.txt'
    comments = Path(args.comments) if args.comments else assets / 'comments.json'
    fixtures = Path(args.fixtures) if args.fixtures else skill / 'scripts' / 'fixtures'
    if args.fixtures and not fixtures.is_dir():
        ap.error('katalog fikstur nie istnieje: %s' % fixtures)
    if args.print_used:
        if not assets.is_dir():
            ap.error('katalog zasobów nie istnieje: %s' % assets)
        for tok in sorted(used_tokens(assets)):
            print(tok)
        return 0

    errors = []
    notes = []
    n = check_theme(theme, required, assets, errors)
    if n is not None:
        notes.append('tokeny: %d' % n)
    if assets.is_dir():
        check_index(assets / 'index.html', errors)
        check_repo_templates(assets, errors)
    else:
        errors.append('brak katalogu zasobów: %s' % assets)
    check_comments_template(comments, errors)
    if fixtures.is_dir():
        notes += check_fixtures(fixtures, args.export, errors)
    else:
        errors.append('brak katalogu fikstur: %s' % fixtures)
    if errors:
        for e in errors:
            print('check_tokens: BŁĄD: %s' % e, file=sys.stderr)
        print('check_tokens: %d problemów' % len(errors), file=sys.stderr)
        return 1
    print('check_tokens: OK (%s)' % ', '.join(notes))
    return 0


if __name__ == '__main__':
    sys.exit(main())
