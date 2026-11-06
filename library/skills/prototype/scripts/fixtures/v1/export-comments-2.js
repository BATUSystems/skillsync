/* Zatwierdzone operacje komentarzy prototypu.
 *
 * Plik wygenerował eksport do repozytorium. Nie edytuj go ręcznie: podmień
 * go na nowy eksport i zatwierdź w repozytorium. Operacje są tylko dopisywane
 * i mają unikalne id. Usunięcie wątku to trwały tombstone.
 */
window.__SKILLSYNC_PROTOTYPE_COMMENTS__ = {
  "version": 1,
  "prototypeId": "demo-a",
  "operations": [
    {
      "id": "op-3b",
      "type": "resolve",
      "threadId": "th-state",
      "at": "2026-11-03T10:30:00.000Z",
      "payload": {}
    },
    {
      "id": "op-2b",
      "type": "reply",
      "threadId": "th-tie",
      "at": "2026-11-03T10:05:00.000Z",
      "payload": {
        "message": {
          "id": "msg-aa",
          "author": "Recenzentka B",
          "text": "Druga wiadomość z remisu.",
          "at": "2026-11-03T10:05:00.000Z"
        }
      }
    },
    {
      "id": "op-dup",
      "type": "reply",
      "threadId": "th-tie",
      "at": "2026-11-03T10:20:00.000Z",
      "payload": {
        "message": {
          "id": "msg-dup1",
          "author": "Recenzentka B",
          "text": "Pierwszy duplikat id: wygrywa.",
          "at": "2026-11-03T10:20:00.000Z"
        }
      }
    },
    {
      "id": "op-1",
      "type": "create",
      "threadId": "th-tie",
      "at": "2026-11-03T10:00:00.000Z",
      "payload": {
        "screen": "s1",
        "anchor": "div:2>button",
        "label": "Zapisz",
        "message": {
          "id": "msg-a0",
          "author": "Recenzent A",
          "text": "Remis i duplikat.",
          "at": "2026-11-03T10:00:00.000Z"
        }
      }
    },
    {
      "id": "op-2a",
      "type": "reply",
      "threadId": "th-tie",
      "at": "2026-11-03T10:05:00.000Z",
      "payload": {
        "message": {
          "id": "msg-zz",
          "author": "Recenzentka B",
          "text": "Pierwsza wiadomość z remisu.",
          "at": "2026-11-03T10:05:00.000Z"
        }
      }
    },
    {
      "id": "op-2c",
      "type": "reply",
      "threadId": "th-tie",
      "at": "2026-11-03T10:06:00.000Z",
      "payload": {
        "message": {
          "id": "msg-zz",
          "author": "Recenzentka B",
          "text": "Powtórzone message.id: pomijane.",
          "at": "2026-11-03T10:06:00.000Z"
        }
      }
    },
    {
      "id": "op-dup",
      "type": "resolve",
      "threadId": "th-tie",
      "at": "2026-11-03T10:15:00.000Z",
      "payload": {}
    },
    {
      "id": "op-3c",
      "type": "create",
      "threadId": "th-state",
      "at": "2026-11-03T10:00:00.000Z",
      "payload": {
        "screen": "s2",
        "anchor": "div:3",
        "label": "Tabela",
        "message": {
          "id": "msg-s0",
          "author": "Recenzent A",
          "text": "Resolve po remisie.",
          "at": "2026-11-03T10:00:00.000Z"
        }
      }
    },
    {
      "id": "op-3a",
      "type": "reopen",
      "threadId": "th-state",
      "at": "2026-11-03T10:30:00.000Z",
      "payload": {}
    },
    {
      "id": "op-4b",
      "type": "reopen",
      "threadId": "th-state2",
      "at": "2026-11-03T10:40:00.000Z",
      "payload": {}
    },
    {
      "id": "op-4c",
      "type": "create",
      "threadId": "th-state2",
      "at": "2026-11-03T10:00:00.000Z",
      "payload": {
        "screen": "s2",
        "anchor": "div:4",
        "label": "Filtr",
        "message": {
          "id": "msg-t0",
          "author": "Recenzent A",
          "text": "Reopen po remisie.",
          "at": "2026-11-03T10:00:00.000Z"
        }
      }
    },
    {
      "id": "op-4a",
      "type": "resolve",
      "threadId": "th-state2",
      "at": "2026-11-03T10:40:00.000Z",
      "payload": {}
    },
    {
      "id": "op-5r",
      "type": "reply",
      "threadId": "th-early",
      "at": "2026-11-03T09:00:00.000Z",
      "payload": {
        "message": {
          "id": "msg-early",
          "author": "Recenzentka B",
          "text": "Przed create: ignorowane.",
          "at": "2026-11-03T09:00:00.000Z"
        }
      }
    },
    {
      "id": "op-5c",
      "type": "create",
      "threadId": "th-early",
      "at": "2026-11-03T10:00:00.000Z",
      "payload": {
        "screen": "s1",
        "anchor": "div:5",
        "label": "Nagłówek",
        "message": {
          "id": "msg-e0",
          "author": "Recenzent A",
          "text": "Operacje przed create.",
          "at": "2026-11-03T10:00:00.000Z"
        }
      }
    },
    {
      "id": "op-5x",
      "type": "resolve",
      "threadId": "th-early",
      "at": "2026-11-03T09:30:00.000Z",
      "payload": {}
    },
    {
      "id": "op-6c",
      "type": "create",
      "threadId": "th-del",
      "at": "2026-11-03T11:00:00.000Z",
      "payload": {
        "screen": "s1",
        "anchor": "div:6",
        "label": "Do usunięcia",
        "message": {
          "id": "msg-d0",
          "author": "Recenzent A",
          "text": "Zniknie.",
          "at": "2026-11-03T11:00:00.000Z"
        }
      }
    },
    {
      "id": "op-6d",
      "type": "delete",
      "threadId": "th-del",
      "at": "2026-11-03T08:00:00.000Z",
      "payload": {}
    },
    {
      "id": "op-7c",
      "type": "create",
      "threadId": "th-move",
      "at": "2026-11-03T10:00:00.000Z",
      "payload": {
        "screen": "s1",
        "anchor": "div:2",
        "label": "Karta",
        "message": {
          "id": "msg-m0",
          "author": "Recenzent A",
          "text": "Przeniesiona.",
          "at": "2026-11-03T10:00:00.000Z"
        }
      }
    },
    {
      "id": "op-7～",
      "type": "reply",
      "threadId": "th-move",
      "at": "2026-11-03T10:55:00.000Z",
      "payload": {
        "message": {
          "id": "msg-bmp",
          "author": "Recenzentka B",
          "text": "Id z BMP.",
          "at": "2026-11-03T10:55:00.000Z"
        }
      }
    },
    {
      "id": "op-7😀",
      "type": "reply",
      "threadId": "th-move",
      "at": "2026-11-03T10:55:00.000Z",
      "payload": {
        "message": {
          "id": "msg-astral",
          "author": "Recenzentka B",
          "text": "Id spoza BMP: wcześniej w UTF-16.",
          "at": "2026-11-03T10:55:00.000Z"
        }
      }
    },
    {
      "id": "op-7r",
      "type": "reanchor",
      "threadId": "th-move",
      "at": "2026-11-03T10:50:00.000Z",
      "payload": {
        "screen": "s2",
        "anchor": "div:3>button",
        "label": "Przycisk"
      }
    }
  ]
};
