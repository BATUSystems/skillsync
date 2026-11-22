# core-en

English translation of the skills, roles and instructions of the core library (`library/`).

It is a translation layer: `kind: org` with `translation_of: core` in `layer.yaml`, and `translation: true` in its entry of the machine config
(`skillsync layers add core-en --kind org --source <this repository> --subdir layers/core-en --translation`). Use it in `layer_rules` after `core`.

Only Markdown texts of skills, roles and instructions (and `instructions/_base.md`) are translated here. A file that is not translated comes from the
core. A skill that is translated at all is translated completely. `translations.lock.yaml` records the sha256 of every core file a translation was
made from; `skillsync layers check` fails when a core file changed afterwards or when the structure (headings, frontmatter) differs.

Protocol tokens that programs read are written in English (`Run:`, `Verdict:`, `Handoff:`, `[EXPERT]`, `waiting:`, `Open questions`, ...) according to the map of CORE-1130-I18N (`skillsync_workflow/protocol_tokens.py`); the parsers of the core accept both languages. Polish stays only where it is a value of code or of an external system, and in quoted labels of the prototype engine (with an English gloss).
