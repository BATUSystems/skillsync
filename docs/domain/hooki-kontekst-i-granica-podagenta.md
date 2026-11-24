---
title: 'Hooki Claude Code: co faktycznie dociera do modelu, kiedy i z jakim payloadem; granica commitu podagenta'
tags: [skillsync, claude-code, hooks, session-context, subagents, guard, commit-boundary]
paths: ['library/hooks/**', 'skillsync_workflow/hooksync.py', 'skillsync_workflow/guardhook.py', 'tests/test_skillsync_hook*.py', 'tests/test_skillsync_hooksync.py']
source: 'Pomiar F0 i próba na żywo F7 przebiegu 2026-11-21-claude-3 (.skillsync/runs/2026-11-21-claude-3/F0.md, F7.md), Claude Code 2.1.318, haiku, konto prywatne, tymczasowy HOME i repo; PostToolUse: próba na żywo 24.11.2026 (Dyspozytor) i dokumentacja hooków'
verified: 2026-11-24
---
Reguła: hook daje modelowi tylko to, co zwróci w `hookSpecificOutput.additionalContext` (albo stderr przy `deny`), i tylko w chwili, którą określa jego zdarzenie. Nie ma innego kanału kontekstu z hooka.

Co dociera (potwierdzone 2.1.318):
- SessionStart: `additionalContext` dociera do modelu dla `startup`, `compact` i `clear` (dla `clear` po wyczyszczeniu kontekstu, po `compact` model cytował znacznik i pamiętał słowo z poprzedniej tury). `resume`: NOT_VERIFIED (wymaga zapisu sesji w profilu konta; payload `source=resume` udokumentowany przez hosta, skrypt testowany wejściem JSON). Bez obietnicy `resume` w CHANGELOG.
- PreToolUse: `additionalContext` dociera PO wykonaniu narzędzia (ostrzeżenie o zapisie widać po jego wyniku). `deny` (exit 2) blokuje narzędzie, a stderr model widzi jako błąd hooka.
- PostToolUse (VERIFIED na żywo 24.11.2026, Claude Code 2.1.318, Haiku, konto prywatne, `claude -p --include-hook-events`, hook z matcherem `Agent|Task`; dowód: /tmp/claude-1000/livehook-out-2.1.318.jsonl na stacji): `hookSpecificOutput.additionalContext` przy exit 0 trafia do kontekstu modelu; model zacytował dosłownie magiczne słowo z hooka („po wywołaniu narzędzia dostałem przypomnienie systemowe… `PostToolUse:Agent`”). Dokumentacja: https://code.claude.com/docs/en/hooks.md, sekcje PostToolUse i „Add context for Claude” (zwykły stdout nie jest kanałem do modelu). Stdin ma `tool_name`, `tool_input`, `tool_response`, a w podagencie `agent_id`. Nazwa narzędzia podagenta: w próbie z 24.11 hook dostał `tool_name = Agent`; wcześniejszy pomiar F0 (21.11, ta sama wersja 2.1.318) podaje `Task` (zob. niżej); rozbieżności nie wyjaśniłem (starsze narzędzie z innego konta lub inny moment uruchomienia?), dlatego matcher to `Agent|Task`. Podagent w tle: hook odpala przy zwrocie wywołania (start, `async_launched`), nie po zakończeniu pracy (z dokumentacji, nie zmierzone). Stderr zdarzenia Stop przy exit 0 widzi tylko człowiek (blokada exit 2 trafia do modelu); `Stop` z `additionalContext` trafia do modelu, ale dokłada turę (limit 8 kolejnych).
- SubagentStop: `last_assistant_message` jest w payloadzie, więc transkrypt nie jest potrzebny.
- Timeout hooka (M6): hook kończy się `cancelled`, sesja idzie dalej (fail-open). SessionStart czeka jednak pełny `timeout`, stąd limit 3 s.

Payloady:
- Wspólne: `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `permission_mode`.
- SessionStart: `source` (`startup`, `resume`, `clear`, `compact`).
- PreToolUse: `tool_name`, `tool_input` (`file_path` bezwzględna), `tool_use_id`. Z podagenta dodatkowo `agent_id` i `agent_type`; z sesji głównej nie ma ich wcale. To jedyny sygnał, po którym `guard-commands.py` odróżnia podagenta od sesji głównej (`hooks.subagent_commit`).
- SubagentStop: `agent_id`, `agent_type`, `agent_transcript_path`, `last_assistant_message`, `stop_hook_active`. Kompaktowanie wywołuje SubagentStop z PUSTYM `agent_type` (wewnętrzny podagent), więc filtr roli musi go pomijać.
- Narzędzia w 2.1.318: `Write`, `Edit`, `NotebookEdit`, `Bash`, `Task` (podagent). `MultiEdit` nie istnieje. W komunikatach modelu podagenta bywa nazwany `Agent`.

Co z tego wynika dla kodu:
- Kontekst startowy (`session-context.py`) działa dla `startup`, `compact`, `clear`; po `resume` nie ma potwierdzenia.
- Granica `deny` dotyczy wyłącznie wywołań z niepustym `agent_id`. `warn` nie jest granicą, bo działa po poleceniu.
- Granica podagenta to guardrail przeciw pomyłce, nie zabezpieczenie: model może ją obejść (skrypt, pipe, alias). Wzorzec Bash w `disallowedTools` nie jest zamiennikiem (usuwa całe Bash, zob. lekcję `disallowedtools-wzorzec-bash-usuwa-cale-narzedzie-podagenta`).

Wyjątki i uwagi:
- `SKILLSYNC_GUARD_EXCEPT` nie omija granicy podagenta.
- Kontrola zawartości kontekstu wymaga próby na żywo, nie samego testu JSON.

Gdzie w kodzie: `library/hooks/session-context.py` (`build`, `parse_manifest`), `library/hooks/check-subagent-report.py` (`REPORT_LINE`, `ROLES`), `library/hooks/guard-generated.py` (`classify`), `library/hooks/guard-commands.py` (`subagent_violation`, `--subagent-commit`), `skillsync_workflow/hooksync.py` (`merge_entry`, `SPECS`, `resolve`).
