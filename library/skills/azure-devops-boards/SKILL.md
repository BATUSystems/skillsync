---
name: azure-devops-boards
description: Use when working with Azure DevOps Boards via az boards - backlog, Epics, Features, Product Backlog Items, Tasks, work items, hierarchy, ticket creation, backlog sync, relation add, WIQL, wiki pages.
---

# Azure DevOps Boards

## Purpose

This skill explains how to work with Azure DevOps Boards so agents can safely create, query, update, and structure backlog items without rediscovering the process.

Project-specific context (organization, project, process, workflow states) lives in the project skill.

Files `references/org-*.md`, if present, are organization-layer extensions: read the ones that match the project.

## Project Baseline

- Read organization, project and process template from the project skill or from `az devops configure --list`.
- Preferred backlog hierarchy: `Epic -> Feature -> Product Backlog Item -> Task` (when the process supports it).

## When To Use This Skill

Use this skill when the task involves:

- creating or updating Azure work items,
- building a backlog from implementation plans,
- linking parent-child hierarchy,
- querying existing tickets to avoid duplicates,
- operating on planning work from the coding agent.

Do not use this skill for Azure infrastructure deployment or Azure resource management. This skill is specifically for Azure DevOps Boards operations.

## Tooling Choice

The reliable operational path is local Azure CLI with the Azure DevOps extension.

- prefer local `az` CLI for Boards work,
- do not assume Azure MCP tools can manage Boards,
- use repo helper scripts where possible,
- treat raw REST calls as fallback when CLI cannot express an operation cleanly.

## Session Learnings (CLI, wiki, pipelines)

- Jesli repo ma `.envrc`, uruchamiaj `source .envrc` przed komendami `az boards` i `az devops`; to stabilizuje domyslna organizacje/projekt i eliminuje przypadkowe odpytywanie zlego kontekstu.
- Po zaladowaniu env uzywaj `AZURE_DEVOPS_EXT_ORG` jako zrodla organization dla `az devops configure --defaults ...`; zmienna `AZURE_DEVOPS_ORG_URL` moze nie byc ustawiona.
- Przy nawigacji backlogu (Epic -> Feature -> PBI -> Task) najbardziej niezawodna metoda to odczyt `relations` z `az boards work-item show --id <ID>` i przejscie po linkach `Hierarchy-Forward`.
- WIQL oparte o `Contains Words` (szczegolnie dla polskich fraz) bywa niepelne; puste wyniki nie oznaczaja automatycznie braku work itemow.
- Ostrzezenie `pkg_resources is deprecated` z rozszerzenia `azure-devops` jest obecnie czeste i nie jest samo w sobie bledem operacji.
- Przy ocenie powodzenia komendy ufaj kodowi wyjscia i tresci odpowiedzi JSON/tabeli, a nie samym warningom na stderr.
- `az pipelines run` potrafi zwrocic ogolny komunikat o walidacji bez szczegolow; w takiej sytuacji preferuj uruchomienie i diagnostyke bezposrednio z UI Azure Pipelines.
- Dla Azure Wiki `az devops wiki page show` bywa mylace (null path/url); do potwierdzania istnienia i tresci strony uzywaj `az devops invoke --area wiki --resource pages` z `includeContent=true`.
- Przy tworzeniu zagniezdzonych stron wiki tworz najpierw ancestor page (np. `/DevOps`), inaczej create dla podstrony zakonczy sie bledem.
- Po przelaczeniu pipeline na self-hosted pool, run zaczyna dzialac dopiero po publikacji YAML do remote brancha (Azure uruchamia konfiguracje z repo, nie z lokalnego drzewa roboczego).
- `UsePythonVersion@0` na self-hosted agentach czesto failuje bez toolcache; bezpieczniej uzywac lokalnego `python3` i dodac krok walidacji wersji (wymagana wersja wynika z projektu).
- Po zmianie puli/zasobow CD moze oczekiwac na `Checkpoint.Authorization`; pierwszy run wymaga recznej autoryzacji w UI.
- CD skonfigurowane na `latestFromBranch` dla `main` nie pobierze artefaktu z branchy roboczych; do testu CD potrzebny jest udany artefakt CI na `main` albo czasowa zmiana strategii pobierania artefaktu.
- `projectwiki` creation can collide with an existing Git repository name (`TF400948`); use a distinct wiki name (Azure normalizes spaces to `-`).
- Dla weryfikacji stron wiki: `az devops invoke --area wiki --resource pages --route-parameters project=<project> wikiIdentifier=<wiki-name> --query-parameters path=/ recursionLevel=full includeContent=false --api-version 7.1 -o json`.

## Required Environment

The agent should expect these prerequisites:

- `az` is installed,
- Azure DevOps extension is installed for `az`,
- PAT is available in `AZURE_DEVOPS_EXT_PAT`,
- defaults are configured for org and project.

Recommended bootstrap:

```bash
export AZURE_DEVOPS_EXT_PAT='...'
az devops configure --defaults organization=https://dev.azure.com/<org> project=<project>
```

Use repo helper scripts (bootstrap, work item create) when the project provides them.

## Standard Operating Procedure

### 1. Query before create

Before creating tickets, check whether matching work items already exist.

Example:

```bash
az boards query --wiql "Select [System.Id], [System.Title], [System.WorkItemType], [System.State] From WorkItems Where [System.TeamProject] = '<project>' And [System.Title] Contains '<fragment>' Order By [System.ChangedDate] Desc" --output table
```

If the query is broader than one title, tighten it before creating anything. Avoid duplicate Epics, Features, and PBIs.

### 2. Create top-down, then link

Create items in this order:

1. `Epic`
2. `Feature`
3. `Product Backlog Item`
4. `Task`

Then attach the child to the parent.

Create examples:

```bash
az boards work-item create --type "Epic" --title "<epic title>" --output json
az boards work-item create --type "Feature" --title "<feature title>" --output json
az boards work-item create --type "Product Backlog Item" --title "<user-visible capability>" --output json
az boards work-item create --type "Task" --title "<implementation unit>" --output json
```

Link example:

```bash
az boards work-item relation add --id CHILD_ID --relation-type parent --target-id PARENT_ID
```

### 3. Prefer stable hierarchy names

Use predictable title prefixes (per project convention, e.g. `PBI:`, `Task:`) so later agents can query by title. Convention comes from the project skill.

### 4. Keep the split meaningful

Use this rule of thumb:

- `Epic`: one broad delivery area,
- `Feature`: one functional slice,
- `PBI`: one user-visible capability,
- `Task`: one implementation unit for backend, frontend, tests, integration, or refactor.

Do not create Tasks directly under Epic when a Feature/PBI split is warranted.

### 5. Verify after write

After bulk creation:

- confirm IDs were returned,
- confirm parent links exist,
- confirm the project process still accepts the target work item type,
- summarize created IDs back to the user.

## Workflow states

- Workflow states differ per process template; inspect the process-supported values before updating work items.
- If `az boards work-item create` rejects `System.State=<state>` during creation, create the item first and then run a separate `az boards work-item update --id <id> --fields System.State='<state>'` step.
- Choose a QA handoff state (if the process has one) when the change needs explicit QA, retest, manual verification, or handoff to another person; do not default everything to `Done`. Agents and subagents follow the same rule.
- System-customized work item types may reject REST writes to process states (`ProcessWorkItemTypeDoesNotExistException`); states are then changed in the admin UI (Organization Settings → Boards → Process).
- Specific states and process of a project: see the project skill.

## Safe Commands Reference

Show project:

```bash
az devops project show --project <project> --output table
```

Show one work item:

```bash
az boards work-item show --id 12345 --output json
```

Update title:

```bash
az boards work-item update --id 12345 --title "Nowy tytuł" --output json
```

Query recently changed assigned work items:

```bash
az boards query --wiql "Select [System.Id], [System.Title], [System.WorkItemType], [System.State], [System.AssignedTo] From WorkItems Where [System.TeamProject] = '<project>' And [System.AssignedTo] <> '' Order By [System.ChangedDate] Desc" --output json
```

## Preferred Execution Pattern For Agents

When asked to create or reorganize backlog items:

1. Read the relevant plan or spec files first.
2. Query Azure Boards for matching existing items.
3. Propose or infer a hierarchy consistent with `Epic -> Feature -> PBI -> Task`.
4. Create items.
5. Add relations.
6. Report created IDs and the resulting structure.

When moving work through delivery:

1. Move the task to the in-progress state before implementation.
2. After code, tests, and docs are ready, decide between `Done` and the QA handoff state.
3. During active QA use the in-test state; after successful QA use `Done`; after failed QA requiring code changes, return to in-progress.
4. After status update, update the relevant Azure Wiki page with delivered scope, validation outcome, and commit/PR references when the project requires it (see project skill).

When asked only for analysis or planning, stop before step 4 of the first list.

## Safety Notes

- Never commit PATs into the repository.
- Prefer environment variables over inline credentials.
- If a PAT appears in chat or terminal history, recommend rotating it.
- Avoid destructive work item deletes unless the user explicitly asks.
- Do not assume Azure CLI warnings mean failure; inspect exit code and returned payload.

## Known Constraints

- Azure DevOps project process switching is treated as an admin UI operation, not a normal CLI operation.
- Azure MCP tools available in this workspace are for Azure resources, not Boards.
- CLI is the default integration path for Boards.
- When state labels are unclear, inspect the process-supported values before updating work items; direct REST can be a practical fallback when CLI docs are ambiguous.

## Quick Decision Rule

- Need one or two items: use direct `az boards work-item create`.
- Need a repeatable hierarchy: use repo scripts.
- Need to avoid duplicates: query first, then create.
