"""Questions of a spec that a table of its `## Pytania otwarte` / `## Open questions` section shows as resolved (U-4a): they are no longer blocking.

Only an EXPLICIT sign resolves a table row of the question `Q<n>` (its first cell): a cell that begins with the tag `[ROZSTRZYGNIĘTE …]` /
`[RESOLVED …]`, or a `Status` column whose value is exactly `rozstrzygnięte` / `resolved` (with an optional date and author). A filled answer column
alone resolves nothing. In doubt the question stays blocking: a word that says it is open anywhere after the question, an empty or placeholder value
in an answer or status column, another status, struck-through text, cells that do not match the header, or the same id open elsewhere in the section.
"""
from __future__ import annotations

import re

from .common import file_lines

_HEADING = re.compile(r'^##\s+(?:Pytania otwarte|Open questions)\b', re.IGNORECASE)
_LIST_QUESTION = re.compile(r'^\s*[-*]\s+(?:\*\*)?(?P<id>Q\d+)(?:\*\*)?(?:\s*\[(?P<tag>[^\]]*)\])?\s*:')
_ROW_ID = re.compile(r'(?:\*\*)?(?P<id>Q\d+)(?:\*\*)?')
_CELL_BORDER = re.compile(r'(?<!\\)\|')
_SEPARATOR_CELL = re.compile(r':?-+:?')
_QUESTION_COLUMN = re.compile(r'(?i)(?:pytanie|question)\b')
_ANSWER_COLUMN = re.compile(r'(?i)(?:rozstrzygni|decyzj|odpowied|resolution|decision|answer)')   # the beginning of the name (`Odpowiedź właściciela`)
_STATUS_COLUMN = re.compile(r'(?i)\b(?:status|stan|state)\b')
_RESOLVED_TAG = re.compile(r'(?i)\[\s*(?:rozstrzygni[eę](?:te|ty|ta|to)|resolved)(?!\w)[^\]]*\]')
_RESOLVED_STATUS = re.compile(r'(?i)(?:rozstrzygni[eę](?:te|ty|ta|to)|resolved)'
                              r'(?:\s+(?:\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}(?:\.\d{2,4})?))?(?:\s*\([^()]*\))?\.?')
# words that say a question is (still, again or partly) open, searched anywhere in a cell after the question
_OPEN = re.compile(r'(?i)\?|~~|otwar|czeka|czekam|oczekuj|nie\s*rozstrz|cz[eę][sś]ciow|w\s+cz[eę][sś]ci|ponown|wst[eę]pn|warunkow|wstrzyman|w\s+trakcie|w\s+toku|'
                   r'odrocz|oczekiw|nie\s+do\s+ko[nń]ca|to\s+be\s|do\s+(?:zatwierdz|ustalenia|decyzji|potwierdzenia|uzgodnienia|weryfikacji|akceptacji|wyboru|om[oó]wienia|wyja[sś]nienia|rozstrzygni)|'
                   r'propo[zn]|suger|jeszcze|unresolved|undecided|re-?open|\bopen\b|pending|await|waiting|partial|partly|tentativ|\bhold\b|'
                   r'\bnot\s+(?:yet|decided|resolved)|\btb[dac]\b|\btodo\b')


def _cells(line):
    parts = _CELL_BORDER.split(line.strip())
    if parts and not parts[0]:
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return [part.strip() for part in parts]


def _plain(cell):
    """The text of a cell without the emphasis around it (`**x**`, `_x_`, backticks)."""
    return cell.strip().strip('*_` ').strip()


def _is_separator(cells):
    return bool(cells) and all(_SEPARATOR_CELL.fullmatch(cell) for cell in cells)


def _row_resolved(header, cells):
    """True only for a row with an explicit sign of resolution and nothing that casts doubt on it."""
    if header is not None and len(cells) != len(header):
        return False   # a stray `|` moved the cells: the columns cannot be told apart
    # the second cell is the question when the header says so, or in a table without a header that has more cells after it; only then may it hold
    # words of doubt (a question may ask about waiting) and end with `?`
    asked = bool(_QUESTION_COLUMN.match(_plain(header[1]))) if header is not None and len(header) > 1 else header is None and len(cells) > 2
    rest = cells[2:] if asked else cells[1:]
    if any(_OPEN.search(cell) for cell in rest):
        return False
    sign = any(_RESOLVED_TAG.match(_plain(cell)) for cell in rest)
    if asked and _RESOLVED_TAG.match(_plain(cells[1])):   # the tag before the question: no word of doubt in the whole cell, `?` aside
        if _OPEN.search(cells[1].replace('?', '')) or '?' in _RESOLVED_TAG.match(_plain(cells[1])).group(0):
            return False
        sign = True
    if header is not None:
        for name, cell in zip(header[1:], cells[1:]):
            value = _plain(cell)
            if _STATUS_COLUMN.search(name):
                if _RESOLVED_STATUS.fullmatch(value):
                    sign = True
                elif not _RESOLVED_TAG.match(value):
                    return False
            elif _ANSWER_COLUMN.match(name) and (not any(char.isalnum() for char in value) or value.lower().startswith(('brak', 'n/a', 'none'))):
                return False
    return sign


def _section(text):
    """The lines of the first open questions section, outside code fences (the rule of `dashboard.parse_questions`)."""
    inside = fenced = False
    for line in file_lines(text):
        if line.lstrip().startswith(('```', '~~~')):
            fenced = not fenced
            yield ''
            continue
        if fenced:
            continue
        if line.startswith('## '):
            if inside:
                return
            inside = bool(_HEADING.match(line))
            continue
        if inside:
            yield line


def resolved_ids(text):
    """The ids of the questions that the section shows as resolved and nowhere as open."""
    resolved, still_open = set(), set()
    table = []

    def read_table():
        separators = [number for number, cells in enumerate(table) if _is_separator(cells)]
        header = [_plain(cell) for cell in table[0]] if separators == [1] else None
        doubtful = separators not in ([], [1])   # two tables without an empty line between them: whose columns are these?
        for cells in table[2:] if header is not None else table:
            found = _ROW_ID.fullmatch(cells[0]) if cells else None
            if found:
                (resolved if not doubtful and _row_resolved(header, cells) else still_open).add(found['id'])
        table.clear()

    for line in _section(text):
        if line.lstrip().startswith('|'):
            table.append(_cells(line))
            continue
        read_table()
        item = _LIST_QUESTION.match(line)
        if item and item['tag'] is None:
            still_open.add(item['id'])
    read_table()
    return resolved - still_open


def drop_resolved(text, blocking):
    """`blocking` (`[{id, question}]`) without the questions that a table of the section shows as resolved."""
    resolved = resolved_ids(text)
    return [item for item in blocking if item['id'] not in resolved]
