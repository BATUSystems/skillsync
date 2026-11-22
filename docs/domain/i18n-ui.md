---
title: "Język interfejsu (klucz manifestu `language`), katalog tekstów `srv.`/`ui.`, liczba mnoga i kontrakt `lang`/`texts` statusu"
tags: [skillsync, i18n, dashboard, manifest, catalog, plural, renderer]
paths: ['skillsync_workflow/i18n.py', 'skillsync_workflow/data/i18n/**', 'skillsync_workflow/dashboard.py', 'skillsync_workflow/dashboard_http.py', 'skillsync_workflow/status.py', 'skillsync_workflow/protocol_tokens.py', 'library/skills/dashboard/assets/dashboard-render.js', 'library/skills/dashboard/assets/app.js', 'library/skills/dashboard/descriptions*.json', 'skillsync_workflow/quality.py', 'skillsync_workflow/test_gate.py', 'scripts/sync_renderer_defaults.py', 'tests/test_skillsync_i18n.py', 'tests/test_skillsync_dashboard_i18n.py', 'tests/test_skillsync_dashboard_renderer_i18n.py', 'tests/test_skillsync_dashboard_renderer_compat.py', 'tests/test_skillsync_dashboard_tokens.py', 'tests/test_skillsync_sync_renderer_defaults.py']
source: "odczyt skillsync_workflow/i18n.py (`resolve_language` :30, `plural_category` :43, `pick_plural` :56, `fmt` :64, `_load` :73, `catalog` :90, `catalog_for_client` :95, `catalog_for_prefix` :101, `t` :106); skillsync_workflow/dashboard.py (`build_status`: `resolve_language` :2178, pole `lang` :2285, `texts` :2325-2326); skillsync_workflow/status.py (findings :573-575); skillsync_workflow/dashboard_http.py (`PROXY_REFUSED` :102, `page_with_language` :521, `descriptions_path` :546, `load_descriptions` :560, `/api/descriptions` :959-960); library/skills/dashboard/assets/dashboard-render.js (`DEFAULT_TEXTS` :43-360, `pluralCategory` :367, `pickForm` :376, `setLanguage`/`raw`/`tr` :588-606, `hostText` dla live/noConnection :615-624, eksport `t`/`lang`/`defaultTexts` :1983-2010); library/skills/dashboard/assets/app.js (`lang` na `<html>` :131-132, `/api/descriptions` :176); library/skills/dashboard/descriptions.en.json; scripts/sync_renderer_defaults.py; skillsync_workflow/protocol_tokens.py (`TOKENS`, `PL_TO_EN`); skillsync_workflow/common.py (`_SECRET_KEY_VALUE` :459-461); testy tests/test_skillsync_i18n.py, tests/test_skillsync_dashboard_i18n.py, tests/test_skillsync_dashboard_renderer_i18n.py, zamrożone kopie tests/fixtures/renderer-1.5.0, renderer-1.11.0, renderer-1.12.0"
verified: 2026-11-22
---
Reguła: interfejs projektu (strona statusu, teksty serwera, atrybut `lang` strony, dymki, `<title>`) ma język z klucza manifestu `language`. Klucz steruje **wyłącznie interfejsem**. Język skilli, ról i instrukcji nie zależy od niego: wybiera go warstwa przez `layer_rules` (`skillsync_workflow/layers.py`). Obsługiwane wartości: `pl` (domyślny) i `en`.

## Klucz `language` w manifeście

- Brak klucza albo `null` = `pl` bez ostrzeżenia (`resolve_language`, `i18n.py:34-36`).
- Wartość będąca dokładnie jednym z `pl`, `en` = ta wartość.
- Każda inna wartość (nieznany kod, `EN`, pusty napis, liczba, bool, lista, słownik) = `pl` i ostrzeżenie. Ostrzeżenie trafia do `findings` `skillsync status` (`status.py:573-575`), nie do błędu. Tekst ostrzeżenia ogranicza długość i usuwa znaki sterujące (`_safe`).
- Status strony (`dashboard.build_status`) bierze język z tej samej funkcji i pole `lang` jest zawsze w JSON-ie.

## Katalog tekstów

- Pliki: `skillsync_workflow/data/i18n/<lang>.json` (`pl.json`, `en.json`) plus fragmenty `<lang>.<przestrzeń>.json`. Loader (`_load`) scala je w jeden płaski słownik.
- Fragment może zawierać tylko klucze własnej przestrzeni (`<przestrzeń>.…`); klucz spoza przestrzeni albo powtórzony w innym pliku = `ValueError`. Pakiety dokładają klucze osobnym plikiem, bez konfliktów w pliku głównym.
- Przestrzenie:
  - `srv.` — teksty budowane przez serwer (zdania JSON-a statusu, np. `srv.status.*`, `srv.http.*`); tłumaczone po stronie serwera wg języka projektu. Nie trafiają do renderera.
  - `ui.` — teksty renderera; `catalog_for_client` wybiera je do pola `texts` statusu.
  - `proto.` — przestrzeń dla prototypów (`catalog_for_prefix`). W tej paczce nie ma jeszcze pliku fragmentu `proto`.
- Wartość to napis albo obiekt form liczby mnogiej. Pl ma formy `one`, `few`, `many` (wymagane) i opcjonalnie `other`; en ma `one`, `other`.
- Wybór formy: `plural_category(lang, n)` (pl: 1 = `one`; 2–4 poza 12–14 = `few`; reszta `many`; en: 1 = `one`, reszta `other`; liczby ujemne, niecałkowite i `bool` = `other`). `pick_plural` bierze kategorię, potem `other`, potem `many`. Renderer robi to samo przez `Intl.PluralRules` z zapasową regułą własną.
- Parametry: `{nazwa}`. `fmt` podstawia raz; wstawione wartości nie są czytane ponownie. Nieznany parametr zostaje tekstem. Nieznany klucz `t` zwraca sam klucz.
- Język nieobsługiwany (`de`) daje katalog polski.

## Kontrakt statusu

- `lang`: zawsze obecne; `pl` albo `en`.
- `texts`: tylko gdy `lang` ≠ `pl`; to katalog `ui.*` języka (napisy albo obiekty form). Przy `pl` pola nie ma, a renderer używa wbudowanego polskiego.
- Pola są addytywne (zob. [dashboard-contract](../dashboard-contract.md), sekcja „Język i teksty interfejsu”).

## Renderer

- Priorytet tekstu `ui.*`: `texts` ze statusu (gdy język inny niż pl) > `DEFAULT_TEXTS` (wbudowany polski). Wyjątek: klucze `live`, `liveTip` i `noConnection` dodatkowo nadpisuje `deps.texts` z hosta (funkcja `hostText` w `create()` renderera); `deps.texts` nie nadpisuje innych kluczy `ui.*`. `deps.texts` to obiekt albo funkcja `(lang) => obiekt`: funkcja jest wołana przy każdym odczycie (także przed pierwszym statusem, z `pl`, i po zmianie języka), a wyjątek, rzucający getter lub wynik inny niż obiekt to brak nadpisania; renderery starsze niż 1.13.0 funkcji nie znają (host podaje im obiekt).
- `DEFAULT_TEXTS` to blok między znacznikami `// <i18n-defaults>` i `// </i18n-defaults>` generowany z `pl.json` przez `scripts/sync_renderer_defaults.py`. Nie edytuje się go ręcznie; `--check` kończy się kodem 1 przy rozjeździe.
- API renderera: `board.t(key, params, n)` (także `trRich` wewnętrznie), `board.lang()` (język ostatniego statusu), `DashboardRenderer.defaultTexts`.
- `app.js` ustawia `<html lang>` z `lang` statusu (pl, gdy pola nie ma).
- Teksty z katalogu trafiają do DOM tylko przez `textContent`. Fragment w odwrotnych apostrofach (`` `ścieżka` ``) to węzeł `mono`, nie HTML.
- Wartości katalogu nie zawierają `<` ani `>` (test `test_no_markup_in_catalog_values`).

### Pułapka: wartości `ui.*` a maskowanie sekretów

`common.redact` maskuje wartość po nazwie klucza: `token`, `secret`, `password`, `passwd`, `api_key` itd., po którym idzie `=` albo `:` (`common.py:459-461`). Tekst katalogu, który wygląda jak `token: …` lub `password: …` (słowo kluczowe, dwukropek), zostanie zamaskowany w każdym wyjściu, które przechodzi przez `redact`. Przy dodawaniu tekstu sprawdzaj, czy nie tworzy takiego wzorca (np. „Token: wpisz…”).

## Opisy dymków

- `library/skills/dashboard/descriptions.json` (pl) i obok `descriptions.en.json`.
- Dla języka innego niż pl serwer czyta `descriptions.<lang>.json` z tej samej warstwy (`descriptions_path`). Brak pliku, plik niezwykły albo dowiązanie = polski `descriptions.json`.
- Host, który kopiuje assety skilla (np. wtyczka), musi skopiować też `descriptions.<lang>.json` obok `descriptions.json`; inaczej dostanie polskie dymki.

## Strona HTML

- `page_with_language` zamienia `<html lang="pl">` na kod języka i `<title>` na `ui.page.title` tylko dla języka innego niż pl. Dla pl zwraca bajty bez zmian.
- Nagłówek „Wczytuję status…” przed pierwszym statusem zostaje po polsku (poza zakresem tej funkcji).

## Parser i tokeny

- Parser stanu (`dashboard.py`, `status.py`) czyta angielskie odpowiedniki tokenów, ale nie przez jeden mechanizm. `protocol_tokens.RUN_LINE` czyta `Przebieg:` (w całym tekście) i `Run:` (tylko w nagłówku przed pierwszym `## `). Pozostałe angielskie formy są wpisane w regexy `dashboard.py`: `IN_PROGRESS_EN_RE` i `DONE_EN_RE` (kotwiczone na początku segmentu; polskie `IN_PROGRESS_RE` i `DONE_RE` zostają tylko polskie, a `_keyword_match` daje polskiemu pierwszeństwo), `WAIT_RE`, `ETA_RE`, `ETA_AT_RE`, `TRWA_RE`, `WHO_RE`, `QUESTIONS_HEADING`, `ACCEPTED_RE`, `READINESS_DATE_RE`, `RETRO_ANSWER_RE`, oraz przez `HEADER_ALIASES` (`Run:`, `Prototype:`, `Date:`, `Handoff:`). Pole `en` tokenu w `protocol_tokens.TOKENS` nie jest jeszcze czytane przez parser jako ogólna tabela aliasów.
- Sekcje ryzyka `Kontrakty`/`Migracja`/`Dane` (`RISKY_SECTIONS` w `dashboard.py`, `PRE_IMPLEMENT_SECTIONS` w `status.py`) są czytane tylko po polsku. Angielskie `Contracts`/`Migration`/`Data` wrócą z osobną paczką aliasów.
- Szczegółowa lista form, które parser czyta w każdym języku: [dashboard-contract](../dashboard-contract.md), sekcje „Format checkpointu” i „assumptions”.
- Tokeny rodzaju `written` (np. `— odpowiedź:`) zapisują się po polsku; ich zapis w języku `en` nie jest częścią tej funkcji.

## Czego ta funkcja nie obejmuje

- Języka skilli, ról i instrukcji (osobna paczka tłumaczeń).
- Własnego interfejsu wtyczek (wtyczka nadal wybiera własne teksty przez `deps.texts`).
- Dymków parsera (segmenty `zrobione`, `w toku`, `raport`).
- Trzeciego języka: obsługiwane są `pl` i `en`.
- Angielskiego `Run:` w `quality._PLAN_RUN` i `test_gate._RUN_LINE`. Oba czytają wyłącznie `Przebieg:`. Przez `test_gate.plan_for_run` idzie też `status.plan_state_for_run` (`planState` zgód CP/READY) i stan planu przebiegu. Odczyt `Run:` w tych miejscach przenosi paczka aliasów tokenów protokołu. Do jej scalenia angielski `Run:` działa w dashboardzie i w `skillsync status`, ale nie w tych dwóch miejscach ani w `planState` zgód.

## Testy

- `tests/test_skillsync_i18n.py`: walidacja `resolve_language` (ostrzeżenie bez echa niezaufanej wartości), reguły liczby mnogiej, parytet kluczy i parametrów pl/en, brak polskich znaków w en, brak `<`/`>`, fragmenty (przestrzeń, duplikat), `fmt` bez ponownego czytania wartości.
- `tests/test_skillsync_dashboard_i18n.py`: pola `lang`/`texts` w statusie (brak klucza = pl bez `texts`), angielskie teksty serwera (akty, zgody, błędy HTTP, odmowy hosta), liczba mnoga, każdy klucz `srv.*` użyty w kodzie jest w obu katalogach i nie ma nieużywanych.
- `tests/test_skillsync_dashboard_renderer_i18n.py`: renderowanie scenariuszy po angielsku (bez polskich znaków w danych tłumaczonych), zgodność `DEFAULT_TEXTS` z `pl.json`, reguły liczby mnogiej w JS i Pythonie, wrogie parametry zostają tekstem.
- `tests/test_skillsync_dashboard_renderer_compat.py`: zamrożone kopie renderera (`tests/fixtures/renderer-1.5.0`, `renderer-1.11.0`, `renderer-1.12.0`) ignorują pola `lang` i `texts`; polski renderer daje ten sam tekst co przed katalogiem; opisy dymków (`descriptions.en.json`) i glosariusz zgodne z językiem strony.
- `tests/test_skillsync_dashboard_tokens.py`: angielskie i polskie formy tokenów parsera (`RUN_LINE`, segmenty checkpointu, `assumed:`/`przyjęte:`, `Date:`, etap `running:`/`trwa:`, retro YES/NO, wynik `Result`), oraz to, że sekcje ryzyka po angielsku jeszcze nie są aliasami.
- `tests/test_skillsync_sync_renderer_defaults.py`: `scripts/sync_renderer_defaults.py` (`--check` kod 1 przy rozjeździe, `rewrite` naprawia kopię, LF, błąd przy brakujących znacznikach).

Źródło wiedzy: zlecenie I18N-1 (paczka CORE-1130), stan kodu z 2026-11-22.
