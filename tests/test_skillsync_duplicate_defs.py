"""Strażnik zdublowanych definicji najwyższego poziomu w kodzie rdzenia.

Żaden plik `.py` w `skillsync_workflow/`, `scripts/` ani sam `skillsync` nie ma dwóch definicji najwyższego poziomu o tej samej nazwie:
funkcji, klasy ani stałej pisanej wielkimi literami. Druga definicja po cichu przesłania pierwszą, więc zdublowany blok (np. po scaleniu
gałęzi, w której ten sam kod został dodany w dwóch miejscach) działa „dobrze”, a zostaje martwym duplikatem i pułapką na kolejne scalenia.
Warianty zależne od systemu leżą pod `if`/`try` (nie na najwyższym poziomie), więc tego nie dotyczą.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_duplicate_defs.py`.
"""
import ast
import collections
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Celowe redefinicje: `{ścieżka względem repo: {nazwa, ...}}`, każda z komentarzem, dlaczego. Pusta, dopóki żadna nie jest potrzebna.
ALLOWED: dict[str, set[str]] = {}


def top_level_duplicates(source: str, filename: str = '<string>') -> dict[str, list[int]]:
    """`{nazwa: [linie definicji]}` dla nazw zdefiniowanych na najwyższym poziomie więcej niż raz (funkcja, klasa, stała WIELKIMI LITERAMI)."""
    tree = ast.parse(source, filename)
    seen = collections.defaultdict(list)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            seen[node.name].append(node.lineno)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    seen[target.id].append(node.lineno)
    return {name: lines for name, lines in seen.items() if len(lines) > 1}


def core_files() -> list[Path]:
    files = sorted((ROOT / 'skillsync_workflow').glob('*.py')) + sorted((ROOT / 'scripts').glob('*.py'))
    entry = ROOT / 'skillsync'
    if entry.is_file():
        files.append(entry)
    return files


class ScannerTests(unittest.TestCase):
    """Sam skaner łapie to, do czego służy (bez tego zielony strażnik niczego nie dowodzi)."""

    def test_a_duplicated_function_class_and_constant_are_found_with_their_lines(self):
        source = 'LIMIT = 1\n\n\ndef helper():\n    pass\n\n\nclass Mixin:\n    pass\n\n\nLIMIT = 2\n\n\ndef helper():\n    pass\n\n\nclass Mixin:\n    pass\n'
        self.assertEqual(top_level_duplicates(source), {'LIMIT': [1, 12], 'helper': [4, 15], 'Mixin': [8, 19]})

    def test_the_same_name_under_a_condition_or_in_a_class_is_not_a_duplicate(self):
        source = ('import os\nif os.name == "nt":\n    def helper():\n        return 1\nelse:\n    def helper():\n        return 2\n\n\n'
                  'class A:\n    def method(self):\n        pass\n\n    def method(self):\n        pass\n\n\nlower = 1\nlower = 2\n')
        self.assertEqual(top_level_duplicates(source), {})

    def test_an_annotated_constant_counts(self):
        self.assertEqual(top_level_duplicates('LIMIT: int = 1\nLIMIT: int = 2\n'), {'LIMIT': [1, 2]})


class CoreHasNoDuplicatedDefinitions(unittest.TestCase):
    def test_no_core_file_defines_a_top_level_name_twice(self):
        files = core_files()
        self.assertTrue(files, 'no core file found')
        found = {}
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            for name, lines in top_level_duplicates(path.read_text(encoding='utf-8'), relative).items():
                if name not in ALLOWED.get(relative, set()):
                    found[f'{relative}:{name}'] = lines
        self.assertEqual(found, {}, 'a top-level name is defined twice (the later one silently wins): remove the duplicate, or list a deliberate '
                                    'redefinition in ALLOWED with the reason')

    def test_every_allowed_exception_is_still_needed(self):
        for relative, names in ALLOWED.items():
            duplicates = top_level_duplicates((ROOT / relative).read_text(encoding='utf-8'), relative)
            for name in names:
                self.assertIn(name, duplicates, f'{relative}:{name} is allowed but no longer duplicated: remove it from ALLOWED')


if __name__ == '__main__':
    unittest.main()
