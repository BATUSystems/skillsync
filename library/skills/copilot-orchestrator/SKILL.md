---
name: copilot-orchestrator
description: Wzorce GitHub Copilot Orchestrator (mcp_copilot_orche_*) - plany i joby dla podumysłów, diagnoza Snapshot Validation, sprzątanie artefaktów po planie.
user-invocable: true
---

# Copilot Orchestrator — Wzorce

## Kiedy używać

- Gdy zadanie da się podzielić na ≥2 niezależne strefy: backend / frontend / docs
- Gdy user prosi o delegowanie do podumysłu ("zrób samodzielnie", "przez Orchestrator")
- **NIE** używaj dla jednorazowych, małych zadań — overhead nie opłaca się

---

## Workflow: Tworzenie i uruchamianie planu

### 1. Scaffold + add jobs (nie `create_copilot_plan` z tablicą)

```python
# Stwórz pusty plan
mcp_copilot_orche_scaffold_copilot_plan(
    name="Tytuł planu",
    baseBranch="feature/docs",
    startPaused=True,
    maxParallel=4,
    additionalSymlinkDirs=[".venv"]  # ważne dla Python venv
)
# Zwraca: {"planId": "...", "specsDir": "..."}

# Dodaj joby kolejno (POPRAWNA składnia)
mcp_copilot_orche_add_copilot_plan_job(
    planId="<plan-id>",
    producerId="unique-job-id",  # ⚠️ WYMAGANE! (np. "backend-auth")
    name="Nazwa wyświetlana",
    task="Krótki opis (1 zdanie)",
    work={
        "type": "agent",  # ⚠️ WYMAGANE!
        "model": "claude-opus-4.5",
        "instructions": "Szczegółowe instrukcje..."
    }
)

# Finalizuj i uruchom
mcp_copilot_orche_finalize_copilot_plan(planId="...")
mcp_copilot_orche_resume_copilot_plan(planId="...")
```

> ⚠️ **Błędy schemy:**
> - `create_copilot_plan` z bezpośrednią tablicą `jobs` failuje — **zawsze używaj scaffold + add**
> - Brak `producerId` w `add_copilot_plan_job` → error "must have required property 'producerId'"
> - Brak `type: "agent"` w `work` → job nie wystartuje
> - `instructions` jako string zamiast w obiekcie `work` → error walidacji

### 2. Prawidłowa struktura parametru `work`

Parametr `work` w `add_copilot_plan_job` **MUSI** być obiektem JSON:

```json
{
  "type": "agent",           // WYMAGANE: typ workera (zawsze "agent")
  "model": "claude-opus-4.5", // Model AI (sonnet-4.5 / opus-4.5)
  "instructions": "..."      // Szczegółowe instrukcje dla agenta
}
```

**Błędne (failuje):**
```python
# ❌ instructions jako oddzielny parametr
add_copilot_plan_job(planId="...", instructions="...")

# ❌ work jako string
add_copilot_plan_job(planId="...", work="Zrób coś")

# ❌ brak type w work
add_copilot_plan_job(planId="...", work={"instructions": "..."})
```

**Poprawne:**
```python
# ✅
add_copilot_plan_job(
    planId="...",
    producerId="unique-id",
    work={"type": "agent", "model": "claude-opus-4.5", "instructions": "..."}
)
```

### 3. Instrukcje jobów — co pisać

Każda instrukcja jobu powinna zawierać:
- Cel (co ma powstać)
- Konkretne nazwy plików / modeli / endpointów
- Wzorzec kodu (serializer, viewset, test pattern)
- Polecenie weryfikacji (`pytest`, `ng build`)
- Polecenie commitu (treść + scope)

Im bardziej szczegółowe instrukcje, tym mniejsze ryzyko hollow job.

---

## Znane problemy i sposoby naprawy

### Problem 0: Snapshot Validation FAILED mimo "różnych plików"

**Objaw:**
- Każdy job dotyka "innych" plików domenowych, ale plan failuje na merge snapshotu.

**Prawdziwa przyczyna:**
- Joby współdzielą choć jeden plik techniczny (najczęściej `__init__.py`, czasem wspólny indeks/registry).
- Snapshot Validation robi merge commitów sekwencyjnie; drugi commit wpada w konflikt na wspólnym pliku.

**Reguła projektowania jobów:**
- Jeśli wiele jobów musi modyfikować ten sam plik: **nie uruchamiaj ich równolegle**.
- Preferuj jeden z wzorców:
    1. **Single-job** dla całego modułu (najbezpieczniej dla docs/refactor).
    2. Joby równoległe tylko dla plików niezależnych + **osobny job finalizujący** wspólny plik (zależny od wszystkich).
    3. Sekwencja zależności między jobami zamiast pełnej równoległości.

**Szybka weryfikacja przed uruchomieniem planu:**
```bash
# znajdź pliki modyfikowane przez więcej niż jeden job (na podstawie list zadań)
# jeśli pojawia się __init__.py w wielu jobach -> zmień topologię planu
```

### Problem 1: Hollow job (job "udaje" że pracował, ale nic nie zrobił)

**Objawy:**
- Job ✅ succeeded, ale commit ma 1 file changed (np. tylko `node_modules` symlink)
- `git show --stat <commit>` pokazuje `120000 node_modules | 1 +`

**Przyczyna:** Agent jobu nie mógł uruchomić Django/npm w worktree (brak venv/node_modules) i zakończył bez error.

**Naprawa:**
```bash
# 1. Cherry-pick tylko wartościowych commitów
git cherry-pick <frontend-commit> <docs-commit>

# 2. Hollow commit zostanie w historii — usuń go przed pushem
git rebase -i HEAD~N
# drop <hollow-commit-hash>
# drop <fix-commit-hash>  # jeśli był fix do hollowa

# 3. Zaimplementuj brakującą pracę ręcznie
```

**Prewencja:** W instrukcji jobu dodaj fallback: "Jeśli venv nie jest dostępny, wskaż dokładnie jakie pliki stworzyć i nie commituj placeholder."

**Dodatkowa prewencja (Python):**
- Przed startem planu upewnij się, że `.gitignore` zawiera:
    - `**/__pycache__/`
    - `*.pyc`
- Po każdym jobie waliduj rzeczywistą zawartość commitu:
```bash
git show --name-only --stat <job-commit>
# jeśli są tylko .pyc / __pycache__ -> traktuj jako hollow job
```

---

### Problem 2: Snapshot Validation FAILED — node_modules merge conflict

**Objaw:**
```
Scalenie nadpisałoby nieśledzone pliki: node_modules
```

**Przyczyna:** Worktree snapshotu zawiera katalog/symlink `node_modules` (nieśledzony), a git odmawia merge.

**Naprawa:**
```bash
# Cherry-pick commitów z jobów bezpośrednio na master
git cherry-pick <job1-commit> <job2-commit> <job3-commit>

# Wyczyść artefakt node_modules z indeksu (jeśli trafił do git)
git rm --cached node_modules
git commit -m "fix: usuń node_modules symlink (artefakt worktree)"
```

**Prewencja:** Dodaj do `.gitignore` (głównego repo):
```
node_modules
.worktrees/
```

---

### Problem 3: Orphaned worktree po zakończeniu planu

**Objaw:** W VS Code Kontrola Źródła widoczny drugi branch `_snapshot-<plan-id>` z plikiem `node_modules`.

**Naprawa:**
```bash
git worktree list  # znajdź ścieżkę
git worktree remove --force .worktrees/_snapshot-<plan-id>
git branch -D orchestrator/snapshot/<plan-id>
```

---

## Checklist po zakończeniu planu Orchestratora

```
[ ] mcp_copilot_orche_get_copilot_plan_status → sprawdź które joby succeeded/failed
[ ] Przed planem: sprawdź czy joby nie współdzielą plików (szczególnie __init__.py)
[ ] git log --oneline → zidentyfikuj commity z jobów
[ ] git show --stat <commit> → zweryfikuj że commit ma >1 file (nie hollow)
[ ] git show --name-only <commit> | grep -E "__pycache__|\.pyc" → wykryj artefakty binarne
[ ] git worktree list → sprawdź czy zostały orphaned worktrees
[ ] git worktree remove --force + git branch -D → usuń artefakty
[ ] Jeśli Snapshot Validation failed → cherry-pick + git rebase --drop hollow
[ ] Jeśli job hollow → zaimplementuj brakującą pracę ręcznie
[ ] git log --oneline → historia czysta, tylko wartościowe commity
```

---

## Anatomia planu

Typowy plan 3-jobowy: backend / frontend / docs. Pliki `references/org-*.md`, jeśli są, to rozszerzenia warstwy organizacji: przeczytaj te, które pasują do projektu.

### Typowe zależności między jobami

- Job `frontend` może zależeć od `backend` (typy API) — rozważ sekwencję zamiast równoległego
- Job `docs` jest zawsze niezależny od pozostałych
- Jeśli backend job jest ryzykowny (nowy model, skomplikowana logika) — zrób go samodzielnie, deleguj tylko frontend + docs

---

## Diagnoza statusu planu

```python
# Sprawdź status
status = mcp_copilot_orche_get_copilot_plan_status(plan_id="...")

# Znajdź szczegóły failującego joba
logs = mcp_copilot_orche_get_copilot_job_logs(job_id="...")
failure = mcp_copilot_orche_get_copilot_job_failure_context(job_id="...")
```

---

## Wnioski z sesji (ogólne)

- Backend job hollowy: agent nie uruchomił venv w worktree → zero pracy, commit z samym symlinkiem `node_modules`; `node_modules` symlink w worktree blokował też Snapshot Validation.
- Joby frontend i docs bywają wartościowe i można je cherry-pickować, a brakującą pracę dokończyć ręcznie.
- Git hygiene: `git rebase -i --drop` hollow commits przed pushem + `git worktree remove` snapshot.
- Równoległe joby modyfikujące wspólny `__init__.py` nie przechodzą Snapshot Validation.
- Część jobów kończy się commitami zawierającymi tylko `.pyc` (hollow job), mimo że log agenta raportuje zmiany źródeł.
- Najskuteczniejsze podejście dla modułu z wspólnym plikiem: pojedynczy job obejmujący cały moduł + walidacja `git show` po commicie.
- Obowiązkowe guardrails: globalne reguły `.gitignore` dla `__pycache__` i `*.pyc`, kontrola shared files przed planem.
