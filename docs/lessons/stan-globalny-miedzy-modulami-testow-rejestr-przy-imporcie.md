---
title: 'Stan globalny między modułami testów w jednym procesie: atrapa rejestrowana przy imporcie jest zielona punktowo, a czerwona w pełnym biegu'
tags: [skillsync, tests, unittest, registry, isolation, test-adapters]
paths: ['tests/test_skillsync_test_adapters.py', 'skillsync_workflow/test_adapters/__init__.py', 'tests/test_skillsync_adapter_contract.py']
source: 'prace nad wydaniem 1.8.0: pełny bieg w jednym procesie wykrył atrapę adaptera zarejestrowaną przy imporcie modułu'
verified: 2026-11-17
---
**Objaw:** moduł testów przechodzi sam, a pełny bieg wszystkich modułów w jednym procesie jest czerwony: inny test widzi w globalnym rejestrze adapterów atrapę, którą dopisał cudzy moduł. Przy punktowym doborze modułów usterki nie widać.

**Przyczyna:** atrapa rejestrowana na poziomie modułu (przy imporcie) żyje do końca procesu. Kolejność ładowania modułów decyduje, kto ją zobaczy, więc wynik zależy od doboru testów.

**Reguła:** rejestrację atrap robi `setUpModule` i sprząta `tearDownModule`, a strażnik osiągalności (każdy adapter da się wybrać) patrzy tylko na obiekty produktu, nie na to, co zostało w rejestrze. Test, który indeksuje rejestr, woła przed tym jego wypełnienie (`discover()`) w `setUpModule`, żeby pojedynczy test i inna kolejność nie dawały `KeyError`.

**Jak wykryć:** puść moduły adapterów w jednym procesie (`python -m unittest a b c`), a potem pojedynczy test osobno; pełny bieg przed wydaniem to jedyny pewny dowód, punktowy tego nie wyłapie.
