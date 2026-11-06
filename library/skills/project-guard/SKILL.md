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
Wymagania architektury, typów i dostępności potwierdzaj właściwym lint/kompilatorem/browserem.
Skonfigurowany guard automatycznie wchodzi do validate quick/final. Puste rules/checks
raportują NOT_RUN. Nie wdrażaj ogólnych zakazów, które przeczą istniejącemu kodowi projektu.

Kontrakty i przykłady: [workflow v2](../workflow/references/eight.md).
