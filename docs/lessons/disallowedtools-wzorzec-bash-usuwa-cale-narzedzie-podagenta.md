---
title: 'Wzorzec Bash w disallowedTools podagenta usuwa cały Bash, a maxTurns liczy odpowiedzi modelu, nie wywołania narzędzi'
tags: [skillsync, claude-code, role, podagenci, maxturns, disallowedtools]
paths: ['library/agents/*.md', 'library/skills/feature/references/claude.md', 'skillsync_workflow/skill_frontmatter.py', 'tests/test_skillsync_agent_roles.py']
source: 'CLAUDE-2, próba F0 z 2026-11-21 na Claude Code 2.1.318: 5 wywołań `claude -p` (haiku, konto prywatne, tymczasowy HOME i repo) i kalibracja z transkryptów podagentów (n = 1836)'
verified: 2026-11-21
---
**Objaw:** plan zakładał, że `disallowedTools: Bash(git commit *)` w roli zabroni podagentowi tylko `git commit`; wstępna kalibracja limitu tur miała się opierać na liczbie wywołań narzędzi w transkryptach, a jednostkę „tury” miała dopiero ustalić próba. Pierwsze założenie było fałszywe, drugie dałoby zawyżone limity.

**Przyczyna:** (1) W frontmatterze podagenta wpis ze specyfikatorem (`Bash(git commit *)` i `Bash(git commit:*)`) usuwa całe narzędzie Bash: podagent dostał tylko `Read`, a ta sama rola bez pola wykonała `git status`, `git commit` i `git log`. Dokumentacja Claude Code mówi o tym jednym zdaniem (wg podsumowania dokumentacji: wpis ze specyfikatorem usuwa całe narzędzie; potwierdzone próbą), łatwo je pominąć. (2) Tura w `maxTurns` to jedna odpowiedź modelu z jej równoległymi wywołaniami narzędzi: przy `maxTurns: 1` podagent wykonał cztery równoległe `Read` w jednej turze, a wynik po limicie był oznaczony jako częściowy (nota „stopped at its N-turn limit … PARTIAL output”, ≥ 2.1.246, wznawialny przez `SendMessage`). Liczba wywołań narzędzi zawyża więc liczbę tur.

**Reguła:** granicy „podumysły nie commitują” nie da się zbudować wzorcem Bash w `disallowedTools`; do tego służy hook (osobny zakres) albo allowlista w `tools`. Limit tur kalibruj liczbą unikalnych odpowiedzi asystenta w transkryptach podagenta (unikalny `message.id`), nie `tool_use`. Zanim dodasz do roli pole z tabeli `ROLE_FIELDS`, sprawdź jego zachowanie na żywo na kopii roli w tymczasowym repozytorium; wynik zapisz znacznikiem w `references/claude.md`, a test pilnuje, że wynik inny niż „skuteczne” oznacza brak pola w rolach.

**Jak wykryć:** próba z rolą z polem i rolą kontrolną bez niego, w tymczasowym repozytorium (`claude -p` z modelem haiku, `--setting-sources project,local`, tymczasowy HOME, konto prywatne): kontrola musi wykonać polecenie, rola z polem nie. Po limicie szukaj w wyniku noty „PARTIAL output”.
