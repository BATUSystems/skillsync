# Tematy dokumentów skilla prototypów (kontrakt treści)

Wymagane tematy dokumentów skilla i szablonów prototypu. Opis tematu jest abstrakcyjny. „Znaczniki” to identyfikatory
kontraktu (komendy, pliki, atrybuty, nazwy sekcji UI), które dokument musi zawierać, żeby temat był obecny. Test obecności sprawdza
wyłącznie znaczniki; sformułowania, układ i kolejność są swobodne.

## `skills/prototype/SKILL.md`

| Temat | Znaczniki |
|---|---|
| Kiedy skill jest używany i czego nie robi (opcjonalna makieta, tylko na prośbę albo za zgodą) | `prototype` |
| Tryby: repo (domyślny) i Artifact | `docs/prototypes/`, `Artifact` |
| Kroki tworzenia prototypu w repo, od założenia do przeglądu | `skillsync prototype init`, `skillsync prototype sync`, `skillsync prototype ids` |
| Pliki prototypu i ich role, w tym pliki silnika i dane komentarzy | `prototype.json`, `index.html`, `comments.json`, `prototype.js` |
| Uchwyty elementów i ekran przykładowy | `data-proto-id`, `data-prototype-example` |
| Przegląd z ludźmi przez serwer komentarzy i konta | `skillsync prototype serve`, `skillsync prototype user` |
| Decyzje po przeglądzie, iteracje i przywracanie | `references/decisions.md`, `skillsync prototype iterate`, `skillsync prototype restore` |
| Akceptacja należy do człowieka; bramka przed implementacją | `skillsync prototype accept`, `skillsync prototype gate` |
| Aktualizacja silnika w istniejącym prototypie | `skillsync prototype migrate` |
| Odnośniki do referencji | `references/comments-format.md`, `references/screen-patterns.md`, `references/style.md` |

## `skills/prototype/assets/repo/README.md` (szablon README prototypu)

| Temat | Znaczniki |
|---|---|
| Nagłówek z tytułem i identyfikatorem prototypu (placeholdery) | `{{TITLE}}`, `{{SLUG}}`, `{{REQUIREMENTS}}` |
| Mapa wymagań na ekrany (tabela do uzupełnienia) | `sN` albo `s1` |
| Jak otworzyć prototyp: plik, zwykły serwer statyczny, serwer komentarzy | `skillsync prototype serve` |
| Tryby pracy silnika i ich ograniczenia | `file://` |
| Komentarze: wątki, głosy, kolejka offline, odrzucone wpisy | `comments.json` |
| Iteracje, porównanie i przywracanie ekranu | `iterations/`, `skillsync prototype restore` |
| Akceptacja ekranu i całego projektu | `skillsync prototype accept` |
| Gdzie zmieniać treść, style i motyw | `project.css`, `theme.css` |
| Aktualizacja silnika | `skillsync prototype migrate` |
| Uchwyty i kontrola uchwytów | `data-proto-id`, `skillsync prototype ids` |

## `skills/prototype/references/screen-patterns.md`

| Temat | Znaczniki |
|---|---|
| Budowa ekranu w dokumencie (sekcja, nagłówek, ramka) | `section`, `screen`, `screen-meta`, `frame` |
| Nawigacja między ekranami | `data-goto`, `data-proto-nav` |
| Powłoka aplikacji z paskiem bocznym i górnym | `sidebar`, `topbar` |
| Tabela danych z filtrami i stronicowaniem | `dt`, `pager` |
| Formularz z polami i błędami | `form-grid`, `field-error` |
| Szuflada i okno | `drawer`, `modal` |
| Tablica kanban | `kanban`, `kcard` |
| Siatka tygodnia (dni × sloty, zdarzenia, kolizje, dzień wolny) | `week-grid`, `week-grid-event`, `week-grid-clash` |
| Stany: pusty, ładowanie, błąd, brak uprawnień | `empty`, `alert` |

## `skills/prototype/references/style.md`

| Temat | Znaczniki |
|---|---|
| Warstwy stylu i kolejność ładowania (tokeny, motyw, silnik, ekrany) | `tokens.css`, `theme.css`, `prototype.css` |
| Gdzie wpisywać styl projektu i czego nie edytować ręcznie | `project.css`, `screens.css` |
| Kolejność nakładania warstw (pasek, ramka, okna, pinezki) | `z-index` |
| Motyw jasny i ciemny, blokada przyciemniania przez rozszerzenia | `data-theme`, `color-scheme`, `darkreader-lock` |
| Wytyczne projektu i tokeny wymagane | `required-tokens.txt`, `prototype.guidelines` |
| Kontrola tokenów i szablonów | `check_tokens.py` |
