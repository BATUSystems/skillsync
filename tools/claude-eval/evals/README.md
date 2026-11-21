# Przypadki eval skilli (CLAUDE-4)

Uruchamiane wyłącznie przez `tools/claude-eval/run.py`. Przypadki kwalifikujące: `feature-with-key`, `bugfix-with-key`, `pre-implement-verdict`;
`feature-without-key` to kontrola (oba ramiona muszą przejść). Przypadek `dispatch` (model nie wywołuje skilla) zostanie dodany po wejściu flagi
`disable-model-invocation` do bazy. Nazwy pól graderów (np. `max_count`, `pattern`) potwierdza próba techniczna E0.
