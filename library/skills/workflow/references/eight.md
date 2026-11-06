# Workflow v2: osiem rozszerzeń

Wspólne źródło: workflow/defaults.yaml → workflow/projects/<project-name>.yaml →
workflow manifestu. CLI: skillsync <komenda> <projekt> [--layer core=<ścieżka>] [--json]
(most; po migracji treści `--layer core=<ścieżka>` z `layer.yaml`).
PASS/FAIL/BLOCKED/NOT_RUN odnoszą się do wykonanej operacji; plan i brak konfiguracji
nie są dowodami przetestowania aplikacji. Stan, logi i backupy pozostają w .skillsync/.

## Środowisko

```yaml
workflow:
  environment:
    start: [python3, server.py]
    cwd: .
    inputs: [server.py, requirements.txt]
    resources: [project-test-db, project-port-8000]
    ttl: 3600
    startup_timeout: 30
    health: {url: 'http://127.0.0.1:8000/health'}
    prepare_checks: []
```

Start/stop: env --action up/down --run. Powtórny start wykorzystuje tylko świeży,
zdrowy proces własny. Deskryptor sprawdza PID/UID/starttime/session; nie jest dowodem
stanu bazy. Port/baza muszą być niezależne od innych sesji. Launchery --generate --run
używają tego samego CLI. Linux lifecycle jest dostępny; Windows backend BLOCKED.

## Obserwacja → E2E

```json
{"id":"checkout", "URL":"http://127.0.0.1:8000/checkout",
 "observed_at":"2026-10-01T12:00:00+00:00",
 "source_fingerprint":{"head":"actual HEAD", "digest":"actual source digest"},
 "steps":[{"action":"click","locator":"observed test id","expected":"observed result"}],
 "evidence":[".skillsync/e2e/artifacts/checkout.png"]}
```

Użyj aktualnego fingerprintu z checkpoint/raportu narzędzia (nie kopiuj przykładu).
Record wymaga --observation .skillsync/e2e/input.json --run; zapisuje observations i
scenarios jako dane. Dopasuj runner do scenariusza, skonfiguruj workflow.e2e.checks
jak validation.checks, argv i kind: e2e, required: true. Run wymaga aktualnej obserwacji,
niezmienionych dowodów, scenariusza i faktycznie wykonanych testów. Scenariusz danych
nie generuje automatycznie testu w języku repo. Brak rzeczywistego browsera ujawnij.

## CI i claims

CI: raport projektu {source: fingerprint, checks: [{name,status,log: ścieżka}]}.
Diagnoza z logu jest kandydatem, cause_verified: false. Konfiguracja delivery.ci:
max_iterations (0..3), rerun_check_ids, fix_check_ids — tylko ID validation checks argv.
Local PASS i ci_status: NOT_RUN nie oznaczają, że pipeline zdalna jest zielona.
Claims: --task-id X --run-id R --action acquire/renew/release/transfer --run; TTL 1..86400,
transfer --to-run-id S, wygaśnięcie wymaga --takeover. Zapis atomowy, obcy aktywny lease
blokuje. Nie przejmuj zadania wyłącznie dlatego, że wygasł czas.

## Migracja i profile

upgrade: revision: v2; artifacts: [{source: workflow/assets/example.yaml,
target: .skillsync/providers/example.yaml}]. Źródło względne biblioteki, cel względny
projektu pod .skillsync/providers/, .skillsync/scripts/, .scripts/skillsync/ lub docs/workflow/.
Plan pokazuje base/local/incoming i create/update/unchanged/preserve-local/conflict.
Apply z --run zapisuje backup i pending przed zmianami; rollback chroni późniejsze edycje.
Łączny budżet transakcji 512 KiB, bez symlinków. link odświeża artefakty generowane.
Profile selected jest addytywne; core/testing wspólne, selected: [] jawny opt-out.
profiles pokazuje katalog opisów i budget, doctor kontroluje tę samą ekspansję co generator.

## Trackery

Biblioteczne workflow/trackers/{github,jira,azure}.yaml deklarują read i update-title.
Wynik wspólny: provider/id/title/state/url. Params github: repo (owner/repo), id; jira: id
(klucz issue); azure: organization, project, id. update-title dodaje title. Parametry są
ścisłe i nie zawierają auth. GH_TOKEN/GITHUB_TOKEN; JIRA_URL + JIRA_TOKEN albo
JIRA_USER/JIRA_API_TOKEN; AZURE_DEVOPS_EXT_PAT. HTTPS, brak redirectów i sekretów argv.
Zapisy wymagają jawnej autoryzacji skutku oraz --run --allow-write. Odczyt sprawdza
żądaną wartość. Gotowość adaptera nie potwierdza autoryzacji konta ani testu produkcyjnego.
Project override: .skillsync/providers/<id>.yaml, version: 1, capabilities, operations.
Operacja: effect read/write, required_params, argv (bez shell) lub builtin, result
(type object/list, required pól), timeout <=300. Write: verify {operation: read, match: pola}.
Substytucja argv obsługuje tylko {param}; literalne nawiasy klamrowe zapisz jako {{ }}.
Kontrakty REST: [GitHub](https://docs.github.com/en/rest/issues/issues),
[Jira](https://developer.atlassian.com/server/jira/platform/updating-an-issue-via-the-jira-rest-apis-6848604/),
[Azure](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/work-items/update?view=azure-devops-rest-7.1).

## Strażnicy konwencji

```yaml
workflow:
  guardrails:
    rules:
    - id: no-debugger
      paths: ['src/**/*.ts']
      forbidden: '\bdebugger\s*;'
      severity: error
    checks: []
```

required/forbidden to string lub lista regexów, multiline. Wynik zawiera file/line/rule,
bez treści dopasowania. Zaufane checks mają argv i kind: lint. guard --files ... --run;
validate quick/final dołącza project-guard gdy rules/checks są skonfigurowane. Native
lint jest właściwy dla AST/import resolution; regex nie zastępuje parsera.
