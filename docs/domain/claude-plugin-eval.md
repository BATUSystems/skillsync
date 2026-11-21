---
title: "Eval zachowania skilli przez claude plugin eval: kontrakt opakowania, pilotaż 21.11 na Haiku, kryteria adopcji niespełnione"
tags: [skillsync, skills, eval, claude-code, plugin-eval, koszt]
paths: ['tools/claude-eval/run.py', 'tools/claude-eval/evals/**', 'tests/test_skillsync_claude_eval_wrapper.py', 'tests/test_skillsync_claude_eval_cases.py']
source: "żywe próby claude 2.1.318 na koncie prywatnym, 21.11 (F0: 4 wywołania Haiku, 0,016 USD; F4: 30 wywołań Haiku, 0,083 USD); claude plugin eval --help; schemat graderów odczytany z binarki"
verified: 2026-11-21
---
Reguła: eval zachowania skilli idzie wyłącznie przez `tools/claude-eval/run.py` (limit kosztu, flagi i środowisko wymuszone, kopie skilli); `claude plugin eval` wprost na bibliotece nie jest wspierane.

Dlaczego: każdy przebieg to prawdziwe wywołanie modelu na koncie; `plugin eval` domyślnie publikuje raport, ma flagi dające skrypty i serwery (`--scaffold`, `--allow-tools`, `--allow-real-servers`) i nie ogranicza kosztu bez `--max-cost-usd`.

Potwierdzone próbą: katalog wtyczki z `.claude-plugin/plugin.json`, `skills/` i `evals/` działa; uwierzytelnienie idzie z `CLAUDE_CONFIG_DIR`; opakowanie zawsze przekazuje `--runs` (domyślnie 3) i `plugin eval` daje mu pierwszeństwo przed `runs` przypadku, więc `runs` z `prompt.md` jest ignorowane; kody wyjścia z `claude plugin eval --help`: 1 wynik poniżej progu, 2 przerwanie limitem kosztu; ślady lądują w `/tmp/claude-eval-*/out/trace.jsonl` i znikają po biegu (opakowanie nie ma `--keep-temp`); schemat graderów jest ścisły (`tool_used`: `tool`, `input_match`, `min`, `max`, `arm`; `regex`: `pattern`, `target`, `flags`, `match`), a `min` domyślnie 1, więc kontrola „zero wywołań” wymaga `min: 0`.

Wynik pilotażu (Haiku, 3 przebiegi na ramię): `feature` z kluczem 1/3 (bez wtyczki 0/3), `bugfix` z kluczem 0/3, `pre-implement` 1/3. Kryterium adopcji (dwa z trzech kwalifikujących przypadków co najmniej 2/3 z wtyczką) niespełnione. Obie kontrole wyszły 0/3 przez błąd gradera (`min` domyślnie 1), po poprawce `min: 0` nie były powtórzone na modelu.

Wyjątki i granice: przyczyn niskich wyników nie ustalono (brak śladów); hipotezy: klucz zgłoszenia podany zdaniem swobodnym („Zadanie: ABC-123”), a nie jako pierwszy wyraz argumentu skilla (reguła `feature` zakłada klucz na początku), regex werdyktu nieskotwiczony i czuły na pogrubienie (`**Werdykt:**`), ucięcie przy `max_turns`, słaby model; wynik na Haiku nie mówi nic o Sonnecie. Opakowanie nie skanuje `SKILL.md` pod kątem hooków (skille rdzenia ich nie mają) i nie ogranicza listy kluczy graderów poza samym `type`.
