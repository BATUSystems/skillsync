---
title: "Asercje E2E na układzie CSS i strukturze DOM starego silnika dają FAIL dla poprawnej implementacji: asercje tylko na słowniku kontraktu"
tags: [testing, e2e, prototype, css, dom, oracle, rewrite]
paths: ['tests/e2e/prototype/**', 'tests/test_skillsync_prototype_e2e.py']
source: 'przegląd skryptów E2E prototypów z 2026-11-05: asercje zależne od struktury rodzica (wykluczenie „sąsiadów” elementu w E3-20), od siatki CSS i liczby wierszy (D23-układ-wiersze) oraz od zmiennej CSS spoza słownika; poprawka w commicie 514fae6 (wykluczenie `e !== c && !e.contains(c)`, pomiar przewijania poziomego i nachodzenia liści zamiast liczby wierszy, zmienna w słowniku kontraktu)'
verified: 2026-11-05
---
**Objaw:** scenariusz E2E kończy się FAIL na zachowaniu zgodnym z kontraktem. Przykłady z przeglądu: liczba wierszy paska
i `grid-template-areas` w D23-układ-wiersze, wykluczenie „sąsiadów” elementu w E3-20. Nowa implementacja zachowuje się
poprawnie, ale asercja mierzy inny układ DOM i CSS.

**Przyczyna:** asercja opisuje sposób, w jaki stary silnik układa elementy: strukturę rodzica, siatkę, liczbę wierszy,
zmienną CSS spoza kontraktu. Nie opisuje zachowania widocznego dla użytkownika. Wyrocznia jest wtedy dopasowana do
implementacji, a nie do kontraktu, i blokuje poprawny przepis.

**Reguła:**
- Asercja E2E odwołuje się do identyfikatorów ze słownika kontraktu (klasy, `data-*`, role i `aria-*`, symbole `ic-*`)
  oraz do zachowania: widoczność, przewijanie poziome, nachodzenie elementów-liści, fokus, komunikaty, stan po akcji.
- Nie licz wierszy siatki, nie czytaj `grid-template-areas`, nie zakładaj konkretnego rodzica elementu. Wykluczenie
  elementu zapisuj jako `e !== c && !e.contains(c)`.
- Zmienna CSS albo klasa w asercji musi być w słowniku kontraktu. Jeśli jej tam nie ma, dopisz ją do słownika
  albo usuń asercję. Nie zostawiaj jej po cichu.
- FAIL scenariusza z pomiarem układu: najpierw sprawdź, czy asercja dotyczy zachowania z kontraktu. Dopiero potem
  uznaj defekt silnika.

**Jak wykryć:** `grep -nE "gridTemplate|grid-template|parentElement|nth-child|getPropertyValue\('--" tests/e2e/prototype/*.mjs`.
Każde trafienie sprawdź względem słownika kontraktu. `parentElement` przy szukaniu tła w łańcuchu przodków bywa
uzasadniony; przy wykluczaniu elementów z listy jest sygnałem do przeglądu.
