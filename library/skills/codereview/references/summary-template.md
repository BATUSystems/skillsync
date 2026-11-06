# Szablon podsumowania code review

Plik: `.skillsync/reviews/<RRRR-MM-DD>-<PR-id|branch>.md`. Treść poniżej jest gotowa do wklejenia w Azure DevOps
(Markdown). Sekcje puste pomijaj (poza „Weryfikacja”: `NOT_RUN` też jest wynikiem i zostaje). Nie wklejaj sekretów, danych osobowych ani surowych logów.

```markdown
## Code review — PR #<id>: <tytuł>  (lub: zmiany przed PR — <branch>)
**Przeglądany stan:** <commit źródła> → <cel>, <N> plików, +<a>/−<d>  ·  **Zadania:** <KLUCZE Jira z tytułami>

### Rekomendacja: ZATWIERDZIĆ | ZATWIERDZIĆ Z UWAGAMI | NIE ZATWIERDZAĆ
<1–3 zdania uzasadnienia powiązanego z zakresem zadania>

### Zakres zadania a zmiana
| Wymaganie / kryterium | Status | Uwagi |
|---|---|---|
| <z Jira / planu> | ✅ spełnione / ⚠️ częściowo / ❌ brak / ❓ nie da się ocenić | <plik:linia lub komentarz> |

Poza zakresem (jeśli jest): <zmiany niepowiązane z zadaniem>

### Do poprawy przed merge (blokujące)
1. **`plik:linia`** — <problem> → <skutek/scenariusz> → <proponowana poprawka>

### Warto poprawić
1. **`plik:linia`** — <uwaga> → <propozycja>

### Drobne / nit
- `plik:linia` — <uwaga>

### Pytania do autora
- <pytanie wynikające z niejasności, nie z braku starań reviewera>

### Co jest dobre
- <konkretnie, tylko jeśli prawdziwe>

### Weryfikacja
| Komenda / kontrola | cwd | Status | Exit | Dowód | Nowa / zastana |
|---|---|---|---|---|---|
| <np. `skillsync validate . --profile final`> | <ścieżka> | PASS / FAIL / BLOCKED / NOT_RUN | <kod> | <log/ścieżka> | <dla FAIL> |

- Przeanalizowano: <diff, konsumenci, instrukcje projektu>. CI PR: <status + źródło; to nie dowód poprawności>.
- Ograniczenia: <czego nie sprawdzono: brak dostępu do Jira, brak środowiska, nie uruchomiono UI itd.>
```

Zasady treści: każda uwaga ma lokalizację, skutek i propozycję; uwagi o kodzie zastanym oznacz „(istniejące, poza zakresem)”.
W trybie „przed PR” nagłówek brzmi „Gotowość do PR”, a rekomendacja: gotowe do PR / po drobnych poprawkach / wróć do poprawek;
dodaj propozycję opisu PR (3–5 zdań: co, dlaczego, jak testowano) tylko gdy użytkownik o nią prosi.
