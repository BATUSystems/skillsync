# {{TITLE}}

Slug: {{SLUG}}

Klikalna makieta ekranów do przeglądu z zespołem przed implementacją. Wymagania: {{REQUIREMENTS}}

## Mapa wymagań na ekrany

Uzupełnij tabelę, gdy powstaną ekrany. Ekran przykładowy `s1` (atrybut `data-prototype-example`) nie jest liczony.

| Ekran (sN) | Nazwa | Wymaganie |
|---|---|---|
| s1 | Ekran przykładowy | — |

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
