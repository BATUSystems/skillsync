---
title: "Python z pyenv bez _hashlib i _ssl: brak hashlib.scrypt, PBKDF2 w czystym Pythonie z DeprecationWarning"
tags: [skillsync, python, pyenv, hashlib, openssl, passwords, prototype]
paths: ['skillsync_workflow/prototype_server.py', 'tests/test_skillsync_prototype_server.py']
source: .skillsync/runs/2026-11-04-prototype-komentarze.karta-2/1.1-1.2-implementer.md (Odkrycia 2); Python 3.11.8 pyenv, spec docs/specs/2026-11-04-prototype-komentarze-wspolne.md (decyzja o PBKDF2)
verified: 2026-11-04
---
**Objaw:** `ImportError: libcrypto.so.1.1 / libssl.so.1.1` przy `import _hashlib`/`import ssl` w pyenv 3.11.8 (system ma nowszy OpenSSL). `hashlib.scrypt` nie istnieje, a `hashlib.pbkdf2_hmac` działa przez przestarzałą implementację w Pythonie z `DeprecationWarning` na stderr (~0,03 s na 60 tys., ~0,3 s na 600 tys. iteracji). `/usr/bin/python3` (3.14) ma `scrypt` i PBKDF2 z OpenSSL (~0,07 s na 600 tys.).

**Przyczyna:** interpreter zbudowany pod OpenSSL 1.1, którego na maszynie już nie ma.

**Reguła:** haszowanie haseł w `prototype_server.py` to wyłącznie PBKDF2-HMAC-SHA256 (600 000 iteracji; jeden algorytm przenośny między hostami); nie dodawaj `scrypt` ani zapasowego algorytmu (skrót z hosta ze `scrypt` nie zweryfikuje się na hoście bez niego). Moduł nie importuje `ssl`. Ryzyko: Python 3.12+ usunął czysto-pythonowy fallback PBKDF2, więc host 3.12+ bez OpenSSL nie ma `pbkdf2_hmac`. Testy wstrzykują 1000 iteracji; pełne 600 000 tylko w jednym teście (`FullIterationsTest`). Ostrzeżenia `DeprecationWarning` w testach CLI wycisza `-W ignore`.

**Jak wykryć:** `python3 -c "import hashlib; print(hasattr(hashlib,'scrypt'))"` oraz `python3 -c "import ssl"` w użytym interpreterze.
