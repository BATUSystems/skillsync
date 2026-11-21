# Adapter Claude Code

Wywołanie: `/feature` (skill z `.claude/skills/feature`). `skillsync` instaluje ten sam skill
co dla Codexa (`.agents/skills/feature`) i ten sam zestaw ról.
Nazwa `agentflow` (używana przejściowo) jest wycofana: `/agentflow` przestaje działać po `skillsync link`,
a `agentflow` w manifeście (`skills:`) działa już tylko jako alias `feature`, nie jako komenda.
W trybie roboczym (nagłówek `dispatch: <run-id> from Dyspozytor (session <id>)`, patrz
multi-session.md) raport i prośby do dyspozytora wysyłaj narzędziem `SendMessage` na `<id>`
z nagłówka; bez `<id>` wypisz wiadomość w formacie wklejki z multi-session.md.

Deleguj natywnym narzędziem `Agent` (starsze wersje: `Task`), jawnie podając `subagent_type`:

| Rola w planie | Typ Claude | Domyślny model | Effort | CLAUDE.md | maxTurns |
|---|---|---|---|---|---|
| explore | explore | haiku | low | pomijany (`omitClaudeMd`) | 40 |
| implementer | implementer | sonnet | medium | tak | 170 |
| test-writer | test-writer | sonnet | medium | tak | 165 |
| test-runner | test-runner | haiku | low | pomijany | 125 |
| reviewer | reviewer | sonnet | high | tak | 85 |
| verifier | verifier | sonnet | low | pomijany | 45 |
| scribe | scribe | haiku | low | tak | 115 |
| mechanic | mechanic | haiku | low | pomijany | 40 |

`maxTurns` (limit tur roli) chroni przed zapętleniem. Tura to jedna odpowiedź modelu razem z jej równoległymi
wywołaniami narzędzi. Po limicie podagent zwraca wynik oznaczony jako częściowy („stopped at its N-turn limit …
PARTIAL output”, Claude Code ≥ 2.1.246), który wznawiasz przez `SendMessage`; to nie błąd. Wartości to p95 liczby
tur z transkryptów podagentów (n = 1836, konto prywatne, 2026-11-21) × 1,5, zaokrąglone w górę do 5 (cap 200);
dla `mechanic` próba była za mała (n = 1), więc zostaje wartość startowa 40. Wznowienia kumulują się w jednym
transkrypcie, więc limit może być luźniejszy od potrzeby. Korekta limitu to zmiana w tej tabeli i we frontmatterze roli
(pilnuje ich zgodności `tests/test_skillsync_agent_roles.py`).

Granica „podumysły nie commitują” (AGENTS.md) jest tylko w treści ról. Próba z 2026-11-21 (Claude Code 2.1.318) pokazała,
że wzorzec Bash w `disallowedTools` podagenta (obie formy: `Bash(git commit *)` i `Bash(git commit:*)`) usuwa całe
narzędzie Bash, a nie tylko wskazane polecenie, więc żadna rola nie ma `disallowedTools`. Twardy hook na commit podagenta
to osobny zakres (CLAUDE-3); `guard-commands` blokuje dziś tylko trwałe usuwanie plików.

F0-disallowedTools: nieskuteczne

Źródłem instrukcji ról są wspólne `agents/*.md` z biblioteki; `skillsync` instaluje je w
`.claude/agents/`. Zestaw ról jest ten sam co w Codexie: projekt z `feature` bez `agents:`
dostaje wszystkie role z `codex.yaml`; `agents:` w manifeście zawęża listę dla obu hostów.
Model roli wynika z frontmattera; trudny krok eskaluj parametrem `model` (np. `opus`)
w pojedynczym wywołaniu `Agent`, nie całą ekipę. Podobnie `effort` (`low|medium|high|xhigh|max`):
parametr wywołania nadpisuje frontmatter tylko dla tego wywołania; eskalacja trudnego kroku
to `model` i/lub `effort` w pojedynczym wywołaniu, nie zmiana definicji roli.
`omitClaudeMd: true` (Claude Code ≥ 2.1.271; starsze wersje cicho ignorują klucz) oznacza, że
rola nie widzi CLAUDE.md — zlecenie musi zawierać komplet poleceń, ścieżek i reguł. Model sesji głównej wybiera użytkownik.
Przy braku roli (np. sesja sprzed `skillsync link`) zgłoś to i poproś o restart sesji
lub `/agents`; nie zastępuj roli innym klientem uruchamianym przez shell.

Nie uruchamiaj `claude -p` ani innego klienta zamiast natywnego podagenta. Jeśli narzędzie
delegacji jest niedostępne, zgłoś brak niezależnego review jako ograniczenie wymagające
decyzji użytkownika.

Limit: maksymalnie 3 równoległe podagenty poza orkiestratorem; testy na wspólnych zasobach
sekwencyjnie. Do poprawek tego samego kroku wznawiaj tego samego agenta (`SendMessage`
do jego id), a niezależne review uruchamiaj w świeżym kontekście; każda runda [pętli przeglądu](review-loop.md)
to nowy `reviewer`. Model recenzenta w pętli (każdy profil: `spec`, `plan`, `kod`, `docs`): sesja na innym modelu niż `sonnet` → domyślny, sesja na `sonnet` → `model: opus`; profil `kod` przy ryzyku planu high może dostać `model: opus` zawsze. Nie przekazuj pełnej
historii; wystarcza zlecenie z planem, zakresem i ścieżkami.

`explore`, `reviewer` i `verifier` mają tylko Read/Grep/Glob — orkiestrator przygotowuje diff i listę
nowych plików do review. `implementer` i `test-writer` mają zapis i Bash; `scribe` zapis bez Bash (tylko dokumentacja),
`mechanic` edycję i Bash (mapowane podmiany, grep); `test-runner` ma
Bash do testów (skutki uboczne testów, nie tryb read-only). Uprawnienia sesji w
`.claude/settings.json` mogą wymagać zgody na komendy testów — nie zmieniaj ich samodzielnie;
zakaz zapisu z instrukcji roli obowiązuje niezależnie od uprawnień.

Plan zatwierdza użytkownik w głównej rozmowie. Nie uzależniaj workflow od `AskUserQuestion`
ani `EnterPlanMode`. Jawna zgoda na konkretny plan obowiązuje także po zmianie kontekstu.
