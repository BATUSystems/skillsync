---
title: "Zgłoszenia zmian do warstwy przez tracker właściciela (`skillsync feedback`, `feedback:` w `layer.yaml`, `feedback_targets`, zgoda `WYŚLIJ <sha12>`)"
tags: [skillsync, feedback, tracker, azure, layers, consent, retro]
paths: ['skillsync_workflow/feedback.py', 'skillsync_workflow/delivery.py', 'library/workflow/trackers/azure-feedback.yaml', 'library/hooks/guard-commands.py', 'library/skills/workflow/references/feedback.md', 'tests/test_skillsync_feedback*.py']
source: "spec i plan CORE-1140-FEEDBACK (22.11.2026, decyzja „całość w 1.14.0”, bramka zgody w modelu CP zatwierdzona przez Dyspozytora); kod i testy jak w polu paths"
verified: 2026-11-22
---
# Zgłoszenia zmian do warstwy: `skillsync feedback` (od 1.14.0)

Zespół, który odkrył potrzebę zmiany warstwy, a nie jest właścicielem jej repozytorium, zgłasza ją do trackera właściciela. Mechanizm jest opcjonalny:
sesje Barcisia same decydują, czy zgłosić do trackera, czy wprowadzić zmianę commitem (`feedback.channel` w manifeście).

## Elementy

| Element | Gdzie | Rola |
|---|---|---|
| sekcja `feedback:` w `layer.yaml` | warstwa | PROPOZYCJA celu: `kind: azure-boards`, `organization`, `project`, `area` (ścieżka pod projektem, segmenty rozdzielone `/`), `type` (domyślnie `Task`), `labels` (do 5). Zła sekcja jest pomijana z ostrzeżeniem `feedback-invalid` i nie blokuje warstwy. Etykiety `fb-`, `fbk-`, `layer:`, `feedback`, `core-candidate` są zarezerwowane. |
| `feedback_targets` | konfiguracja maszyny | AUTORYZACJA celu: lista `{layer, kind, organization, project}` zatwierdzona przez człowieka (`skillsync feedback trust <warstwa>`, tylko z terminala). `layer.yaml` bywa z niezaufanego repozytorium, a zalogowany `az` sięga do każdej organizacji użytkownika. Zmiana organizacji albo projektu w `layer.yaml` unieważnia zgodę (`changed`). Zły wpis to `LayerError` przy wczytaniu konfiguracji. |
| szkic `.skillsync/feedback/F<n>.json` | GŁÓWNY checkout | `layer`, `kind` (`bug`, `change`, `tool`, `docs`), `title` (≤120), `target` (plik warstwy albo nazwa skilla), `problem`, `evidence`, `proposal`, `acceptance` (neutralne przypadki), `cause_key`, `core_candidate`, `retro`. Teksty są danymi niezaufanymi: bez znaków sterujących i bidi, z limitami. |
| dostawca `azure-feedback` | `library/workflow/trackers/azure-feedback.yaml` | `internal: true`: `skillsync tracker` go odrzuca, deskryptor jest czytany tylko z warstwy core z konfiguracji maszyny (nie z projektu, wyższej warstwy ani `--layer`). Operacje: `workitem-find`, `workitem-find-id`, `workitem-create`, `workitem-comment`, `workitem-last-comment`. Treść idzie przez `--in-file`, nigdy w argv; poświadczenia z `AZURE_DEVOPS_EXT_PAT` albo `az devops login`. |

## Przepływ

1. `skillsync feedback new --file szkic.json` zapisuje szkic jako `F<n>`.
2. `skillsync feedback draft F<n>`: sprawdza cel (`trusted`), szuka otwartego elementu z tagiem `fbk-<hash12>` (warstwa + `target` + `cause_key`):
   jest = tryb KOMENTARZ, nie ma = NOWY element. Skanuje wycieki dokładnych bajtów (tytuł, tagi, treść) przeciw tożsamościom INNYCH warstw (cel skanu to warstwa
   docelowa) i szuka sekretów; drukuje podgląd z `sha12` i poziomem pokrycia tożsamości (`generic` = nazwy klientów NIE są sprawdzane). Zapisuje `F<n>.preview.json`.
   Tytuł istniejącego elementu to cytat z trackera (dane, ≤80 znaków).
3. Człowiek wpisuje w sesji `WYŚLIJ <sha12>` (albo `WYSLIJ`); sesja woła `skillsync feedback approve F<n> --word "WYŚLIJ <sha12>"`: wpis zgody
   `{id, draft, sha, at, source}` w `.skillsync/feedback/approvals.jsonl` (główny checkout, 0600) z PEŁNYM sha256 treści.
   To osobny plik od `approvals.jsonl` przebiegów: tamten nie niesie szkicu ani zużycia, a `skillsync approvals record` nie przyjmuje tej zgody.
4. `skillsync feedback send F<n>` wysyła tylko, gdy: cel jest `trusted`, skan PASS, istnieje podgląd tej treści, wpis zgody ma `draft` i pełne sha identyczne z bieżącym,
   jest późniejszy niż podgląd, młodszy niż 24 h, nie z przyszłości, i niezużyty. Zgoda jest zużywana identyfikatorem w `F<n>.sending.json` / `F<n>.sent.json`;
   ponowna wysyłka wymaga `--replace` i NOWEJ zgody. Limit: 5 wysyłek na 24 h w głównym checkoucie. `send` odmawia w worktree i w sesji karty (`SKILLSYNC_CARD_ALIAS`).
5. Wysyłka: blokada `F<n>.sending.lock`, intencja `F<n>.sending.json` (sha, tryb, element, `uncertain`), zapis przez strzeżone `delivery.tracker` z odczytem kontrolnym po
   tagu `fb-<sha12>` (komentarz: znacznik `[fb-<sha12>]`), wynik `F<n>.sent.json` (id, adres, czas, `retro`). Po przerwaniu `send` wznawia z trybem i elementem
   z intencji; gdy zapis mógł się udać, a nie ma śladu (`uncertain`), zatrzymuje się dla człowieka zamiast pisać drugi raz.

## Sha treści

`sha` = sha256 kanonicznego JSON (`organization`, `project`, `area`, `type`, `mode`, `item`, `title`, `body`, `tags` bez `fb-`). Tag `fb-<sha12>` i metadane są dopisywane
PO obliczeniu (brak zależności cyklicznej). Zmiana szkicu, celu, trybu, elementu deduplikacji albo jego stanu zmienia sha i unieważnia zgodę.

## Granice (czego mechanizm nie gwarantuje)

- Granicą zaufania jest maszyna (jak przy `CP`): proces z dostępem do plików mógłby zapisać wpis zgody, a bezpośrednie `az` agenta nie jest zatrzymane; reguła
  strażnika poleceń (`guard-commands`: `azure-feedback` w poleceniu `skillsync tracker` albo `az devops/boards`) to dopasowanie tekstu, czyli heurystyka.
- Pola wyniku `az` sprawdzone na żywo (23.11, projekt Basic): `boards query` (nic nie drukuje przy braku wyników, stąd `on_empty`; `System.StateCategory` nie jest polem WIQL), tworzenie elementu (`devops invoke ... workItems`, `type=Task` bez `$`), odczyt kontrolny po tagu i ponowna wysyłka (BLOCKED „already sent”). Wersja API komentarzy to `7.1-preview` (`az devops invoke` odrzuca postać `X.Y-preview.N` błędem „could not convert string to float”, a serwer odrzuca `7.1`); kształt wyniku operacji komentarza potwierdzi test F2. Zapytanie WIQL widzi nowy element z opóźnieniem indeksu: odczyt kontrolny może dać BLOCKED z `mutation_completed`, a ponowny `send` znajdzie element po tagu.
- Skan wycieków sprawdza znane kształty sekretów i tożsamości z plików tożsamości warstw; podgląd człowieka pozostaje główną kontrolą.
- Przycisk zgody na stronie statusu jest poza wersją 1.14.0 (format wpisu ma `source: dashboard`).
- CLI nie ma jak sprawdzić, kto wpisał słowo zgody: agent, który odczyta `sha12` z wyniku `draft`, może sam wywołać `approve`. To świadoma decyzja (zgoda w modelu `CP`):
  chroni przed pomyłką i przed wiadomością z innej sesji, nie przed agentem, który łamie procedurę (`workflow/references/feedback.md`).
- Odmowa `send` i `approve` w sesji karty działa, gdy proces ustawi zmienną środowiska `SKILLSYNC_CARD_ALIAS` (nic w generatorze jej dziś nie ustawia): w praktyce
  chroni procedura karty (karta zgłasza STATUS, nie wysyła).
- Limity: `approvals.jsonl` ma 256 KiB (około 1100 wpisów; po przekroczeniu `approve` i `send` odmawiają: zarchiwizuj plik ręcznie po sprawdzeniu, że nie ma wysyłek w toku);
  czasy zgody i podglądu mają rozdzielczość mikrosekundy (zgoda musi być nie wcześniejsza niż podgląd); deskryptor dostawcy czytany z warstwy core, która jest dowiązaniem, jest
  odrzucany (komunikat „Missing or oversized provider descriptor”).
- Odstępstwa od brzmienia speca: zła wartość `feedback.channel` to błąd `doctor` (nie ostrzeżenie); odczyt kontrolny po utworzeniu to osobna operacja `workitem-find-id`;
  zapytania o otwarte elementy obejmują kategorię stanu `Resolved`; wpis zgody nie ma pola `by`; podgląd i skan odrzucają tekst, który `redact` statusu zamaskowałby
  (podgląd musi być tym, co idzie w świat).

## Pliki i testy

`skillsync_workflow/feedback.py`, `skillsync_workflow/delivery.py` (`INTERNAL_PROVIDERS`, `tracker(internal=)`), `skillsync_workflow/layers.py` (`parse_feedback`,
`parse_feedback_targets`), `library/workflow/trackers/azure-feedback.yaml`, `library/hooks/guard-commands.py`; testy `tests/test_skillsync_feedback*.py` (atrapa `az`, bez sieci).
