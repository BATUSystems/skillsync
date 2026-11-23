---
title: 'Szacunki czasu (eta:): seria szacunku, przesunięcie o czekanie na użytkownika, rozbieżność estimates[] i sygnał estimate-error'
tags: [skillsync, eta, estimates, dashboard, timing, retro, estimate-error]
paths: ['skillsync_workflow/dashboard_estimates.py', 'skillsync_workflow/dashboard.py', 'library/skills/feature/references/measurement.md', 'library/skills/feature/references/retro-quality.md', 'library/workflow/cause-keys.yaml', 'tests/test_skillsync_dashboard_estimates.py', 'tests/test_skillsync_dashboard_eta.py']
source: 'spec i plan CORE-1150-ETA (23.11.2026, uwaga retro: szacowany koniec w aktach, odliczanie, rozbieżność szacunków bez czekania na użytkownika), kod i testy jak w polu paths'
verified: 2026-11-23
---
Reguła: szacunek terminu (`eta:` w raporcie postępu) jest rozliczany na SERIĘ (ta sama operacja i te same kroki), a opóźnienie spowodowane czekaniem na użytkownika nie jest błędem szacunku; szacunek za duży liczy się zawsze.

Fakty i decyzje:
- Jedna tabela końca serii (w `dashboard_estimates.py` i w `docs/dashboard-contract.md`, sekcja `eta`), stosowana w kolejności od góry: plan zamknięty, `eta: -`, `zrobione` wszystkich kroków, linia z `czeka:` (seria z krokami trwa, bez kroków kończy się), ten sam szacunek (trwa; inny termin przesuwa), inna operacja (nowa seria), pierwsza linia po reakcji z „w toku” kroku (trwa raz), każda inna linia bez `eta:` (kończy). `build_eta` czyta tabelę na najnowszej linii, `build_estimates` dodatkowo z podglądem jednej linii (pominięte powtórzenie nie dzieli serii).
- Przesunięcie: szacunek z krokami przechodzi przez `czeka:` i po reakcji użytkownika `untilIso` = termin z checkpointu + zamknięte okna oczekiwania NA UŻYTKOWNIKA od ustawienia terminu (`shiftSeconds`); okno, którego każda część ma `(kto: agent)` (CI, LEASE), nie przesuwa i nie jest wyjmowane z błędu. Nowy `eta:` z innym terminem zeruje przesunięcie, powtórzony ten sam termin nie.
- Asymetria błędu (`estimates[].adjustedErrorSeconds`): przy opóźnieniu `max(0, error − userWait)`, przy skróceniu `error` bez korekt. Praca krótsza od szacunku przy łącznym czasie dłuższym przez czekanie daje `ontime` (mierzymy termin zakończenia, nie samą pracę).
- Stałe wyzwalacza 8 retro (`estimate-error`): 600 s, a przy szacunku za dużym także 30% `estimatedSeconds`; `estimateTotals` w `dashboard metrics` do linii „Szacunki:” w „Wyniku”.
- Seria zamknięta tylko zamknięciem planu kończy się na ostatnim checkpoincie, więc przy ostatniej linii z `eta:` daje `early`; to znane uproszczenie tabeli (wiersz 1).

Wyjątki i uwagi: serie bez kroków (spec, plan, review) mają wiersz `steps: []`, ale nie przeżywają `czeka:`, więc „czeka: CI (kto: agent)” w środku review kończy serię; brak `RUN=` = brak wierszy i `untilIso: null`.

Gdzie w kodzie: `skillsync_workflow/dashboard_estimates.py` (`fold_series`, `current_eta`, `build_rows`, `totals`), `skillsync_workflow/dashboard.py` (`eta_records`, `build_eta`, `build_estimates`).
