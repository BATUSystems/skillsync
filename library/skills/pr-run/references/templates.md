# Szablony pr-run

## Opis PR-a

Opis tworzysz jako draft i aktualizujesz do chwili zamknięcia przebiegu. Każdą wersję opisu (także komentarz i notatkę wydania) zapisz do pliku i przepuść przez `skillsync leakcheck --text <plik>` przed publikacją: nazwy osób, klientów i wydarzeń oraz aliasy kart w publicznym rdzeniu to FAIL.

```markdown
## Cel
<jedno zdanie>

## Zakres zmiany
<obszary; non-goals>

## Wpływ na API i bazę
<kontrakty, migracje albo „brak”>

## Testy
<komenda; wynik z exit code; `NOT_RUN` z powodem>

## Ryzyko i rollback
<ryzyka z planu; sposób cofnięcia>

Plan: docs/plans/RRRR-MM-DD-<slug>.md

Ticket: <KEY>

Status: in-progress
```

Stan `Status: complete` ustawiasz dopiero po spełnieniu dwóch warunków: wszystkie pozycje w `Progress` są `[x]` i kontrole zakończyły się PASS. Układ zgodny z zasadami PR-ów w [delivery-workflow-jira-azure](../../delivery-workflow-jira-azure/SKILL.md).

## Komentarz końcowy

Dla każdego PR-a wystarcza jeden taki komentarz; zanim wyślesz kolejny, sprawdź `pr-last-thread`.

```markdown
<!-- pr-run:summary -->
**Wynik:** <complete / in-progress / BLOCKED>
**Zmiana:** <kroki `[x]` w `Progress`; liczba commitów>
**Dowody:** <komenda: PASS/FAIL, exit code; review: PASS albo CHANGES_REQUIRED>
**Otwarte:** <kontrole w toku (np. CI), blokery, `NOT_RUN` z powodem>
**Następna akcja:** <np. `/pr-run --resume`, review człowieka, decyzja o wersji>
```

Limit: ok. 15 linii. Nie zamieszczaj poświadczeń ani surowych logów, a stan ukończenia podawaj wyłącznie, gdy faktycznie go osiągnięto.
