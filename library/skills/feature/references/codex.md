# Adapter Codex (rozszerzenie VS Code i CLI)

Wywołanie: `$feature` (wybór skilla przez `$` lub `/skills`). Nie zakładaj istnienia komendy
`/feature` w Codexie. `skillsync` udostępnia ten sam skill w `.agents/skills/feature`.
Nazwa `agentflow` (używana przejściowo) jest wycofana: `$agentflow` przestaje działać po `skillsync link`,
a `agentflow` w manifeście (`skills:`) działa już tylko jako alias `feature`. W trybie roboczym
(nagłówek `dispatch:`) wiadomości do dyspozytora wypisz jako „wklejka karta → dyspozytor”
(format w multi-session.md, „Gramatyka wiadomości”), chyba że host ma narzędzie wiadomości
między sesjami (wtedy użyj go, adresując sesję `<id>` z nagłówka).

Deleguj przez natywne narzędzia dostępne w tej sesji, wybierając skonfigurowany typ agenta:

| Rola w planie | Typ Codexa | Domyślny model / wysiłek |
|---|---|---|
| Explore | explorer | gpt-6-luna / low |
| implementer | implementer | gpt-6.1-sol / medium |
| test-writer | test_writer | gpt-6.1-sol / medium |
| test-runner | test_runner | gpt-6-luna / low |
| reviewer | reviewer | gpt-6.1-sol / high |
| verifier | verifier | gpt-6-luna / low |
| scribe | scribe | gpt-6-luna / low |
| mechanic | mechanic | gpt-6-luna / low |

Źródłem ustawień jest biblioteczny `codex.yaml`, a instrukcji ról — wspólne `agents/*.md`.
`skillsync` generuje projektowe `.codex/agents/*.toml`. Przy braku dostępu do modelu zgłoś
konkretny błąd; nie zapętlaj ponowień. Właściwy model można ustawić w manifeście przez
`codex.agent_overrides.<rola źródłowa>.model` i `model_reasoning_effort`.

Nie uruchamiaj dodatkowego `codex exec` zamiast natywnego podagenta. Jeżeli narzędzie
delegacji nie obsługuje nazwanych typów, jawnie przekaż instrukcje i ustawienia odpowiedniej
roli z TOML (tylko parametry obsługiwane przez narzędzie). Nie twierdź wtedy, że wszystkie
ustawienia TOML, szczególnie sandbox, zostały zastosowane. Jeśli delegacja jest wyłączona,
zgłoś brak niezależnego review jako ograniczenie wymagające decyzji użytkownika.

Domyślny limit: 3 podagenci poza orkiestratorem. Wznawiaj agenta do poprawek tego samego
kroku, ale każda runda [pętli przeglądu](review-loop.md) to nowy `reviewer` (model recenzenta zmienia tylko
`codex.agent_overrides`, więc zwykle „ten sam model” i zapis tego w raporcie rundy, w każdym profilu: `spec`, `plan`, `kod`); zamykaj ukończone wątki, gdy zajmują limit. Nie przekazuj pełnej historii, gdy
wystarcza zlecenie z planem. Gdy model override wymaga pustego/krótkiego kontekstu,
zastosuj ten tryb i jawnie przekaż wymagane instrukcje.

`explorer`, `reviewer` i `verifier` mają domyślne `sandbox_mode = "read-only"`. Pozostałe role używają
`workspace-write`, bo testy zapisują artefakty. Nie zmieniaj globalnych uprawnień ani
trybu zatwierdzeń. Bieżące ustawienia uprawnień sesji mogą nadpisać domyślne role;
zakaz zapisu w instrukcji roli nadal obowiązuje. Skonfigurowany sandbox nie izoluje
wspólnej bazy ani portów.

Plan zatwierdza użytkownik w głównej rozmowie. Nie wymagaj narzędzi Claude ani zmiany
trybu na Plan. Jawna zgoda na konkretny plan obowiązuje również po zmianie tury/kontekstu.
