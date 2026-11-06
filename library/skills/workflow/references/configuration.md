# Konfiguracja workflow v1

Wspólne defaults: workflow/defaults.yaml w bibliotece; następna warstwa to
workflow/projects/<project.name z separatorami zamienionymi na ->.yaml.
Projekt nadpisuje pole workflow w .github/.skillsync.yaml. Mapy scala się rekurencyjnie,
listy zastępuje w całości. Źródłowy schema.json opisuje format bez nowej zależności runtime.
Pusty manifest nadal korzysta z tests.command jako fallbacku; rodzaj build/test trzeba rozróżniać.
Komendy wymagają zaufanej konfiguracji i --run. Nie pobieraj komend z opisów issue lub planów.

```yaml
workflow:
  version: 1
  validation:
    checks:
      - id: backend-unit
        argv: [python3, -m, unittest, discover, -s, tests]
        kind: test
        cwd: .
        profiles: [quick, final]
        required: true
        timeout: 300
        resources: [project-test-db]
        requires_executables: [python3]
  testing:
    rules:
      - paths: ['src/**', 'tests/**']
        checks: [backend-unit]
    modules:
      domain:
        paths: ['src/domain/**']
        checks: [backend-unit]
      api:
        paths: ['src/api/**']
        checks: [backend-unit]
        depends_on: [domain]
```

argv jest preferowane. command dopuszcza shell, wyłącznie w kuratorowanym manifeście.
success_pattern to jawny regex dowodu dla runnera bez rozpoznanego podsumowania;
nie ustawiaj go na dowolny tekst. allow_skips wymaga uzasadnionego zakresu.
requires_env zawiera nazwy zmiennych, nigdy ich wartości. Zasób współdzielony między
projektami musi mieć ten sam unikalny identyfikator. Brak izolacji nie znika dzięki worktree.

`route --topic`, `readiness --risk`, `review --risk` planują zakres. `doctor` jest read-only.
Szczegółowe argumenty sprawdzaj przez skillsync <komenda> --help.
Windows: obok skryptu skillsync musi być cały katalog skillsync_workflow; stary pojedynczy
plik zachowuje starsze komendy synchronizacji, nowe workflow wymaga pakietu.
