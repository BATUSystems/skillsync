# Scenariusze E2E prototypu (stały zestaw)

Lista scenariuszy stałego zestawu E2E silnika prototypów (`tests/e2e/prototype/`). Każdy scenariusz opisuje zachowanie
z poziomu użytkownika; selektory wyłącznie ze słownika kontraktu (`slownik-html-css.md`), ról, `aria-*` i tekstów UI.
Wymagania źródłowe: spec W1 `docs/specs/2026-11-04-prototype-komentarze-wspolne.md` (kryteria W1-K i decyzje D).
ID z W1 zostają; `D23-…`, `DR-…` i `N-…` są nadane w zestawie E2E.
Kolumna „Krytyczny”: scenariusz chroni zachowanie, którego regresja blokuje przegląd albo akceptację.
Zachowanie wyroczni opisuje `README.md` w tym katalogu.

## Środowisko wspólne (dotyczy wszystkich wierszy, chyba że wiersz mówi inaczej)

- Dzierżawa: headless Chrome na porcie z `SKILLSYNC_E2E_PORTS` (domyślnie 9333) z własnym profilem i `skillsync prototype serve`
  na 4445 i 4446. Porty 9222 i 4444 są zakazane. Ciężkie biegi idą pod `systemd-run … MemoryMax=4G`.
- Konta syntetyczne, hasła z pliku sekretów środowiska, nigdy w logach:
  - anna, bartek: komentujący;
  - celina: akceptujący, z pełnym imieniem „Celina Akcept”;
  - dawid: programista;
  - ewa: komentujący z `mustChange`;
  - marta: akceptujący z `mustChange`.
- Prototypy testowe (tworzone przez `prototype init` z biblioteki):
  - `demo` (F1/F2): s1 z `data-proto-id` `s1-karta`, `s1-karta-tytul`, `s1-usuwany`, `s1-dalej`; s2 z `s2-form`,
    `s2-nazwa`, `s2-typ`, `s2-zapisz`;
  - `alfa`: s1, s2 do akceptacji i s3 z `data-prototype-example`;
  - `beta`: s1, s2;
  - `delta`: v1, bez `comments.json`;
  - `plan`: 28 ekranów (kopia planu zajęć, D23, F1-S23).
- Techniki CDP użyte w scenariuszach:
  - offline: `Network.emulateNetworkConditions`;
  - Fetch-mock: `Fetch.enable` na `*/_api/v1/prototypes/*/operations*`, `/_api/v1/project/accept` i `restore-preview`,
    wstrzymanie albo podmiana odpowiedzi;
  - wygaśnięcie sesji: `Network.deleteCookies` albo restart serwera (sesje są trzymane w pamięci);
  - motyw: `Emulation.setEmulatedMedia` (`prefers-color-scheme`, `prefers-reduced-motion`);
  - klawisze: zdarzenia z kodami vk.
- Pułapki sterownika CDP:
  - klawiaturę dostaje tylko karta na wierzchu, więc przed interakcją trzeba wywołać `Page.bringToFront`;
  - zmiana samego hash nie emituje `loadEventFired`;
  - pinezki są odtwarzane przy przewinięciu, więc węzeł trzeba wyszukać ponownie po `scrollIntoView`;
  - przewinięcie przy kliknięciu zmienia `.screen.is-current`, dlatego część kliknięć idzie przez DOM.
- E3-99/S99 liczy `Runtime.exceptionThrown` i `console.error`. Wpisy `log:network` z celowo wymuszonych 4xx, z trybu offline
  i z restartu serwera nie liczą się jako błąd.

## 1. F1: komentarze wspólne

| ID | Nazwa | Kroki użytkownika (UI) | Oczekiwany wynik | W1-K/D | Krytyczny | Uwagi środowiskowe |
|---|---|---|---|---|---|---|
| F1-S01 | Tryb `file://` | Otwórz `index.html` z dysku; otwórz panel „Wątki”; C, klik elementu | Chip „Bez serwera”; w panelu komenda `skillsync prototype serve`; C+klik nie otwiera okienka; 0 żądań HTTP; pusty `localStorage` | K21 | tak | bez serwera |
| F1-S02 | Tryb statyczny | Otwórz prototyp ze zwykłego serwera plików (bez API) | Chip „Tylko odczyt”, brak „Zaloguj”, sekcje widoczne, 0 wyjątków | K21, K33 | tak | `python3 -m http.server` na porcie 4445 po zatrzymaniu serve |
| F1-S03 | Logowanie w oknie silnika | Kliknij „Zaloguj”; okno „Zaloguj się, żeby komentować”; wpisz e-mail (`#anno-login-email`) i hasło (`#anno-login-password`); Enter | Chip „Anna · komentujący”, stan zapisu „Zapisano” (`.anno-save`) | K8, K20, K41 | tak | konto komentujący |
| F1-S04 | Komentarz do elementu | C (tryb komentowania); klik `[data-proto-id="s1-karta-tytul"]`; pisz; Ctrl+Enter; dodaj 3 kolejne wątki | Okienko `.anno-composer-pop` z nagłówkiem „Element „Karta podsumowania”” i id elementu, fokus w polu; pinezka „1” z klasą `anno-st-open`, aria „Wątek 1: Otwarty” | K17, K18, K22 | tak | — |
| F1-S05 | Odczyt bez logowania | Wejdź bez sesji; otwórz panel; C+klik elementu | Chip „Zaloguj się, żeby komentować”; 4 pinezki i wątki w panelu; C+klik otwiera okno logowania zamiast okienka | K8, K21, D16-Q7 | tak | anonim |
| F1-S06 | Odpowiedź innej osoby na żywo | B odpowiada w drugim kontekście; A patrzy na panel | Wpis B widoczny u A ≤ 6 s | K15 | tak | dwa konteksty przeglądarki; karta A `visible` |
| F1-S07 | Głosy | A: 👍 (`.anno-vote`); B: 👎; A zmienia 👍→👎, potem cofa | `aria-pressed`, liczby i imiona (`.anno-voters`) widoczne u drugiej osoby; po cofnięciu 0 i bez imion | K7 | tak | dwa konta |
| F1-S08 | Edycja i wycofanie wpisu | Autor: edytuj wpis; „Wycofaj” → okno „Wycofać wpis?” → potwierdź | „· edytowano”; zaślepka „Wpis wycofany przez autora.” widoczna też u B | K6, D16-Q6 | tak | — |
| F1-S09 | Filtry panelu | Przełączaj `.anno-filters button`: Otwarte, Nowe, Decyzje agenta, Bez uchwytu, Wszystkie | Liczniki 4/1/0/0/4 zgodne z liczbą kart; `aria-pressed` tylko na aktywnym | K19 | tak | „Nowe” = wpis A pod nieobecność B |
| F1-S10 | Wątek bez uchwytu | Usuń `s1-usuwany` z HTML; przeładuj; filtr „Bez uchwytu” | „Bez uchwytu 1”, wątek tylko w tej grupie, brak pinezki, Otwarte 3 | K18, K19, K22 | tak | edycja pliku na dysku |
| F1-S11 | Offline i kolejka | Przejdź offline; dodaj wpis; wróć online | Chip „Offline · 1 w kolejce”; baner z „Ponów teraz” i „Pobierz kolejkę”; klucz kolejki `skillsync-prototype-queue:v2:<proto>:<login>` ma 1 pozycję; po powrocie wpis wysłany (≈250 ms), kolejka pusta | K16 | tak | CDP offline |
| F1-S12 | Odrzucenie wpisu z kolejki | Offline: 2 wpisy; programista usuwa wątek 3 (API); online; otwórz „Odrzucone wpisy” | „Błąd zapisu (1)”; baner „Serwer odrzucił 1 wpis…”; okno „Odrzucone wpisy”; drugi wpis zapisany, odrzucony nie | K16 | tak | CDP offline + drugie konto |
| F1-S13 | Wygaśnięcie sesji po restarcie serwera | Wpis w kolejce; restart serwera; zaloguj ponownie w oknie | „Sesja wygasła”; okno „Zaloguj się ponownie” z wypełnionym e-mailem; kolejka czeka; po zalogowaniu wpis na serwerze | K11, K16, K20 | tak | restart procesu serve po PID |
| F1-S14 | Wymuszona zmiana hasła | Zaloguj kontem `mustChange`; C+klik; ustaw nowe hasło (pola „nowe” i „powtórz”) | Okno „Ustaw własne hasło”; chip „Ustaw własne hasło”; C+klik bez okienka z komunikatem „Najpierw ustaw własne hasło.”; po zmianie „Zapisano” | K42, D17 | tak | konto `mustChange` |
| F1-S15 | Usunięcie wątku przez programistę | Programista: „Usuń wątek” → okno „Usunąć wątek #4?” → potwierdź | Pinezka znika, numeracja innych bez zmian, `delete` w dzienniku | K14, K18, D16-Q6 | tak | konto programista |
| F1-S16 | Motywy i szerokości | „Motyw” jasny/ciemny (`data-theme`) × 1440/900/768/375; strona, panel, okienko | `scrollWidth == clientWidth` w 24 przypadkach; okienko w obrębie widoku | K17 (UI), strategia testów | tak | zmiana rozmiaru okna |
| F1-S17 | Esc i widoczny fokus | Esc w okienku, drugi Esc, Esc w panelu; Tab po pasku | Esc zamyka okienko (fokus wraca na element), drugi Esc wyłącza tryb, Esc zamyka panel (fokus na „Wątki”); fokus widoczny (obrys 2px) także na aktywnym segmencie i `.btn-outline` | K17 | tak | — |
| F1-S18 | Klawiatura na elementach | Tryb C; Tab na `div.card` (`s1-karta`) + Enter; Tab na `h3` + Spacja | Okienko dla właściwego `data-proto-id`; strona się nie przewija | K17 | tak | — |
| F1-S19 | Komentarz do ekranu | C; przycisk „Komentarz do ekranu” (`.anno-screen-comment`) na s2; wyślij | W dzienniku kotwica `kind: "screen"`, x/y 0–1, `iteration`, `screen: "s2"` | K18, K1 | tak | przycisk widoczny tylko w trybie C |
| F1-S20 | Dwie karty, restart serwera | Dwie karty A; restart serwera; zaloguj w karcie 1 | Wpis z kolejki karty 2 zapisany 1 raz, nie w „Odrzuconych” | K4, K16 | tak | wspólny kontekst, `Page.bringToFront` |
| F1-S21 | Dwie karty offline | Dwie karty A offline, po wpisie w każdej; online | „Offline · 2 w kolejce” w obu; po powrocie oba wpisy po 1 kopii w API i pliku | K4, K16 | tak | jak wyżej |
| F1-S22 | Wpisy v1 z `localStorage` | Wstaw klucz v1 (`skillsync-prototype-comments:v1:<slug>`, 2 wątki i odpowiedź); wejdź; „Wyślij jako…”; powtórz; „Pobierz plik” | Baner „Wpisy ze starej wersji”; „Wyślij jako” dopiero po wczytaniu operacji; wysyłka 2 create + 1 reply, klucz usunięty; ponowna wysyłka bez nowych operacji; „Pobierz plik” zostawia klucz i pokazuje „Usuń z przeglądarki” | K31 | tak | Fetch wstrzymuje GET operations |
| F1-S23 | Migracja kopii planu zajęć | `migrate --dry-run`, `migrate`, `ids --check`, `serve`; otwórz stronę | 804 `data-proto-id`, `commentsFormat: 2`; 28 `section.screen`; odczyt anonimowy działa; 0 błędów | K30, K32, K33 | nie (CLI) | kopia, nie oryginał; serve wymaga pliku użytkowników |
| F1-S99 | Konsola | Cały bieg | 0 wyjątków, 0 `console.error` | — | tak | `log:network` z offline, restartu, 401 i 404 dozwolony |

## 2. F2: iteracje, porównanie, przywrócenie

| ID | Nazwa | Kroki użytkownika (UI) | Oczekiwany wynik | W1-K/D | Krytyczny | Uwagi środowiskowe |
|---|---|---|---|---|---|---|
| F01 | Zamrożona iteracja | CLI `iterate`; otwórz `iterations/1/`; C, klik `s2-nazwa` | `index.html` z `data-prototype-iteration` i `data-prototype-frozen`; `.anno-iteration` „Iteracja 1 · zamrożona”; baner „zamrożona · tylko do odczytu… Wróć do bieżącej”; brak okienka | K23 | tak | iteracja zamrożona na bieżącym silniku |
| F02 | „Pokaż w iteracji N” | Zmień `s2-typ` w bieżącej wersji; panel → filtr „Bez uchwytu” → karta → link | Link `a.anno-iter-link` „Pokaż w iteracji 1” → `iterations/1/index.html#thread=<threadId>` | K23, K38 | tak | edycja fixture po zamrożeniu |
| F03 | Porównaj z iteracją | Programista albo anonim: „Porównaj z iteracją N” w `.screen-meta` / `.anno-screen-tools`; zmień iterację w `.anno-compare-pick select`; Esc | Okno z 2 ramkami `?embed=compare` (`.anno-compare-frame`) bez paska; zmiana wyboru przeładowuje ramkę; Esc zamyka i oddaje fokus na „Porównaj” | K39, D13 | tak | — |
| F04 | Przywróć ekran | Programista: „Przywróć…” w nagłówku s2; wybierz iterację (`#anno-restore-from`); powód (`#anno-restore-why`); podgląd skutków; zapisz | 201; w dzienniku `iteration` + `restore`; sekcja s2 = iteracja 1, reszta pliku bez zmian | K37, K39, D13, D16-Q8 | tak | serwer bez `--no-source-writes` (przy tej fladze przycisk celowo ukryty) |
| F05 | Brak „Przywróć…” dla komentującego | Zaloguj komentującego; obejrzyj nagłówek ekranu | Brak „Przywróć…”; API restore → 403 | K14, K39 | tak | — |
| F06-A | Przywrócenie przez świeżą kartę | Edycja s2 na dysku; świeża karta A: „Przywróć…” s2 z iteracji 1 | 201; s2 = iteracja 1; reszta bez zmian | K39, D13 | tak | — |
| F06-B | Stara karta po zmianie | Karta B otwarta przed zmianą; `bringToFront`; „Przywróć…” | Baner „Prototyp zmieniono, przeładuj”; przycisk `aria-disabled`; klik bez zapisu (409 `stale_view`), 0 operacji | K15, K39 | tak | dwie karty |
| F07 | Dry-run i odtworzenie restore | CLI `restore --dry-run`; przerwanie po zapisie dziennika; restart serwera | Dry-run bez zapisu; przy starcie „dokończono przywrócenie ekranu s2” | K37 | nie (CLI/serwer) | symulacja przerwania zapisem pliku |
| F08 | Sterownik scalania git | Dwie gałęzie z różnymi operacjami; `git merge` ze sterownikiem | 12 unikalnych operacji, 0 duplikatów | K29 | nie (CLI) | repo tymczasowe |
| D27-UI | Przywrócenie identycznego ekranu | „Przywróć…” z iteracją identyczną z bieżącą; potem wybierz inną | Komunikat „jest taki sam jak bieżący”, przycisk `disabled` + `aria-disabled`, 0 operacji; inna iteracja odblokowuje | D27 | tak | — |
| D27-API | Identyczny restore przez API | POST restore z treścią równą bieżącej | 200 `{unchanged: true, version}`, 0 operacji | D27 | nie (serwer) | — |
| F2-K38 | Pinezki po przywróceniu | Przywróć s2 z iteracji 2; panel | Wątki z przywróconymi `data-proto-id` mają pinezki; reszta w „Bez uchwytu” z „Pokaż w iteracji N” | K38 | tak | — |
| F2-migrate | Migracja silnika | CLI `migrate` dwa razy | `copy_engine` (js, css, screens.css) + `theme_lock_meta` (`color-scheme`, `darkreader-lock`); drugi raz `already_migrated`; `comments.json` bez zmian | K30, K33 | nie (CLI) | iteracje zamrożone wcześniej zostają na starym silniku |
| F2-przełącznik | Przełącznik ekranów na serwowanym prototypie | Enter na `.proto-screens-main`; w polu `.proto-screens-search` Home/End/←; przy pustym polu End/Home/→/↓; ‹ na pierwszym; Tab z listy na › na ostatnim; `prefers-reduced-motion` | Lista otwarta, `aria-expanded`; `aria-activedescendant` na polu wyszukiwania; w polu z tekstem klawisze ruszają kursor; ‹/› na krańcach `aria-disabled` (nie `disabled`); przy `reduce` przewinięcie natychmiast | D23 | tak | klawisze z kodami vk |
| E1-2 | Tab i Shift+Tab z listy ekranów | Otwórz listę; Tab; ponownie, Shift+Tab | Tab zamyka listę, fokus na „›”; Shift+Tab na `.proto-screens-main`, lista zamknięta | D23 | tak | — |
| E1-5 | Podpowiedzi i blokada „Przywróć…” | Bez powodu: Ctrl+Enter; zmiana s2 z zewnątrz → poczekaj 5 s → nagłówek | „Podaj powód przywrócenia.” (`#anno-restore-hint`, `aria-describedby`), brak zapisu; w nagłówku „Przywróć…” `aria-disabled` z powodem „Prototyp zmieniono od wczytania strony: przeładuj.”; klik i Enter nie otwierają okna | K39, D13 | tak | zmiana pliku poza s1 (s1 przykładowy jest poza hashem wersji) |
| E2-1 | Ponowienie podglądu skutków | Otwórz „Przywróć…” przy 503 na `restore-preview`; „Ponów podgląd” | „Nie udało się pobrać podglądu…”, zapis zablokowany; po ponowieniu lista skutków (`.anno-effects li`), przycisk „Ponów podgląd” ukryty | K39 | tak | Fetch-mock 503 |
| S1-1-UI | Wyścig dziennik ↔ `index.html` | — | — | K37 | nie | nie da się wywołać z przeglądarki; pokryte testami serwera |
| F2-S99 | Konsola | Cały bieg | 0 wyjątków, 0 `console.error` | — | tak | 409 przy F06 oczekiwany |

## 3. F3: akceptacja ekranów i bramka

| ID | Nazwa | Kroki użytkownika (UI) | Oczekiwany wynik | W1-K/D | Krytyczny | Uwagi środowiskowe |
|---|---|---|---|---|---|---|
| E3-01 | Chip i przycisk dla akceptującego | Zaloguj akceptującego; `alfa` | Chip „Akceptacja 0/2” (`.anno-accept`, `.anno-accept-count`); „Akceptuj…” (`.anno-accept-open`) przy ekranach | K20, K24 | tak | akceptujący |
| E3-02 | Komentujący bez akceptacji | Zaloguj komentującego | Chip jako `span role=note`; brak `.anno-accept-open` | K14, K24 | tak | komentujący |
| E3-03 | `file://` bez akceptacji | Otwórz `alfa/index.html` z dysku | Brak chipu i „Akceptuj…” | K21 | tak | bez serwera |
| E3-04 | Cel okna i ekran przykładowy | Przewiń na s3 (`data-prototype-example`); chip; `#s2 .anno-accept-open` po przewinięciu na dół | Brak „Akceptuj…” na s3; chip na s3 → „Ten ekran (s1 …)”, chip na s2 → s2; przycisk s2 → s2, nawet gdy `.is-current` = s3; Esc zamyka | K24, K26 | tak | klik przez DOM (klik z przewinięciem zmienia `.is-current`) |
| E3-05 | Akceptacja ekranu | „Akceptuj…” s1 → „Akceptuję”; to samo s2 | Chip 1/2, potem 2/2 (< 0,5 s) | K24 | tak | — |
| E3-06 | Cofnięcie własnej akceptacji | Chip → okno → „Cofnij akceptację” | Okno: „Zaakceptowano: <imię>, <kiedy>, iteracja N (UI)”; 2/2 → 1/2 | K24 | tak | — |
| E3-07 | Zmiana pliku po wczytaniu | Zmień tekst s2 na dysku; poczekaj | Pasek „Prototyp zmieniono, przeładuj.”; „Akceptuj…” s2 `aria-disabled="true"` z powodem „Prototyp zmieniono od wczytania strony: przeładuj…”; POST ze starym hashem i z wersją → 409 `stale_view` | K15, K24, K25 | tak | edycja pliku i przywrócenie z kopii |
| E3-08 | Akceptacja offline | Otwórz okno; offline; klik „Akceptuję”; Esc; online; otwórz ponownie; zaakceptuj | Offline: chip, „Akceptuj…” i „Akceptuję” `aria-disabled` z powodem „Akceptacja wymaga połączenia z serwerem i nie trafia do kolejki.”; 0 POST; po powrocie aktywne, także w ponownie otwartym oknie; akceptacja → 1/2 | K16, K24 | tak | CDP offline |
| E3-09 | Sesja wygasła i `mustChange` przy akceptacji | (a) usuń ciasteczko sesji; „Akceptuj…” → „Akceptuję”; (b) konto akceptujące z `mustChange` | (a) blokada dopiero przy zapisie: okno logowania „Sesja wygasła: zaloguj się ponownie…”; (b) formularz „Ustaw własne hasło”, „Akceptuj…” `aria-disabled` z powodem „Najpierw ustaw własne hasło.”; API accept 403 `password_change_required`; CLI `accept` BLOCKED (exit 2) przed pytaniem o hasło | K8, K42, spec „Tryby silnika” | tak | `Network.deleteCookies`; konto marta |
| E3-10 | Zakres „Wszystkie prototypy” | Okno akceptacji → `#anno-accept-project` → „Akceptuję” | `GET /_api/v1/project` dopiero po wyborze; lista z iteracją i liczbą ekranów, „pominięto 1”, `delta` „wymaga migracji”; POST `/project/accept` 201; toast „Zaakceptowano 2 prototypy; pominięto 1.” | K43, D16-Q1 | tak | prototyp v1 w projekcie |
| E3-11 | Nieaktualny prototyp w akceptacji projektu | (tylko API) `POST /project/accept` z wersją `beta` sprzed zmiany | 409 `{error: "stale_view", stale: ["beta"]}`, brak zapisu | K43 | nie (serwer) | kroku UI nie wykonano |
| E3-12 | Zapis częściowy i „Ponów” | Akceptacja projektu przy odpowiedzi 409 `partial`; „Ponów” | Ramka „Zapisano część akceptacji. Zaakceptowano: alfa. Czeka: beta.” z „Ponów”; ponowienie z tym samym `cid`; beta zaakceptowana | K4, K43 | tak | Fetch-mock 409 |
| E3-13 | Odmowa serwera w oknie | Akceptacja przy 403 `forbidden`, potem 400 `unknown_screen` | Okno zostaje otwarte; „Serwer odmówił akceptacji: brak uprawnień.” / „…ekranu nie ma w bieżącej wersji albo jest przykładowy.” | K14, K24 | tak | Fetch-mock |
| E3-14 | Klawiatura i fokus we wszystkich oknach | W każdym oknie: Tab z ostatniego, Shift+Tab z pierwszego, pełny obieg, Esc. Okna: akceptacja (przycisk s1/s2, chip), Przywróć…, Porównaj, Logowanie, Ustaw własne hasło (wymuszone), Usunąć wątek?, Wycofać wpis?, Odrzucone wpisy, Zaloguj się ponownie | Fokus krąży w oknie, nigdy na BODY; Esc zamyka i oddaje fokus otwierającemu (Zaloguj ponownie → `.anno-user`); okna wymuszonego hasła Esc nie zamyka; wyjść można przez „Wyloguj”; Tab i Shift+Tab nie trafiają na elementy `disabled`, `tabindex="-1"` ani ukryte, element `aria-disabled="true"` zostaje w obiegu, grupa radio to jeden przystanek (zaznaczona, a bez zaznaczenia pierwsza przy Tab; przy Shift+Tab bez zaznaczenia dowolna jedna pozycja, bo środek obiegu wstecz robi przeglądarka); w oknie akceptacji s1 test wstrzykuje przez DOM na początku i na końcu elementy `disabled`, ukryte, `aria-disabled="true"`, grupy radio (początek bez zaznaczenia, koniec z zaznaczoną drugą pozycją) i `tabindex="-1"` oraz sprawdza zawinięcie Tab i Shift+Tab | K17, K42 | tak | „Odrzucone wpisy” otwiera „Pokaż odrzucone”; „Zaloguj się ponownie” otwiera się sam po próbie zapisu |
| E3-15 | Imię przy akceptacji z CLI | Akceptacja przez CLI; otwórz okno w UI | „Zaakceptowano: Celina Akcept, …, iteracja 1 (CLI)”; imię z pliku użytkowników, nie z loginu | K24, K41 | tak | — |
| E3-16 | Ciemny motyw | `prefers-color-scheme: dark`; chip i okno akceptacji | Tło `body` i okna z tokenów ciemnych (`oklch(0.145 0 0)`, `oklch(0.205 0 0)`) | — (jakość UI) | tak | `setEmulatedMedia` |
| E3-17 | Akceptacja przez CLI widoczna w UI | CLI `accept beta --all`; otwórz `beta` | JSON PASS `via cli`; chip 2/2; okno „(CLI)” | K24, K26 | tak (część UI) | CLI przez pty |
| E3-18 | Bramka prototypu | `gate --plan` przed, po akceptacji, po zmianie s2; `readiness --files` | exit 1 FAIL `brak`; exit 0 PASS `via ui`; exit 1 `nieaktualna` s2; readiness BLOCKED (exit 2) | K26, K27 | nie (CLI) | bez historii git readiness wymaga `--files` |
| E3-19 | Narzędzia ekranu programisty | Programista na s1: `.anno-screen-tools`; „Porównaj”; otwórz `iterations/1/` | „Porównaj z iteracją 1”, „Akceptuj…”, „Przywróć…”; okno z 2 ramkami, URL bez zmian; iteracja bez chipu i „Akceptuj…”, z `data-prototype-frozen` | K23, K39 | tak | — |
| E3-20 | Chip na szerokościach | 1440/900/768/375 | Chip w granicach `.proto-toolbar`, brak nachodzenia, `scrollLeft` 0; na 375 tekst skrócony, `aria-label` pełny | K20 | tak | — |
| E3-21 | Zduplikowany id ekranu | Zmień `id="s1"` na `s2`; `gate` | FAIL `niejednoznaczny_ekran`; po przywróceniu PASS | K26 | nie (CLI) | — |
| E3-99 | Konsola | Wszystkie scenariusze F3 i zestawu końcowego | 0 wyjątków, 0 `console.error` | — | tak | `log:network` z Fetch-mock i offline dozwolony |

## 4. Zestaw końcowy (Z1–Z4, R3-02)

| ID | Nazwa | Kroki użytkownika (UI) | Oczekiwany wynik | W1-K/D | Krytyczny | Uwagi środowiskowe |
|---|---|---|---|---|---|---|
| R3-02a | Pełny obieg Tab w Porównaniu | Programista, s1: „Porównaj z iteracją 1”; Tab ×14; Shift+Tab z pierwszego; Esc | Kolejność: SELECT → „Przywróć…” → ramki → „Zamknij”; po „Zamknij” zawinięcie na SELECT; 0 kroków poza oknem, 0 BODY; Esc zamyka | K17, K39 | tak | każda ramka zajmuje 2 kroki Tab |
| R3-02b | Esc w trakcie zapisu akceptacji | „Akceptuję” przy wstrzymanym POST; Esc; puść POST | Okno zamknięte przed odpowiedzią; POST 201; chip k+1 (2/2); toast „Zaakceptowano ekran „…”.” i status „Zapisano” | K24 | tak | Fetch pause |
| Z1 | Szkic odpowiedzi i Esc | Otwórz wątek pinezką; pisz w `textarea[aria-label="Odpowiedź"]`; Esc; Esc; „Anuluj”; powtórz z „Porzuć szkic”; pusty szkic + Esc; × z niepustym szkicem → Anuluj / Porzuć; Tab w oknie | Esc 1: blur, fokus na `.anno-panel-title`, szkic zostaje; Esc 2: okno „Porzucić szkic?”; Anuluj: fokus wraca do pola; Porzuć (ścieżka Esc): powrót do listy, panel otwarty; pusty szkic: Esc zamyka panel bez okna; ×: okno; × + Porzuć zamyka panel; Tab krąży w oknie; Esc w widoku wątku bez szkicu (fokus poza polem) wraca do listy, panel zostaje otwarty | K17 | tak | — |
| Z1-b | Esc w widoku listy ze szkicem zostawionym w wątku | Otwórz wątek pinezką; wpisz szkic w `textarea[aria-label="Odpowiedź"]`; przejdź do listy („Wszystkie wątki”); fokus poza polem; Esc; „Porzuć szkic”; otwórz wątek ponownie | Esc w liście ze szkicem pyta „Porzucić szkic?” (`panelEscapeAction`: `close` z `confirm`); „Porzuć szkic” usuwa szkic, po ponownym otwarciu wątku pole jest puste (okno: „Zamknięcie go usunie.”) | K17 | tak | znany defekt, patrz sekcja 10 |
| Z2 | Fokus przy odpytywaniu z błędem | GET operations → 409 `invalid_log`; fokus na chipie konta (`[data-anno-focus="user-chip"]`) 12,5 s; fokus na „Spróbuj ponownie” w `.anno-banners` 12 s | Fokus zostaje na chipie i przycisku, węzły w DOM; ≥1 odpytanie w oknie; 0 mutacji banera | K15, K17 | tak | Fetch-mock 409 na GET; przerwa między odpytaniami rośnie |
| Z3 | Regresja końcowa | R3-02a, R3-02b, konsola; komentarz komentującego i przeładowanie; chip akceptacji w trybie odczytu | Jak R3-02a/b; po przeładowaniu wpis zapisany (3 pinezki, 8 operacji, 0 błędów konsoli); chip widoczny w trybie odczytu | K15, K24 | tak | część na kopii podglądowej |
| Z4 | Właściciel kolejki | A: odpowiedź przy wstrzymanym POST; menu konta (`.anno-user-menu`) → „Wyloguj”; zaloguj B (`[data-anno-focus="user-login"]`, `#anno-login-email`); POST A dostaje 409 | `skillsync-prototype-rejected:v2:<proto>:<B>` pusty; wpis A zostaje w kolejce A; nie trafia do kolejki B | K16 | tak | Fetch pause + fulfill 409; po wylogowaniu chip konta znika |

## 5. D23: przełącznik ekranów (kopia planu zajęć, `file://`, 28 ekranów)

| ID | Nazwa | Kroki użytkownika (UI) | Oczekiwany wynik | W1-K/D | Krytyczny | Uwagi środowiskowe |
|---|---|---|---|---|---|---|
| D23-ulepszenie | Ulepszenie `nav.screen-nav` | Wczytaj stronę | `nav` z klasą `proto-screens`; 28 linków w DOM, ukrytych; ‹ wyłączony na pierwszym; aria-label „Następny ekran: 2 …” | D23 | tak | bez zmian HTML prototypu |
| D23-układ-przewijanie | Brak poziomego przewijania | 1920/1280/1000/375, oba motywy | Dokument bez poziomego przewijania (`scrollWidth ≤ innerWidth`); pasek i jego narzędzia bez przewijania poziomego (od 1000 px); `--proto-toolbar-height` = wysokość paska; cele ≥ 36 px (≥ 44 px poniżej 600 px szerokości) | D23 | tak | — |
| D23-układ-wiersze | Jeden lub dwa wiersze paska | 1920 / 1280 / 1000 | Dla każdej szerokości: pasek i dokument bez poziomego przewijania; elementy paska (liście drzewa DOM) nie nachodzą na siebie; liczba wierszy paska dowolna | D23 | nie | — |
| D23-lista | Lista ekranów | Klik `.proto-screens-main` albo G | `aria-expanded=true`; fokus w `.proto-screens-search`; 28 pozycji w ≥ 3 kolumnach, w widoku na 1920 | D23 | tak | — |
| D23-szukaj | Wyszukiwanie | Wpisz „tydzien”, „1”, „zzz” | Dopasowanie bez polskich znaków (2 wyniki); numer prefiksowo (11); „Brak ekranu „zzz”.” | D23 | tak | — |
| D23-klawiatura-listy | Klawiatura listy | Strzałki, Home/End; Enter; Esc | Zaznaczenie się przesuwa; Enter przechodzi do wybranego ekranu (`.screen.is-current` to `#sN` z wybranej pozycji) i zamyka listę; Esc zamyka, fokus na `.proto-screens-main` | D23 | tak | — |
| D23-zamykanie | Zamknięcie listy | Klik poza listą; otwarcie panelu „Wątki” | Lista zamknięta | D23 | tak | — |
| D23-skróty | Skróty globalne | ←/→ i G na `body`; te same klawisze w polu tekstowym | Ekrany 1→2→1, G otwiera; w polu bez zmiany ekranu | D23 | tak | — |
| D23-scroll-spy | Śledzenie przewijania | Przewiń do s5 | Przełącznik „5 / 28”, `.screen.is-current` to `#s5`, hash bez zmian | D23 | tak | — |
| D23-ostatni | Skok na ostatni ekran | Wybierz ekran 28 | Zostaje „28 / 28” mimo śledzenia przewijania | D23 | tak | — |
| D23-fokus | Widoczny fokus | Tab po `[data-screen-step]`, `.proto-screens-main`, polu | Obrys 2px (`outline: solid 2px`) | D23 | tak | — |
| D23-375 | Telefon | 375 px, otwórz listę | `.proto-screens-pop` na całą szerokość widoku (lewa krawędź ≤ 1 px, szerokość ≥ szerokość widoku − 2) i brak poziomego przewijania dokumentu oraz paska | D23 | tak | znany defekt paska, patrz sekcja 10 |
| D23-motywy | Oba motywy | Jasny/ciemny × 4 szerokości | Brak czarnych pól w jasnym motywie; kontrast tła i tekstu większy niż próg skryptu | D23 | tak | — |
| D23-prezentacja | Tryb „Prezentacja” | Prezentacja; › | „2 / 28”, ekran s2 | D23 | tak | — |
| D23-strip | Wyłącznik | `nav.screen-nav[data-proto-nav="strip"]` | Brak `proto-screens`, stary pasek linków widoczny | D23 | tak | atrybut wstrzyknięty przed DOMContentLoaded |

## 6. DR: pozostałości Dark Readera

| ID | Nazwa | Kroki użytkownika (UI) | Oczekiwany wynik | W1-K/D | Krytyczny | Uwagi środowiskowe |
|---|---|---|---|---|---|---|
| DR-start | Fallback przy starcie | Wczytaj prototyp z `meta[name="darkreader-lock"]`; fallback `style.darkreader.darkreader--fallback` wstrzyknięty na `document_start` | Meta blokady obecna; 0 `style.darkreader`; obcy styl bez klasy `darkreader` zostaje; tło `body` jasne (luminancja > 0,8) | D25 | tak | symulacja przez `Page.addScriptToEvaluateOnNewDocument`, bez rozszerzenia |
| DR-późny | Fallback dopisany później | Po starcie dopisz `style.darkreader` do `<head>` | Usunięty; tło jasne | D25 | tak | jak wyżej |
| DR-kontrola | Kontrola sondy na stronie bez prototypu | Otwórz pustą stronę `data:` z tym samym wstrzykniętym stylem fallbacku | Pozostałość zostaje (`drLeft` ≥ 1, tło ciemne): sonda wykrywa wstrzyknięty węzeł | — | nie (kontrola) | — |
| DR-rozszerzenie | Prawdziwy Dark Reader | Chrome z rozszerzeniem; otwórz podgląd | Zachowanie jak w DR-start i DR-późny przy prawdziwym rozszerzeniu; skrypt zwraca NOT_RUN z powodem | D25 | tak | wymaga rozszerzenia i zgody właściciela (D25); patrz sekcja 10 |

## 7. Pozostałe ustalenia i znane defekty

Sekcje 8 i 9 opisują scenariusze uzupełniające i ustalenia kontraktu. Sekcja 10 zawiera listę znanych defektów.

## 8. Scenariusze uzupełniające (luki w pokryciu W1, obowiązkowe w zestawie)

| ID | Nazwa | Kroki | Oczekiwany wynik | W1-K/D | Krytyczny |
|---|---|---|---|---|---|
| N-01 | Nawigacja wątków `[` / `]` | panel „Wątki” otwarty, fokus poza polem tekstowym, `]` i `[` | aktywna karta przechodzi na następny/poprzedni wątek; w polu tekstowym klawisze wpisują znak | W1-K17, spec W1 „Klawisze” | tak |
| N-02 | `serve --login-to-view` | serwer z flagą; anonim otwiera prototyp i API | dokument HTML: 302 na `/_login`; API i pliki: 401; wyjątki `/_login`, `/_api/v1/login`, `/_api/v1/session` dostępne; po zalogowaniu treść widoczna | W1-K8 | tak |
| N-03 | Odpytywanie przy ukrytej karcie | dwie karty; w karcie B stan ukryty (emulacja widoczności); wpis z karty A | przy widocznej karcie wpis innej osoby w ≤ 6 s; przy ukrytej odstęp odpytywania ≈ 30 s (pomiar żądań `operations`) | W1-K15 | tak |
| N-04 | Ponawianie 5xx z narastającą przerwą | Fetch zwraca 503 dla `POST operations` zakolejkowanego wpisu kilka razy, potem przepuszcza | wpis zostaje w kolejce, kolejne próby w rosnących odstępach, po sukcesie znika; brak przeniesienia do „Odrzuconych” | W1-K16 | tak |
| N-05 | Kolory pinezek według statusu | wątki w statusach open, awaiting, decided, closed, deferred (przez CLI `triage`, `decide`, `resolve`) | każda pinezka ma klasę/stan statusu ze słownika i inny kolor z tokenów; wątki zamknięte i odłożone widoczne w filtrze „Wszystkie iteracje” | W1-K18, W1-K19 | tak |
| N-06 | Filtry „Decyzje agenta” i „Nowe od ostatniej wizyty” z licznikiem > 1 | kilka wątków z `triage`/`decision`; wpisy innej osoby po ostatniej wizycie | liczniki filtrów równe liczbie kart po włączeniu filtra | W1-K19 | tak |
| N-07 | Przełącznik ekranów na serwowanym prototypie 1280 i 1440 z zalogowanym chipem | zalogowany użytkownik, szerokości 1280 i 1440, zmiana szerokości w obie strony | pasek bez nachodzenia elementów i bez poziomego przewijania; po dwóch zmianach szerokości ten sam układ; wysokość paska zgodna z `--proto-toolbar-height`; chip konta (`[data-anno-focus="user-chip"]`) przy otwartym panelu Wątki nie jest zasłonięty (element w jego środku to chip albo jego potomek) | D23 | tak |
| N-08 | E3-11 w UI | okno akceptacji zakresu „Wszystkie prototypy”, w trakcie zmiana pliku innego prototypu | po „Akceptuję” 409 `stale_view` pokazany w oknie z listą nieaktualnych; brak zapisu | W1-K43 | tak |
| N-09 | Odrzucone wpisy przez baner | wpis odrzucony (Fetch 403/409) | baner „Błąd zapisu (n)” z przyciskiem otwierającym „Odrzucone wpisy”; ta sama lista co z menu konta | W1-K16 | tak |

## 9. Ustalenia kontraktu (zachowania doprecyzowane interpretacją)

- Wygaśnięcie sesji: stan „sesja wygasła” powstaje przy pierwszym 401 (odczyt publiczny); blokada akceptacji i okno logowania
  pojawiają się przy zapisie (spec W1 „Tryby silnika”, W1-K8).
- Szkic odpowiedzi: Esc w niepustym polu zdejmuje fokus; opuszczenie widoku wątku ze szkicem (Esc poza polem, „×”, „Wątki”) pyta
  „Porzucić szkic?”; potwierdzenie wykonuje akcję, która wywołała pytanie (Esc z widoku wątku → lista, „×” → zamknięcie panelu).
- Fokus po zamknięciu okna bez otwierającego elementu: element paska (nigdy `BODY`); scenariusz sprawdza „nie BODY i w pasku”,
  nie konkretną klasę.
- „Odrzucone wpisy”: dostępne z menu konta i z banera (N-09).
- Okno Porównania: pełny obieg Tab wraca na pierwszy element okna; liczba kroków nie jest kontraktem.
- Po akceptacji: komunikat o wyniku i aktualny stan zapisu; liczba komunikatów nie jest kontraktem.
- Utrata aktualności akceptacji po podmianie arkuszy silnika lub motywu jest zgodna z W1-K25 (CSS w `assets_hash`), nie defektem.

## 10. Czysta konsola i znane defekty

- Czysta konsola: 0 nieobsłużonych wyjątków i 0 `console.error` w całym przebiegu scenariusza; wpisy sieciowe przeglądarki
  (`Failed to load resource`, `ERR_INTERNET_DISCONNECTED`) dopuszczone tylko dla żądań celowo przerwanych lub odrzuconych w scenariuszu
  i wymienione w jego wyniku.
- Znane defekty (wpisy `KNOWN_DEFECTS` w `tests/test_skillsync_prototype_e2e.py`; zagnieżdżony punkt `- <ID>: wymagane zachowanie`): brak. V-23 (skok strony przy
  pierwszym dopasowaniu wysokości iframe widoku nad oglądanym ekranem, D249) naprawiony w NR1-V23 i wymagany bez wyjątku (≤ 2 px po każdej fazie).
- Skok strony przy ładowaniu iframe widoku (V-23, N-R1 mobile; D277): przyczyna to fragment adresu ramki `#sN`. Dokument ramki ma element o id `sN`, więc
  ładowanie ramki (także leniwe, np. gdy panel zmniejsza układ i ramka wchodzi w okno) przewijało również stronę nadrzędną (ramka tego samego
  pochodzenia): openShift = closeShift ≈ −3000…−3300 px w N-R1, skok o setki px w V-23. Fragment ma przedrostek (`?embed=view#view-sN`), a instancja
  osadzona przyjmuje też dawny zapis `#sN` (V-24). Iframe porównania iteracji (`?embed=compare#sN`) ładuje zamrożone starsze wydania i zostaje bez zmian.
  Odtwarzanie N-R1 lokalnie: obciążenie CPU (po jednym procesie na rdzeń) przy DejaVu Sans; przed poprawką DejaVu 6/6 i zwykły font 3/3 FAIL, po poprawce 10/10 PASS w obu fontach.
- Dokończenie przerwanego przywracania nie jest wywoływalne z UI (F07, tylko CLI).
- Prawdziwe rozszerzenie Dark Reader nie jest uruchamiane (wymaga zgody); zachowanie sprawdzane jest wstrzykniętym węzłem stylu (DR-start, DR-późny).

## 11. Narzędzie layout-probe (`tests/e2e/prototype/layout-probe.mjs`)

Kontrakt narzędzia jest opisany w `slownik-html-css.md` (sekcja „Narzędzie layout-probe”). Skrypt wstrzykuje `layout-probe.js` z
`SKILLSYNC_TEST_LIBRARY` do strony planu zajęć (28 ekranów, `file://`, 1920×1080) w Chrome ze środowiska E2E.

| ID | Nazwa | Kroki | Oczekiwany wynik | W1-K/D | Krytyczny |
|---|---|---|---|---|---|
| LP-01 | Pomiar domyślny: kształt wyniku | `layoutProbe()` bez opcji | `ok` boolean (true), `viewport{width,height,theme,panelOpen}`, `toolbar{height,bottom,share,cssVar,screens}` zgodne z DOM (`screens` = 28, `share` = wysokość paska / wysokość okna z zaokrągleniem do 0,001), `a1`/`a2` z `ok` true/false/null i `skipped` przy null, brak pól opcjonalnych, przewinięcie strony przywrócone | D23 | tak |
| LP-02 | Opcja `target` | `layoutProbe({target:'s3'})` | `target{ok,screen,h2Top,h2Bottom,toolbarBottom,ariaCurrent}`; `ok` = nagłówek pod paskiem i w oknie; strona przewinięta do ekranu | D23 | tak |
| LP-03 | Opcja `measureCurrent` | wejście z `#s2`, `layoutProbe({measureCurrent:true})` | `current.screen` = bieżący ekran z DOM, `ariaCurrent` = `location`, `ok` true, bez przewijania | D23 | tak |
| LP-04 | Opcje `focusRing`, `focusMargin` | `focusRing:true`; `focusRing:true, focusMargin:1000` | `focusRing{ok,margin:4,buttons[{ok,label,space{left,right,top,bottom}}]}` dla każdego widocznego przycisku paska; margines 1000 daje `ok:false` (także w `ok` wyniku) | D23 | tak |
| LP-05 | Opcja `compareSingle` | `layoutProbe({compareSingle:true})` | `compareSingle{ok,before,after,diff,note}`; `before.screens` = 28, `after.screens` = 1, `diff` = różnica wysokości, `ok` = równe wysokości | D23 | tak |
| LP-06 | Błędne wejście | strona bez `.doc-toolbar`; `target:'s999'`; `a1:false,a2:false` | `ok:false` z `error` (zawiera „doc-toolbar”); `target{ok:false,error}` z nazwą ekranu i `ok:false` w wyniku; wyłączone pomiary nie występują w wyniku | D23 | tak |

## 12. Panel komentarzy: zwężenie i skalowanie (`tests/e2e/prototype/nr.mjs`)

Zachowanie „zwężenie + skalowanie”: w oknie od 1024 px otwarty panel Wątki zabiera szerokość, a makieta pomniejsza się proporcjonalnie do zwężonego obszaru (układ bez zmian); poniżej 1024 px panel nakłada się na treść bez przeliczania. Pomiar tylko przez DOM (prostokąty elementów ze słownika, atrybuty); skala jest liczona z szerokości ramki ekranu (`.frame`) po i przed, bez zakładania techniki skalowania. Prototyp `plan` (28 ekranów) z serwera, konto zalogowane, szerokości 1920, 1440, 1280, 1024 (zwężenie) oraz 768 i 375 (nakładka), oba motywy, ekrany s1, s2, s7 i s28, po jednym wątku z pinezką na ekranie. Każdy przypadek obejmuje stan zamknięty, otwarty i ponownie zamknięty. To kryteria silnika, nie znane defekty: scenariusze mogą kończyć się FAIL na silniku bez tego zachowania.

Widok mobile: N-R1…N-R8 i N-R10 biegną także z mierzonymi ekranami w widoku mobile (przełącznik lokalny ekranu). Ramką ekranu jest wtedy prostokąt iframe widoku, a elementy, kotwice i pinezki pochodzą z dokumentu iframe, przeliczone na współrzędne strony; wątki tego wariantu mają widok mobile. N-R9 i N-S1 biegną tylko w widoku desktop: pasek nie zależy od widoku ekranu (pasek bez kontrolki widoku mierzy V-02).

| ID | Nazwa | Kroki | Oczekiwany wynik | W1-K/D | Krytyczny |
|---|---|---|---|---|---|
| N-R1 | Panel nie przesuwa oglądanego ekranu | otwórz i zamknij panel na s1, s2, s7, s28 (>= 1024) | górna krawędź ekranu w oknie zmienia się o <= 2 px po otwarciu i po zamknięciu, niezależnie od położenia ekranu w dokumencie | — | tak |
| N-R1b | Panel przy s28 przewiniętym do maksimum | 1920 i 1440, oba motywy; dokument przewinięty do końca; otwórz i zamknij panel | górna krawędź `#s28` w oknie zmienia się o <= 2 px po otwarciu i po zamknięciu (przypadek brzegowy N-R1: koniec dokumentu) | — | tak |
| N-R2 | Makieta zachowuje układ | porównaj elementy `data-proto-id` ekranu przed i po otwarciu (>= 1024) | wysokości po przeliczeniu do skali (wysokość po / skala) różnią się o <= 2 px; liczba wierszy i kolumn bez zmian; nie przybywa elementów wystających poza prawą krawędź ramki; brak nowego poziomego przewijania ramki | — | tak |
| N-R3 | Brak poziomego przewijania strony | szerokość dokumentu przy zamkniętym i otwartym panelu (>= 1024) | `scrollWidth` <= `clientWidth` w obu stanach | — | tak |
| N-R4 | Panel nie zakrywa kontrolek paska | kontrolki `.doc-toolbar` przy otwartym panelu (>= 1024) | żadna kontrolka (przełącznik ekranów, tryby, „Wątki”, motyw, konto) nie jest zakryta przez panel; wysokość paska zmienia się o <= 2 px; liczba wierszy kontrolek bez zmian | — | tak |
| N-R5 | Pinezki przy elementach | odległość pinezki od kotwicy przed i po (>= 1024) | w skali makiety zmienia się o <= 2 px na obu osiach; pinezka nie leży pod panelem, gdy jej element jest widoczny | — | tak |
| N-R6 | Płynne zwężenie na 1920 | próbki położenia ramki co klatkę w trakcie otwierania panelu | przesunięcie środka ramki nie przekracza połowy szerokości panelu (bez przeskoku z wyśrodkowania do lewej krawędzi); środek i szerokość zmieniają się monotonicznie, bez zawracania i migotania | — | tak |
| N-R7 | Nakładka poniżej 1024 px | otwórz panel przy 768 i 375 | pozycja i rozmiar ramki ekranu oraz szerokość dokumentu bez zmian; panel nachodzi na treść | — | tak |
| N-R8 | Nakładka: przełącznik i zamknięcie dostępne | kontrolki przy otwartym panelu (768, 375) | panel nie zakrywa przełącznika ekranów ani „Wątki”; przycisk zamknięcia panelu jest w oknie i klikalny bez przewijania; przy 375 kontrolki paska mieszczą się w oknie albo istnieje menu (`.doc-toolbar [aria-haspopup]`) | — | tak |
| N-S1 | Powłoka aplikacji wypełnia ramkę ekranu | prototyp z ekranem z powłoką (pasek boczny z nawigacją, okruszki, nagłówek, tabela albo formularz); 1440, 1024, 375, oba motywy, panel zamknięty | powłoka wypełnia szerokość ramki (±2 px), brak pustego pasa przed nią, nic nie wystaje poza ramkę; pasek boczny ma stałą szerokość, a główna część zajmuje resztę; nagłówek h1 ma najwyżej 2 linie; na 375 pasek boczny jest ukryty (`display` `none` albo prostokąt o zerowej szerokości, żaden potomek nie jest w kolejności Tab), a główna część wypełnia szerokość ramki (±2 px), bez poziomego przewijania strony | D193 | tak |
| N-R9 | Pasek prototypu na telefonie | 375 i 599 (menu), 600 i 768 (bez menu); oba motywy; zalogowany i niezalogowany; panel zamknięty | przy 375 i 599 wysokość `.doc-toolbar` ≤ 2 × wysokość najwyższej kontrolki paska + 32 px, każda widoczna kontrolka paska (`button`, `select`, `a`, `[role=button]`) ma prostokąt ≥ 24 × 24 px; „Pokaż hotspoty”, „Prezentacja”, „Ukryj pinezki”, „Motyw” nie są widoczne w pasku, jest przycisk `[aria-haspopup="menu"]` z nazwą „Więcej”; przy 600 i 768 te cztery przyciski są widoczne w pasku, a przycisk „Więcej” jest ukryty i poza kolejnością Tab; brak poziomego przewijania; `--proto-toolbar-height` równa wysokości paska (±1 px) | D194 | tak |
| N-R10 | Menu „⋯” z klawiatury i myszy | 375, oba motywy | fokus na „Więcej”, Enter → `aria-expanded="true"`, `role="menu"` widoczne w oknie, fokus na pierwszej pozycji; strzałka w dół i w górę przechodzą w kółko, Home/End; Esc zamyka menu i oddaje fokus na „Więcej”; Spacja otwiera ponownie; pozycja „Motyw” Enterem zmienia `data-theme` na `<html>` i zamyka menu; „Ukryj pinezki” ukrywa pinezki (i odwrotnie); „Prezentacja” włącza tryb prezentacji tak jak przycisk; klik poza menu zamyka je; zmiana szerokości na 768 przy otwartym menu zamyka je i pokazuje przyciski w pasku; menu konta (chip konta, zalogowany) otwarte przy 375 mieści się w oknie; Tab i Shift+Tab przy otwartym menu zamykają je; `aria-checked` pozycji przełączników zgodne ze stanem po użyciu z menu; przy otwartym menu `c` nie zmienia `aria-pressed` „Komentarze”, a strzałka w prawo nie zmienia bieżącego ekranu; konsola bez błędów; przycisk menu paska wybierany po nazwie „Więcej” | D194 | tak |

## 13. Ścieżki o podwyższonym ryzyku (`tests/e2e/prototype/ryzyka.mjs`)

Tryby pracy według specu W1: start bez sieci (błąd sieci na `GET /_api/v1/session` przy pierwszym wczytaniu) daje tryb offline bez wiedzy o użytkowniku, pisanie jest zablokowane do pierwszej udanej odpowiedzi, potem tryb wraca do pełnego bez przeładowania; w trybie pełnym offline oznacza błąd sieci albo przekroczony czas żądania. Parametr czasu żądania w R-02: `REQUEST_TIMEOUT_MS` = 20 000 ms (założenie: silnik uznaje żądanie za nieudane najpóźniej po tym czasie).

| ID | Nazwa | Kroki | Oczekiwany wynik | W1-K/D | Krytyczny |
|---|---|---|---|---|---|
| R-01 | Start bez sieci | zaloguj; blokuj `GET /_api/v1/session` (błąd sieci); otwórz stronę; zdejmij blokadę | pasek pokazuje „Offline”; tryb komentowania nie otwiera okienka; po pierwszej udanej odpowiedzi tryb wraca do pełnego bez przeładowania i można pisać (wpis trafia na serwer raz) | K16 | tak |
| R-02 | Zawieszone żądanie zapisu | odpowiedź na POST zapisu nie przychodzi; napisz odpowiedź w wątku; odczekaj `REQUEST_TIMEOUT_MS`; odblokuj | po przekroczeniu czasu pasek pokazuje „Offline”, wpis zostaje w kolejce; po odblokowaniu kolejka wysyła się bez przeładowania, wpis jest na serwerze dokładnie raz | K16 | tak |
| R-03 | Edycja, potem nawigacja w panelu | „Edytuj” na własnej wiadomości; „Wszystkie wątki” (droga A) albo pinezka innego wątku (droga B) | niezapisana edycja jest szkicem: opuszczenie widoku pyta „Porzucić szkic?” (potwierdzenie wykonuje nawigację); kolejny widok wątku ma pełną treść (tytuł i wiadomości tego wątku) | K17 | tak |
| R-04 | Kontrast numerów pinezek | wątki w statusach otwarty, czeka na decyzję, zdecydowany, zamknięty, odłożony; oba motywy | kontrast koloru tekstu pinezki do jej tła >= 4,5:1 dla każdego statusu w obu motywach (z obliczonych kolorów) | K18 | tak |
| R-05 | Klawiatura na `data-goto` w elementach nienatywnych | tryb Klikalny; Tab do `tr[data-goto]` i `div[data-goto]`; Enter, potem Spacja | element ma `role="button"`, jest osiągalny Tabem, Enter i Spacja przechodzą do celu (`s2`, `s3`) | K17 | tak |
| R-06 | Powrót karty z tła | karta „w tle”; w tym czasie inna osoba dodaje wątek; karta znów widoczna (`visibilitychange`) | nowy wątek (pinezka) pojawia się w <= 6 s | K16 | nie |
| R-07 | Motyw systemowy bez zapisanego wyboru | emulacja `prefers-color-scheme` dark, potem light; pusty `localStorage`; (a) wyłączony JS, (b) z JS | (a) bez JS tło strony odpowiada motywowi systemu (ciemne < 0,3, jasne > 0,7 luminancji względnej); (b) od pierwszego renderu do startu silnika każda próbka tła (po pierwszej klatce) i stan po starcie odpowiadają motywowi systemu, bez mignięcia przeciwnym motywem | — | nie |

## 14. Widok makiety desktop / mobile (`tests/e2e/prototype/widok.mjs`)

Spec: `docs/specs/2026-11-06-prototyp-tryb-widoku.md` (K1–K15). Prototyp testowy tworzy sam skrypt (`init` w katalogu
biegu): co najmniej 3 ekrany z ramką (`.frame`), jeden ekran bez ramki, w CSS projektu (`project.css`) element widoczny
tylko poniżej 600 px (`[data-test-mobile-only]`) i element widoczny tylko od 600 px (`[data-test-desktop-only]`), ekran z
`min-height: 100vh` w ramce. Każdy scenariusz bez innego zapisu biegnie przy 1440 i 375 oraz w obu motywach. W każdym
przypadku wyjątek strony albo `console.error` w czasie scenariusza daje FAIL (`consoleErrors`); jedynym dozwolonym
wpisem jest komunikat ładowania iframe widoku z nazwą ekranu w V-20. Haki: słownik, część C, punkt „Widok makiety”.

| ID | Nazwa | Kroki | Oczekiwany wynik | Spec | Krytyczny |
|---|---|---|---|---|---|
| V-01 | Start i przeładowanie (D246) | po wczytaniu sprawdź przełączniki lokalne; lokalnie „Mobile” na każdym ekranie z ramką, potem „Desktop”; „Mobile” i przeładowanie | start: każdy ekran z ramką w desktop, „Desktop” `aria-pressed=true` w każdym przełączniku lokalnym; „Mobile” daje iframe widoku na każdym ekranie; „Desktop” przywraca; po przeładowaniu wszystkie ekrany desktop | K1, D246 | tak |
| V-02 | Budżet paska bez grupy „Widok” (D246) | zmierz `.doc-toolbar` przy 375, 599, 600, 768 i 1000 (zalogowany i nie, oba motywy) na prototypie testowym i na prototypie wzorcowym z tymi samymi ekranami i silnikiem sprzed D28b (4 pliki z fikstury `tests/fixtures/prototype/engine-v2-prev/`) | w pasku nie ma grupy „Widok”; wysokość paska ≤ wysokość na prototypie wzorcowym + 2 px (D271: pasek nie urósł; może być niższy); przy 375 i 599 ≤ 2 × najwyższa kontrolka + 32 px (jak N-R9); brak poziomego przewijania; brak fikstury = NOT_RUN z powodem | K1, D246 | tak |
| V-03 | Przełącznik lokalny | „Mobile” na s2 (Enter), „Mobile” na s3 (Spacja), „Desktop” na s2 (klik), „Desktop” na s3 | każdy przełącznik zmienia tylko swój ekran, ekrany są niezależne; `aria-pressed` zgodne z wyborem | K2 | tak |
| V-04 | Ekran bez ramki | wszystkie ekrany z ramką lokalnie w „Mobile” | ekran bez ramki nie ma przełącznika lokalnego ani iframe widoku; jego pinezki i nowe komentarze są desktop | K2 | tak |
| V-05 | Media queries w iframe widoku | s1 w mobile | w iframe widoku `[data-test-mobile-only]` widoczny, `[data-test-desktop-only]` ukryty; w desktop odwrotnie; szerokość dokumentu iframe = min(375, dostępna), margines treści 0 | K3 | tak |
| V-06 | Wysokość ramki według treści | s1 w mobile; zmień wysokość treści w iframe (rozwiń element); ekran z `min-height: 100vh` | wysokość iframe = wysokość treści (±1 px), bez wewnętrznego przewijania; po zmianie treści nowa wysokość; ekran z `100vh` ma stałą wysokość bez pętli zmian | K3 | tak |
| V-07 | Strona w widoku mobile | s1 w mobile | pasek, opis ekranu i notatki zachowują szerokość jak w desktop; treść ramki na stronie ukryta (`display: none`, poza drzewem dostępności); plik prototypu bez zmian | K3 | tak |
| V-08 | Desktop jak dziś | start; Mobile → Desktop na s1 | brak `iframe` w `.frame`; prostokąty elementów ramki po powrocie = start (±1 px), przy 1440 i 375 | K4 | tak |
| V-09 | Znacznik widoku w dzienniku | zalogowany; komentarz do elementu i do ekranu w mobile oraz w desktop | dziennik: `create` z mobile ma `payload.view: "mobile"`, z desktop bez pola | K5 | tak |
| V-10 | Pinezki per widok | wątki mobile i desktop na s1; przełączaj widok s1 | pinezki mobile tylko w iframe widoku, desktop tylko na stronie; przełączenie bez przeładowania | K6 | tak |
| V-11 | Stan strony w iframe widoku | s1 w mobile; „Pokaż hotspoty”, „Ukryj pinezki”, „Prezentacja”, „Motyw” | `<html>` iframe widoku ma te same klasy `proto-hotspots`, `proto-pins-hidden`, `proto-presentation` i ten sam `data-theme` co strona po każdym przełączeniu | K6 | tak |
| V-12 | Panel: plakietka, filtr, klik | wątki mobile i desktop; filtr „Widok wątków”; „Otwarte” + „Mobile”; klik w kartę mobile | każda karta ma `[data-proto-view-badge]`; karty Mobile ∪ Desktop = Wszystkie; liczniki statusu liczone po filtrze widoku (Mobile + Desktop = Wszystkie); „Otwarte” + „Mobile” pokazuje tylko otwarte mobile; klik ustawia ekran wątku na mobile, przewija do niego i pokazuje pinezkę w iframe | K7 | tak |
| V-13 | Serwer bez obsługi widoków | przechwyć odpowiedź `GET /_api/v1/session` i usuń „views” z `server.features`; spróbuj komentarza w mobile; potem w desktop | komentowanie w mobile zablokowane z komunikatem; brak wpisu w kolejce i na liście odrzuconych; komentarz w desktop powstaje (bez `view`) | K9 | tak |
| V-14 | Akceptacja i hashe | stan akceptacji i `version` przed i po przełączaniu widoków | hashe ekranów, `version` i chip „Akceptacja k/n” bez zmian | K11 | tak |
| V-15 | Iteracje zamrożone | `iterate` na nowym silniku; druga iteracja z poprzednim silnikiem (`prototype.js` z fikstury `tests/fixtures/prototype/engine-v2-prev/`, kopia silnika sprzed widoków) | strona iteracji z nowym silnikiem ma przełączniki lokalne i widoki jak strona bieżąca; strona iteracji ze starym silnikiem działa tylko desktop (bez grupy „Widok”, bez iframe, 0 błędów); brak fikstury = NOT_RUN z powodem | K12 | tak |
| V-16 | Porównanie i podgląd przywracania | „Porównaj z iteracją N” i „Przywróć…” dla iteracji nowej i starej | w `dialog[open]` grupa „Widok” (Desktop domyślnie); „Mobile” ustawia iframe podglądu na min(375, dostępna) dla obu iteracji | K12 | tak |
| V-17 | `file://` i serwer statyczny | otwórz prototyp jako `file://` i przez serwer statyczny; lokalnie „Mobile” na s1 | iframe widoku renderuje się, media queries działają; pinezki i komentowanie jak dziś w tych trybach; konsola bez błędów | K13 | tak |
| V-18 | Okienko komentarza w mobile | tryb „Komentarze”; klik w element iframe widoku na ekranie niższym niż okienko | okienko otwiera się na stronie przy klikniętym miejscu, w całości w oknie; zapis tworzy wątek mobile; Esc zamyka i oddaje fokus do elementu w iframe | K14 | tak |
| V-19 | Klawiatura | Tab od pierwszej kontrolki paska przez przełącznik lokalny do pinezki w iframe; Enter i Spacja na przełączniku lokalnym; pinezka w iframe z klawiatury | przełącznik lokalny, pasek i pinezki osiągalne z klawiatury; fokus nie ginie przy przejściu strona ↔ iframe | K14 | tak |
| V-20 | iframe widoku bez gotowości | zablokuj ładowanie iframe widoku (przechwycenie żądania); przewiń do ekranu | po 5 s od rozpoczęcia ładowania ramka pokazuje „Nie udało się wczytać widoku mobile” i „Spróbuj ponownie”; po odblokowaniu i kliknięciu iframe działa; w konsoli jedyny wpis to błąd z nazwą ekranu | K15 | tak |
| V-21 | Okablowanie `postMessage` (bezpieczeństwo) | 1440 i 375, motyw jasny; zalogowany; s1 i s3 w mobile, tryb „Komentarze”; (a) strona wysyła sama `postMessage({type: 'skillsync-proto:pick', anchor, label, rect})` w kształcie przyjmowanym przez stronę (oraz warianty dodatkowe), obca ramka tego samego pochodzenia wysyła to samo do strony; kontrola dodatnia kształtu: ta sama wiadomość z okna iframe widoku s1; potem prawdziwy klik w element iframe widoku i zapis komentarza; (b) licznik żądań sieci przez CDP z identyfikatorem ramki | (a) po wiadomościach od strony i obcej ramki okienko komentarza się nie otwiera, a w dzienniku nie ma nowej operacji; ta sama wiadomość z iframe widoku otwiera okienko (`shapeAcceptedFromView`); prawdziwy klik otwiera okienko, a zapis tworzy wątek z `view: "mobile"`; (b) żadne żądanie do `/_api/` nie pochodzi z ramek iframe widoku (po co najmniej 7 s od gotowości), zapisy i odczyty idą tylko ze strony | D2a, Bezpieczeństwo | tak |
| V-22 | Brak globalnego przełącznika widoku (D246) | 375, 599, 600, 768, 1440; zalogowany i nie; motyw jasny; sprawdź pasek, a przy widocznym „Więcej” także otwarte menu | w `.doc-toolbar` nie ma grupy z nazwą „Widok” ani kontrolki o nazwie „Desktop” / „Mobile”; w menu „Więcej” nie ma pozycji „Desktop” / „Mobile” ani `menuitemradio`; każdy ekran z ramką ma przełącznik lokalny | D246 | tak |
| V-23 | Brak skoku strony przy zmianie wysokości iframe widoku (D248) | co najmniej 2 ekrany z ramką nad oglądanym przełączone lokalnie na mobile; oglądany ekran niżej przewinięty do widoku; odpowiedzi iframe `embed=view` opóźnione (interceptor CDP, krócej niż 5 s), potem zwolnione; następnie rozwinięcie i zwinięcie bloku w iframe nad oglądanym ekranem | górna krawędź oglądanego ekranu względem okna zmienia się o ≤ 2 px po każdej fazie: wzrost lub spadek iframe z placeholdera do treści, wzrost treści, zmniejszenie treści; dowód: wysokości iframe przed i po, delta, największe przejściowe odchylenie | D248 | tak |
| V-24 | Ładowanie iframe widoku nie przewija strony (NR1-V23) | 1440 i 375; ekrany s1 i s2 przełączone lokalnie na mobile; (a) czekanie na wczytanie ramek; (b) instancja osadzona otwarta wprost jako `?embed=view#view-s2` i `?embed=view#s2` | (a) przewinięcie strony zmienia się o ≤ 2 px; `src` ramek kończy się `#view-s1` / `#view-s2`; (b) oba zapisy fragmentu wskazują ekran s2 (`.is-embed-target`) | NR1-V23 | tak |
