---
title: "socketserver.TCPServer.request_queue_size = 5: równoległe połączenia dostają ConnectionResetError"
tags: [skillsync, python, http-server, socketserver, concurrency, prototype, tests]
paths: ['skillsync_workflow/prototype_server.py', 'tests/test_skillsync_prototype_server.py']
source: .skillsync/runs/2026-11-04-prototype-komentarze.karta-2/1.6a-test-writer.md (BUG-1), 1.3-implementer.md (Poprawki po 1.6); socketserver.py TCPServer.server_activate
verified: 2026-11-04
---
**Objaw:** test 20 równoległych zapisów HTTP (`ConcurrencyTests`) kończył się u 2-3 klientów `ConnectionResetError: [Errno 104]` bez odpowiedzi; zapis nie dochodził do pliku. Błąd występował w ~5 z 6 uruchomień, więc łatwo go uznać za niestabilny test.

**Przyczyna:** `socketserver.TCPServer.request_queue_size` wynosi 5 (backlog `listen`). `ThreadingHTTPServer` go nie zwiększa; przy serii połączeń jądro odrzuca te ponad kolejkę, zanim wątek obsługi zdąży je przyjąć.

**Reguła:** każdy serwer na `ThreadingHTTPServer`/`TCPServer`, który ma obsłużyć kilka równoczesnych klientów, ustawia `request_queue_size` jawnie (w `PrototypeHTTPServer`: 128). Test współbieżności ma być ścisły (bez ponawiania po stronie klienta), inaczej maskuje ten błąd.

**Jak wykryć:** `python3 -W ignore -m unittest tests.test_skillsync_prototype_server.ConcurrencyTests` w pętli (6 razy); `grep -n request_queue_size skillsync_workflow/prototype_server.py`.
