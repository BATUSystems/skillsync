---
title: "Maska wyjścia obejmuje tekst, nie bajty: trace.zip i zrzuty PNG idą bez maskowania sekretów"
tags: [skillsync, ui-verify, secrets, redaction, trace, png]
paths: ['library/skills/ui-verify/scripts/lib/out.mjs', 'library/skills/ui-verify/scripts/capture.mjs', 'library/skills/ui-verify/scripts/lib/playwright-adapter.mjs']
---
Objaw: `capture --allow-trace` zapisuje `trace.zip` z zawartością strony i logowania. Zrzuty PNG przechodzą tym samym zapisem co tekst, ale maska wartości sekretów ich nie obejmuje. Stan: potwierdzone odczytem kodu (lokalnie nie ma Chrome, więc bez przebiegu w przeglądarce).
Przyczyna: `createSink.writeFile` maskuje tylko dane tekstowe (`typeof data === 'string'`, `out.mjs:64-66`). Bufory przechodzą bajtowo. Adapter zwraca trace jako `Buffer` (`playwright-adapter.mjs:187-194`), a `capture.mjs` zapisuje zrzuty jako bufory (`capture.mjs:169`, `:178`). Tylko `storageState` jest tekstem i jest maskowany.
Reguła: nie zakładaj, że maska objęła pliki binarne. Sekret widoczny na ekranie może trafić do PNG bez zmian. Wyjście z `--allow-trace` trzymaj poza kontrolą wersji. Każdy nowy zapis binarny w narzędziach ui-verify wymaga osobnej decyzji o maskowaniu albo jawnego wyjątku w dokumentacji skilla. Nie dodawaj zapisu bajtowego bez tej decyzji.
Jak wykryć: `grep -n "typeof data === 'string'" library/skills/ui-verify/scripts/lib/out.mjs` (maskowanie tylko tekstu). `grep -n "writeFile" library/skills/ui-verify/scripts/capture.mjs` (zapisy bajtowe zrzutów i trace). Przy zmianie gardła wyjścia uruchom `node library/skills/ui-verify/scripts/capture.mjs --selftest` i sprawdź, czy test sekretu nadal obejmuje tylko raport, a nie pliki binarne.
