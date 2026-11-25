# Jedna strona: status i prototypy (CORE-2000)

Slug: unified-page

Klikalna makieta ekranów do przeglądu z zespołem przed implementacją. Wymagania: spec CORE-2000-UNIFIED (przechowywany poza repozytorium rdzenia)

## Mapa wymagań na ekrany

Źródło: spec CORE-2000-UNIFIED, sekcja „Ekrany prototypu (a–h)” i kryteria K1–K18. Dane są syntetyczne (Projekt Przykład, plan „Zapisy”, pseudonimy Ola i Marek).

| Ekran (sN) | Nazwa | Ekran specu | Kryteria / sekcje |
|---|---|---|---|
| s1 | Widok anonimowy, tryb `pr`, pasek „Zaloguj się, aby zatwierdzać” | a | K1, K2, K5, K7; „Co staje się widoczne anonimowo” |
| s2 | Logowanie, wariant HTTP w LAN | b | K3, K8; sekcja 2 (logowanie w sieci, Q3) |
| s3 | Approver: GO plan, okno z hasłem | c | K3, K4, K7; sekcja 3 |
| s4 | Tryb `direct`: CP | d | K3, K4, K7; sekcja 3 |
| s5 | Tryb `pr`: READY | e | K3, K4, K7; sekcja 3 |
| s6 | Bez roli approver: zgody wyszarzone | f | K3, K7; sekcja 2 (macierz ról, Q1) |
| s7 | Zakładka „Prototypy” | g | K1, K2, K6; sekcja 5 |
| s8 | „Pytania” bez prototypów i stany odmowy (401, 403 x2, 409, HTTP bez TLS) | h | K3, K6, K8; sekcje 3 i 5 |
| s9 | Origin prototypów: osadzenie, przekazanie sesji, baner single-origin, blokada limitera (429, 503) | — | K17, K18, K12–K14 (częściowo); sekcje 1, 2, 3 i 7 |

Kryteria bez ekranu (testy i dokumentacja, nie makieta): K9 (samodzielny `prototype_server.py`), K10 (zmiana reguły zgód w instrukcjach), K11 (migracja i `docs/upgrading.md`); K5 widać tylko pośrednio (adres bez `#zgoda=`); K15 i K16 poza makietą.

## Dwa originy

Jeden proces serwera ma dwa originy: stronę statusu (domyślnie port 5140: status, zakładki, logowanie, sesja i zgody) oraz prototypy (domyślnie 5142: galeria i `/<slug>/…`, bez tras zgód i bez formularza hasła). Prototyp to kod pisany przez agenta, więc nie może działać w originie, w którym wpisuje się hasło; sesję na originie prototypów daje jednorazowy kod (60 s) ze strony i nie niesie on roli „zatwierdzający”. Tryb `--single-origin` łączy je w jeden, wyłącza zgody ze strony i pokazuje trwały czerwony baner.

## Jak otworzyć prototyp

- Plik: otwórz `index.html` w przeglądarce. Z adresu `file://` pasek pokazuje „Bez serwera”: to tylko podgląd ekranów,
  bez komentowania, logowania i akceptacji.
- Zwykły serwer statyczny (np. `python3 -m http.server` w tym katalogu): wygląd i nawigacja jak z pliku, a pasek pokazuje
  „Tylko odczyt”: silnik czyta `comments.json` i pokazuje wątki, ale niczego nie zapisuje.
- Serwer komentarzy: `skillsync prototype serve <projekt>` z kontami użytkowników; komentarze, głosy, decyzje i akceptacja
  są wspólne dla wszystkich osób z dostępem.

## Komentarze

Komentowanie (tylko przy `skillsync prototype serve`, po zalogowaniu) włącza przycisk „Komentarze (C)” na pasku albo klawisz C. Kliknięty element dostaje wątek; do wątku można odpowiadać, głosować
i zmieniać jego status. Zapis idzie do `comments.json` (dziennik operacji, który tylko rośnie, więc dobrze się scala w gicie).
Gdy serwer jest niedostępny, wpisy czekają w kolejce offline przeglądarki i można je ponowić; odrzucone przez serwer wpisy
zostają na liście „Pokaż odrzucone”.

## Widok desktop / mobile

Przełącznik „Desktop” / „Mobile” w nagłówku ekranu z ramką zmienia widok tylko tego ekranu; po wczytaniu strony każdy ekran
jest w Desktop. Widok mobile pokazuje ramkę ekranu w iframe o szerokości 375 px, więc działają
media queries makiety. Komentarz dostaje widok, w którym powstał, a jego pinezka jest widoczna tylko w tym widoku; panel
„Wątki” ma plakietkę i filtr widoku. Akceptacja ekranu obejmuje oba widoki.

## Iteracje i przywracanie

Po rundzie uwag agent zamraża oglądaną wersję w podkatalogu `iterations/` (`skillsync prototype iterate`, kolejne numery N) i dopiero potem zmienia
`index.html`. W ekranie można porównać bieżący stan z wybraną iteracją. Wcześniejszy wygląd pojedynczego ekranu wraca
poleceniem `skillsync prototype restore <projekt> <slug> --screen sN --from N --reason "…" --who <login>` (najpierw z `--dry-run`).

## Akceptacja

Ekran albo cały projekt akceptuje człowiek z rolą akceptującego albo programisty: w interfejsie albo poleceniem `skillsync prototype accept`
(hasło wpisane w terminalu). Zmiana ekranu po akceptacji unieważnia ją. Bramka `skillsync prototype gate` sprawdza przed
implementacją, czy każdy ekran ma aktualną akceptację.

## Gdzie zmieniać treść, style i motyw

- Treść ekranów: `index.html`; każdy ekran to `<section class="screen" id="sN">`.
- Style projektu: `project.css` (ładowany jako ostatni arkusz, po `screens.css`; `sync` i `migrate` go nie ruszają). Pliki biblioteki `prototype.css`, `components.css` i `screens.css` nadpisuje `migrate`, więc nie edytuj ich ręcznie.
- Motyw (kolory, czcionki, zaokrąglenia): pliki wskazane w manifeście projektu (`prototype.theme.tokens_from`, `prototype.theme.css`).
  `tokens.css` i `theme.css` w tym katalogu generuje z nich `skillsync prototype sync` (także `migrate`); nie edytuj ich ręcznie.
- Silnik (`prototype.js`, `prototype.css`) jest kopią z biblioteki; nie zmieniaj go lokalnie.

## Aktualizacja silnika

`skillsync prototype migrate <projekt> <slug>` kopiuje pliki silnika (`prototype.js`, `prototype.css`, `components.css`,
`screens.css`) z biblioteki, gdy się różnią, a prototyp w starym formacie (`comments.js`) przenosi do `comments.json`.
Kopia nadpisuje te pliki w całości, dlatego własne style trzymaj w `project.css`, którego migracja nie dotyka. Przy starszym prototypie `migrate` utworzy `project.css` i ostrzeże o klasach ze starego `screens.css`, których nie ma w nowym; przenieś te reguły do `project.css`.
Z opcją `--dry-run` pokazuje zmiany bez zapisu. `skillsync prototype sync` odświeża tylko `tokens.css` i `theme.css`.

## Uchwyty elementów

Każdy element komentowalny w ramce ekranu ma stały uchwyt `data-proto-id` (np. `s2-zapisz`). Wątek trzyma się uchwytu, więc
po nadaniu nie zmieniaj go. Brakujące uchwyty sprawdza `skillsync prototype ids <projekt> <slug> --check`, a `--fix` dopisuje
je, nie zmieniając wyglądu.
