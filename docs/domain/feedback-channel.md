---
title: "Zgłoszenia zmian do warstwy przez tracker właściciela (`skillsync feedback`, `feedback:` w `layer.yaml`, `feedback_targets`, zgoda `WYŚLIJ <sha12>` albo przycisk na stronie statusu)"
tags: [skillsync, feedback, tracker, azure, layers, consent, retro, dashboard]
paths: ['skillsync_workflow/feedback.py', 'skillsync_workflow/delivery.py', 'skillsync_workflow/dashboard_http.py', 'skillsync_workflow/dashboard.py', 'library/workflow/trackers/azure-feedback.yaml', 'library/hooks/guard-commands.py', 'library/skills/workflow/references/feedback.md', 'library/skills/dashboard/assets/*', 'tests/test_skillsync_feedback*.py', 'tests/test_skillsync_dashboard*.py']
source: "spec i plan CORE-1140-FEEDBACK (22.11.2026, decyzja „całość w 1.14.0”, bramka zgody w modelu CP zatwierdzona przez Dyspozytora); przycisk strony: spec przycisku FEEDBACK (decyzje przeglądu 22.11, D1–D6, Q1–Q6), kod `feedback.approve_from_preview`, `drafts_state`, `cmd_wait` i `dashboard_http.feedback_approve` (23.11.2026); kod i testy jak w polu paths"
verified: 2026-11-23
---
# Zgłoszenia zmian do warstwy: `skillsync feedback` (od 1.14.0)

Zespół, który odkrył potrzebę zmiany warstwy, a nie jest właścicielem jej repozytorium, zgłasza ją do trackera właściciela. Mechanizm jest opcjonalny:
sesje Barcisia same decydują, czy zgłosić do trackera, czy wprowadzić zmianę commitem (`feedback.channel` w manifeście).

## Elementy

| Element | Gdzie | Rola |
|---|---|---|
| sekcja `feedback:` w `layer.yaml` | warstwa | PROPOZYCJA celu: `kind: azure-boards`, `organization`, `project`, `area` (ścieżka pod projektem, segmenty rozdzielone `/`), `type` (domyślnie `Task`), `labels` (do 5). Zła sekcja jest pomijana z ostrzeżeniem `feedback-invalid` i nie blokuje warstwy. Etykiety `fb-`, `fbk-`, `layer:`, `feedback`, `core-candidate` są zarezerwowane. |
| `feedback_targets` | konfiguracja maszyny | AUTORYZACJA celu: lista `{layer, kind, organization, project}` zatwierdzona przez człowieka (`skillsync feedback trust <warstwa>`, tylko z terminala). `layer.yaml` bywa z niezaufanego repozytorium, a zalogowany `az` sięga do każdej organizacji użytkownika. Zmiana organizacji albo projektu w `layer.yaml` unieważnia zgodę (`changed`). Zły wpis to `LayerError` przy wczytaniu konfiguracji. |
| szkic `.skillsync/feedback/F<n>.json` | GŁÓWNY checkout | `layer`, `kind` (`bug`, `change`, `tool`, `docs`), `title` (≤120), `target` (plik warstwy albo nazwa skilla), `problem`, `evidence`, `proposal`, `acceptance` (neutralne przypadki), `cause_key`, `core_candidate`, `retro`. Teksty są danymi niezaufanymi: bez znaków sterujących i bidi, z limitami. |
| dostawca `azure-feedback` | `library/workflow/trackers/azure-feedback.yaml` | `internal: true`: `skillsync tracker` go odrzuca, deskryptor jest czytany tylko z warstwy core z konfiguracji maszyny (nie z projektu, wyższej warstwy ani `--layer`). Operacje: `workitem-find`, `workitem-find-id`, `workitem-create`, `workitem-comment`, `workitem-last-comment`. Treść idzie przez `--in-file`, nigdy w argv; poświadczenia z `AZURE_DEVOPS_EXT_PAT` albo `az devops login`. |

## Przepływ

1. `skillsync feedback new --file szkic.json` zapisuje szkic jako `F<n>`.
2. `skillsync feedback draft F<n>`: sprawdza cel (`trusted`), szuka otwartego elementu z tagiem `fbk-<hash12>` (warstwa + `target` + `cause_key`):
   jest = tryb KOMENTARZ, nie ma = NOWY element. Skanuje wycieki dokładnych bajtów (tytuł, tagi, treść) przeciw tożsamościom INNYCH warstw (cel skanu to warstwa
   docelowa) i szuka sekretów; drukuje podgląd z `sha12` i poziomem pokrycia tożsamości (`generic` = nazwy klientów NIE są sprawdzane). Zapisuje `F<n>.preview.json`.
   Tytuł istniejącego elementu to cytat z trackera (dane, ≤80 znaków).
3. Człowiek daje zgodę jednym z dwóch sposobów (obie dają ten sam wpis):
   - wpisuje w sesji `WYŚLIJ <sha12>` (albo `WYSLIJ`); sesja woła `skillsync feedback approve F<n> --word "WYŚLIJ <sha12>"`;
   - klika przycisk na stronie statusu (sekcja „Przycisk na stronie statusu” niżej).
   Wpis zgody to `{id, draft, sha, at, source}` w `.skillsync/feedback/approvals.jsonl` (główny checkout, 0600) z PEŁNYM sha256 treści;
   `source` to `cli`, `ide` albo `dashboard`.
   To osobny plik od `approvals.jsonl` przebiegów: tamten nie niesie szkicu ani zużycia, a `skillsync approvals record` nie przyjmuje tej zgody.
4. Sesja agenta, która czeka na zgodę, woła `skillsync feedback wait F<n>` (tylko odczyt: 0 = ważna zgoda, 3 = czas minął, 2 = błąd użycia; zob. `workflow/references/feedback.md`), a potem `skillsync feedback send F<n>`. Zgoda ze strony nie budzi sesji. Polecenie człowieka „wyślij F<n>” jest poleceniem, nie zgodą: bez ważnego wpisu `send` odmawia.
5. `skillsync feedback send F<n>` wysyła tylko, gdy: cel jest `trusted`, skan PASS, istnieje podgląd tej treści, wpis zgody ma `draft` i pełne sha identyczne z bieżącym,
   jest późniejszy niż podgląd, młodszy niż 24 h, nie z przyszłości, i niezużyty. Zgoda jest zużywana identyfikatorem w `F<n>.sending.json` / `F<n>.sent.json`;
   ponowna wysyłka wymaga `--replace` i NOWEJ zgody. Limit: 5 wysyłek na 24 h w głównym checkoucie. `send` odmawia w worktree i w sesji karty (`SKILLSYNC_CARD_ALIAS`).
6. Wysyłka: blokada `F<n>.sending.lock`, intencja `F<n>.sending.json` (sha, tryb, element, `uncertain`), zapis przez strzeżone `delivery.tracker` z odczytem kontrolnym po
   tagu `fb-<sha12>` (komentarz: znacznik `[fb-<sha12>]`), wynik `F<n>.sent.json` (id, adres, czas, `retro`). Po przerwaniu `send` wznawia z trybem i elementem
   z intencji; gdy zapis mógł się udać, a nie ma śladu (`uncertain`), zatrzymuje się dla człowieka zamiast pisać drugi raz. Czysty błąd (`az` zakończył się błędem, nic nie zapisano: `uncertain: false`) można ponowić tą samą zgodą także w trybie komentarza; intencja bez znacznika `uncertain` (starsza wersja) liczy się jak niepewna.

## Przycisk na stronie statusu (od 1.15.0)

Strona statusu (`skillsync dashboard serve`) pokazuje szkice w sekcji „Zgłoszenia do warstw”, najnowsze pierwsze (do 10, reszta w zwiniętej liście).
Kafelek to dokładnie to, co poleci do trackera: `sha12`, cel, tryb, tytuł, tagi, treść, wynik skanu z poziomem pokrycia. Przycisk
„Daj zgodę na wysyłkę” zapisuje zgodę `source: dashboard` dla tej treści. Nie wysyła nic: wysyłkę wykonuje `skillsync feedback send F<n>`
w sesji głównej, w głównym checkoucie.

- **Migawka podglądu.** `F<n>.preview.json` ma pole `view` (zapisuje je `draft`): cel, tryb, cytat tytułu elementu (oczyszczony, do 80 znaków),
  tytuł, tagi, treść (`body` dokładnie tak, jak idzie do trackera), skan i pokrycie, ostrzeżenie. `view` powstaje tylko przy skanie PASS
  i gdy plik mieści się w 128 KiB; większy podgląd zapisuje się bez `view`. `view` służy tylko do pokazania, nigdy do wysyłki.
- **Rysowanie bez sieci.** Stan strony liczy się z plików lokalnych: strona nie woła trackera ani `az`, a podgląd nie zależy od sieci.
  Deduplikacja (tryb, element) jest zamrożona w podglądzie; `send` i tak przelicza ją od nowa.
- **Stany szkicu** (kolejność sprawdzania): `sent` (wysłany albo zgoda zużyta), `stale` (brak podglądu albo `view`, podgląd starszy niż 24 h,
  cel niezaufany, szkic albo cel zmieniony po podglądzie: sha przeliczony lokalnie; `view` różny od przeliczonego tytułu, treści, tagów i celu), `blocked` (skan nie PASS albo limit 5 wysyłek na 24 h),
  `after` (ważna, niezużyta zgoda dla bieżącego sha: pokazany czas i źródło), `waiting` (przycisk aktywny). `stale` i `blocked` nie mają przycisku;
  `stale` pokazuje polecenie `skillsync feedback draft F<n>`, `after` polecenie `send`.
- **Pokrycie `generic`.** Nazwy klientów nie są sprawdzane: okno zgody pokazuje ostrzeżenie i wymaga zaznaczenia potwierdzenia.
- **Okno zgody.** Przycisk otwiera okno z `sha12`, celem i trybem: „Wysłać po zgodzie: <sha12> do <organizacja>/<projekt> (tryb)”. Treść z szkicu
  i trackera jest zwykłym tekstem (nie HTML).
- **Rola.** Treść, cel i przycisk widzi tylko rola z prawem zgód (gniazdo zgód albo `approvals.here`). Rola tylko do odczytu dostaje
  wyłącznie `{draft, state}`: bez sha, treści i celu. Kontrakt JSON: `docs/dashboard-contract.md`, sekcja `feedback`.
- **Zapis zgody.** `POST /api/feedback-approve` z `{draft, sha}` (pełny sha z kafelka) idzie tymi samymi drogami i zabezpieczeniami co `POST /api/approve`
  (`docs/domain/dashboard-server.md`). Serwer porównuje sha z żądania z podglądem i z przeliczeniem szkicu; różnica albo zmiana szkicu, celu
  lub skanu to odmowa, bez zapisu. Zapis idzie przez tę samą funkcję co `feedback approve` (`feedback.approve_from_preview`) pod blokadą zapisu zgód.
- **Idempotencja.** Gdy ważna, niezużyta zgoda dla tego szkicu i sha już istnieje (także po równoległych kliknięciach), serwer odpowiada 200
  z `already: true` i nie dopisuje drugiego wiersza. Kody odmów: `card`, `no_preview`, `content_changed`, `stale`, `sent`, `scan`.
- **Ślad.** Log serwera zapisuje tylko szkic, `sha12` i `via` (port, Host, adres połączenia). Wpis zgody nie ma pola `by`.
- **Zgoda a sesja.** Zgoda ze strony nie uruchamia wysyłki. Sesja czeka przez `skillsync feedback wait F<n>` (zob. niżej) i sama woła `send`.

### `skillsync feedback wait`

`skillsync feedback wait F<n> [--timeout S] [--interval S]` (domyślnie 1800 s, co 5 s) czeka na ważną zgodę tego szkicu i sprawdza ją tym samym
kryterium co `send` (sha, pełny, niezużyty, po podglądzie, nie starszy niż 24 h). Nic nie zapisuje. Wynik: 0 = zgoda (`{approval, sha12, source}`),
3 = `NOT_RUN` po czasie, 2 = błąd użycia (zły identyfikator, nieznany szkic, `stale` albo już wysłany). `stale` dla `wait` znaczy zmieniony szkic lub cel (sha); brak `view` i podgląd starszy niż 24 h nie blokują, bo `send` ich nie wymaga. `wait` nie zastępuje `send`. `wait` zwraca PASS także dla stanu `blocked` (limit wysyłek lub skan), bo sprawdza tylko zgodę; `send` wtedy odmówi.

## Sha treści

`sha` = sha256 kanonicznego JSON (`organization`, `project`, `area`, `type`, `mode`, `item`, `title`, `body`, `tags` bez `fb-`). Tag `fb-<sha12>` i metadane są dopisywane
PO obliczeniu (brak zależności cyklicznej). Zmiana szkicu, celu, trybu, elementu deduplikacji albo jego stanu zmienia sha i unieważnia zgodę.

## Granice (czego mechanizm nie gwarantuje)

- Granicą zaufania jest maszyna (jak przy `CP`): proces z dostępem do plików mógłby zapisać wpis zgody, a bezpośrednie `az` agenta nie jest zatrzymane; reguła
  strażnika poleceń (`guard-commands`: `azure-feedback` w poleceniu `skillsync tracker` albo `az devops/boards`) to dopasowanie tekstu, czyli heurystyka.
- Pola wyniku `az` sprawdzone na żywo (23.11, projekt Basic): `boards query` (nic nie drukuje przy braku wyników, stąd `on_empty`; `System.StateCategory` nie jest polem WIQL), tworzenie elementu (`devops invoke ... workItems`, `type=Task` bez `$`), odczyt kontrolny po tagu i ponowna wysyłka (BLOCKED „already sent”). Wersja API komentarzy to `7.1-preview` (`az devops invoke` odrzuca postać `X.Y-preview.N` błędem „could not convert string to float”, a serwer odrzuca `7.1`); komentarz (`workitem-comment`, kontrakt `[id, text]`) i odczyt ostatniego komentarza (`workitem-last-comment`, `$top=1` z `order=desc`) też działają na żywo (element #696, 23.11). Wszystkie pola `az` używane przez dostawcę są zweryfikowane na żywo 23.11. Zapytanie WIQL widzi nowy element z opóźnieniem indeksu: odczyt kontrolny może dać BLOCKED z `mutation_completed`, a ponowny `send` znajdzie element po tagu.
- Skan wycieków sprawdza znane kształty sekretów i tożsamości z plików tożsamości warstw; podgląd człowieka pozostaje główną kontrolą.
- Przycisk na stronie statusu (od 1.15.0) zapisuje zgodę, ale niczego nie wysyła. Zgoda z przycisku obowiązuje na tej samej zasadzie co słowo (granica zaufania ta sama: maszyna).
- CLI nie ma jak sprawdzić, kto wpisał słowo zgody: agent, który odczyta `sha12` z wyniku `draft`, może sam wywołać `approve`. To świadoma decyzja (zgoda w modelu `CP`):
  chroni przed pomyłką i przed wiadomością z innej sesji, nie przed agentem, który łamie procedurę (`workflow/references/feedback.md`).
- Odmowa `send` i `approve` w sesji karty działa, gdy w środowisku polecenia jest zmienna `SKILLSYNC_CARD_ALIAS` albo w katalogu projektu leży znacznik `.skillsync/card-alias`
  (od 1.15.0; karta zapisuje alias zgodnie ze skillem `dispatch` i usuwa znacznik po raporcie; obecność pliku wystarcza, także pustego albo dowiązania). Znacznik jest lokalny (poza gitem), więc
  sesja główna w innym katalogu nie jest dotknięta, a w tym samym katalogu odmówi, dopóki znacznik nie zostanie usunięty. To nie jest granica bezpieczeństwa (proces z dostępem do plików może
  usunąć znacznik): chroni przed pomyłką karty; reguła proceduralna karty (STATUS zamiast wysyłki) zostaje.
- Przycisk na stronie liczy zmienną ze środowiska procesu SERWERA strony i znacznik w katalogu projektu serwera, nie w katalogu karty. Serwer z katalogu głównego i karta
  w worktree: znacznik worktree nie jest widoczny dla serwera, a kliknięcie użytkownika przechodzi (klik to czynność użytkownika, fałszywy POST wymaga tokenu i nagłówków; granicą zaufania
  jest maszyna). Odwrotnie: serwer uruchomiony ze zmienną karty albo z zostawionym znacznikiem odpowiada na każde kliknięcie 403 `card`; tekst odpowiedzi podaje wyjście
  (usuń `.skillsync/card-alias` w katalogu projektu serwera albo uruchom serwer bez `SKILLSYNC_CARD_ALIAS`). Odmowa karty w `feedback approve` ma pierwszeństwo przed oceną słowa zgody.
- Limity: `approvals.jsonl` ma 256 KiB (około 1100 wpisów; po przekroczeniu `approve` i `send` odmawiają: zarchiwizuj plik ręcznie po sprawdzeniu, że nie ma wysyłek w toku);
  czasy zgody i podglądu mają rozdzielczość mikrosekundy (zgoda musi być nie wcześniejsza niż podgląd); deskryptor dostawcy czytany z warstwy core, która jest dowiązaniem, jest
  odrzucany (komunikat „Missing or oversized provider descriptor”).
- Odstępstwa od brzmienia speca: zła wartość `feedback.channel` to błąd `doctor` (nie ostrzeżenie); odczyt kontrolny po utworzeniu to osobna operacja `workitem-find-id`;
  zapytania o otwarte elementy obejmują kategorię stanu `Resolved`; wpis zgody nie ma pola `by`; podgląd i skan odrzucają tekst, który `redact` statusu zamaskowałby
  (podgląd musi być tym, co idzie w świat).

## Pliki i testy

`skillsync_workflow/feedback.py`, `skillsync_workflow/delivery.py` (`INTERNAL_PROVIDERS`, `tracker(internal=)`), `skillsync_workflow/layers.py` (`parse_feedback`,
`parse_feedback_targets`), `library/workflow/trackers/azure-feedback.yaml`, `library/hooks/guard-commands.py`; przycisk: `skillsync_workflow/feedback.py` (`approve_from_preview`, `drafts_state`, `read_form`, `cmd_wait`), `skillsync_workflow/dashboard_http.py` (`feedback_approve`, trasa `POST /api/feedback-approve`), `skillsync_workflow/dashboard.py` (klucz `feedback`), `library/skills/dashboard/assets/app.js` i `dashboard-render.js` (ekran, okno zgody), `skillsync_workflow/data/i18n/{pl,en}.json` (`srv.feedback.*`); testy `tests/test_skillsync_feedback*.py` (atrapa `az`, bez sieci) i `tests/test_skillsync_dashboard*.py`.
