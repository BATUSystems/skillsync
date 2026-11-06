# Dokumentacja i wiedza domenowa

Każde zadanie implementacyjne zostawia projekt lepiej udokumentowanym, niż go zastało.
To część definicji `done`, nie dodatek po zakończeniu.

## Co jest czym

| Rodzaj | Miejsce | Zawartość |
|---|---|---|
| Dokumentacja projektu | README, `docs/`, instrukcje modułów, docstringi kontraktów | Jak system działa teraz: uruchomienie, API, konfiguracja, decyzje |
| Wiedza domenowa | jedno wspólne `docs/domain/` projektu + `index.yaml` | Pojęcia biznesowe, reguły, wyjątki, słownik, źródła prawdy |
| Lekcje | `docs/lessons/` (projekt), `workflow/lessons/` (biblioteka) | Potwierdzone awarie techniczne i reguły, które je wykluczają |
| Wiedza wspólna dla projektów | skill w bibliotece (np. `globex`, `acme`) | Domena klienta/organizacji wykraczająca poza jedno repo |
| Lekcje wspólne organizacji | Acme: repo `Lessons` (skill `acme`); pozostałe: `workflow/lessons/` w bibliotece | Przenośne pułapki techniczne stosu (upgrade, CI, biblioteki), bez szczegółów projektu |

## Klasyfikacja odkryć (każde odkrycie trafia dokładnie w jedno miejsce)

| Odkrycie | Miejsce |
|---|---|
| Reguła biznesowa, pojęcie, wyjątek, źródło prawdy | `docs/domain/` |
| Pułapka techniczna tego repo (build, testy, migracje, konfiguracja, zachowanie biblioteki w tym kodzie) | `docs/lessons/` |
| Pułapka przenośna na inne projekty tego stosu | Acme → repo `Lessons`; inne organizacje → `workflow/lessons/` biblioteki |
| Pułapka środowiska agentów (shell, narzędzia sesji, procesy) | skill `agent-runtime-pitfalls` |
| Jak system działa teraz | dokumentacja obszaru (`README`, `docs/`) |

Lekcje są pisane **dla podumysłów i przyszłych sesji**: krótko, w formie wykonawczej — `Objaw` → `Przyczyna` →
`Reguła` (co robić / czego nie robić) → `Jak wykryć` (komenda, test, grep), z `paths` wskazującymi obszar kodu,
żeby `skillsync route --files` dobrał rekord do zlecenia. Przed pierwszym zapisem wiedzy orkiestrator uruchamia
`skillsync knowledge init <projekt>` (tworzy brakujące `docs/domain/index.yaml`, `docs/lessons/index.yaml`,
`docs/README.md`; nigdy nie nadpisuje), dopiero potem zleca zapis `scribe`. Brak katalogu nie jest powodem,
by pułapkę zapisać w `docs/domain` albo pominąć.

Wiedza domenowa to wszystko, czego nie da się wyczytać z samego kodu, a co zmienia sens
zmiany: dlaczego reguła istnieje, kto jest jej właścicielem, jakie są wyjątki i przykłady,
co znaczy pojęcie w języku klienta, które dane są źródłem prawdy. Odkrywa się ją w ticketach,
odpowiedziach użytkownika, w danych, w zachowaniu systemu i w konsumentach kodu.

## Jedno wspólne miejsce

Wiedza domenowa nie różni się między backendem a frontendem — projekt ma **jedną** lokalizację:

- jedno repo (także monorepo backend+frontend): `docs/domain/` w katalogu projektu;
- katalog z osobnymi repo backendu/frontendu (np. DEMO, Northwind — manifest w katalogu nadrzędnym):
  `docs/domain/` w tym katalogu, nie w żadnym z repo;
- osobne projekty skillsync dla backendu i frontendu (np. Shop: `shop-web` + `shop-api`):
  wspólny katalog nadrzędny, w obu manifestach
  `workflow.knowledge.domain: ../docs/domain`.

`paths` w rekordach i indeksie są względne do katalogu zawierającego `docs/`
(np. `demo-shop/Domain/**`, `shop-web/src/app/**`), więc jeden rekord obejmuje
oba repo. Nie zakładaj `docs/domain` w podrepozytoriach — `doctor` zgłasza takie indeksy jako
rozproszoną wiedzę. Katalog nadrzędny może nie być w gicie; wtedy rekordy nie mają historii
ani synchronizacji między maszynami.

## Rekord domenowy

`docs/domain/<temat>.md` z YAML frontmatter:

```markdown
---
title: Numeracja faktur korygujących
tags: [billing, invoices]
paths: ['billing/**', 'invoices/**']
source: DEMO-1234, odpowiedź użytkownika 2026-10-02
verified: 2026-10-02
---
Reguła: ...
Dlaczego: ...
Wyjątki i przykłady: ...
Gdzie w kodzie: billing/numbering.py:42
```

`docs/domain/index.yaml`:

```yaml
version: 1
entries:
  - path: billing-numbering.md
    title: Numeracja faktur korygujących
    tags: [billing, invoices]
    paths: ['billing/**']
```

`skillsync route` zwraca pasujące rekordy ze wspólnej lokalizacji w polu `domain` (po tagach/ścieżkach;
`--topics domain` zwraca wszystkie). Rekord z `load: bootstrap` w indeksie jest zwracany zawsze,
także bez tematów i ścieżek. Brak `load` zachowuje dotychczasowe dopasowanie; jedyna dozwolona
jawna wartość to `bootstrap` (null i pusta wartość są błędami). `skillsync doctor` sprawdza frontmatter, `load` i ostrzega,
gdy projekt z `feature` nie ma jeszcze indeksu — utwórz go przy pierwszym rekordzie.

## Glosariusz domenowy na wejściu

Przed interpretacją ticketu i wyborem plików uruchom `skillsync route <projekt>` i przeczytaj
zwrócone rekordy `domain` z `load: bootstrap`. CLI wybiera ścieżki; odczyt treści wykonuje agent.
Bez CLI odczytaj `docs/domain/index.yaml` ze wspólnej lokalizacji projektu i wskazane tam
rekordy bootstrap. Nie zakładaj, że slang projektu ma znaczenie potoczne lub znane z innego projektu.

Glosariusz projektu to `docs/domain/glossary.md`, z frontmatterem zwykłego rekordu domenowego.
Indeksuj go tak:

```yaml
  - path: glossary.md
    title: Glosariusz domenowy projektu
    tags: [terminology]
    load: bootstrap
```

Jeden nagłówek na termin: **znaczenie w projekcie**, **nie oznacza** (gdy istnieje ryzyko pomyłki),
**formy i synonimy**, **nazwy w kodzie** (jeśli potwierdzone), **źródło i data potwierdzenia**,
opcjonalnie odsyłacz do szczegółowych reguł. Źródłem może być jawna definicja użytkownika,
ticket, kod lub dane; nazwa klasy sama nie potwierdza znaczenia biznesowego ani równoważności synonimów.

Zapisuj pojęcia, które zmieniają interpretację wymagania, odbiegają od znaczenia potocznego
i mogą powrócić w kolejnych zadaniach. Pomijaj oczywiste terminy techniczne, jednorazowe skróty
i duplikaty. Kandydat z rozmowy lub analizy trafia do sekcji „Terminologia domenowa” planu
z hipotezą znaczenia i pytaniem/źródłem do sprawdzenia. Do glosariusza trafia dopiero po
potwierdzeniu; nie wymagaj ponownej zgody, gdy użytkownik już wprost podał definicję.
Sprzeczne definicje wymagają wyjaśnienia, nie cichego nadpisania.

Celuj w maksymalnie 8 KB UTF-8; przy zbliżeniu do 12 KB przenieś szczegóły do tematycznych
rekordów domenowych, pozostawiając krótkie definicje i odsyłacze. 12 KB jest limitem procedury,
nie automatyczną kontrolą CLI. Nie ładuj całej historii reguł do bootstrap i nie dopisuj
glosariusza do rdzenia instrukcji. Brak nowych terminów jest poprawnym wynikiem zadania.

Wartość YAML z `: ` (dwukropek i spacja) bierz w cudzysłów (`title: 'Polityka plików: limity'`); tagi to
stringi (`'409'`, nie `409`). Bez tego rekord jest nieczytelny dla routera, a `doctor` zgłasza błąd
(rekordy bez nagłówka albo z niecytowanym dwukropkiem). W worktree bez
`.github/.skillsync.yaml` (ignorowany katalog) `doctor` zwraca `BLOCKED: Missing manifest` — to nie błąd;
nagłówki sprawdzisz tam parserem YAML, a pełny `doctor` w katalogu głównym repo.

## Zasady zapisu

- Zapisuj fakty potwierdzone (kod, dane, ticket, decyzja użytkownika) ze wskazaniem źródła.
  Hipotezę oznacz jako hipotezę albo nie zapisuj. Bez sekretów i danych osobowych.
- Najpierw aktualizuj istniejący rekord/dokument; duplikat zamiast poprawki to błąd.
  Usuń lub popraw treść, którą zmiana zdezaktualizowała.
- Jeden temat na rekord, krótko: reguła, uzasadnienie, wyjątki, przykład, miejsce w kodzie.
- Domena wspólna dla kilku repo trafia do skilla w bibliotece (`push`/`link`), nie do kopii
  w każdym projekcie. Reguły bezpieczeństwa zostają również w instrukcji najbliższego obszaru.
- Rdzeń instrukcji (`_base.md`, `CLAUDE.md`, `AGENTS.md`) nie jest dziennikiem wiedzy —
  tam trafia najwyżej odsyłacz.

## W cyklu zadania

1. Plan: sekcja „Dokumentacja i wiedza” — które dokumenty zmiana dezaktualizuje, jakiej
   wiedzy się spodziewamy, kto ją zapisze. „Nie dotyczy” tylko z uzasadnieniem.
   Sekcja „Terminologia domenowa” rozdziela terminy potwierdzone do dodania/aktualizacji,
   kandydatów do wyjaśnienia i uzasadnione „brak nowych terminów”.
2. Zlecenie dla podumysła zawiera ścieżki pasujących lekcji i rekordów domenowych (`skillsync route <projekt> --files
   <pliki kroku>` → pola lessons/domain); podumysł czyta tylko je. Podagent raportuje „Odkrycia” (domena,
   dokumentacja nieaktualna, pułapki) ze źródłem.
3. Orkiestrator przed review zbiera odkrycia, klasyfikuje je według tabeli wyżej i przydziela zapis (domyślnie `scribe`).
   `scribe` aktualizuje glosariusz tylko dla terminów potwierdzonych ze źródłem; kandydaci
   pozostają w planie. Wiedzę potwierdzoną podczas planowania można utrwalić już wtedy.
   Odkrycie-pułapka bez lekcji wymaga uzasadnienia w planie; zapis do repo `Lessons` (Acme) — plik lokalnie,
   commit/push wyłącznie na polecenie użytkownika (repo współdzielone z zespołem).
4. Reviewer sprawdza dokumentację i wiedzę jak kod, w tym źródła definicji, deduplikację,
   rozdzielenie kandydatów od faktów, indeks bootstrap i limit glosariusza;
   brak wymaganego zapisu bez uzasadnienia = `CHANGES_REQUIRED`.
5. „Wynik” w planie ma podsekcję „Zdobyta wiedza” z odnośnikami do zapisanych plików.
