# Plan i punkty kontrolne

Plan ma cel, mierzalne kryteria, zakres/non-goals, etapy, kontrakty, testy, zasoby, ryzyka
dokumentację i wiedzę oraz otwarte decyzje. Mała naprawa może mieć pięć zdań; wielowarstwowa zmiana wymaga faz.
Polecenie realizacji uprzednio przedstawionej konkretnej listy zatwierdza ten zakres.
Plan z linią `Spec:` nie powtarza zakresu, kontraktów, migracji ani ryzyk: są w specu ([spec](../../feature/references/spec.md)).

W sekcji TESTY określ każdą warstwę, komendę/cwd, środowisko, przydział zasobów i dowód.
Kontrakty: wskaż plik i rzeczywistych konsumentów. Migracja: istniejące dane i rollback,
albo uzasadnione „nie dotyczy”. Ryzyka: awaria, skutek, waga, kontrola, właściciel i ryzyko rezydualne (dlaczego akceptowalne).
Dokumentacja i wiedza: dokumenty do aktualizacji, spodziewana wiedza domenowa, miejsce
zapisu i właściciel ([knowledge.md](knowledge.md)); „nie dotyczy” tylko z uzasadnieniem.
Terminologia domenowa: po odczytaniu rekordów `load: bootstrap` z `skillsync route <projekt>`
wskaż terminy potwierdzone do dodania/aktualizacji oraz kandydatów wymagających wyjaśnienia
(hipoteza znaczenia, źródło/pytanie). Gdy brak nowych terminów, zapisz to z krótkim uzasadnieniem.
Do glosariusza trafiają wyłącznie definicje potwierdzone, według `knowledge.md`.

Krok ma stabilne id, kolumnę Exec (`inline|dispatch|group` + klasa modelu) i wpis w sekcji `Progress` z linią `PR:`
([konwencja](../../feature/references/plan-details.md)). Orkiestrator aktualizuje postęp dopiero po sprawdzeniu dowodów;
checkpoint wiąże je z fingerprintem aktualnych staged/unstaged/untracked zmian.
Przed wznowieniem sprawdź plan, źródła instrukcji, baseline, stare porażki i otwarte review.
Nie powtarzaj udanych kontroli na niezmienionym stanie, nie zachowuj PASS po zmianie kodu.
