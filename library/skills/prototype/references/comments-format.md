# Format komentarzy prototypu (v2)

Źródło kontraktu dla CLI i serwera (`skillsync prototype …`). Opis wynika z `assets/repo/prototype.js`
(silnik v2) i z modułu `skillsync_workflow/prototype_server.py` w Skillsync; przy rozbieżności wygrywa kod,
a ten plik trzeba poprawić. Fold w Pythonie jest portem 1:1 foldu JS (test różnicowy w repo Skillsync).
Czyste funkcje silnika (bez DOM) są w `module.exports`: `foldDocument`, `foldOperations`, `normalizeOperations`,
`sortOperations`, `mergeOperations`, `computeLabels`, `peopleFromOperations`, `canonicalOperation`,
`serializeCommentsDocument`, `normalizeCommentsDocument`, `convertV1Operations`, funkcje iteracji (`iterationList`,
`iterationForThread`, `restoreAvailability`, `restoreSubmitState`, `classifyRestoreResult`, `restoreEffects`) i inne, więc testy uruchamiają je w Node.
Prototypy v1 (`comments.js`) opisuje na końcu sekcja „Format v1”.

## Plik comments.json

```text
{"format":"skillsync-prototype-comments","version":2,"prototypeId":"<slug>","operations":[
{"id":"op-…","type":"create","threadId":"th-…","at":"2026-11-05T09:12:03.120Z","by":"jan.kowalski@example.com","cid":"c-…","payload":{…}},
{"id":"op-…","type":"vote","threadId":"th-…","at":"…","by":"…","payload":{"messageId":"m-…","value":"up"}}
]}
```

- UTF-8 bez BOM (przy odczycie BOM jest tolerowany), ścisły JSON bez `NaN`/`Infinity`. Linia 1 to zwarty nagłówek, potem
  jedna operacja w linii (klucze `id`, `type`, `threadId`, `at`, `by`, `cid`, `payload`, kompaktowo), `,` na końcu każdej linii poza
  ostatnią, `]}` i znak nowej linii. Pusty dziennik: nagłówek i `]}`. Szablon `assets/repo/comments.json` ma `"prototypeId":"{{SLUG}}"`.
- Dziennik **tylko rośnie**: serwer i CLI dopisują pod blokadą (proces i plik `comments.json.lock`, limit oczekiwania 5 s), zapis jest atomowy
  (plik tymczasowy, `fsync`, `os.replace`). Pliki `comments.json.lock` i `*.tmp` są w `.gitignore` prototypu, a `.gitattributes`
  wskazuje sterownik scalania `skillsync-comments` (rejestracja w gicie jest opcjonalna; bez niej git pokazuje zwykły konflikt, który rozwiązuje ręczne `merge-comments` na obu wersjach pliku).
- `id` operacji: `op-` + 26 znaków Crockford base32 (10 czasu + 16 licznika i losowości), nadaje serwer pod blokadą, rosnące. `threadId`: `th-…`, wpis: `m-…`
  (nadaje klient przy `create`/`reply`); identyfikatory z v1 (`<prefiks>-<base36>-<losowe>`, `[A-Za-z0-9_-]{1,80}`) pozostają poprawne.
- `at`: czas serwera UTC `YYYY-MM-DDTHH:MM:SS.mmmZ`, `max(teraz, ostatnie + 1 ms)` (monotoniczny nawet po cofnięciu zegara); porównywany jako napis.
- `by`: login z sesji (adres e-mail), `agent` (CLI), `import` (migracja) albo `server` (odtwarzanie przy starcie `serve`). `agent`, `import` i `server` są zarezerwowanymi loginami. Klient nie wysyła `by`. `by: agent` oznacza autora-AI (np. `reply` z `skillsync prototype reply`): UI pokazuje przy wpisie i na karcie wątku plakietkę „AI”, a `prototype wait` takich wpisów nie traktuje jak komentarza osoby. Nazwy agenta format nie przechowuje (stare kopie silnika czytają dziennik bez zmian).
- `cid`: identyfikator klienta do deduplikacji (`by`, `cid`); wymagany w żądaniach z przeglądarki.
- Limity: tekst wpisu ≤ 4000 jednostek UTF-16, ciało żądania ≤ 64 KiB, plik ≥ 10 MiB daje ostrzeżenie w `list`, ≥ 20 MiB serwer odmawia zapisu (507).
- Dokument z innym `format`, `version` lub niepustym obcym `prototypeId` jest odrzucany przez silnik jako pusty, a przez serwer jako błąd pliku.

## Operacje

| `type` | `payload` | Kto |
|---|---|---|
| `create` | `message {id, text}`, `screen`, `anchor {kind: element\|screen\|lost, protoId?, x, y, iteration}`, `label?`, `hint {text, legacyPath?}?`, `view?` (`"desktop"\|"mobile"`; silnik zapisuje je tylko dla mobile) | każda rola (`lost` tylko migracja) |
| `reply` | `message {id, text}` | każda rola; także CLI agenta (`prototype reply`, `by: agent`) |
| `edit` | `messageId`, `text` | autor wpisu |
| `retract` | `messageId` | autor wpisu |
| `vote` | `messageId`, `value: up\|down\|none` | każda rola |
| `resolve` | `reason: done\|kept\|out_of_scope\|author`, `iteration?` | autor wątku, programista (z przeglądarki tylko `author`) |
| `reopen` | `{}` | autor wątku, programista |
| `reanchor` | `screen`, `anchor`, `label` | autor wątku, programista |
| `delete` | `{}` | programista |
| `triage` | `class: clear\|disputed\|out_of_scope`, `note?` | tylko CLI agenta (`triage`); przeglądarka 403 `cli_only` |
| `decision` | `kind: agent\|developer`, `who?` (login programisty przy `developer`), `outcome: apply\|keep\|variant\|defer\|out_of_scope`, `text`, `iteration?` | tylko CLI agenta (`decide`) |
| `iteration` | `number` (zamrożona iteracja), `path` (`iterations/N/`), `hash` (SHA-256 z `assets_hash`, `head_hash` i hashy ekranów kopii), `commit?` (sha commita przed iteracją) | CLI `iterate` i `restore`; serwer tylko w parze z `restore` |
| `restore` | `screen`, `from` (iteracja źródłowa), `frozen` (iteracja zamrożona przed podmianą), `reason` (1–500 znaków), `who?` (przy CLI), `hashBefore`, `hashAfter` (hash ekranu) | endpoint `restore` (akceptujący, programista) albo CLI `restore --who` |
| `accept` | `scope` (`screen`, `all`, `project`), `screens {sN: hash}`, `iteration`, `via` (`ui` z przeglądarki, `cli` z CLI), `who?` (tylko przy `via: cli`) | przeglądarka: rola `acceptor`/`developer`, `scope: screen` z jednym ekranem; CLI `accept` (hasło z TTY); zakres projektu: `POST …/project/accept` |
| `unaccept` | `screens [sN]` (co najmniej jeden) | przeglądarka: `acceptor` tylko własne (`who` równe loginowi), `developer` dowolne; CLI tego nie robi |

Schemat jest ścisły: nieznane pola w `payload` to 400, a `view` przyjmuje wyłącznie napis `"desktop"` albo `"mobile"`; sesja (`GET /_api/v1/session`) zgłasza obsługę pola w `server.features` (lista, zawiera `"views"`), a silnik bez tej flagi nie tworzy komentarzy w widoku mobile; `anchor` `element`/`screen` wymaga `x`, `y`, `iteration`; `lost` pochodzi tylko z pliku/migracji.
`anchor` `element` wskazuje `data-proto-id` najbliższego przodka klikniętego elementu; `screen` to pozycja względna w ramce ekranu.

## Algorytm składania (fold)

Realizuje go `foldDocument` (JS) i `fold_operations` (Python), identycznie. Stan nie zależy od kolejności w pliku, tylko od `(at, id)`.

1. **Normalizacja**: odrzuć operacje bez niepustych napisów `id`, `at` (i `threadId` dla typów wątkowych) lub z nieznanym `type`; przy powtórzonym `id` wygrywa pierwsza poprawna.
   `by` brak/niepoprawne = `null`. `payload` nie-obiekt (także tablica) = `{}`.
2. **Sortowanie** po `(at, id)` w jednostkach UTF-16 jak operator `<` w JS.
3. **Powstanie wątku**: z pierwszego `create` z prawdziwym (w sensie JS) `payload.message` i napisem `payload.screen`. **Numer** wątku = pozycja takiego `create` wśród wszystkich wątków
   (także usuniętych i ukrytych: luka). `delete` usuwa wątek w całości (tombstone); `retract` wpisu otwierającego ukrywa wątek, jeśli w chwili tej operacji nie ma niewycofanej odpowiedzi innej osoby.
   **Widok** wątku (`view`): `"mobile"` tylko wtedy, gdy `create`, z którego powstaje wątek, ma `payload.view === "mobile"`;
   w każdym innym przypadku (brak pola, `"desktop"`, inny typ lub wartość) `"desktop"`; późniejsze operacje go nie zmieniają. Odczyt pliku jest tolerancyjny: `view` o niepoprawnej wartości w pliku daje `desktop`. `merge-comments` waliduje operacje ściśle, więc operacja scalana z niepoprawnym `view` daje konflikt.
4. **Wpisy**: `reply` dopisuje wpis (powtórzone `message.id` pomijane); `edit` i `retract` tylko autora, `edit` i `vote` na wycofanym wpisie pomijane; `vote`: `up`/`down` ustawia, `none` usuwa głos.
5. **Status**: `open` (start), `awaiting`, `decided`, `closed`, `deferred`. `resolve` zamyka zawsze (`statusReason` = `reason` lub `null`), `reopen` otwiera; `triage disputed` → `awaiting`;
   `decision` (tylko CLI): `keep`/`out_of_scope` → `closed`, `apply`/`variant` → `decided`, `defer` → `deferred`. `reanchor` zmienia `screen`, `anchor`, `label`.
6. **Iteracja bieżąca** (`currentIteration`): `iteration.number` ≥ 1 → `max + 1`. `restore` nie zmienia licznika ani statusów
   wątków: zawsze idzie w parze z operacją `iteration` zamrożenia (`frozen`) zapisaną w tym samym zapisie, a o pinezce decyduje
   obecność `protoId` w bieżącym `index.html`.
7. **Otwarte** (do liczników): `open`, `awaiting`, `decided`. **Wątek bez uchwytu** (`lost`): kotwica `lost`, kotwica `element` o `protoId` nieobecnym w bieżącym HTML ekranu albo wątek, którego ekranu (`screen`) nie ma w bieżącej wersji; kotwica `screen` z istniejącym ekranem zawsze ma uchwyt.
8. **Akceptacja**: `accept` ustawia `acceptance[sN] = {hash, by, who, at, iteration, via, scope, opId}` dla każdego ekranu z `screens`; `unaccept` usuwa wpisy wymienionych ekranów. Pomijane są: `accept` z `screens` niebędącym obiektem, `unaccept` z `screens` niebędącym listą i wartości ekranów niebędące napisami. `who` = `payload.who` tylko przy `via: "cli"`, inaczej `by`. Fold nie liczy aktualności: `valid` (hash równy bieżącemu hashowi ekranu) liczy serwer.

**Etykiety osób** (`computeLabels`, `label` w API): tylko loginy z `by`; imię i nazwisko z `name` w pliku użytkowników albo z adresu (część przed `@`, kropki = wyrazy, pierwszy punkt kodowy wielką literą);
unikalne imię → samo imię; kolizja imion → `Imię X.` (inicjał nazwiska); powtórzona kolizja → pełne imię i nazwisko. Porównanie imion jest dokładne (`Paweł` ≠ `Pawel`).

## Serializacja

`serializeCommentsDocument(prototypeId, ops)` (JS) i `serialize_document` (Python) dają **identyczne bajty**. Nie używaj `json.dumps`: Python serializuje przez `js_stringify`
(liczby jak `Number::toString`, samotne surogaty jako `\udxxx`, klucze-indeksy najpierw rosnąco), bo `json.dumps` i `JSON.stringify` różnią się dla `2.0`, wykładników, liczb > 2^53 i surogatów.

## Pamięć przeglądarki (localStorage v2)

| Klucz | Zawartość |
|---|---|
| `skillsync-prototype-queue:v2:<slug>:<login>` | kolejka offline: tablica `[{cid, type, threadId, payload}]`; tylko 8 typów kolejkowalnych; wpis najpierw trafia tu, potem do serwera; wpis bez `view` to desktop |
| `skillsync-prototype-rejected:v2:<slug>:<login>` | odrzucone przez serwer (4xx) do podglądu, kopiowania, pobrania i usunięcia |
| `skillsync-prototype-seen:v2:<slug>:<login\|anon>` | największe `at` po ostatnim odczycie (filtr „Nowe”); utrata danych = wszystko przeczytane |
| `skillsync-prototype-comments:v1:<slug>` | dane z silnika v1 (tylko odczyt); silnik v2 proponuje „Wyślij jako <imię>…” (dostępne po pierwszym udanym odczycie operacji; wpisy trafiają do kolejki v2, a klucz v1 jest usuwany bez dodatkowego pytania, bo treść jest już w kolejce) i „Pobierz plik” (potem „Usuń z przeglądarki”, bez okna potwierdzenia); bez jednej z tych akcji nic nie jest usuwane. `localStorage` jest per origin (schemat, host, port): wpisy v1 widać tylko pod dawnym adresem, np. `serve --port <dawny port>` na `127.0.0.1`; pobrany plik zachowaj albo prześlij programiście |
| `skillsync-prototype-theme` | `light` lub `dark` |
| `skillsync-prototype-shortcuts` | `on` lub `off` (przełącznik skrótów klawiszowych) |

Każdy dostęp do `localStorage` jest w `try/catch`. Odpytywanie `GET /_api/v1/prototypes/<slug>/operations?after=<n>&prefix=<odcisk>` co 5 s (30 s w tle, ×2 przy błędach); `reset: true` albo niezgodny odcisk
prefiksu wymusza pełne przeładowanie.

## Iteracje w dzienniku i w silniku

- `iterate` kopiuje bieżącą wersję do `iterations/N/` (bez `comments.json`, `iterations/` i plików blokad), w kopii ustawia
  `<html data-prototype-frozen data-prototype-iteration="N">` i dopisuje `iteration {number: N, path, hash}`; bieżąca iteracja
  rośnie do N+1. Kolejność zapisu `restore`: zamrożenie C (`iterations/C/`), jeden zapis `comments.json` z `iteration {number: C}`
  i `restore {…}`, potem podmiana sekcji `sN` w `index.html`. Dziennik nie jest edytowany wstecz: cofnięcie przywrócenia to kolejne
  `restore --from C`.
- `GET /_api/v1/prototypes/<slug>/operations` zwraca też `version` (SHA-256 hashy ekranów), `screens {sN: hash}` i
  `iterations: [{number, path, exists}]` (zamrożone iteracje; `exists` = `iterations/N/index.html` jest zwykłym plikiem). Silnik zapamiętuje `version`
  z pierwszej odpowiedzi; inna wartość później = pasek „Prototyp zmieniono, przeładuj” i wyłączone „Przywróć…”.
- Ta sama odpowiedź ma `acceptance` `{sN: {valid, who, at, iteration, via}}` (tylko ekrany z wpisem na bieżącej liście). Okno „Akceptuj…” i chip „Akceptacja k/n” czytają ją; zakres „wszystkie prototypy” idzie przez `GET /_api/v1/project` i `POST /_api/v1/project/accept` `{cid, versions}` (409 `partial` przy zmianie wersji lub blokadzie, a 507 `file_too_large`, 409 `invalid_log`/`needs_migration` i 500 `write_failed` z listą `accepted`/`pending`; przy `partial`, 507 i 500 ponów z tym samym `cid`). Przed zapisem serwer zwraca 409 `stale_view` z `{stale: [slug]}`, gdy wersja któregoś prototypu jest inna albo jej brak; ponowienie z tym samym `cid` nie pomaga, trzeba przeładować stronę i wysłać nowe `versions`.
- **Strona zamrożona** (`iterations/N/index.html`, kopia ze swoim `prototype.js`; iteracje zamrożone przed aktualizacją silnika,
  np. przed `migrate`, działają na starym silniku bez poniższych elementów): pasek „Iteracja N · zamrożona”, baner tylko do odczytu z „Wróć do bieżącej”;
  bez nowych wątków, przenoszenia kotwic i akceptacji; odpowiedzi i głosy w istniejących wątkach działają w trybie pełnym (ten sam
  dziennik, API ze `slug` z `data-prototype-id`); w trybie tylko do odczytu czyta `../../comments.json`. `#thread=<id>` otwiera wątek
  i podświetla pinezkę.
- **„Pokaż w iteracji N”** przy wątku bez uchwytu: N = najnowsza zamrożona iteracja z istniejącym katalogiem, której `index.html`
  ma `data-proto-id` wątku w ekranie wątku (kotwica `screen`: sam ekran); silnik pobiera `iterations/K/index.html` od najnowszej
  i trzyma wynik w pamięci na czas strony; brak takiej iteracji = brak przycisku. W trybie tylko do odczytu lista iteracji pochodzi
  z operacji `iteration`, a istnienie sprawdza żądanie `GET` `iterations/N/index.html` (status 200).
- **„Porównaj z iteracją N”** (nagłówek ekranu, także tylko do odczytu i bez logowania): okno z ramkami
  `iterations/N/index.html?embed=compare#sN` i `index.html?embed=compare#sN`. W trybie `?embed=compare` silnik pokazuje tylko ekran
  z hasha (bez paska, panelu, komentowania i odpytywania serwera); Esc w ramce zamyka okno.
- **„Przywróć…”** (tryb pełny, role `acceptor`/`developer`, `session.server.sourceWrites`): skutki z
  `GET …/restore-preview?screen=sN&from=N` (`threadsBack`, `threadsLost`, `acceptanceLost`, `unchanged`), potem
  `POST …/restore {cid, screen, from, reason, version}`; 409 `stale_view` = pasek „Prototyp zmieniono”, sukces = przeładowanie strony.
  `unchanged: true` w podglądzie (ekran w iteracji N ma ten sam hash co bieżący, D27) = komunikat „Ekran w iteracji N jest taki
  sam jak bieżący.” i nieaktywny przycisk zapisu (pole powodu zostaje aktywne; zmiana iteracji pobiera podgląd od nowa);
  200 `{unchanged: true, version}` na POST (wyścig) = ten sam komunikat, bez zapisu, przeładowania i błędu.
  Przywrócenie nie trafia do kolejki offline. Przy `serve --no-source-writes` endpointy `restore` zwracają 403 `source_writes_disabled`.

## Tryby silnika

`file://` (bez serwera: makieta, komunikat i komenda `serve`), serwer statyczny bez API (`GET /_api/v1/session` = 404/nie-JSON: tylko odczyt `comments.json`), pełny (API serwera: logowanie, zapis, kolejka).
Przy braku sesji silnik pokazuje „Zaloguj się, żeby komentować”; `mustChange` otwiera okno ustawienia własnego hasła.
Chip akceptacji i „Akceptuj…” nie pojawiają się w `file://`, na stronie zamrożonej iteracji ani w `?embed=compare`. Na zwykłym serwerze plików chip działa tylko do odczytu z opisem „aktualność sprawdza serwer”.

## Fikstury i kontrola

- `scripts/fixtures/v1/`: eksporty `comments.js` v1 (`export-comments{,-2}.js`) z oczekiwanym foldem `*.expected.json`. `export-comments-2.js` celowo zawiera surową, nieposortowaną tablicę z duplikatem `id` (pierwszy wygrywa).
- `scripts/fixtures/v2/`: `comments-v2.json` z `comments-v2.expected.json` (głosy, edycje, `retract`, `delete`, decyzje, `triage`, `reanchor`, remisy `at`, duplikaty `id`, trzy osoby „Pawel”).
  Wejście i plik `*.expected.json` zmieniaj razem; zgodność sprawdza `check_tokens.py` i test różnicowy w Skillsync.
- `scripts/check_tokens.py` sprawdza szablony v2 (układ `comments.json`, `.gitignore`, `.gitattributes`, brak `comments.js`, brak odwołania w `index.html`), fikstury v1 (fold v1) i niezależny fold v2 z etykietami w Pythonie
  dla `fixtures/v2`; rozjazd = kod 1. `--fixtures` wskazuje katalog z `v1/` i `v2/`, `--comments` szablon `comments.json`.

## Format v1 (`comments.js`, tylko do migracji)

```text
window.__SKILLSYNC_PROTOTYPE_COMMENTS__ = {"version":1,"prototypeId":"<slug>","operations":[…]};
```

Reguła C1-U1a: fragment od `window.__SKILLSYNC_PROTOTYPE_COMMENTS__ = ` do **ostatniego** `;` to ścisły JSON. Operacje v1: `create`, `reply`, `reanchor`, `resolve`, `reopen`, `delete` z `message {id, author, text, at}`
i kotwicą jako ścieżką elementu (`tag:n`, indeks 1-based, bez `:n` gdy jedyny). `skillsync prototype migrate` zachowuje `id`/`threadId`/`at`, ustawia `by: import` i `importedAuthor`, a ścieżkę zamienia na `data-proto-id`
tylko gdy jest jednoznaczna (inaczej kotwica `lost` z `hint.legacyPath`). `list` czyta `comments.js` do czasu migracji (`commentsFormat: 1`).
