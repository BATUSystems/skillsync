---
title: "„waiting for” w approve czyści dopisek po Status:, a nie jest zgodą: nie rozszerzaj nim stanów zgody"
tags: [skillsync, approvals, consent, status, plan, i18n]
paths: ['skillsync_workflow/status.py', 'skillsync_workflow/protocol_tokens.py']
source: "docs/specs/2026-11-22-angielskie-aliasy-tokenow-protokolu.md, ustalenie 20 i Q8; skillsync_workflow/status.py:653-656 (stan @ ccc01eb), status.py:657 (aktualnie: token_alternation('waiting'))"
verified: 2026-11-22
---
Objaw: dopisek `(waiting for <zgoda>)` albo `(czeka na <zgoda>)` po `Status:` może zostać odczytany jako zgoda albo jako nowy stan zgody, który wpływa na `CP` lub `READY`.
Przyczyna: `approve` usuwa ten dopisek wyrażeniem regularnym z tokenem `waiting` i nazwą zgody. Dopisek jest informacją dla człowieka. Zgody zapisuje wyłącznie użytkownik, wpisem w `approvals.jsonl`.
Reguła: `waiting for` i `czeka na` nigdy nie są słowem zgody ani źródłem stanu zgody. Nie dodawaj tokenu do stanów `CP`, `READY`, `approvals` ani `needsHuman`. Usunięcie dopisku przy `approve` nie zmienia statusu planu ani wyniku.
Jak wykryć: `grep -rn "waiting" skillsync_workflow` — poza definicją tokenu w `protocol_tokens.py` jedynym użyciem ma być czyszczenie dopisku w `status.py` (`approve`). Każda inna ścieżka czytająca zgodę nie korzysta z tego tokenu.
