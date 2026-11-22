---
name: karta
description: "Zwięzła forma raportów sesji roboczej (karty) dla Dyspozytora, zgodna z gramatyką wiadomości kart."
keep-coding-instructions: true
---
# Styl odpowiedzi: karta

Ten styl dotyczy wyłącznie formy raportów sesji roboczej (karty) przekazywanych Dyspozytorowi. Gramatykę wiadomości definiuje protokół kart (skill `feature`, referencja `multi-session`); styl jej nie zmienia.

## Wymagane elementy wiadomości

- Pierwsza linia: `date: <dosłowny wynik polecenia date, uruchomionego tuż przed wysłaniem>`. Godzin nie podawaj z pamięci.
- Druga linia: `STATUS <paczka> <akt> <czeka-na>` (czeka-na: użytkownik, zasób, dyspozytor albo nic) albo `REPORT DONE|BLOCKED <paczka> commit=… checks=… debt=… retro=…`.

## Forma treści

- Pisz zwięźle: fakty, liczby i ścieżki zamiast opisu procesu, bez wstępów i powtórzeń.
- Każdą kontrolę podaj z komendą, katalogiem, kodem wyjścia i wynikiem (PASS, FAIL, BLOCKED, NOT_RUN).
- Czego nie uruchomiono, oznacz jawnie jako NOT_RUN z przyczyną i komendą dla runnera.
- Blokery wymień osobno albo napisz "brak". Pytanie zadaj jednym zdaniem z proponowaną odpowiedzią.

Styl nie zmienia sposobu pracy z kodem i narzędziami, tylko formę raportu.
