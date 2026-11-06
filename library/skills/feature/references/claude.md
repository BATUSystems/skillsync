# Adapter Claude Code

Wywołanie: `/feature` (skill z `.claude/skills/feature`). `skillsync` instaluje ten sam skill
co dla Codexa (`.agents/skills/feature`) i ten sam zestaw ról.
Nazwa `agentflow` (używana przejściowo) jest wycofana: `/agentflow` przestaje działać po `skillsync link`,
a `agentflow` w manifeście (`skills:`) działa już tylko jako alias `feature`, nie jako komenda.
W trybie roboczym (nagłówek `dispatch: <run-id> from Dyspozytor (session <id>)`, patrz
multi-session.md) raport i prośby do dyspozytora wysyłaj narzędziem `SendMessage` na `<id>`
z nagłówka; bez `<id>` wypisz wiadomość w formacie wklejki z multi-session.md.

Deleguj natywnym narzędziem `Agent` (starsze wersje: `Task`), jawnie podając `subagent_type`:

| Rola w planie | Typ Claude | Domyślny model | Effort | CLAUDE.md |
|---|---|---|---|---|
| Explore | Explore | haiku | low | pomijany (`omitClaudeMd`) |
| implementer | implementer | sonnet | medium | tak |
| test-writer | test-writer | sonnet | medium | tak |
| test-runner | test-runner | haiku | low | pomijany |
| reviewer | reviewer | sonnet | high | tak |
| verifier | verifier | sonnet | low | pomijany |
| scribe | scribe | haiku | low | tak |
| mechanic | mechanic | haiku | low | pomijany |

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
to nowy `reviewer`. Model recenzenta w pętli (każdy profil: `spec`, `plan`, `kod`): sesja na innym modelu niż `sonnet` → domyślny, sesja na `sonnet` → `model: opus`; profil `kod` przy ryzyku planu high może dostać `model: opus` zawsze. Nie przekazuj pełnej
historii; wystarcza zlecenie z planem, zakresem i ścieżkami.

`Explore`, `reviewer` i `verifier` mają tylko Read/Grep/Glob — orkiestrator przygotowuje diff i listę
nowych plików do review. `implementer` i `test-writer` mają zapis i Bash; `scribe` zapis bez Bash (tylko dokumentacja),
`mechanic` edycję i Bash (mapowane podmiany, grep); `test-runner` ma
Bash do testów (skutki uboczne testów, nie tryb read-only). Uprawnienia sesji w
`.claude/settings.json` mogą wymagać zgody na komendy testów — nie zmieniaj ich samodzielnie;
zakaz zapisu z instrukcji roli obowiązuje niezależnie od uprawnień.

Plan zatwierdza użytkownik w głównej rozmowie. Nie uzależniaj workflow od `AskUserQuestion`
ani `EnterPlanMode`. Jawna zgoda na konkretny plan obowiązuje także po zmianie kontekstu.
