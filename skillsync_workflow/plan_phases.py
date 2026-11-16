"""Phases of a plan: rows of the step tables (columns `Id` and `Krok`), the `[UI]` marker and the `[T]` marker of a test step (180-A).

Fail closed: a duplicated id or a table without both columns yields no phases, so the prototype gate keeps
covering the whole plan.  The `[UI]` marker is matched literally (case sensitive).
"""
import re

from .common import file_lines

UI_MARK = '[UI]'
TEST_MARK = '[T]'   # a step that must run its tests through skillsync (`test-run`): the gate of `checkpoint`, `doctor` and the status page
_CHECKLIST_STEP = re.compile(r'^\s*[-*]\s+\[[ xX]\]\s+([A-Z]{1,3}-?\d+[a-z]?(?:\.\d+[a-z]?)?)(?![\w-])\s*(.*)$')
_INLINE_CODE = re.compile(r'`[^`]*`')
_SEPARATOR_CELL = re.compile(r':?-+:?')
_FENCE_OPEN = re.compile(r'(`{3,}|~{3,})')


def _cells(line):
    """Cells of a table row: split on unescaped pipes, outer pipes dropped, each cell stripped."""
    body = line.strip()
    if body.startswith('|'):
        body = body[1:]
    if body.endswith('|') and not body.endswith('\\|'):
        body = body[:-1]
    return [cell.strip() for cell in re.split(r'(?<!\\)\|', body)]


def _is_separator(cells):
    return bool(cells) and all(_SEPARATOR_CELL.fullmatch(cell) for cell in cells)


def _outside_code(text):
    return _INLINE_CODE.sub('', text)


def _step_is_ui(cell):
    """`[UI]` at the start or at the end of a `Krok` cell (inline code left out)."""
    text = _outside_code(cell).strip()
    return text.startswith(UI_MARK) or text.endswith(UI_MARK)


def _title_of(rest):
    """The title of a checklist step: the text before the note (` — sha`, ` -- note`), as `dashboard.parse_progress` cuts it."""
    for sep in (' — ', ' -- ', ' – '):
        if sep in rest:
            return rest.split(sep, 1)[0]
    return rest


def has_test_mark(text):
    """`[T]` at the start or at the end of a step title or `Krok` cell (inline code left out), case sensitive."""
    text = _outside_code(text).strip()
    return text.startswith(TEST_MARK) or text.endswith(TEST_MARK)


def strip_test_mark(title):
    """The title of a step without its `[T]` marker (start or end), as the status page shows it."""
    text = title.strip()
    if text.startswith(TEST_MARK):
        return text[len(TEST_MARK):].strip()
    if text.endswith(TEST_MARK):
        return text[:-len(TEST_MARK)].strip()
    return text


def parse_test_steps(text):
    """Ids of the test steps (`[T]`): checklist lines of `## Progress` (`- [ ] F5.4 Tests [T]`) and rows of the step tables (the `[T]` in the `Id` or
    `Krok` cell); code fences are skipped; in order of appearance, without duplicates."""
    found = []
    fence = False
    for line in file_lines(text):
        if line.lstrip().startswith(('```', '~~~')):
            fence = not fence
            continue
        if fence:
            continue
        match = _CHECKLIST_STEP.match(line)
        if match and has_test_mark(_title_of(match.group(2))) and match.group(1) not in found:
            found.append(match.group(1))
    found += [item for item in parse_table_phases(text)['test_phases'] if item not in found]
    return found


def parse_plan_phases(text):
    """`{'phases': [id…], 'ui_phases': [id…], 'stray_ui': int}` for the whole plan text."""
    info = parse_table_phases(text)
    return {'phases': info['phases'], 'ui_phases': info['ui_phases'], 'stray_ui': info['stray_ui']}


def parse_table_phases(text):
    """The tables of the plan: `{'phases', 'ui_phases', 'test_phases', 'stray_ui'}` (`[T]` = a test step, 180-A)."""
    phases, ui_phases, test_phases, stray = [], [], [], 0
    fence = None  # (character, length) of the open code fence
    table = None  # None outside a table, otherwise (id column, step column) or False for an unrecognised table
    for line in file_lines(text):   # a lone CR and CRLF are line ends here, so no line keeps a `\r`
        stripped = line.lstrip(' ')
        indented = len(line) - len(stripped) >= 4 or stripped.startswith('\t')
        opening = None if indented else _FENCE_OPEN.match(stripped)
        if fence is not None:
            # CommonMark: the closing fence is the same character, at least as long, with nothing after it
            if opening and opening.group(1)[0] == fence[0] and len(opening.group(1)) >= fence[1] \
                    and not stripped[opening.end():].strip():
                fence = None
            table = None
            continue
        if opening:
            fence = (opening.group(1)[0], len(opening.group(1)))
            table = None
            continue
        if indented or not stripped.startswith('|'):
            table = None
            continue
        cells = _cells(stripped)
        if table is None:  # the header row of a new table
            if 'Id' in cells and 'Krok' in cells:
                table = (cells.index('Id'), cells.index('Krok'))
                continue
            table = False
        if table is False:
            if UI_MARK in _outside_code(stripped):
                stray += 1
            continue
        if _is_separator(cells):
            continue
        id_column, step_column = table
        if id_column >= len(cells):
            continue
        id_cell = cells[id_column]
        token = id_cell.replace(UI_MARK, ' ').replace(TEST_MARK, ' ').split()
        if not token:
            continue
        phases.append(token[0])
        step = cells[step_column] if step_column < len(cells) else ''
        if UI_MARK in _outside_code(id_cell) or _step_is_ui(step):
            ui_phases.append(token[0])
        if TEST_MARK in _outside_code(id_cell) or has_test_mark(step):
            test_phases.append(token[0])
    if len(set(phases)) != len(phases):
        phases, ui_phases, test_phases = [], [], []
    return {'phases': phases, 'ui_phases': ui_phases, 'test_phases': test_phases, 'stray_ui': stray}
