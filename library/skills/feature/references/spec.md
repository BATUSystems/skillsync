# Specyfikacja dużej zmiany (`docs/specs`)

Czytaj, gdy bramka `plan-large` ([plan-large](plan-large.md)) każe utworzyć spec, przy starcie realizacji
planu z linią `Spec:`, przed jego review i przy jego zamykaniu. Przy `direct` i `reuse` spec nie powstaje.
Spec to projekt jednej dużej zmiany (decyzje, kontrakty, dane, ryzyka); **plan** opisuje wykonanie,
a stan bieżący systemu opisują `docs/architecture.md` i `docs/domain/`. Po wdrożeniu spec jest zamrożoną historią decyzji.

## Plik i wiązanie

- Ścieżka: `docs/specs/RRRR-MM-DD-<slug>.md` w katalogu projektu (data jak w planie), nagłówek `Status: <status>` pod tytułem.
- Plan wskazuje spec linią `Spec: <ścieżka>` w nagłówku ([plan-details](plan-details.md)). Jeden spec należy do jednego planu;
  kolejna faza dużej zmiany to nowy spec, a poprzedni zostaje bez zmian (`superseded` tylko, gdy nowa decyzja go zastępuje).
- Spec nie powtarza treści planu (kroki, TESTY, zasoby, wersja, Progress), a plan nie powtarza treści speca.

## Statusy

| Status | Znaczenie | Kto i kiedy |
|---|---|---|
| `draft` | szkielet z pytaniami `Q` albo projekt przed „GO plan” (akt I); także na czas zmiany merytorycznej po „GO plan” (`Spec-sha` nieważne do nowej zgody) | agent przy tworzeniu i przy takiej zmianie |
| `approved` | przyjęty do realizacji (nie „opisuje stan bieżący”) | agent przy „GO plan”, razem z planem-szkicem; plan zatwierdzony przyciskiem albo `skillsync plan approve` przy specu `draft` to STOP i pytanie (tabela wznowienia) |
| `implemented` | wdrożony, zamrożony | agent w tym samym commicie co kod; zob. niżej |
| `superseded` | zastąpiony albo porzucony | agent, z odnośnikiem do nowego speca albo z powodem przy porzuconym planie |

Kolejność przy „GO plan”: spec `approved`, plan-szkic z linią `Spec-sha:` ([plan-large](plan-large.md) §5). Przy „GO implement”:
kontrola `Spec-sha`, plan `in_progress`.

**`implemented`:**
- Warunek: spec ma status `approved` i zgodny `Spec-sha`. Inny stan to STOP i pytanie, nigdy zamrożenie speca bez zgody.
- Wejście z klucza zgłoszenia: przy „rób PR”, po kontroli `Spec-sha` i przed commitem ([pr-after-approval](pr-after-approval.md));
  plan `done` przychodzi tam dopiero po utworzeniu PR-ów.
- `/pr-run`: w ostatnim commicie przed ready ([pr-run](../../pr-run/SKILL.md)).
- Pozostałe przebiegi: przy `done` planu, razem z jego zamknięciem.
- Wiele repozytoriów: status zmienia się tuż przed commitem tego repozytorium, które zawiera `docs/specs`; spec jest wtedy
  zawsze w ścieżkach zadania tego repozytorium, także gdy wcześniej nie było go w diffie.
- Katalog projektu bez gita: spec nie idzie do commita, a status zmienia się lokalnie po ostatnim commicie.

## Sha, zamrożenie i wznowienie

- Po ustawieniu `approved` zapisz w planie `Spec-sha: <git hash-object speca>` (działa także poza repozytorium).
- Policz sha ponownie przed „GO implement”, przed review i tuż przed ustawieniem `implemented`. Różnica to STOP i pytanie; nie aktualizuj
  sha sam, poza poprawką mechaniczną albo minor w akcie II (niżej).
  Porównanie obowiązuje tylko przy specu `approved`: po `implemented` nie działa, także przy „wypchnij poprawki” i „popraw uwagi z PR”.
- Zmiana speca w akcie II: mechaniczna albo minor (redakcja, doprecyzowanie bez zmiany decyzji, kontraktu, zakresu ani kryteriów) =
  poprawka speca, przeliczony `Spec-sha` w planie i lista zmian od „GO plan” w prośbie o „GO implement”, bez nowej pętli `spec`;
  merytoryczna = powrót do aktu I i nowa zgoda (tabela niżej). Okno między przeglądem a zgodą chroni zasada ponownego odczytu
  zmian użytkownika po zgodzie.
- Zamrożony spec (`implemented`, `superseded`) zmienia się tylko w polu `Status:` (przy `superseded` z odnośnikiem).
  Prośba o edycję takiego speca: odmów i zaproponuj nowy spec. Odejścia od speca zapisuje „Wynik” planu, a stan bieżący
  `docs/architecture.md` i `docs/domain/`.
- Nowa decyzja w obszarze speca `implemented`: nowy spec, a stary dostaje `superseded` z odnośnikiem dopiero, gdy nowy ma `approved`; porzucony `draft` niczego nie zastępuje. Nowa decyzja przy specu
  `approved` i planie w toku to pytanie do użytkownika, bez samodzielnego `superseded`.
- Błąd lub `direct`, które odsłonią zmianę kontraktu albo danych nieopisaną w planie: pytanie o spec, bez rozszerzania zakresu; po zgodzie praca stoi, plan dostaje jednocześnie `in_progress (blocked — czeka na spec)` i linię `Spec:`, a dalej idzie pełna ścieżka [plan-large](plan-large.md) ze szkieletem, przeglądem i GO, po którym blokada znika; istniejący plan dostaje linię `Spec:`, a nowy powstaje tylko, gdy nie pokrywa zmiany.

**Wznowienie:**

| Spec | Plan | Akt i działanie |
|---|---|---|
| `draft` z `Q` | brak | akt I: szkielet jest w `docs/specs/`, zadaj pytania, bez nowego szkieletu |
| `draft` z `Q` | dowolny status | pytania, bez startu |
| `draft` bez `Q` | brak | akt I: pętla `spec` albo czekanie na „GO plan” |
| `draft` bez `Q` | `approved*` (także `approved (czeka na spec)` po `skillsync plan approve` albo przycisku) | STOP i pytanie, nigdy zwykły start; po potwierdzeniu użytkownika stary plan rusza starym startem (spec `approved`, `Spec-sha`, `in_progress`) |
| `draft` bez `Q` (zmiana merytoryczna w akcie II) | `draft (czeka na spec)` | akt I od nowa: pętla `spec` od 1, „GO plan”, nowy `Spec-sha` w tym samym planie, pętla `plan` od 1 |
| `draft` bez `Q` | `in_progress (blocked — czeka na spec)` | spec zmieniony w akcie III: [pętla `spec`](review-loop.md) od 1, potem jedno GO zdejmujące blokadę; pętla `plan` tylko, jeśli plan zmienia się merytorycznie |
| `draft` | `in_progress` bez tej blokady | brak zgody na spec: STOP i pytanie |
| `approved` | brak | nie występuje po „GO plan”; z przerwanego zapisu: utwórz plan-szkic i porównaj sha z tym, co zatwierdził użytkownik (różnica = pytanie) |
| `approved` | `draft` bez sufiksu | akt II: pętla `plan` (rekordy `plan-review` bez PASS) albo plan w pisaniu; po zmianie speca mechanicznej albo minor w akcie II: poprawka speca, nowy `Spec-sha`, lista zmian w prośbie o „GO implement” |
| `approved` | `draft (czeka na spec)` | „GO plan” po zmianie merytorycznej: zdejmij sufiks (plan `draft`), pętla `plan` od 1; PASS `plan-review` sprzed tej zgody jest nieważny |
| `approved` | `approved (czeka na spec)` (przycisk albo `skillsync plan approve` w trakcie powrotu do aktu I) | pytanie: plan zatwierdzono przed nową pętlą `plan`, kontynuować? |
| `approved` | `draft (czeka na GO implement)` | akt II: czekanie na „GO implement” (ostatni `plan-review` PASS albo opcjonalna runda lub brak pętli przy `direct`) |
| `approved` z `Spec-sha` | `approved` (także `approved (czeka na GO implement)` po `skillsync plan approve`) z PASS `plan-review` po ostatnim „GO plan” albo bez obowiązkowej pętli | „GO implement” udzielone: porównaj sha, usuń sufiks, plan `in_progress`, akt III |
| `approved` | `approved` bez PASS obowiązkowej pętli `plan` po ostatnim „GO plan” | pytanie: plan zatwierdzono przed końcem pętli (np. przyciskiem), kontynuować? |
| `approved` bez `Spec-sha` | `approved` | przerwanie przy zapisie: policz, zapisz, plan `in_progress` |
| `approved` bez `Spec-sha` | `in_progress` | pytanie |
| `approved` z `Spec-sha` | `in_progress (blocked — czeka na spec)` | przerwanie po GO zdejmującym blokadę w akcie III: porównaj sha, zdejmij blokadę, akt III |
| `approved` | `in_progress` | akt III |
| `implemented` | `in_progress`, zmiana statusu bez commita (brak wiersza `committed` repozytorium ze specem; w `/pr-run`: zmiana widoczna w `git status`) | przerwanie przed commitem: pytanie, bo sha już nie chroni treści |
| `approved` | `done` | niezgodność stanów: pytanie |

Gdy pasuje kilka wierszy, wygrywa bardziej szczegółowy (sufiks statusu, z `Spec-sha` albo bez), a przy remisie ostrożniejsze
działanie (pytanie). Koniec pętli to rekord `PASS` albo rekord `BLOCKED` ze STOP i zapisaną decyzją użytkownika (GO po STOP). Przed startem aktu III,
także po zatwierdzeniu przyciskiem, sprawdź odpowiedź o wersję i `readiness`; brak to pytanie. Plan `direct` i `bugfix`
nie mają wierszy aktu I; „GO implement” przychodzi po pętli albo opcjonalnej rundzie planu.

## Wymagana zawartość

Problem i odniesienie do wymagań (numery z mapy FRD, bez kopiowania), zakres, kryteria akceptacji (`K1`, `K2`…, testowalne),
przy FRD mapa wymaganie → kryterium ([frd-intake](frd-intake.md) §5), decyzje i alternatywy, badanie wzorców
(gdy zmiana wprowadza nowy mechanizm), `Kontrakty`, dane, `Migracja`, bezpieczeństwo, `Ryzyka` (rejestr z ryzykiem rezydualnym),
odwrócenie zmian (każda mutacja lub jawne „nieodwracalne”), strategia testów (podejście; komendy są w planie), raport spójności.
Sekcja bez zastosowania: „nie dotyczy” z powodem, bez wymyślania treści. Pytania `Q` żyją tylko w szkielecie i znikają po odpowiedziach.

Nagłówki `Kontrakty`, `Migracja` i `Ryzyka` zostaw dosłownie, każdy z treścią bezpośrednio pod sobą (bez podnagłówka zaraz pod spodem):
`skillsync readiness` kończy sekcję na najbliższym nagłówku dowolnego poziomu i dopasowuje podciąg nagłówka bez wielkości liter.
W treści speca nie zostawiaj słów `TODO` ani `TBD`, bo blokują `readiness`.

## Szablon speca

````markdown
# Spec: <tytuł>

Status: draft
Data: <RRRR-MM-DD>
Plan: <docs/plans/RRRR-MM-DD-slug.md, po utworzeniu>

## Problem

<kto ma problem, skutek, odniesienie do wymagań (numery z mapy FRD niżej)>

## Zakres

<co obejmuje, czego nie obejmuje; przy wejściu z klucza także dane z przyjęcia zgłoszenia, zanim powstanie plan>

## Pytania otwarte

- Q1: <krótkie pytanie, najlepiej binarne lub z wyborem>

## Kryteria akceptacji

| # | Kryterium |
|---|---|
| K1 | <testowalne kryterium> |

## Mapa wymagań FRD

| Wymaganie | Kryterium | Uwagi |
|---|---|---|
| <numer z FRD> | <K…> | <poza zakresem z powodem, gdy dotyczy; bez FRD: „nie dotyczy”> |

## Decyzje i alternatywy

| Decyzja | Uzasadnienie |
|---|---|
| <decyzja> | <powód> |

| Alternatywa | Powód odrzucenia |
|---|---|
| <alternatywa> | <powód> |

## Badanie wzorców

<co sprawdzono, co przyjęto, co odrzucono; albo „nie dotyczy” z powodem>

## Kontrakty

<API, DTO, zdarzenia, CLI, rzeczywiści konsumenci i zgodność wsteczna; albo „nie dotyczy” z powodem>

## Dane

<modele, stan trwały, uprawnienia do danych; albo „nie dotyczy” z powodem>

## Migracja

<jak przejść ze stanu obecnego, mieszany stan wersji, ponowienie; albo „nie dotyczy” z powodem>

## Bezpieczeństwo

<uprawnienia, izolacja, dane wrażliwe; albo „nie dotyczy” z powodem>

## Ryzyka

| Scenariusz | Waga | Mitygacja | Ryzyko rezydualne |
|---|---|---|---|
| <scenariusz> | <critical/high/medium/low> | <mitygacja> | <ryzyko i dlaczego akceptowalne> |

## Odwrócenie zmian

<dla każdej mutacji odwrócenie albo „nieodwracalne” z uzasadnieniem>

## Strategia testów

<podejście i obszary; komendy są w planie>

## Raport spójności

| Sprawdzenie | Status | Uwagi |
|---|---|---|
| <sprawdzenie> | <OK / luka> | <uwagi> |
````

Po odpowiedziach usuń sekcję „Pytania otwarte” (decyzje trafiają do „Decyzji”) i ustaw `Plan:`.

## Nagłówki planu przy specu

Plan zawiera dosłowne nagłówki `Cel` (z odnośnikiem do speca), `Kryteria akceptacji (mapowanie)` (kryterium speca → kroki →
dowód; kryteria i mapa FRD są w specu), `TESTY` (komendy), `Dokumentacja i wiedza`, a także kroki z kolumną ryzyka, zasoby,
wersję, sha FRD i założenia z `LUKI` (przy FRD), Progress i Wynik ([plan-details](plan-details.md)).
