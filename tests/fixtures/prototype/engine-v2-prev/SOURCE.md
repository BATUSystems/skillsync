# Kopia poprzedniego silnika v2 (przed widokami desktop/mobile)

Plik `prototype.js` to bajtowa kopia `skills/prototype/assets/repo/prototype.js` z biblioteki (commit `efaa3ff`,
sha256 `fa3908180d40b6e1e0cbe2c17fd7aef025b5adee8ee6f6083a6d0d3d87e8c70b`). Służy wyłącznie testowi odczytu wstecznego: poprzedni silnik czyta dziennik z polem
`payload.view` bez błędu i bez utraty wątków. Nie aktualizuj tej kopii przy zmianach silnika.

Pliki `prototype.css`, `components.css` i `screens.css` to bajtowe kopie `skills/prototype/assets/repo/*.css` z tego samego commitu `efaa3ff`.
E2E V-02 składa z nich z `prototype.js` prototyp wzorcowy (silnik sprzed widoków), bez dostępu do historii gita.
