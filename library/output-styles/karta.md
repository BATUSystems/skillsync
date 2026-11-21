---
name: karta
description: "Zwięzła forma raportów sesji roboczej (karty) dla Dyspozytora: STATUS i REPORT z dowodami."
keep-coding-instructions: true
---
# Styl odpowiedzi: karta

Ten styl dotyczy wyłącznie formy raportów sesji roboczej (karty) przekazywanych Dyspozytorowi.

## Zasady

- Pisz zwięźle i rzeczowo: fakty zamiast opisu procesu, bez wstępów i podsumowań powtarzających treść.
- Raport zaczynaj od linii `STATUS:` z jednym ze stanów: gotowe, w toku, zablokowane, czeka na decyzję.
- Po niej podaj sekcję `REPORT:` o stałym układzie:
  1. Co zrobione: lista zmienionych plików i zachowania, po jednej linii na pozycję.
  2. Dowody: dla każdej kontroli komenda, katalog, kod wyjścia i wynik (PASS, FAIL, BLOCKED, NOT_RUN).
  3. Blokery: co stoi na przeszkodzie i czego brakuje, albo "brak".
  4. NOT_RUN: czego nie uruchomiono, z przyczyną i komendą dla runnera.
- Wniosek o wyniku opieraj na końcowym stanie i dowodzie, nie na zapowiedzi.
- Pytanie do decyzji formułuj jednym zdaniem z proponowaną odpowiedzią.

Styl nie zmienia sposobu pracy z kodem i narzędziami, tylko formę raportu.
