# Scenariusz ręczny: 3 karty, 4 paczki, 1 zależność, 1 zasób dzierżawiony

Cel: zweryfikować, że dyspozytor zadaje pytania o karty, sonduje, przydziela, obsługuje
dzierżawę i kolejkę merge oraz wznawia się z pliku stanu po restarcie. Wersja „na sucho” dla
trybu ręcznego (Codex bez wiadomości między sesjami) — na końcu. Reguły: [SKILL.md](../SKILL.md),
protokół: [multi-session.md](../../feature/references/multi-session.md), stan: [state.md](state.md).

## Dane

Repo `/home/user/Repos/Acme/App`, gałąź główna `main` (`1a2b3c4`), `run-id` `2026-10-02-a`.
Cel: `/dispatch domknij paczki W1–W4`. Manifest: `dispatch.max_parallel: 3`,
`leased_resources: [{name: e2e, ports: [5500, 4250], databases: [app_e2e]}]`,
`guards: {min_free_disk_gb: 6, max_load: 12}`, `id_registries: [SEC]`.

| Paczka | Zakres | Zależność | Zasób dzierżawiony |
|---|---|---|---|
| W1 | endpoint API + migracja (ryzyko wysokie) | — | — |
| W2 | widok listy (UI) | — | `e2e` |
| W3 | eksport raportu, migracja | — | `e2e` |
| W4 | testy kontraktowe i dokumentacja API | **W1** (wymaga W1 na `main`) | — |

## Przebieg

**0. Bramka modelu.** Dyspozytor odczytuje swój model z kontekstu. `claude-sonnet-5-5` →
„Dyspozytor wymaga Opus 5.5 lub nowszego; przełącz `/model opus` i ponów `/dispatch …`.
Nie startuję.” Po przełączeniu `claude-opus-5-5` → dalej. (Powtarzane w każdej turze.)

**1. Pytanie o karty.** `ListAgents` zwraca: „Zarządca” (ta sesja), dwie sesje „Nowy czat”
(duplikat nazwy), „Fabrikam stary” (waiting od 2 h). Dyspozytor pokazuje listę z identyfikatorami
i stanami i pyta (wielokrotny wybór): których wolno użyć. Użytkownik wybiera obie „Nowy czat”
i „Fabrikam stary”. Aliasy: `Dyspozytor`, `karta-1`, `karta-2`, `karta-3` (kolejność wyboru).
Host nie zmienia nazw programowo → tabela „obecna nazwa → alias”; użytkownik zmienia ręcznie;
dyspozytor weryfikuje `ListAgents` (3 różne nazwy, zgodne z identyfikatorami). Zapis stanu.

**2. Sonda.** `PROBE` do 3 kart. `karta-1` i `karta-2`: `FREE`. `karta-3` („Fabrikam stary”):
brak odpowiedzi → ponowna sonda → brak = `unavailable`; prośba do użytkownika o restart karty.
Po restarcie nowa sonda: `FREE` (stary identyfikator zastąpiony, mapowanie zaktualizowane).
Dyspozytor nie czekał na sesję-ducha z przydziałem.

**3. Zasoby.** `df` 38 GB wolne, load 2.1 → guardy spełnione; limit 3 (≤ `max_parallel`). Ostrzeżenie:
E2E przy load > 12 bywa niestabilne; dzierżawa serializuje `e2e`, więc 3 karty są akceptowalne.

**4. Plan dyspozycji.** Graf: W4 zależy od W1; ścieżka krytyczna W1 → W4. Równolegle: W1, W2, W3
(rozłączne pliki; W2/W3 współdzielą `e2e` → dzierżawa). Plan: `karta-1`←W1, `karta-2`←W2,
`karta-3`←W3, W4 po wejściu W1 na `main` (preferowana `karta-1`, ten sam obszar). Kolejka merge:
W1, W2, W3, W4 (zależność W4→W1 ma pierwszeństwo przed regułą „zmiany kontraktów ostatnie”; W1 jest addytywne dla W2/W3, więc wchodzi pierwsze i odblokowuje W4). Użytkownik: „OK, plan”. Dyspozytor zapisuje stan i **nie** traktuje tego jako GO
dla paczek. Rejestruje claimy (`task-claim`, `--run-id 2026-10-02-a.karta-1` itd., `--ttl 14400`, z katalogu głównego repo) W1–W3.

**5. Przydział.** `ASSIGN` do każdej karty (po aktualnej sondzie): nagłówek
`dispatch: 2026-10-02-a from Dyspozytor (session <id dyspozytora>)`, `/feature` z paczką, `input_commit=1a2b3c4`,
worktree `../App-wt/W1`, baza `app_integration_test_W1`, obszary zakazane, zasady dzierżaw/PUSH
SLOT/ID, podpis `karta-1/W1`, informacja „GO daje użytkownik w tej karcie”. Karty tworzą worktree
i bazy sami. Użytkownik daje GO planom paczek lokalnie w kartach.

**6. Identyfikatory.** `karta-1/W1`: `ID REQUEST SEC W1`; `karta-3/W3`: `ID REQUEST SEC W3`.
Dyspozytor sprawdza rejestr stanu i repo (ostatnie SEC-32): `ID GRANT SEC W1 SEC-33`,
`ID GRANT SEC W3 SEC-34`. Do tego czasu karty używały `SEC-TBD-<paczka>-1`.

**7. Dzierżawa.** `karta-2/W2`: `LEASE REQUEST e2e W2` → dyspozytor sprawdza porty 5500/4250
(wolne), brak procesów, load → `LEASE GRANT e2e W2`. `karta-3/W3`: `LEASE REQUEST e2e W3` →
`LEASE QUEUED e2e W3` (pozycja 1). `karta-2/W2`: `LEASE RELEASE e2e W2 exit=0 log=.../e2e-W2.log`
→ dyspozytor czyta log (liczba scenariuszy, exit 0, czas logu po ostatniej zmianie kodu),
sprawdza, że porty wolne → `LEASE GRANT e2e W3`.

**8. Raport i kolejka merge (tryb `direct`; w `pr` karta zgłasza status `review`, a PR scala człowiek).** `REPORT DONE W1 commit=f00d111 checks=… debt=… retro=0` → dyspozytor sprawdza
dowody w logach i `skillsync retro` (odbiór paczki), oznacza W1 `review→done`. `PUSH SLOT W1` → `PUSH GRANT W1` (pierwsza w kolejce)
→ `PUSHED W1 9e8d7c6` → `git ls-remote origin main` = `9e8d7c6` (zweryfikowano) → `MASTER MOVED
9e8d7c6 "endpoint+migracja W1" rebase=tak, migracja=regeneruj` do `karta-2` i `karta-3`
(W3 ma migrację: integrujący później regeneruje ją po rebase). Zależność W4 spełniona →
`ASSIGN W4` do `karta-1` z `input_commit=9e8d7c6`.

**9. Restart.** W2 dostał `PUSH GRANT` i wypchnął zmiany (`PUSHED W2` wysłane), W3 trzyma dzierżawę
`e2e`, W4 w toku. Sesja dyspozytora ginie (restart VS Code) zanim zapisze `PUSHED W2`. Stan na
dysku (fragment): `merge_queue: [{W2, granted}, {W3, waiting}]`, `leases: [{e2e, holder: W3,
released_at: null}]`, `master.head: 9e8d7c6`.

**10. Wznowienie.** Nowa sesja, model Opus 5.5 (bramka), `/dispatch resume 2026-10-02-a`.
Dyspozytor wczytuje plik; `ListAgents` pokazuje trzy „Nowy czat” (nazwy zresetowane) — dopasowuje po
identyfikatorach i prosi o ręczną zmianę nazw wg tabeli; weryfikuje. `STATUS?` do `karta-2`
(„DONE, pushed `a1b2c3d`”), `karta-3` („BUSY, e2e w toku”), `karta-1` („W4, czeka na użytkownika”).
Uzgodnienie: `git ls-remote` pokazuje `main = a1b2c3d` ≠ `master.head` (`9e8d7c6`) → `W2` rzeczywiście
wypchnięte (stan poprawiony na `verified` po weryfikacji hasha, wysłane `MASTER MOVED` do
`karta-3`/`karta-1`). Dzierżawa `e2e`: porty zajęte przez proces `karta-3` → zachowana
(nie zwalniana automatycznie). Dyspozytor pokazuje odtworzony `/dispatch status` i rozbieżności;
po akceptacji użytkownika wznawia pętlę (`karta-3` zwalnia dzierżawę, push W3, `ASSIGN`/koniec).

**11. Zakończenie.** Po `DONE` W3 i W4: raport zbiorczy (paczki, commity, dowody, dług,
otwarte decyzje). Pytanie o sprzątanie worktree i baz testowych — tylko za zgodą.

## Kryteria zaliczenia

- Zapytał o karty i nie użył „idle” jako „wolna”; aliasy i mapowanie zweryfikowane na liście.
- Sonda przed każdym przydziałem; duch nie zablokował paczki.
- Zgoda na plan ≠ GO; GO w kartach lokalnie.
- ID tylko od dyspozytora; dzierżawa `e2e` jedna naraz, GRANT po zweryfikowanym RELEASE.
- Push na bazę (tryb `direct`) tylko w slocie, hash zweryfikowany na zdalnym repo; `MASTER MOVED` po każdym pushu.
- Resume z pliku, uzgodnienie ze stanem faktycznym (ls-remote, procesy, karty) i potwierdzenie
  użytkownika przed wznowieniem; bramka modelu w każdej turze.

Warianty do osobnego przejścia: `MASTER RED` (stała data w teście: jeden właściciel poprawki,
reszta nie naprawia), `/dispatch drain` z GO użytkownika w karcie wbrew wygaszaniu (stan
aktualizowany, bez sporu), spadek modelu w trakcie (zapis stanu, brak grantów).

## Wersja „na sucho” (tryb ręczny Codex)

Host bez wiadomości między sesjami; recenzent gra użytkownika i karty, wklejając odpowiedzi.
Przebieg ten sam, różnice:

1. Krok 0: dyspozytor nie ma pewności co do tieru modelu → prosi użytkownika o potwierdzenie
   „najsilniejszy dostępny model rozumujący” przed startem.
2. Krok 1: zamiast `ListAgents` pyta „ile kart i jak są oznaczone?”; użytkownik: „3 okna”;
   dyspozytor nadaje aliasy i prosi o oznaczenie okien `karta-1, karta-2, karta-3`. `mode: manual`, `session_id: null`.
3. Każda wiadomość (`PROBE`, `ASSIGN`, `LEASE GRANT`, `PUSH GRANT`, `MASTER MOVED`) to blok
   `>>> WKLEJ W KARCIE: <Alias> … <<< KONIEC`, po czym dyspozytor kończy turę i prosi o wklejenie
   odpowiedzi z podpisem. Brak wklejki ≠ odpowiedź; brak podpisu → dopytanie.
4. Ścieżka ducha: użytkownik nie wkleja odpowiedzi na `PROBE` po prośbie ponownej → `unavailable`.
5. Weryfikacje (`git ls-remote`, logi, `df`, load) dyspozytor wykonuje samodzielnie.
6. Restart: użytkownik otwiera nową sesję, `$dispatch resume 2026-10-02-a`; dyspozytor czyta plik
   stanu, prosi o wklejenie bieżących `STATUS` z kart (dyspozytor drukuje bloki `STATUS?`),
   uzgadnia z `git ls-remote` i potwierdza z użytkownikiem.

Zaliczenie na sucho: wszystkie kryteria powyżej + każdy blok ma jawnego adresata, stan zapisany
po każdym zdarzeniu, żadnego użycia `ListAgents`/`SendMessage` i brak założonych odpowiedzi.
