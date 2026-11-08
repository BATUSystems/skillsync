# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), wersje: [SemVer](https://semver.org/lang/pl/).

## [1.0.0] - 2026-11-08

### Changed
- Tryb dostarczania (`delivery.mode`): `instructions/_base.md`, `feature`, `pr-run`, `worker-mode`, `multi-session`, `dispatch`
  i `workflow/references/release.md` opisują `pr` (domyślny) i `direct`, słowa zgody `CP`, `CPD`, `READY` (z wariantami `!`),
  commity po każdym sprawdzonym kroku planu i zakaz merge/complete PR. `skills/skillsync/SKILL.md` opisuje klucz `delivery` i pole `route`.

## [0.9.0]

### Added
- Pierwsze wydanie warstwy core po podziale biblioteki na warstwy (core, org, personal).
- Lekcje rdzenia w `workflow/lessons/`: uogólniona wiedza o testowaniu (zawężone biegi, Playwright i
  `TestServer`, Angular, kontrakty API, coverage wycinka, django-tenants, izolacja zasobów).
