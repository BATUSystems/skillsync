---
name: angular-patterns
description: Cross-project Angular patterns with a focus on server-side table handling (sorting, filtering, pagination).
user-invokable: true
---

# Angular Patterns (Cross-Project)

Ten skill zawiera neutralne, wieloprojektowe wzorce Angular dla widoków tabelarycznych.

## Kiedy używać

Użyj tego skilla, gdy implementujesz:

- server-side sorting,
- server-side filtering,
- server-side pagination,
- debounced search,
- utrzymanie stanu listy po zmianach filtrów i paginacji.

## Core pattern: Server-driven table state

1. Trzymaj stan tabeli w jednym obiekcie (`page`, `pageSize`, `sort`, `filters`).
2. Każda zmiana filtra/sortu resetuje stronę do pierwszej.
3. UI opiera się wyłącznie o odpowiedź backendu (`count`, `results`, `next`, `previous`).
4. Nie mieszaj client-side i server-side filtrowania dla tej samej listy.

## Core pattern: Debounced text filters

- Text inputy: debounce (`300-500ms`) + `distinctUntilChanged()`.
- Selecty i checkboxy: natychmiastowy reload.
- Buduj parametry requestu tylko z aktywnych filtrów.

## Core pattern: Request contract hygiene

- Typuj DTO request/response.
- Dla pustych filtrów nie wysyłaj zbędnych parametrów.
- Pilnuj spójności nazw query params między frontendem i backendem.

## Core pattern: UX bez regresji

- Pokaż loading state podczas requestu.
- Obsłuż błąd i retry bez utraty stanu filtrów.
- Empty-state musi rozróżniać „brak danych” i „brak wyników po filtrze”.

## Project-specific variants

Pliki `references/org-*.md`, jeśli są, to rozszerzenia warstwy organizacji: przeczytaj te, które pasują do projektu.
