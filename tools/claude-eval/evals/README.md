# Przypadki eval skilli (CLAUDE-4)

Uruchamiane wyłącznie przez `tools/claude-eval/run.py`. Przypadki kwalifikujące: `feature-with-key`, `bugfix-with-key`, `pre-implement-verdict`;
`feature-without-key` to kontrola (oba ramiona muszą przejść). Przypadek `dispatch` (model nie wywołuje skilla) zostanie dodany po wejściu flagi
`disable-model-invocation` do bazy. Pola graderów potwierdzone próbą F0 (schemat `claude plugin eval` jest ścisły): `tool_used` ma `tool`, `input_match`, `min`, `max`, `arm`; `regex` ma `pattern`, `target`, `flags`, `match`.
