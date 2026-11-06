# Adapter Codex (dispatch)

Wywołanie: `$dispatch <cel>`, `$dispatch status|drain|resume <run-id>` (wybór skilla przez `$`
lub `/skills`; nie zakładaj komendy `/dispatch`). `skillsync` udostępnia skill w `.agents/skills/dispatch`.

## Bramka modelu

Wymagany jest najsilniejszy dostępny model rozumujący w Codexie, sprawdzany w każdej turze
dyspozytora. Jeśli tier modelu sesji jest niepewny lub niepotwierdzony, **poproś użytkownika
o potwierdzenie przed startem** (i po zmianie modelu w trakcie) oraz o przełączenie na najsilniejszy
dostępny. Nie deklaruj modelu, którego nie potwierdziłeś. Tekst skilla nie przełącza modelu sesji.
Przy niespełnionej bramce w trakcie pracy: zapisz stan, nie wydawaj grantów ani przydziałów.

## Wykrywanie trybu

1. Jeśli host udostępnia narzędzia listowania sesji i wiadomości między sesjami — użyj ich
   zgodnie z [adapterem Claude](claude.md) (adresowanie po identyfikatorze, brak pollingu).
2. W przeciwnym razie (stan domyślny) działa **tryb ręczny**: użytkownik jest łącznikiem.

## Tryb ręczny

Użytkownik otwiera karty (sesje Codex lub Claude) i sam przekazuje wiadomości.

- **Karty**: zamiast `ListAgents` zapytaj, ile kart ma użytkownik i jak są oznaczone w UI;
  nadaj aliasy `karta-1`… i poproś o oznaczenie okien (nazwa/etykieta) tymi aliasami.
  Alias jest kluczem w pliku stanu; identyfikatora sesji może brakować (`session_id: null`).
- **Wysyłanie**: każde zlecenie i wiadomość drukuj jako osobny blok do wklejenia, z jawnym
  adresatem w nagłówku bloku, np.:

  ````
  >>> WKLEJ W KARCIE: karta-2  (wiadomość 7, run-id 2026-10-02-a)
  dispatch: 2026-10-02-a from Dyspozytor (session n/a)
  PROBE
  <<< KONIEC
  ````

  Dla zlecenia `ASSIGN` blok zawiera pełną treść zlecenia z SKILL.md (nagłówek, `/feature`
  — w Codexie `$feature` — izolacja, zakazy, zasady, podpis).
- **Odbiór**: po wydrukowaniu zakończ turę i poproś: „Wklej odpowiedzi kart, każdą z linią
  podpisu (`<Alias>/<paczka>`).” Nie zakładaj odpowiedzi, której użytkownik nie wkleił.
  Wiele odpowiedzi w jednej wklejce przetwarzaj kolejno; odpowiedź bez podpisu — dopytaj,
  od której karty pochodzi.
- **Sonda i duchy**: brak wklejonej odpowiedzi na `PROBE` po prośbie ponownej = karta
  niedostępna (zapisz w stanie), przydziel paczkę innej.
- **Weryfikacja** (`git ls-remote`, logi, `df`, load) dyspozytor wykonuje sam — nie wymaga kart.
- **Stan**: po każdym przetworzonym zdarzeniu zapisz plik stanu; to jedyna pamięć dyspozycji
  (patrz [state.md](state.md)). Wydrukuj też krótki „status” po większych zdarzeniach, aby
  użytkownik mógł wznowić z niego, gdy sesja padnie.
- **Dzierżawy i PUSH SLOT** działają tak samo; `LEASE GRANT`/`PUSH GRANT` użytkownik przekazuje
  wklejając blok do właściwej karty. Do czasu wklejenia grant uznaj za nieprzekazany.
