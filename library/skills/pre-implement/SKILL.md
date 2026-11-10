---
name: pre-implement
description: "Readiness gate before risky changes (contracts, API, data, migration): checks real consumers, rollout, tests; verdict READY/NEEDS_CHANGES/BLOCKED. Read-only."
---

# Gotowość do implementacji

Bramka przed ryzykowną zmianą: kontrakt, API, dane, migracja, wysokie ryzyko. Wejście: plan, a gdy plan ma linię `Spec:` także spec; najbliższe instrukcje modułu; reguły kontraktów ([kontrakty](../workflow/references/contracts.md)).

Kontrola strukturalna: `skillsync readiness <projekt> --plan <plik> --risk high --files <ścieżki>`, obejmuje sekcje planu i miejsca zmian. Sam raport nie zwalnia z czytania konsumentów.

Procedura w tej kolejności:
1. Określ cel, kryteria i zakres minimalny; wskaż, kto faktycznie z tego korzysta.
2. API, DTO i zdarzenia: zgodność klientów; wdrożenie przy współistnieniu starej i nowej wersji.
3. Dane zastane: migracja, transakcje, cofanie, uprawnienia, równoległość.
4. Każde ryzyko ma swoją kontrolę lub test; luki i rozstrzygnięcia zapisz jawnie.
5. Werdykt: `READY`, `NEEDS_CHANGES` albo `BLOCKED`, z dowodami w formie plik:linia. Nie dopisuj testów, które nie istnieją.

Wagi znalezisk: `Critical` blokuje start; `Warning` wymaga pomostu kompatybilności (alias, podwójne emitowanie, reeksport). Luki grupuj: blokujące, ważne, opcjonalne.

Wykonawca i raport: podumysł tylko do odczytu (`reviewer` z briefem pre-implement), zlecony przez orkiestratora. Raport
zapisuje orkiestrator (rola nie ma Write) w `.skillsync/runs/<run-id>/pre-implement.md`, a przy kilku bramkach w przebiegu
`pre-implement-<krok>.md`; pierwsza linia raportu to `Werdykt: READY|NEEDS_CHANGES|BLOCKED`. Strona statusu i narzędzia
rozpoznają raport po nazwie `pre-implement*`.

Tryb tylko do odczytu: nie zmieniaj planu, speca ani kodu; zmiany zaproponuj w raporcie. Zapis wniosków należy do orkiestratora: kontrakty, migracja i ryzyka trafiają do speca, jeśli istnieje, zanim ruszy pętla `spec` i zgoda GO plan, bo po tym `Spec-sha` chroni treść; inaczej — do planu.

Przy dużej zmianie zastosuj [plan-large](../feature/references/plan-large.md). Gdy zwykły plan odsłoni kontrakt albo dane, zadaj pytanie o spec zamiast samodzielnie rozszerzać zakres. Mała, lokalna zmiana wystarcza krótkim zapisem w planie; speca nie tworzysz, gdy bramka `plan-large` tego nie wymaga.

Nowej zgody wymaga tylko wyjście poza autoryzowany zakres.
