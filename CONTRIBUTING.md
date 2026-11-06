[English summary below](#english-summary)

# Współtworzenie Skillsync

Dziękujemy za chęć pomocy. Ten plik mówi, jak przygotować środowisko, sprawdzić zmianę i zgłosić ją.

Pull requesty z zewnątrz są mile widziane. CI uruchamia na każdym PR testy i `leakcheck`; PR trafia do przeglądu dopiero, gdy oba przejdą.

## Zgłoszenia

Błędy i propozycje zgłaszaj w GitHub Issues: co robisz, czego oczekujesz, co się dzieje, wersja (`skillsync --version`) i system. Podatności zgłaszaj wyłącznie według [SECURITY.md](SECURITY.md), nie w publicznym zgłoszeniu.

## Środowisko

Python 3.11 lub nowszy i PyYAML. Kod leży w pakiecie `skillsync_workflow/` (polecenia w `skillsync_workflow/cli.py`);
plik `skillsync` w korzeniu repo to wrapper, który uruchamia pakiet z checkoutu (`./skillsync …`).

> **TODO [I1]** instalacja deweloperska `uv tool install -e .` (po kroku pakowania).

## Testy i kontrole

Kody wyjścia kontroli: PASS i NOT_RUN = 0, FAIL = 1, BLOCKED = 2.

Te same kontrole uruchamia CI (`.github/workflows/ci.yml`) na Linuksie, macOS i Windows:

```bash
python3 -m py_compile skillsync skillsync_workflow/*.py
bash -n skillsync-ctl && bash -n scripts/smoke.sh
python3 tests/k1_grep.py
python3 -m unittest discover -s tests -p 'test_skillsync*.py'
```

Na Windows CI uruchamia te same polecenia przez `python` (bez `bash -n`) i `scripts/smoke.ps1` zamiast `scripts/smoke.sh`.

Zasady testów:

- testy tworzą projekty, warstwy i config maszyny w katalogach tymczasowych; nie czytają ani nie zapisują Twojego configu
  i Twoich warstw;
- test zależny od systemu ma jawny `skipUnless` z powodem; skip bez powodu nie przejdzie przeglądu;
- nowe zachowanie `link` sprawdzaj najpierw `skillsync link <projekt-testowy> --dry-run` na projekcie testowym.

Mapa „co gdzie zmieniać” (warstwa core, ustawienia workflow, komendy, generator): [docs/workflow.md](docs/workflow.md),
sekcja „Gdzie zmieniać, co”.

## Zasady treści

To repo jest publiczne. Każda zmiana (kod, dokumentacja, przykłady, nazwy plików, opisy commitów) musi być neutralna:

- bez nazw firm, klientów i ich projektów; w przykładach używaj `acme`, `example.com`;
- bez ścieżek z Twojej maszyny (katalog domowy z nazwą użytkownika, profil Windows); pisz `~/…` lub `<projekt>`;
- bez danych osobowych i sekretów.

Przed wysłaniem uruchom `skillsync leakcheck . --target core` (szczegóły: [docs/security.md](docs/security.md)).
CI uruchamia go ponownie i blokuje PR z wyciekiem.

## Zmiana kontraktu

Nazwy skilli i ról, ścieżki artefaktów, klucze manifestu i configu, kody wyjścia i schematy JSON to kontrakt.
Zmiana wymaga testów, aktualizacji `docs/` i wpisu w `CHANGELOG.md` (sekcja **Breaking** przy zmianie niezgodnej).

## Commity i PR

Opisy commitów w stylu [Conventional Commits](https://www.conventionalcommits.org/) (`feat(...)`, `fix(...)`, `docs(...)`).
Jeden PR = jedna spójna zmiana; opis mówi, co i dlaczego, oraz jak to sprawdzono.

Zadania CI opisuje [development](docs/development.md#ci). Zadanie `leakcheck` działa tylko w pull requestach repo
`BATUSystems/skillsync`.

---

## English summary

External pull requests are welcome; CI runs the tests and `leakcheck` on pull requests. You need Python 3.11+ and PyYAML;
run Skillsync from the checkout (`./skillsync`), run the checks from „Testy i kontrole” (`py_compile`, `tests/k1_grep.py`, unit tests), keep all content neutral (no company or client names,
no local paths, no personal data), run `skillsync leakcheck . --target core` before opening a PR, and
update `docs/` and `CHANGELOG.md` for any contract change. Tests use temporary directories only and never touch
your own config or layers. Report bugs in GitHub Issues; report vulnerabilities only as described in
[SECURITY.md](SECURITY.md).
