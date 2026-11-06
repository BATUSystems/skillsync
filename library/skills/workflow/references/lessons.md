# Indeksowane lekcje

Poziomy: produktowe `docs/lessons` w projekcie (pułapki tego repo); wspólne organizacji — Acme: repo `Lessons`
(Azure `acme/Shop/Lessons`, format w jego README; szczegóły: skill `acme`), pozostałe: biblioteka
`workflow/lessons`; środowisko agentów: skill `agent-runtime-pitfalls`. Klasyfikacja: `knowledge.md`.
Lekcje czytają głównie podumysły — orkiestrator podaje im w zleceniu ścieżki rekordów z `skillsync route --files`.
index.yaml ma version: 1 i lessons: listę path/title/tags/paths. Ścieżka jest względna
do indeksu, nie wychodzi poza katalog. Task router czyta indeks, a następnie wybrane rekordy.
Nie wczytuj wszystkich lekcji i nie wpisuj całej historii do _base.md.

Rekord Markdown ma YAML title, tags (lista tematów), paths (opcjonalne wzorce).
Treść (forma wykonawcza dla agentów): Objaw → Przyczyna → Reguła → Jak wykryć; dowód (log, test, commit),
dotknięte obszary i granice aktualności. Pierwsza lekcja repo zakłada `docs/lessons/index.yaml`.
Popraw istniejący rekord zamiast tworzyć duplikat. Reguły bezpieczeństwa muszą zostać
również w instrukcji najbliższego obszaru. Doctor kontroluje indeks i rekordy.

Przykład:
```yaml
version: 1
lessons:
  - path: test-resource-isolation.md
    title: Izolacja zasobów testowych
    tags: [testing, concurrency]
    paths: ['**/conftest.py', '**/migrations/**']
```

Zapisuj wiedzę wynikającą z potwierdzonego doświadczenia. Nie dopisuj domysłów ani sekretów.
To dokumentacja workflow; aktualizacja pamięci Codexa pozostaje wyłącznie na jawne polecenie.

## Właściciel poprawki: jedno najmniejsze miejsce

Nowa lekcja albo poprawka po błędzie agenta ma jedno miejsce docelowe; każde inne miejsce zawiera co najwyżej odesłanie. Wyjątek bezpieczeństwa ma nadal jednego właściciela, ale skrót reguły z odesłaniem powtarza się w instrukcji obszaru, którego dotyczy. Gdy wiedzę wnosi zależność (biblioteka, narzędzie, zewnętrzny skill), jej właścicielem jest ta zależność. Pozorna potrzeba dwóch właścicieli: podziel regułę na dwie albo wykaż, że to jeden kontrakt w dwóch miejscach, i dołóż kontrolę pilnującą zgodności.

| Rodzaj defektu | Najmniejszy właściciel |
|---|---|
| niezmiennik lub granica bezpieczeństwa obowiązująca w całym projekcie | plik instrukcji projektu w bibliotece (`instructions/<projekt>.md`) |
| zły dobór instrukcji, lekcji lub wiedzy do zadania albo plików | reguła `routing` w `workflow/defaults.yaml` (lub manifest); narzędzie `skillsync route` |
| procedura konkretnej gałęzi przebiegu lub etapu | pojedyncza referencja w `skills/<skill>/references/` |
| udokumentowana pułapka techniczna | rekord w `docs/lessons` (w projekcie) albo `workflow/lessons` (wspólne dla wielu) |
| reguła biznesowa lub pojęcie domenowe | rekord `docs/domain`, glosariusz |
| fakt o konfiguracji lub repo sprawdzalny maszynowo | kontrola w `skillsync doctor` albo generowana mapa, a nie proza |
| narzędzie robi zły zapis lub omija strażnika | hook albo reguła uprawnień |
| brak powtarzalnego scenariusza | test lub benchmark (`workflow/benchmark.yaml`) |

Tabela obejmuje typy właścicieli używane w CLI i retrospektywie: `instruction`, `skill`, `route`, `test`, `hook` oraz `docs`.

**Najpierw zawodzący przypadek.** Zanim zmienisz regułę albo skill po błędzie agenta, przygotuj odtwarzalny przypadek (test, kontrolę `doctor` albo scenariusz benchmarku), który przed zmianą **najpierw zawodzi**, a po niej przechodzi. Duplikaty rozpoznawaj po znaczeniu błędu, nie po brzmieniu opisu.
Wyjątek: propozycja retrospektywy jakościowej z właścicielem `skill`, `instruction`, `docs` albo `route`, przyjęta przez „TAK P<n>” albo „TAK <run-id>/P<n>”, nie wymaga takiego przypadku ([retro-quality](../../feature/references/retro-quality.md)).
Drobna lekcja bez odtwarzalnego przypadku trafia jako rekord z dowodem, a nie jako nowa reguła rdzenia.
Decyzję o kandydacie `skillsync retro` (`approved`/`rejected`, dzień, `owner`, `case`, `ref`) zapisz w `docs/retro/decisions.yaml` projektu przez `skillsync retro-decide`;
`retro` oznacza go wtedy `decided`, a nawrót po dniu decyzji jako `recurred`.
