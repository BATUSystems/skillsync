---
title: "json.dumps nie odtwarza JSON.stringify: liczby, samotne surogaty i kolejność kluczy-indeksów"
tags: [skillsync, python, javascript, json, serialization, prototype, tests]
paths: ['skillsync_workflow/prototype_server.py', 'tests/test_skillsync_prototype_engine.py', 'tests/fixtures/prototype/engine-v2/**']
source: .skillsync/runs/2026-11-04-prototype-komentarze.karta-2/1.1-1.2-implementer.md (Odkrycia 8, aktualizacja 1.4), 1.4-implementer.md (Odkrycia 5), 1.5-test-writer.md (Odkrycia 1-2)
verified: 2026-11-04
---
**Objaw:** plik `comments.json` zapisany przez Pythona różni się bajtowo od zapisanego przez silnik JS dla tego samego dziennika (diff w gicie, naruszone K2), albo `json.dumps(..., ensure_ascii=False)` wywala zapis `UnicodeEncodeError` na samotnym surogacie.

**Przyczyna:** `JSON.stringify` i `json.dumps` różnią się: liczby całkowite zapisane jako float (`2.0` → JS `2`), wykładniki (`1e-07` vs `1e-7`, progi 1e16 vs 1e21), liczby całkowite > 2^53 (JS przez double), samotne surogaty (JS `\udXXX`, Python przepuszcza znak) oraz kolejność kluczy obiektu (JS wystawia klucze-indeksy tablicowe `"1","3"` najpierw, rosnąco).

**Reguła:** serializację współdzieloną z JS robi `prototype_server.js_stringify`/`js_number`, nie `json.dumps`. Zmieniając format zapisu, popraw obie strony i odśwież kopię silnika. Parser odrzuca `NaN`/`Infinity`. `serializeCommentsDocument` w JS rzuca na elementach nie-obiektowych, więc testy serializują tylko obiekty.

**Jak wykryć:** `python3 -m unittest tests.test_skillsync_prototype_engine` (klasy `EngineV2DifferentialTests`, `EngineV2LabelTests`; 3600 dzienników, porównanie bajtów serializacji); wymaga `SKILLSYNC_TEST_NODE` (node ≥ 18).
