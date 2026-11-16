"""Guard: a documentation table that says "measured" points at a record of the measurement.

Rule (docs/measurements/README.md): a table row with the word `zmierzone`/`measured` needs a "Pomiar"/"Measurement" column in that
table whose cell links to an existing `measurements/<id>.md`; in a table with such a column every row either links to a record
or is marked `niezmierzone`/`oczekiwane`/`unmeasured`/`expected`. A record has the fields Data, System, Wersja, Polecenie, Wynik.
"""
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEASURED = re.compile(r'(?<![\w])(zmierzone|zmierzono|measured)(?![\w])', re.IGNORECASE)
UNMEASURED = re.compile(r'(?<![\w])(niezmierzon\w*|oczekiwane|unmeasured|expected)(?![\w])', re.IGNORECASE)
HEADER = re.compile(r'^(pomiar|measurement)$', re.IGNORECASE)
LINK = re.compile(r'\]\(([^)\s]*measurements/[A-Za-z0-9._-]+\.md)\)')
FIELDS = ('Data:', 'System:', 'Wersja:', 'Polecenie:', 'Wynik:')


def cells(line):
    return [cell.strip() for cell in line.strip().strip('|').split('|')]


def is_separator(line):
    return bool(re.fullmatch(r'\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*', line))


def tables(text):
    """`(header cells, [(line number, row text, cells)])` for every pipe table (fenced code blocks skipped)."""
    lines = text.splitlines()
    found, fenced, i = [], False, 0
    while i < len(lines):
        if lines[i].lstrip().startswith(('```', '~~~')):
            fenced = not fenced
        if not fenced and lines[i].lstrip().startswith('|') and i + 1 < len(lines) and is_separator(lines[i + 1]):
            header, rows, j = cells(lines[i]), [], i + 2
            while j < len(lines) and lines[j].lstrip().startswith('|'):
                rows.append((j + 1, lines[j], cells(lines[j])))
                j += 1
            found.append((header, rows))
            i = j
            continue
        i += 1
    return found


def problems(path, text, root):
    """Violations of the rule in one Markdown file as `file:line: text`."""
    result = []
    for header, rows in tables(text):
        column = next((index for index, name in enumerate(header) if HEADER.match(name.strip('*_` '))), None)
        for number, line, row in rows:
            where = f'{path}:{number}'
            cell = row[column] if column is not None and column < len(row) else None
            if column is None:
                if MEASURED.search(line):
                    result.append(f'{where}: wiersz mówi „zmierzone”, a tabela nie ma kolumny „Pomiar”')
                continue
            link = LINK.search(cell or '')
            if link:
                target = (path.parent / link.group(1)).resolve()
                if not target.is_file():
                    result.append(f'{where}: zapis pomiaru nie istnieje: {link.group(1)}')
            elif MEASURED.search(line) or not UNMEASURED.search(cell or ''):
                result.append(f'{where}: wiersz bez odesłania do zapisu pomiaru musi być oznaczony „niezmierzone” albo „oczekiwane”')
    return result


def markdown_files(root):
    for base in (root / 'docs', root):
        for file in sorted(base.rglob('*.md') if base.name == 'docs' else base.glob('*.md')):
            if 'measurements' not in file.relative_to(root).parts:
                yield file


class MeasurementTables(unittest.TestCase):
    def test_repository_docs(self):
        found = []
        for file in markdown_files(ROOT):
            found += problems(file, file.read_text(encoding='utf-8'), ROOT)
        self.assertEqual(found, [])

    def test_records_have_all_fields(self):
        records = sorted((ROOT / 'docs' / 'measurements').glob('*.md'))
        self.assertTrue([r for r in records if r.name != 'README.md'], 'no measurement records')
        for record in records:
            if record.name == 'README.md':
                continue
            text = record.read_text(encoding='utf-8')
            for field in FIELDS:
                self.assertRegex(text, r'(?m)^' + re.escape(field) + r'\s*\S', f'{record.name}: missing {field}')


class RuleBehaviour(unittest.TestCase):
    def check(self, text, files=()):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / 'docs' / 'measurements').mkdir(parents=True)
            for name in files:
                (base / 'docs' / 'measurements' / name).write_text('x', encoding='utf-8')
            page = base / 'docs' / 'page.md'
            return problems(page, text, base)

    TABLE = '| Sposób | Skutek | Pomiar |\n|---|---|---|\n{}\n'

    def test_measured_with_existing_record_passes(self):
        self.assertEqual(self.check(self.TABLE.format('| a | b | zmierzone: [x](measurements/x.md) |'), ['x.md']), [])

    def test_measured_without_record_fails(self):
        self.assertEqual(len(self.check(self.TABLE.format('| a | b | zmierzone |'))), 1)

    def test_link_to_missing_record_fails(self):
        self.assertIn('nie istnieje', self.check(self.TABLE.format('| a | b | zmierzone: [x](measurements/x.md) |'))[0])

    def test_unmeasured_and_expected_pass(self):
        text = self.TABLE.format('| a | b | niezmierzone |\n| c | d | oczekiwane z dokumentacji |\n| e | f | unmeasured |')
        self.assertEqual(self.check(text), [])

    def test_empty_cell_fails(self):
        self.assertEqual(len(self.check(self.TABLE.format('| a | b |  |'))), 1)

    def test_measured_row_in_table_without_column_fails(self):
        self.assertEqual(len(self.check('| a | b |\n|---|---|\n| x | zmierzone na laptopie |\n')), 1)

    def test_prose_and_code_fences_are_ignored(self):
        self.assertEqual(self.check('Zmierzone w prozie.\n```\n| a | b |\n|---|---|\n| x | zmierzone |\n```\n'), [])

    def test_not_a_substring_match(self):
        self.assertEqual(self.check('| a | b |\n|---|---|\n| x | niezmierzone, pomierzone |\n'), [])


if __name__ == '__main__':
    unittest.main()
