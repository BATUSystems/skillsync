---
title: "Pełny bieg testów pada na strażniku real-state, gdy ktokolwiek dotknie gitem prawdziwych klonów warstw"
tags: [testing, layers, lease, dispatch, flaky, git]
paths: ['tests/layers_helpers.py', 'tests/test_skillsync_workflow.py']
source: 'INST-1, bieg pełny 2026-11-09 10:22–11:10: po 1 FAIL w każdym trybie, za każdym razem inny test; mtime .git/index klonów warstwy core i warstwy organizacji 10:39 i 10:53'
verified: 2026-11-09
---
**Objaw:** w pełnym zestawie pada jeden losowy test z asercją „test touched real Skillsync state dirs”
(`tests/layers_helpers.py:real_state_snapshot`, cleanup w `test_skillsync_workflow.py`). Padający test zmienia się między biegami,
a sam w izolacji przechodzi.

**Przyczyna:** strażnik porównuje rozmiar i mtime każdego pliku pod `~/.local/share/skillsync` przed i po teście. `git status`,
`git log`, `layers check`, `leakcheck` na configu maszyny albo `skillsync pull` na prawdziwych klonach warstw odświeżają
`.git/index` (albo `FETCH_HEAD`), więc test, który akurat biegł, dostaje różnicę. Sprawcą jest proces zewnętrzny (inna sesja,
Dyspozytor, usługa watch), nie kod pod testem.

**Reguła:**
- W trakcie `LEASE` pełnego biegu nikt (karty, Dyspozytor, watch) nie dotyka gitem ani komendami skillsync prawdziwych klonów warstw
  i configu maszyny. Usługę watch na czas biegu zatrzymaj (`skillsync-ctl stop`) albo przyjmij ryzyko jednego FAIL.
- Taki FAIL rozstrzygaj dowodem, nie powtórką w ciemno: `find ~/.local/share/skillsync -newermt <start biegu>` pokazuje dotknięte
  pliki, a nanosekundowe mtime z komunikatu asercji przeliczone na czas wskazują chwilę ingerencji. Potem powtórz tylko padnięty moduł
  (`python3 -m unittest discover -s tests -p "test_skillsync_<moduł>.py"`; moduły importują się nawzajem, więc nie przez `tests.<moduł>`).
- Dowodem końcowym pełnego zestawu jest CI PR, które ma własny, czysty HOME.

**Jak wykryć:** komunikat „test touched real Skillsync state dirs” z nazwą pliku `.git/index` lub `FETCH_HEAD` w diffie.
