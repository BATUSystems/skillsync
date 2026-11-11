# Przerwanie i pomiar

Czytaj przed pierwszym checkpointem przebiegu, przy przerwaniu pracy, wznowieniu albo zapisie pomiarów.

## Raport postępu: po każdym kroku albo co 30 minut

Raport postępu to linia `- HH:MM zrobione: …; w toku: …; ryzyko: …[; czeka: …]` w `.skillsync/runs/<run-id>/checkpoints.md`
(czyta ją człowiek i strona statusu). Dopisuj ją **po każdym kroku ALBO co 30 minut, co nastąpi pierwsze**. Agent nie ma
zegara: przy każdym powrocie podumysła i przed każdą długą operacją sprawdź godzinę (`date +%H:%M`) i, gdy od ostatniej
linii minęło 30 minut, dopisz linię `w toku: …` nawet bez zakończonego kroku. Długa praca jednego podumysła nie zwalnia
z raportu: strona statusu po ok. 35 minutach ciszy przy planie `in_progress` pokazuje „brak raportu od N min”.
Raport „jedna linia na podkrok”: po każdym podkroku `Fn.k` dopisz jedną linię (podkrok, wynik albo sha, czas), albo co 30 minut,
co nastąpi pierwsze. Ta linia wypełnia istniejący format `- HH:MM zrobione: …` (podkrok i wynik albo sha w `zrobione`), a nie zastępuje go.
Raport nie zastępuje rekordu `skillsync checkpoint` (niżej) ani odwrotnie.

## Checkpoint po każdym wyniku (obowiązkowe)

Rekord zapisuje orkiestrator, nie równolegle, po każdym wyniku kroku, także `FAIL`, `BLOCKED` i `NOT_RUN`:
`skillsync checkpoint <projekt> --run-id <id> --step <krok> --status <status> --evidence <plik> --next <opis>`.

- `<projekt>`: katalog, w którym pracujesz; w dispatch worktree paczki.
- `--run-id`: slug planu; w dispatch `<run>.<paczka>`, a przy ponowieniu w nowym worktree `<run>.<paczka>.2`.
- `--status`: `CHANGES_REQUIRED` zapisuj jako `FAIL`; rundy `spec-review` i `plan-review` bez `--cause-key`, rundy `code-review*` z kluczem merytorycznym (kroki i wyjątek: `review-loop.md`). Zamierzona czerwień testu to `PASS` osobnego kroku z logiem czerwieni, nie porażka.
- `--evidence`: wymagany przy `PASS`; przy `FAIL` podaj log porażki. Każdy rekord ma własny plik w `.skillsync/runs/<run-id>/` (`<krok>-<próba>.log`), bez nadpisywania. Plan ani inny plik śledzony nie jest dowodem: zmieni się i trwale da `partial`.
- Ponowienie i domknięcie porażki idą pod **tą samą nazwą** `--step` z kolejnym `--attempt`, także gdy porażkę zamyka inna rola, np. kolejna runda `code-review` po poprawkach. Nowa nazwa kroku zostawia stary na `FAIL` i daje `incomplete`. Wyjątek: ponowne zamknięcie `final-2`, bo `final` ma już `PASS`. `retro` sprawdzaj też w trakcie przebiegu, nie dopiero przy zamknięciu.
- Przy problemie (porażka, ponowienie, niewykonanie): `--cause <opis>`, `--cause-key <stały slug>`, `--attempt <n>` (liczy próby kroku w tym przebiegu, nie `.N`); `--owner instruction|skill|hook|route|test` tylko przy pewności, inaczej pomiń. `warnings` w wyniku zapisu znaczy, że tak zakończony przebieg dostanie klasę `unexplained`: od razu dopisz rekord tego kroku z przyczyną lub nowym dowodem.
- Znane liczby, przyrost w tym rekordzie: `--denied <n>` (odrzucone akcje), `--inherited <n>` (zastane porażki).
- Zamknięcie: osobny krok `final` z logiem końcowych kontroli i `--status PASS --final`, przy ponownym zamknięciu `final-2`. Powtórzony krok to problem, a rekord po `--final` otwiera przebieg. Bez zamknięcia przebieg ma klasę `incomplete`.
- Krok, który kończy się `NOT_RUN` albo `BLOCKED`, zostaje taki: nie zamieniaj go na `PASS`. Przebieg pozostaje `incomplete`; powód podaj w „Wyniku”.
- W `--step`, `--cause-key` ani nazwie dowodu nie wpisuj tajemnic ani treści sesji: te pola trafiają do raportu.

Zapis samych `PASS` daje fałszywe „czysto”: narzędzie nie wykryje porażki bez rekordu.
Checkpoint niewykonalny (brak CLI albo manifestu): `NOT_RUN` w „Wyniku” z powodem; w dispatch `REPORT BLOCKED`.

## Pętla samodoskonalenia (przy zamknięciu, obowiązkowe)

Przed `final` idzie retrospektywa jakościowa ([retro-quality](retro-quality.md)). Plan `done` wymaga wcześniej rekordu `--final` przebiegu z linii `Przebieg:`. Hook Stop zablokuje turę raz na sesję i plan, gdy go brakuje, a `doctor` zgłosi ostrzeżenie. `CHANGES_REQUIRED` zapisuj jako `FAIL` z kluczem z `workflow/cause-keys.yaml` (rundy `spec-review` i `plan-review` bez klucza, [pętla](review-loop.md)); `unrecorded_signals` w `retro` pokazuje pominięte. Klucz nazywa to, co review znalazło (`docs-behind-code`, `test-gap`, `state-rule-gap`, inna pozycja listy, w ostateczności `other`), nie sam fakt uwag: `review-changes-required` jest historyczny, a nowy rekord pod nim wraca jako `recurred`. Ponowny zapis kroku po poprawce: `--attempt N` z tym samym `--cause-key`, inaczej przebieg dostaje klasę `unexplained`; drugi `--final` zapisuj tylko wtedy, gdy stan po pierwszym się zmienił.

1. `skillsync retro <projekt> --run-id <id>`: klasa przebiegu i rubryka do „Wyniku”; `incomplete`/`unexplained` wyjaśnij, nie poprawiaj rekordów.
2. Kandydaci są liczeni z całego projektu (próg: ten sam `cause_key` w ≥ 2 przebiegach). Przedstaw użytkownikowi `recurred`
   (poprawka nie zadziałała), potem `ready`; `needs_owner`/`needs_case` z brakiem; `decided` pomiń. `BLOCKED` przez
   `decisions.status: invalid` napraw w rejestrze przed decyzjami.
3. Poprawka kandydata = jeden najmniejszy właściciel i przypadek, który najpierw zawodzi ([lessons](../../workflow/references/lessons.md)).
   Wyjątek: propozycja retrospektywy jakościowej z właścicielem `skill`, `instruction`, `docs` albo `route`, przyjęta przez „TAK P<n>” albo „TAK <run-id>/P<n>”, nie wymaga przypadku, który najpierw zawodzi, ani osobnego `bugfix`/`feature` ([retro-quality](retro-quality.md)); kod (`test`, `hook`) bez zmian. Krok 4 (osobny `bugfix`/`feature`) dalej obowiązuje kandydatów `skillsync retro` z porażek.
   Bez zgody użytkownika nic nie zmieniaj; propozycja nie blokuje `done` bieżącego przebiegu.
4. Decyzję zapisz poleceniem `skillsync retro-decide <projekt> --cause-key K --decision approved|rejected`, przy `approved` z `--owner` i `--case`;
   nie edytuj YAML ręcznie. Po poprawce: `--decision approved --replace --ref <commit>`, co przesuwa datę na dzień poprawki. Poprawkę realizuj jako osobny `bugfix`/`feature` z tym przypadkiem jako oracle.
5. Kolejne przebiegi weryfikują: `recurred` wraca do kroku 3 z nowym przypadkiem, a `recurred` z `approved` i `fixed: false` znaczy, że poprawka jeszcze czeka; `decided` nie jest ponownie proponowany.
   Problem jednorazowy (jeden przebieg) zostaje rekordem z `--cause-key`; nie twórz dla niego reguły (wyjątek: korekta użytkownika w retrospektywie jakościowej, [retro-quality](retro-quality.md)).

## Wznowienie i pomiar

Checkpoint jest zapisem oceny operatora, nie samoczynnym potwierdzeniem poprawności; wiąże go fingerprint kodu i dowodów.
`skillsync resume` przed wznowieniem sprawdza aktualność, a `metrics` agreguje realne
pomiary. Zapisywanie czasu/kosztu jest opcjonalne; brak pomiaru pozostaje unknown.
Powtarzalne wnioski prowadź przez `workflow-retro` (jeśli zainstalowany — `workflow-extended`), nie przez powiększanie rdzenia.
