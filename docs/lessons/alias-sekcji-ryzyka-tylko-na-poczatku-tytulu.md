---
title: "Angielski alias sekcji ryzyka łapie słowo tylko na początku tytułu: „Data model” nie jest sekcją Data"
tags: [skillsync, protocol, pre-implement, regex, aliases, risk]
paths: ['skillsync_workflow/protocol_tokens.py', 'skillsync_workflow/status.py']
source: "docs/specs/2026-11-22-angielskie-aliasy-tokenow-protokolu.md, Q1 i Q1a; stan @ ccc01eb: status.py:105 (\\b…\\b bez wielkości liter, dowolne miejsce tytułu)"
verified: 2026-11-22
---
Objaw: plan lub spec z nagłówkiem „Data model”, „Data flow” albo „Migration of users” jest traktowany jak sekcja ryzyka i wymusza pre-implement, mimo że żadnej sekcji ryzyka nie ma.
Przyczyna: reguła `\b(Kontrakty|Migracja|Dane)\b` bez wielkości liter łapie słowo w dowolnym miejscu tytułu. Polskie słowa są rzadkie w innych nagłówkach, więc taka reguła była bezpieczna. Angielskie `Data`, `Migration`, `Contracts` są pospolite.
Reguła: polskie słowa zostają `\b…\b` w dowolnym miejscu tytułu. Angielski alias liczy się tylko na początku tytułu (po zdjęciu numeru `1.`, znaczników `*`, `_`, `` ` `` i spacji), po którym jest koniec tytułu albo separator (`:`, `-`, `–`, `—`, `/`, `,`, `+`, `&`, `(`) albo ` and `. Każdą nową formę tytułu dopisz do testu, nie do wyrażenia „na oko”. Świadome ryzyko, bez obejścia: polski nagłówek `## Data` (słowo „data”) liczy się jako sekcja `Data`.
Jak wykryć: w `protocol_tokens.risk_section_title` sprawdź: „Data model”, „Migration of users”, „API contracts” daje fałsz; „Data”, „3. Data and migration”, „Contracts: API” daje prawdę.
