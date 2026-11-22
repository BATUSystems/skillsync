"""The English translation layer of this repository (`layers/core-en`, CORE-1130-I18N) conforms to the core library (`library`).

Whatever is translated so far must keep the structure of its source and the lock must be current; a core file changed without
`skillsync layers translations --update` after its translation was reviewed fails here, in the repository that owns both.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import LayersCase  # noqa: E402
from skillsync_workflow import layers, translations  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


class CoreEnglishLayerTest(LayersCase):
    def layerset(self):
        self.write_config([('core', 'core', REPO, {'subdir': 'library'}), ('core-en', 'org', REPO, {'subdir': 'layers/core-en', 'translation': True})],
                          [(['**'], ['core', 'core-en'])])
        return layers.resolve_layers(self.project('en-project'))

    def test_the_layer_is_a_translation_of_the_core_in_english(self):
        layerset = self.layerset()
        layer = next(item for item in layerset.layers if item.id == 'core-en')
        self.assertTrue(layer.is_translation)
        self.assertEqual(layer.language, 'en')
        self.assertEqual(translations.skill_language(layerset), 'en')

    def test_the_translation_conforms_to_the_core(self):
        layerset = self.layerset()
        found = [item for item in layers.check_layers(layerset, hooks=False, orphans=False)
                 if item['severity'] in ('blocked', 'error')]
        self.assertEqual(found, [], '\n'.join(f"{item['code']}: {item['message']}" for item in found))

    def test_nothing_in_the_layer_is_an_orphan_or_a_translated_file_missing_from_the_lock(self):
        layerset = self.layerset()
        layer = next(item for item in layerset.layers if item.id == 'core-en')
        lock, error = translations.read_lock(layer)
        self.assertIsNone(error)
        pairs = translations.translated_files(layer, layerset.core)
        self.assertEqual(set((lock or {}).get('files', {})), set(pairs))


if __name__ == '__main__':
    unittest.main()
