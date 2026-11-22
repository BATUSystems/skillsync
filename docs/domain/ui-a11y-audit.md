---
title: 'Audyt dostępności `a11y` (ui-verify): pomiar z pikseli, UNVERIFIED zamiast zgadywania, wyjątki i zależności'
tags: [skillsync, ui-verify, a11y, wcag, verification, exit-codes, exceptions]
paths: ['library/skills/ui-verify/scripts/a11y.mjs', 'library/skills/ui-verify/scripts/lib/a11y-*.mjs', 'library/skills/ui-verify/fixtures/a11y/**', 'library/skills/ui-verify/references/a11y.md', 'tests/ui_verify/a11y-*.test.mjs']
source: spec CORE-1140-T2 rewizja 4 (skillsync-ops, docs/specs) i kod a11y.mjs oraz lib/a11y-*.mjs w skillu ui-verify
verified: 2026-11-22
---
Reguła: audyt dostępności `a11y` mierzy kontrast tekstu z renderowanych pikseli, a nie ze składania kolorów warstw.
Brak pomiaru daje UNVERIFIED (kod 3), nigdy zgadywany PASS. Wyjątki i ręczne potwierdzenia zmieniają wynik tylko
z jawnym `reason` i terminem. Wyjątek zamienia FAIL w EXCEPTED, a potwierdzenie zamienia UNVERIFIED o przyczynie
pomiarowej w CONFIRMED. Oba liczą się jak PASS, ale są wymienione w raporcie z kluczami.

Dlaczego pomiar z pikseli: składanie `background-color` po łańcuchu przodków pomija pozycjonowane rodzeństwo,
nakładki, pseudoelementy, obrazy, gradienty i color-scheme, a grupowa semantyka `opacity` różni się od mnożenia
alf. Naiwny odczyt koloru samej półprzezroczystej warstwy daje inny werdykt niż pomiar z pikseli. Przypadek
`#layer-fail` w fikstury `contrast` (stosunek 2,52, FAIL) pokazuje to, a `naiveLayerRatio` jest tylko do testu
porównawczego (`library/skills/ui-verify/scripts/lib/a11y-contrast.mjs:140`). Tło to próbki ze zrzutu z ukrytym
tekstem; ważność próbki sprawdza `elementFromPoint` (nakładka, przycięcie).

Dlaczego UNVERIFIED zamiast zgadywania: przyczyny pomiarowe (`opacity-group-with-paint`, `background-clip-text`,
`color-unconvertible`, `occluded`, `clipped`, `filter-or-blend`, `inline-important-color`, `autofill`,
`media-background`) to nie wynik, tylko brak dowodu. Przyczyny limitów (`page-too-tall`, `layout-shift`,
`truncated`, `sequence-truncated`, `tab-frame`, `mouse-capture-failed`, `no-samples`, `no-assertion`, `no-checks`)
nie mają ścieżki wyjątku ani potwierdzenia. Źródło listy: `library/skills/ui-verify/scripts/lib/a11y-schema.mjs:23-29`;
mapowanie na werdykt: `lib/a11y-page.mjs:553-561`.

Zasada wyniku: `counted` w raporcie pomija tylko EXEMPT i INFO, a EXCEPTED i CONFIRMED liczy jako PASS
(`library/skills/ui-verify/scripts/lib/a11y-report.mjs:57-60`). `combine` daje FAIL, jeśli jest choć jedno FAIL;
PASS tylko z niepustej listy samych PASS; w pozostałych przypadkach UNVERIFIED, także dla pustej listy
(`lib/verdict.mjs:10-15`). Stąd: bieg bez zmierzonej kontroli, z samymi EXEMPT albo bez asercji to kod 3.
Test: selftest `report` w `a11y.mjs` (linie 105-111).

Wyjątki i potwierdzenia: klucz `<reguła>|<page>|<selektor>`, dopasowanie przez `key`, przez `rule` i dokładny
`selector` albo (tylko console) `messageContains` co najmniej 8 znaków. Wpis po terminie jest ignorowany z
ostrzeżeniem `exception-expired`. Potwierdzenie ma `result: PASS`, `checkedBy`, `reason` i `expires` najwyżej
12 miesięcy od daty biegu (`library/skills/ui-verify/scripts/lib/a11y-exceptions.mjs:61-82`, dopasowanie i
zamiana w `applyExceptions`, linie 117-165). Wyjątek jest decyzją człowieka: agent dopisuje go tylko za wyraźną zgodą.

Progi: zejście poniżej 4,5 albo 3,0 wymaga `reason`, inaczej błąd 2; z `reason` ostrzeżenie `threshold-lowered`
(`lib/a11y-contrast.mjs:37-60`). Werdykt z niezaokrąglonego stosunku. Duży tekst: 24 px albo 56/3 px przy wadze
700 i więcej (`lib/a11y-contrast.mjs:6-7`, `isLargeText` w linii 32).

Mysz opt-in: `focus.mouse` wymaga jawnej listy do 20 selektorów. `report` daje INFO, `forbid` zamienia widoczną
ramkę w FAIL, `require` odwrotnie (`lib/a11y-focus.mjs:28-35`). Kliknięcie wykonuje akcje strony, więc
audyt nigdy nie klika sam z siebie.

Tryb `--expect`: kod wyjścia wynika wyłącznie z porównania z `expected.json` (0 zgodne, 1 niezgodność elementu lub
progu, 3 element nieobecny albo UNVERIFIED; `lib/a11y-expect.mjs:95-96`). Zgodny FAIL celowej fikstury to zgodność.

Zależności i granice kodu: `a11y.mjs` używa tych samych modułów co `measure` i `capture` skilla (`deps`,
`runtime`, `chrome`, `playwright-adapter`, `policy`, `out`, `verdict`, `cli`), ten sam cache i `package.json`
(`library/skills/ui-verify/scripts/a11y.mjs:13-34`, `resolveCacheDir` i `loadImaging` w linii 247-249). Nie importuje
`color.mjs`: jego `blend` zaokrągla kanały, a własna arytmetyka zmiennoprzecinkowa jest w `a11y-contrast.mjs`. Kolor,
którego nie da się zamienić na sRGB, to UNVERIFIED. Tekst ze strony to dane niezaufane: `cleanText` czyści znaki
sterujące i bidi oraz tnie do limitu (`lib/a11y-schema.mjs:38-49`).

Limity i granice są w `library/skills/ui-verify/references/a11y.md`. Kontrast elementów nietekstowych (WCAG 1.4.11)
i wskaźnika fokusu (2.4.13) jest poza v1. Zamknięty shadow DOM i iframe z innego originu nie są audytowane.
