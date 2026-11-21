---
title: 'Hook dokładający kontekst sesji: testy JSON nie wykryją pustego delivery.mode ani statusu „unknown”; czytnik liniowy manifestu zna tylko jedną formę'
tags: [skillsync, claude-code, hooks, session-context, manifest, parser]
paths: ['library/hooks/session-context.py', 'tests/test_skillsync_hook_session_context.py', 'docs/manifest.md']
source: 'F7 przebiegu 2026-11-21-claude-3 (.skillsync/runs/2026-11-21-claude-3/F7.md, wywołanie L1: próba na żywo, claude -p, haiku, konto prywatne), naprawa w commicie z testami (28 testów hooka)'
verified: 2026-11-21
---
**Objaw:** hook `session-context.py` miał zielone testy wejściem JSON i wyglądał poprawnie. Próba na żywo w sesji startowej pokazała kontekst bez `(delivery.mode: …)`, a status planu jako „unknown” zamiast tekstu z pliku planu.

**Przyczyna:** (1) czytnik manifestu `parse_manifest` czyta linie, a nie YAML; nie znał formy `delivery: {mode: direct, base: main}` (flow w jednej linii), którą ma większość projektów. Testy używały formy blokowej. (2) status z `skillsync status --json` przychodzi jako `unknown` z osobnym polem `statusText`; kod czytał tylko `status`. Test sprawdzał kształt JSON wejścia, nie to, co wraca do modelu.

**Reguła:**
- Czytnik liniowy manifestu obsługuje obie formy wartości: blokową (`delivery:` i wcięte `mode:`) i inline (`delivery: {mode: X, base: Y}`). Każdą nową formę, której czytnik nie zna, dodaj jako test przypadku z tekstem manifestu, nie z wywołania JSON.
- Kontrolę zawartości `additionalContext` potwierdzaj próbą na żywo (`claude -p --output-format stream-json --verbose --include-hook-events` w tymczasowym repo i HOME; zdarzenia `hook_response` plus cytat znacznika). Test jednostkowy tylko pokazuje, że funkcja zwraca tekst.
- Format manifestu: zob. `docs/manifest.md`, klucz `delivery`. Nie zakładaj, że wszystkie projekty mają jedną formę.

**Jak wykryć:** `grep -n "delivery" .github/.skillsync.yaml` w kilku projektach (różne formy); uruchom hook na tym manifeście bez modelu (`python3 library/hooks/session-context.py --cli '["skillsync"]' < payload.json` z `cwd` tym projektem) i sprawdź, czy wynik zawiera `delivery.mode`. Dla statusu: plan z `statusText` daje czytelny status, nie `unknown`.
