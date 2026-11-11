"""Phases of a plan: rows of the step tables (columns `Id` and `Krok`) and the `[UI]` marker.

Fail closed: a duplicated id or a table without both columns yields no phases, so the prototype gate keeps
covering the whole plan.  The `[UI]` marker is matched literally (case sensitive).
"""
import re

UI_MARK = '[UI]'
_INLINE_CODE = re.compile(r'`[^`]*`')
_SEPARATOR_CELL = re.compile(r':?-+:?')
_FENCE = ('```', '~~~')


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


def parse_plan_phases(text):
    """`{'phases': [id…], 'ui_phases': [id…], 'stray_ui': int}` for the whole plan text."""
    phases, ui_phases, stray = [], [], 0
    in_fence = False
    table = None  # None outside a table, otherwise (id column, step column) or False for an unrecognised table
    for raw in text.splitlines():
        line = raw.rstrip('\r')
        stripped = line.lstrip()
        if stripped.startswith(_FENCE):
            in_fence = not in_fence
            table = None
            continue
        if in_fence:
            continue
        if not stripped.startswith('|'):
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
        token = id_cell.replace(UI_MARK, ' ').split()
        if not token:
            continue
        phases.append(token[0])
        step = cells[step_column] if step_column < len(cells) else ''
        if UI_MARK in _outside_code(id_cell) or _step_is_ui(step):
            ui_phases.append(token[0])
    if len(set(phases)) != len(phases):
        phases, ui_phases = [], []
    return {'phases': phases, 'ui_phases': ui_phases, 'stray_ui': stray}
