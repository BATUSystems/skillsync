"""I18N-1: katalog tekstów interfejsu (`pl.json`/`en.json`), liczba mnoga i klucz manifestu `language`."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tmp_paths  # noqa: E402
from skillsync_workflow import i18n  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CATALOG_DIR = ROOT / "skillsync_workflow" / "data" / "i18n"
POLISH = re.compile("[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")
PARAM = re.compile(r"\{(\w+)\}")


def _load(lang):
    """The main file plus the fragments `<lang>.<namespace>.json`, as the loader merges them."""
    merged = json.loads((CATALOG_DIR / f"{lang}.json").read_text(encoding="utf-8"))
    for path in sorted(CATALOG_DIR.glob(f"{lang}.*.json")):
        merged.update(json.loads(path.read_text(encoding="utf-8")))
    return merged


def _texts(value):
    """Wszystkie napisy wartości katalogu (napis albo obiekt form liczby mnogiej)."""
    return list(value.values()) if isinstance(value, dict) else [value]


class ResolveLanguage(unittest.TestCase):
    def test_missing_key_is_polish_without_warning(self):
        self.assertEqual(i18n.resolve_language({}), ("pl", None))
        self.assertEqual(i18n.resolve_language({"language": None}), ("pl", None))
        self.assertEqual(i18n.resolve_language(None), ("pl", None))

    def test_supported_values(self):
        self.assertEqual(i18n.resolve_language({"language": "pl"}), ("pl", None))
        self.assertEqual(i18n.resolve_language({"language": "en"}), ("en", None))

    def test_unknown_or_malformed_value_warns_and_falls_back_to_polish(self):
        for value in ("de", "EN", "", " en", 1, True, ["en"], {"x": 1}):
            lang, warning = i18n.resolve_language({"language": value})
            self.assertEqual(lang, "pl", value)
            self.assertIsInstance(warning, str, value)
            self.assertIn("language", warning)

    def test_warning_does_not_echo_untrusted_value_unbounded(self):
        lang, warning = i18n.resolve_language({"language": "x" * 500 + "\n\x1b[31m"})
        self.assertEqual(lang, "pl")
        self.assertLess(len(warning), 200)
        self.assertNotIn("\x1b", warning)
        self.assertNotIn("\n", warning)


class Plurals(unittest.TestCase):
    def test_polish_categories(self):
        expected = {0: "many", 1: "one", 2: "few", 3: "few", 4: "few", 5: "many", 11: "many", 12: "many", 13: "many",
                    14: "many", 21: "many", 22: "few", 24: "few", 25: "many", 100: "many", 101: "many", 102: "few",
                    112: "many", 1000: "many"}
        for n, category in expected.items():
            self.assertEqual(i18n.plural_category("pl", n), category, n)

    def test_english_categories(self):
        for n, category in {0: "other", 1: "one", 2: "other", 5: "other", 21: "other", 101: "other"}.items():
            self.assertEqual(i18n.plural_category("en", n), category, n)

    def test_non_integers_and_negative_are_other(self):
        for lang in ("pl", "en"):
            for n in (1.5, -1, None, "3", True):
                self.assertEqual(i18n.plural_category(lang, n), "other", (lang, n))

    def test_plural_text_falls_back_to_other_then_many(self):
        forms = {"one": "{n} plik", "few": "{n} pliki", "other": "{n} plików"}
        self.assertEqual(i18n.pick_plural("pl", forms, 3), "{n} pliki")
        self.assertEqual(i18n.pick_plural("pl", forms, 5), "{n} plików")  # brak `many`: `other`
        self.assertEqual(i18n.pick_plural("en", {"one": "{n} file", "other": "{n} files"}, 1), "{n} file")


class Catalogs(unittest.TestCase):
    def test_same_keys_in_both_languages(self):
        pl, en = _load("pl"), _load("en")
        self.assertEqual(sorted(pl), sorted(en), "pl.json i en.json mają różne klucze")

    def test_same_parameters_in_both_languages(self):
        pl, en = _load("pl"), _load("en")
        for key in pl:
            self.assertEqual(self._params(pl[key]), self._params(en[key]), key)

    def test_plural_entries_have_the_forms_the_language_needs(self):
        for lang, needed in (("pl", {"one", "few", "many"}), ("en", {"one", "other"})):
            for key, value in _load(lang).items():
                if isinstance(value, dict):
                    self.assertTrue(needed <= set(value), (lang, key, set(value)))
                    self.assertTrue(set(value) <= {"one", "few", "many", "other"}, (lang, key))

    def test_english_catalog_has_no_polish_characters(self):
        for key, value in _load("en").items():
            for text in _texts(value):
                self.assertIsNone(POLISH.search(text), (key, text))

    def test_values_are_strings_or_plural_objects_of_strings(self):
        for lang in ("pl", "en"):
            for key, value in _load(lang).items():
                self.assertRegex(key, r"^[a-z][a-z0-9]*(\.[a-z0-9][a-zA-Z0-9]*)+$", key)
                for text in _texts(value):
                    self.assertIsInstance(text, str, (lang, key))

    def test_no_markup_in_catalog_values(self):
        for lang in ("pl", "en"):
            for key, value in _load(lang).items():
                for text in _texts(value):
                    self.assertNotRegex(text, r"[<>]", (lang, key))

    @staticmethod
    def _params(value):
        """Parametry wszystkich form wartości (pl ma trzy formy liczby mnogiej, en dwie: porównujemy zbiór parametrów klucza)."""
        return sorted({name for text in _texts(value) for name in PARAM.findall(text)})


class Fragments(unittest.TestCase):
    """`<lang>.<namespace>.json` fragments: separate files per package, merged by the loader (no merge conflicts in the main file)."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self._old = i18n.CATALOG_DIR
        i18n.CATALOG_DIR = self.dir
        i18n._load.cache_clear()
        self.addCleanup(self._restore)

    def _restore(self):
        i18n.CATALOG_DIR = self._old
        i18n._load.cache_clear()

    def _write(self, name, data):
        tmp_paths.child(self.dir, name).write_text(json.dumps(data), encoding="utf-8")

    def test_fragment_is_merged_and_selected_by_prefix(self):
        self._write("pl.json", {"ui.a": "A"})
        self._write("pl.proto.json", {"proto.b": "B", "proto.c": {"one": "{n} x", "few": "{n} y", "many": "{n} z"}})
        self.assertEqual(i18n.catalog("pl")["proto.b"], "B")
        self.assertEqual(sorted(i18n.catalog_for_prefix("pl", "proto.")), ["proto.b", "proto.c"])
        self.assertEqual(i18n.catalog_for_prefix("pl", "ui."), {"ui.a": "A"})
        self.assertEqual(i18n.t("pl", "proto.c", n=3), "3 y")

    def test_key_outside_the_namespace_or_duplicate_is_an_error(self):
        self._write("pl.json", {"ui.a": "A"})
        self._write("pl.proto.json", {"ui.b": "B"})
        with self.assertRaisesRegex(ValueError, "outside the namespace"):
            i18n.catalog("pl")
        i18n._load.cache_clear()
        self._write("pl.proto.json", {"proto.b": "B"})
        self._write("pl.json", {"ui.a": "A", "proto.b": "dup"})
        with self.assertRaisesRegex(ValueError, "duplicate key"):
            i18n.catalog("pl")

    def test_fragments_of_other_languages_are_not_mixed_in(self):
        self._write("pl.json", {"ui.a": "A"})
        self._write("en.json", {"ui.a": "A"})
        self._write("en.proto.json", {"proto.b": "B"})
        self.assertNotIn("proto.b", i18n.catalog("pl"))
        self.assertIn("proto.b", i18n.catalog("en"))


class Formatting(unittest.TestCase):
    def test_t_substitutes_parameters_and_leaves_unknown_braces_alone(self):
        self.assertEqual(i18n.fmt("{a} i {b}", a="x", b=2), "x i 2")
        self.assertEqual(i18n.fmt("{a} {zz}", a="x"), "x {zz}")
        self.assertEqual(i18n.fmt("bez parametrów"), "bez parametrów")

    def test_values_are_not_reinterpreted(self):
        self.assertEqual(i18n.fmt("{a} {b}", a="{b}", b="X"), "{b} X")

    def test_unknown_key_returns_key_not_exception(self):
        self.assertEqual(i18n.t("en", "no.such.key"), "no.such.key")

    def test_unsupported_language_uses_polish(self):
        self.assertEqual(i18n.catalog("de"), i18n.catalog("pl"))

    def test_catalog_is_the_whole_language_catalog(self):
        self.assertEqual(i18n.catalog("en"), _load("en"))
        self.assertEqual(i18n.catalog("pl"), _load("pl"))

    def test_catalog_for_client_is_the_ui_subset(self):
        for lang in ("pl", "en"):
            expected = {key: value for key, value in _load(lang).items() if key.startswith("ui.")}
            self.assertEqual(i18n.catalog_for_client(lang), expected)
        self.assertEqual(i18n.catalog_for_client("de"), i18n.catalog_for_client("pl"))


if __name__ == "__main__":
    unittest.main()
