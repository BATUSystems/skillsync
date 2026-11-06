---
name: delivery-workflow-jira-azure
description: Neutral workflow for Jira Azure DevOps and Git including branching PR policy and release propagation.
user-invokable: true
---

# Delivery Workflow: Jira Azure Git

## Kiedy używać tego skilla
Uzywaj, gdy zadanie dotyczy:
- pracy na ticketach Jira,
- przygotowania branchy i pull requestow,
- propagacji poprawek między branchami release,
- utrzymania porządku commitów i traceability.

## Standard pracy
- Jedna zmiana funkcjonalna = jeden ticket = jedna linia commit history.
- Nazwa brancha zawiera identyfikator ticketu, np. feature/ABC-123-opis.
- Opis PR laczy zmiany techniczne z ryzykiem i planem testow.

## PR policy
- PR musi zawierac:
  - zakres zmiany,
  - wplyw na API/DB,
  - wynik testow,
  - plan rollbacku dla ryzykownych zmian.
- Rebase/merge wybieraj zgodnie z polityka repo, ale zachowuj czytelna historie.

## Hotfix i propagacja
- Hotfix tworz od najblizszego branchu releasowego.
- Po merge do release propaguj do pozostalych aktywnych galezi.
- Konflikty rozwiazuj swiadomie, nie automatycznie bez weryfikacji testami.

## Jira hygiene
- Ticket aktualizuj o status, decyzje i wynik testow.
- Linkuj PR i commit hash do ticketu.
- Przy zmianach kontraktowych dopisz notatke dla QA/ops.

## Definicja done
- Kod zmergowany.
- Testy przechodza.
- Ticket ma komplet informacji wdrozeniowych.
- Brak otwartych blockerow i jawny plan monitoringu po wdrozeniu.
