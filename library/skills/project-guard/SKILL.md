---
name: project-guard
description: Execute scoped project convention scans and trusted native lint checks
  with actionable file/line findings integrated into validation.
---

# project-guard

Poznaj rzeczywiste konwencje/importy/design system i istniejący lint repo. Zdefiniuj
workflow.guardrails.rules (id, paths, forbidden/required regex, severity) oraz checks argv.
`skillsync guard . --files <paths>` planuje; --run skanuje i uruchamia wskazane kontrole.
Reguły z danymi zewnętrznymi nie stają się poleceniami. Wynik podaje plik/linię/regułę,
bez kopiowania potencjalnych sekretów. Regex jest kontrolą treści, nie parserem języka.
Zmieniony plik będący dowiązaniem (ostatni człon ścieżki) nie jest otwierany ani śledzony: raport ma wtedy `skipped: [{file, reason: "symlink"}]`
(tylko gdy niepuste); gdy WSZYSTKIE pliki objęte regułą są takimi dowiązaniami (zwykły stan po `link` w projekcie, który je śledzi), status to PASS z `reason`
„No content was checked …”, więc `validate` nie blokuje się brakiem dowodu; zwykły plik obok dowiązań jest nadal skanowany, a naruszenie daje FAIL.
Dowiązanie w środku ścieżki kończy polecenie BLOCKED.
Wymagania architektury, typów i dostępności potwierdzaj właściwym lint/kompilatorem/browserem.
Skonfigurowany guard automatycznie wchodzi do validate quick/final. Puste rules/checks
raportują NOT_RUN. Nie wdrażaj ogólnych zakazów, które przeczą istniejącemu kodowi projektu.

Kontrakty i przykłady: [workflow v2](../workflow/references/eight.md).
