import argparse
import copy
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import yaml

from .common import discover_excludes, is_excluded

CONFIG_PATH = Path(os.environ.get("SKILLSYNC_CONFIG") or (Path.home() / ".config" / "skillsync" / "config.yaml"))
STATE_FILE = Path(".github") / ".skillsync-state.json"
BASE_PREAMBLE_END_MARKER = "<!-- SKILLSYNC:BASE-END -->"
CLAUDE_MD_HEADER = (
    "# CLAUDE.md\n\n"
    "This file provides guidance to Claude Code (claude.ai/code) when working "
    "with code in this repository.\n\n"
)
AGENTS_BEGIN = "<!-- SKILLSYNC:CODEX-BEGIN -->"
AGENTS_END = "<!-- SKILLSYNC:CODEX-END -->"
CODEX_NOTE_BEGIN = "<!-- SKILLSYNC:CODEX-NOTE-BEGIN -->"
CODEX_NOTE_END = "<!-- SKILLSYNC:CODEX-NOTE-END -->"
CLAUDE_IMPORT = "@AGENTS.md\n"
ENTRYPOINT_MODES = ("inline", "agents")
CODEX_NOTE = ("## Skillsync: Codex\n\n"
              "Codex only: for an implementation using `$feature`, read `.agents/skills/feature/SKILL.md`\n"
              "and its Codex adapter. Delegate the planned roles to native subagents from\n"
              "`.codex/agents/`; do not start this pipeline for questions or audits.\n"
              "Shared sources are in the skillsync library; do not edit generated TOML files.")
DEFAULT_MCP_SERVERS = {
    "chrome-devtools": {
        "command": "npx",
        "args": ["-y", "chrome-devtools-mcp@latest", "--browserUrl", "http://127.0.0.1:9222"],
    }
}
# Keys shared by VS Code's mcp.json and Claude Code's .mcp.json server entries.
MCP_SERVER_KEYS = ("type", "command", "args", "env", "url", "headers")
# Workflow skill: `feature` is canonical; `agentflow` is the deprecated name, accepted in manifests as an
# alias (and used as a transitional fallback while the library still ships `skills/agentflow`).
# Mirrored in skillsync_workflow/common.py (tests keep both copies identical).
WORKFLOW_SKILL = "feature"
LEGACY_WORKFLOW_SKILL = "agentflow"
WORKFLOW_COMPANIONS = ("workflow", "pre-implement", "smart-test", "bugfix", "codereview")
# Removed skills kept as manifest aliases: `go-jira` (removed from the library) resolves to `acli`.
# Mirrored in skillsync_workflow/common.py (tests keep both copies identical).
REMOVED_SKILL_ALIASES = {"go-jira": "acli"}
# Renamed roles kept as manifest aliases for one release: `Explore` -> `explore` (lower case like the other roles).
RENAMED_ROLE_ALIASES = {"Explore": "explore"}


def write_text_lf(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data


def save_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=False)


def load_config(optional: bool = False) -> dict:
    """Machine config dict; `optional` (explicit `--layer` overrides): a missing file gives `{}`."""
    if not CONFIG_PATH.exists():
        if optional:
            return {}
        raise FileNotFoundError(f"Missing config: {CONFIG_PATH}")
    return load_yaml(CONFIG_PATH)


def discover_manifests(config: dict) -> list[Path]:
    """Manifests under `discover_paths`, without projects equal to or under a `discover_exclude` path.

    A bad `discover_exclude` is a `ValueError` with a fixed message (`main` prints it, exit 2)."""
    excludes = discover_excludes(config)
    manifests = []
    for root in config.get("discover_paths", []):
        root_path = Path(root).expanduser()
        if not root_path.exists():
            continue
        for path in root_path.rglob(".github/.skillsync.yaml"):
            if excludes and is_excluded(path.parent.parent, excludes):
                continue
            manifests.append(path)
    return sorted(set(manifests))


def project_name_from_path(project_dir: Path) -> str:
    repos_root = Path.home() / "Repos"
    try:
        return str(project_dir.resolve().relative_to(repos_root))
    except ValueError:
        return project_dir.name


def slug_from_project_name(name: str) -> str:
    return name.replace("/", "-")


LAYERSET_KEY = "_layerset"      # LayerSet attached to the config dict by `with_layers` (link, instructions)
COMPOSITES_KEY = "_composites"  # {skill: hash} of composites placed during one link


def _layers_module():
    """`skillsync_workflow.layers`, imported on demand (the entry script must load without the package)."""
    try:
        from skillsync_workflow import layers
    except ModuleNotFoundError as error:
        if error.name != "skillsync_workflow":
            raise
        raise ValueError("Layers require the skillsync_workflow package next to the skillsync script") from None
    return layers


def machine_config(config: dict):
    """The machine config dict parsed and validated as `layers.MachineConfig` (raises LayerError)."""
    return _layers_module().parse_machine_config(
        {key: value for key, value in config.items() if not key.startswith("_")}, CONFIG_PATH, exists=CONFIG_PATH.exists())


def resolve_project_layers(project_dir: Path | None, config: dict, overrides: list | None = None, machine=None):
    """LayerSet for a project (`layer_rules` by its path) or, without a project, for the current directory;
    `overrides` (`--layer id=path`) replace the configured layers. Raises LayerError (BLOCKED)."""
    return _layers_module().resolve_layers(project_dir, layers=list(overrides or []) or None,
                                           config=machine if machine is not None else machine_config(config))


def layer_overrides(args) -> list:
    """`--layer` values of a command (callers may pass a bare Namespace without them)."""
    value = getattr(args, "layers", None)
    return list(value) if isinstance(value, (list, tuple)) else []


def add_layer_flag(parser: argparse.ArgumentParser) -> None:
    """Shared repeatable `--layer id=path` (same as `skillsync_workflow.layers.add_layer_argument`)."""
    parser.add_argument("--layer", dest="layers", action="append", default=[], metavar="ID=PATH",
                        help="Use this layer instead of the configured ones (repeatable, ascending precedence)")


class LayerGate:
    """K12 gate of `link`/`instructions`: `check_layers` without hooks and orphans; any `blocked`
    finding (core_min, requires, missing version/core_min, invalid layer.yaml, cycle...) refuses the
    project. Resolution notes (`layer-absent`...) and `error` findings (including
    the trust model: `override-without-consent`) are printed once as `[warn]` and never block."""

    def __init__(self):
        self.cache = {}
        self.printed = set()

    def findings(self, layerset) -> list:
        key = tuple((layer.id, str(layer.root)) for layer in layerset.layers)
        if key not in self.cache:
            found = _layers_module().check_layers(layerset, hooks=False, trust=True, orphans=False)
            self.cache[key] = found[len(layerset.notes):]
        return list(layerset.notes) + self.cache[key]

    def blocked(self, project_dir: Path, layerset, verb: str = "linked") -> bool:
        notes = layerset.notes
        refused = False
        for finding in self.findings(layerset):
            severity = finding.get("severity")
            if severity == "blocked":
                refused = True
                print(f"[blocked] {project_dir}: {finding['message']}; not {verb} (details: `skillsync layers check`)")
                continue
            if finding not in notes and severity != "error":
                continue
            line = f"[{'info' if severity == 'info' else 'warn'}] layers: {finding['message']}"
            if line not in self.printed:
                self.printed.add(line)
                print(line)
        return refused


def project_manifests(config: dict, path_arg) -> list[Path]:
    if path_arg:
        path = Path(path_arg).expanduser()
        if path.is_file() and path.name == ".skillsync.yaml":
            return [path]
        return [path / ".github" / ".skillsync.yaml"]
    return discover_manifests(config)


def gated_projects(args: argparse.Namespace):
    """(config, [(manifest, LayerSet)], status) for `link`/`instructions`: projects whose layers cannot be
    resolved (no `layer_rules` entry, legacy config...) or fail the K12 gate are reported as BLOCKED."""
    overrides = layer_overrides(args)
    config = load_config(optional=bool(overrides))
    machine = machine_config(config)
    if not overrides and not machine.layers:
        try:  # legacy `library:`-only config, no config or no layers: one BLOCKED line, nothing linked
            resolve_project_layers(None, config, machine=machine)
        except ValueError as exc:
            print(f"[blocked] {exc}")
            return config, [], 2
    gate = LayerGate()
    status, selected = 0, []
    for manifest in project_manifests(config, getattr(args, "path", None)):
        if not manifest.exists():
            print(f"[warn] Missing manifest: {manifest}")
            continue
        project_dir = manifest.parent.parent
        try:
            layerset = resolve_project_layers(project_dir, config, overrides, machine)
        except ValueError as exc:
            print(f"[blocked] {project_dir}: {exc}")
            status = 2
            continue
        if gate.blocked(project_dir, layerset):
            status = 2
            continue
        try:  # expansion errors (unsafe name, unknown profile...) block this project only, before any write
            project_skills(load_yaml(manifest), with_layers(config, layerset))
        except ValueError as exc:
            print(f"[blocked] {project_dir}: {exc}")
            status = 2
            continue
        selected.append((manifest, layerset))
    return config, selected, status


def with_layers(config: dict, layerset) -> dict:
    """Copy of `config` carrying the LayerSet used by every build_* function of one project."""
    result = dict(config)
    result[LAYERSET_KEY] = layerset
    result[COMPOSITES_KEY] = {}
    return result


def config_layers(config: dict):
    """LayerSet attached by `with_layers`; otherwise resolved like a command without a project."""
    layerset = config.get(LAYERSET_KEY)
    if layerset is None:
        layerset = resolve_project_layers(None, config)
    return layerset


SAFE_SKILL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def safe_skill_name(name) -> bool:
    """Same rule as `skillsync_workflow/common.py:expand_project_skills` (parity)."""
    return isinstance(name, str) and bool(SAFE_SKILL_NAME.fullmatch(name)) and ".." not in name


def available_skills(source) -> list[str]:
    """Skills = directories with `SKILL.md` (as `common.expand_project_skills`): union across a LayerSet,
    or the entries of a `skills` directory (`Path`: `expand_skills` without the workflow package)."""
    if isinstance(source, (str, Path)):
        skills_root = Path(source)
        if not skills_root.exists():
            return []
        return sorted([p.name for p in skills_root.iterdir() if p.is_dir()])
    return source.skill_names(require_skill_md=True)


def expand_skills(specs: list[str], source, tags: dict, manifest: dict | None = None) -> list[str]:
    """Expanded skill list (mirror of `skillsync_workflow/common.py:expand_project_skills`).

    `source`: LayerSet or the `skills` directory of one library (`Path`, wrapped by `as_layerset` as a
    one-layer LayerSet without K12 checks; without the workflow package only directory names are read)."""
    try:
        from skillsync_workflow.common import as_layerset
        from skillsync_workflow.maintenance import resolve_profile_skills
    except ModuleNotFoundError as error:
        if error.name not in ("skillsync_workflow", "skillsync_workflow.common", "skillsync_workflow.maintenance"):
            raise
        if manifest is not None and manifest.get("workflow", {}).get("skill_profiles", {}).get("selected"):
            raise ValueError("Skill profiles require the skillsync_workflow package") from None
        available = available_skills(source)
    else:
        library = as_layerset(Path(source).parent if isinstance(source, (str, Path)) else source)
        if manifest is not None:
            specs = list(specs) + resolve_profile_skills(manifest, library)
        available = available_skills(library)
    expanded = []
    seen = set()

    def add_skill(name: str) -> None:
        # Same check and message as skillsync_workflow/common.py:expand_project_skills (parity).
        if not safe_skill_name(name):
            raise ValueError(f"Unsafe skill name: {name}")
        if name == LEGACY_WORKFLOW_SKILL and WORKFLOW_SKILL in available:
            name = WORKFLOW_SKILL
        if name in REMOVED_SKILL_ALIASES and REMOVED_SKILL_ALIASES[name] in available:
            name = REMOVED_SKILL_ALIASES[name]
        if name not in seen:
            seen.add(name)
            expanded.append(name)

    for spec in specs:
        if spec.startswith("@"):
            tag_name = spec[1:]
            for tag_skill in tags.get(tag_name, []):
                add_skill(tag_skill)
            continue
        if "*" in spec or "?" in spec:
            for match in available:
                if safe_skill_name(match) and fnmatch.fnmatch(match, spec):  # unsafe directory names: skipped
                    add_skill(match)
            continue
        add_skill(spec)

    if WORKFLOW_SKILL in seen or LEGACY_WORKFLOW_SKILL in seen:
        for name in WORKFLOW_COMPANIONS:
            if name in available:
                add_skill(name)

    return expanded


def ensure_gitignore(github_dir: Path, dry_run: bool = False) -> None:
    gitignore_path = github_dir / ".gitignore"
    entries = ["copilot-instructions.md", "skills/", ".skillsync.yaml"]

    if gitignore_path.exists():
        existing = gitignore_path.read_text(encoding="utf-8").splitlines()
    else:
        existing = []

    updated = list(existing)
    for entry in entries:
        if entry not in existing:
            updated.append(entry)

    if updated == existing:
        return

    if dry_run:
        print(f"[dry-run] Would update: {gitignore_path}")
        return

    write_text_lf(gitignore_path, "\n".join(updated).rstrip() + "\n")


def apply_variables(content: str, manifest: dict, config: dict) -> str:
    variables = {}
    variables.update(config.get("variables", {}))
    variables.update((manifest.get("instructions") or {}).get("variables", {}))
    for key, value in variables.items():
        content = content.replace(f"{{{{ {key} }}}}", str(value))
        content = content.replace(f"{{{{{key}}}}}", str(value))
    return content


def compose_base(layerset, text: str) -> str:
    """`_base` of the core plus `instructions/_base.d/*.md` of the active layers, each in its
    `SKILLSYNC:FRAGMENT` markers: `00-`..`09-` before the core text, the rest after it, all before
    `SKILLSYNC:BASE-END`. Without fragments the text is returned unchanged."""
    fragments = layerset.base_fragments()
    if not fragments:
        return text
    layers = _layers_module()
    blocks = {"pre": [], "post": []}
    for fragment in fragments:
        blocks[fragment["position"]].append(
            layers.fragment_block(fragment["layer"], fragment["name"], fragment["path"].read_text(encoding="utf-8")))
    pre = "\n".join(blocks["pre"])
    post = "\n".join(blocks["post"])
    if post and BASE_PREAMBLE_END_MARKER in text:
        before, after = text.split(BASE_PREAMBLE_END_MARKER, 1)
        text = before.rstrip("\n") + "\n\n" + post + "\n" + BASE_PREAMBLE_END_MARKER + after
    elif post:
        text = text.rstrip("\n") + "\n\n" + post
    return (pre + "\n" + text) if pre else text


def read_instruction(layerset, name: str) -> str | None:
    """Text of `instructions/<name>.md` resolved per file across layers (later wins); `_base` only from
    the core, composed with the `_base.d` fragments. None when no layer has it."""
    found = layerset.resolve(f"instructions/{name}.md")
    if found is None:
        return None
    text = found[1].read_text(encoding="utf-8")
    return compose_base(layerset, text) if name == "_base" else text


def missing_instruction(layerset, name: str) -> str:
    return f"instructions/{name}.md (layers: {', '.join(layerset.ids) or 'none'})"


def build_instructions(project_dir: Path, manifest: dict, config: dict, dry_run: bool = False) -> None:
    instructions = manifest.get("instructions") or {}
    template_name = instructions.get("template")
    if not template_name:
        return

    layerset = config_layers(config)
    merge_list = list(instructions.get("merge") or [])
    names = merge_list + [template_name]

    parts = []
    for name in names:
        text = read_instruction(layerset, name)
        if text is None:
            print(f"[warn] Missing instructions template: {missing_instruction(layerset, name)}")
            continue
        parts.append(text)

    if not parts:
        return

    content = apply_variables("\n\n".join(parts).rstrip() + "\n", manifest, config)

    target = project_dir / ".github" / "copilot-instructions.md"
    if dry_run:
        print(f"[dry-run] Would write: {target}")
        return
    write_text_lf(target, content)


def claude_enabled(manifest: dict) -> bool:
    claude_cfg = manifest.get("claude")
    if claude_cfg is False:
        return False
    if isinstance(claude_cfg, dict) and claude_cfg.get("enabled") is False:
        return False
    return True


def ensure_root_gitignore(project_dir: Path, dry_run: bool = False, entry: str = "CLAUDE.md") -> None:
    gitignore_path = project_dir / ".gitignore"

    if gitignore_path.exists():
        existing = gitignore_path.read_text(encoding="utf-8").splitlines()
    else:
        existing = []

    if entry in existing:
        return

    if dry_run:
        print(f"[dry-run] Would update: {gitignore_path}")
        return

    updated = existing + [entry]
    write_text_lf(gitignore_path, "\n".join(updated).rstrip() + "\n")


def handwritten_claude_md(project_dir: Path, manifest: dict) -> bool:
    """A CLAUDE.md without the generator header in a project without a template is the user's own file."""
    target = project_dir / "CLAUDE.md"
    return (not (manifest.get("instructions") or {}).get("template") and target.exists()
            and not target.read_text(encoding="utf-8").startswith(CLAUDE_MD_HEADER))


def claude_md_content(project_dir: Path, manifest: dict, config: dict, quiet: bool = False,
                      ignore_handwritten: bool = False) -> str | None:
    """Body of the generated CLAUDE.md (without header), or None when nothing is generated
    (also handwritten CLAUDE.md, unless `ignore_handwritten`)."""
    instructions = manifest.get("instructions") or {}
    template_name = instructions.get("template")
    layerset = config_layers(config)
    merge_list = list(instructions.get("merge") or [])
    if not template_name and not merge_list and has_feature(manifest, config):
        merge_list = ["_base"]
    names = merge_list + ([template_name] if template_name else [])
    if not names:
        return None

    target = project_dir / "CLAUDE.md"
    if not ignore_handwritten and handwritten_claude_md(project_dir, manifest):
        if not quiet:
            print(f"[warn] Skipping handwritten instructions: {target}")
        return None

    parts = []
    for name in names:
        text = read_instruction(layerset, name)
        if text is None:
            if not quiet:
                print(f"[warn] Missing instructions template: {missing_instruction(layerset, name)}")
            continue
        parts.append(text)

    if not parts:
        return None

    content = apply_variables("\n\n".join(parts).rstrip() + "\n", manifest, config)

    if not template_name and (project_dir / ".github" / "copilot-instructions.md").exists():
        content += "\n## Project instructions\n\nRead `.github/copilot-instructions.md` for project-specific conventions.\n"
    return content


def build_claude_md(project_dir: Path, manifest: dict, config: dict, dry_run: bool = False, mode: str = "inline") -> None:
    # Same source of truth as copilot-instructions.md (instructions.merge + .template);
    # this just renders it to CLAUDE.md at the project root with the Claude Code
    # boilerplate header prepended, so every skillsync-linked project gets a
    # Claude Code–aware CLAUDE.md for free, created if missing. In `agents` mode the
    # body lives in AGENTS.md and CLAUDE.md only imports it.
    if not claude_enabled(manifest):
        return
    content = claude_md_content(project_dir, manifest, config)
    if content is None:
        return
    target = project_dir / "CLAUDE.md"
    if mode == "agents":
        content = CLAUDE_IMPORT
    if dry_run:
        print(f"[dry-run] Would write: {target}")
        ensure_root_gitignore(project_dir, dry_run=dry_run)
        return
    write_text_lf(target, CLAUDE_MD_HEADER + content)
    ensure_root_gitignore(project_dir, dry_run=dry_run)


def project_skills(manifest: dict, config: dict) -> list[str]:
    return expand_skills(manifest.get("skills", []), config_layers(config), config.get("tags", {}), manifest)


def has_feature(manifest: dict, config: dict) -> bool:
    expanded = project_skills(manifest, config)
    return WORKFLOW_SKILL in expanded or LEGACY_WORKFLOW_SKILL in expanded


def codex_defaults(config: dict) -> dict:
    """`codex.yaml` merged across the layers (maps deep, `exclude_skills` = union)."""
    data = config_layers(config).merged_file("codex.yaml").data
    return data if isinstance(data, dict) else {}


def resolve_roles(manifest: dict, config: dict) -> list[str]:
    """Same roster for Claude and Codex: explicit `agents:` wins; feature defaults to all roles."""
    if "agents" in manifest:
        roles = []
        for name in manifest.get("agents") or []:
            name = RENAMED_ROLE_ALIASES.get(name, name)
            if name not in roles:
                roles.append(name)
        return roles
    if not has_feature(manifest, config):
        return []
    agents = codex_defaults(config).get("agents", {})
    return list(agents) if isinstance(agents, dict) else []


def codex_enabled(manifest: dict, config: dict) -> bool:
    value = manifest.get("codex")
    if value is False or isinstance(value, dict) and value.get("enabled") is False:
        return False
    return has_feature(manifest, config) or value is True or isinstance(value, dict)


def write_managed_text(path: Path, content: str, state: dict, key: str, dry_run: bool = False) -> bool:
    """Derived files only: local edits are never pushed back as source or overwritten."""
    if path.is_symlink() or path.exists() and (key not in state or tree_hash(path) != state[key]):
        print(f"[conflict] Preserving local file: {path}; edit library sources to regenerate")
        return False
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return True
    if dry_run:
        print(f"[dry-run] Would generate: {path}")
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(path, content)
    state[key] = tree_hash(path)
    return True


def replace_managed_block(original: str, start: str, end: str, body: str) -> str:
    if original.count(start) != original.count(end) or original.count(start) > 1:
        raise ValueError("Malformed skillsync markers")
    block = start + "\n" + body.rstrip() + "\n" + end if body else ""
    if start in original:
        before, rest = original.split(start, 1)
        middle, after = rest.split(end, 1)
        return before + block + after
    return original + ("\n" if original and not original.endswith("\n") else "") + ("\n" if original else "") + block + ("\n" if block else "")


def update_codex_entrypoint(project_dir: Path, enabled: bool, dry_run: bool = False) -> None:
    target = project_dir / "AGENTS.md"
    if target.is_symlink():
        print(f"[warn] Symlinked AGENTS.md left intact; include .codex/skillsync-instructions.md manually: {target}")
        return
    original = target.read_text(encoding="utf-8") if target.exists() else ""
    body = ("## Skillsync: Codex\n\n"
            "Read `.codex/skillsync-instructions.md` before working in this project.\n"
            "For an implementation using `$feature`, read `.agents/skills/feature/SKILL.md`\n"
            "and its Codex adapter. Delegate the planned roles to native subagents from\n"
            "`.codex/agents/`; do not start this pipeline for questions or audits.\n"
            "Shared sources are in the skillsync library; do not edit generated TOML files.") if enabled else ""
    if not enabled and "<!-- SKILLSYNC:CODEX-BEGIN -->" not in original:
        return
    try:
        content = replace_managed_block(original, "<!-- SKILLSYNC:CODEX-BEGIN -->", "<!-- SKILLSYNC:CODEX-END -->", body)
    except ValueError as exc:
        print(f"[warn] {target}: {exc}")
        return
    if content == original:
        return
    if dry_run:
        print(f"[dry-run] Would update Codex entrypoint: {target}")
        return
    if original:
        backup = target.with_name("AGENTS.md.skillsync-backup")
        if not backup.exists():
            write_text_lf(backup, original)
        ensure_root_gitignore(project_dir, entry=backup.name)
    write_text_lf(target, content)
    if not original:
        ensure_root_gitignore(project_dir, entry="AGENTS.md")


def instructions_entrypoint(manifest: dict, config: dict) -> str:
    """`instructions.entrypoint` from the manifest, else `entrypoint` from the machine config, else `inline`."""
    value = (manifest.get("instructions") or {}).get("entrypoint")
    if value is None:
        value = config.get("entrypoint")
    if value is None:
        return "inline"
    if value not in ENTRYPOINT_MODES:
        print(f"[warn] Unknown instructions entrypoint {value!r}; using inline (allowed: {', '.join(ENTRYPOINT_MODES)})")
        return "inline"
    return value


def only_generator_block(text: str) -> bool:
    if text.count(AGENTS_BEGIN) != 1 or text.count(AGENTS_END) != 1 or text.index(AGENTS_BEGIN) > text.index(AGENTS_END):
        return not text.strip()
    before, rest = text.split(AGENTS_BEGIN, 1)
    return not (before + rest.split(AGENTS_END, 1)[1]).strip()


def agents_md_shareable(project_dir: Path, text: str) -> bool:
    """False when filling AGENTS.md could publish private instructions: inside git and not ignored,
    or outside git with own content. Any git failure counts as "not ignored"."""
    def git(*args: str) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(["git", "-C", str(project_dir), *args], capture_output=True, text=True, timeout=10,
                                  env={**os.environ, "LC_ALL": "C"})
        except (OSError, subprocess.SubprocessError):
            return None
    inside = git("rev-parse", "--is-inside-work-tree")
    if inside is None:
        return False
    if inside.returncode != 0:
        # Only a plain "not a git repository" means outside git; other git errors count as "not ignored".
        return "not a git repository" in (inside.stderr or "").lower() and only_generator_block(text)
    ignored = git("check-ignore", "-q", "--", "AGENTS.md")
    return ignored is not None and ignored.returncode == 0


def write_agents_block(project_dir: Path, manifest: dict, config: dict, dry_run: bool = False) -> str:
    """`agents` mode: write the instruction body once into the AGENTS.md block. Returns the effective mode;
    `inline` (also on fallback) means the generator keeps today's three files."""
    if instructions_entrypoint(manifest, config) != "agents":
        return "inline"
    codex = codex_enabled(manifest, config)
    claude_imports = claude_enabled(manifest) and not handwritten_claude_md(project_dir, manifest)
    if not (claude_imports or codex):
        return "inline"
    # Warnings come from build_claude_md when Claude renders; otherwise from here.
    content = claude_md_content(project_dir, manifest, config, quiet=claude_imports, ignore_handwritten=True)
    if content is None:
        return "inline"
    target = project_dir / "AGENTS.md"
    if target.is_symlink():
        print(f"[warn] Symlinked AGENTS.md left intact; instructions stay in CLAUDE.md: {target}")
        return "inline"
    original = target.read_text(encoding="utf-8") if target.exists() else ""
    if target.exists() and not agents_md_shareable(project_dir, original):
        print(f"[warn] AGENTS.md is shared (not ignored by git or has own content); instructions stay in CLAUDE.md: {target}")
        return "inline"
    body = content.rstrip() + ("\n\n" + CODEX_NOTE_BEGIN + "\n" + CODEX_NOTE + "\n" + CODEX_NOTE_END if codex else "")
    try:
        updated = replace_managed_block(original, AGENTS_BEGIN, AGENTS_END, body)
    except ValueError as exc:
        print(f"[warn] {target}: {exc}; instructions stay in CLAUDE.md")
        return "inline"
    if updated == original:
        return "agents"
    if dry_run:
        print(f"[dry-run] Would write instructions block: {target}")
        return "agents"
    if original and not only_generator_block(original):
        backup = target.with_name("AGENTS.md.skillsync-backup")
        if not backup.exists():
            write_text_lf(backup, original)
        ensure_root_gitignore(project_dir, entry=backup.name)
    write_text_lf(target, updated)
    if not original:
        ensure_root_gitignore(project_dir, entry="AGENTS.md")
    return "agents"


def update_codex_config(project_dir: Path, limit: int, enabled: bool, dry_run: bool = False) -> None:
    target = project_dir / ".codex" / "config.toml"
    if target.is_symlink():
        print(f"[warn] Not modifying symlinked Codex config: {target}")
        return
    original = target.read_text(encoding="utf-8") if target.exists() else ""
    start, end = "# SKILLSYNC:AGENTS-BEGIN", "# SKILLSYNC:AGENTS-END"
    if not enabled and start not in original:
        return
    try:
        tomllib.loads(original)
        stripped = replace_managed_block(original, start, end, "") if start in original else original
        data = tomllib.loads(stripped)
        agents = data.get("agents", {})
        lines = []
        if enabled:
            if "enabled" not in agents:
                lines.append("enabled = true")
            elif not agents["enabled"]:
                print(f"[warn] Native Codex agents disabled by local config: {target}")
            if "max_concurrent_threads_per_session" not in agents and "max_threads" not in agents:
                lines.append(f"max_concurrent_threads_per_session = {limit}")
        if start in original:
            content = replace_managed_block(original, start, end, "\n".join(lines))
        elif lines:
            block = start + "\n" + "\n".join(lines) + "\n" + end + "\n"
            table = re.search(r"(?m)^\s*\[agents\][ \t]*(?:#[^\n]*)?\n", original)
            if table:
                content = original[:table.end()] + block + original[table.end():]
            elif "agents" in data:
                print(f"[warn] Nonstandard agents table, configure manually: {target}")
                return
            else:
                content = original.rstrip() + ("\n\n" if original.strip() else "") + "[agents]\n" + block
        else:
            content = stripped
        tomllib.loads(content)
    except (ValueError, TypeError) as exc:
        print(f"[warn] Invalid Codex config or markers, preserving {target}: {exc}")
        return
    if content == original:
        return
    if dry_run:
        print(f"[dry-run] Would update native Codex settings: {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if original:
        backup = target.with_name("config.toml.skillsync-backup")
        if not backup.exists():
            write_text_lf(backup, original)
    write_text_lf(target, content)


def build_codex(project_dir: Path, manifest: dict, config: dict, dry_run: bool = False, mode: str = "inline") -> None:
    enabled = codex_enabled(manifest, config)
    layerset = config_layers(config)
    roots = managed_roots(layerset)
    defaults = codex_defaults(config)
    if enabled and not defaults:
        print(f"[warn] Missing native Codex defaults: codex.yaml (layers: {', '.join(layerset.ids) or 'none'})")
        return
    local = manifest.get("codex") if isinstance(manifest.get("codex"), dict) else {}
    limit = local.get("max_concurrent_threads_per_session", defaults.get("max_concurrent_threads_per_session", 3))
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("codex.max_concurrent_threads_per_session must be a positive integer")
    state = load_state(project_dir)
    method = (manifest.get("sync") or {}).get("method", "symlink")
    skills = project_skills(manifest, config) if enabled else []
    excluded = (defaults.get("exclude_skills") or []) + (local.get("exclude_skills") or [])
    skills = [name for name in skills if name not in excluded]
    skill_target = project_dir / ".agents" / "skills"
    prune_managed(skill_target, "codex-skill", set(skills), roots, state, dry_run)
    for name in skills:
        dest = skill_target / name
        src = skill_source(config, name, method, dry_run)
        if src is None:
            print(f"[warn] Missing skill: skills/{name} (layers: {', '.join(layerset.ids) or 'none'})")
            continue
        if dest.is_symlink() and not is_library_symlink(dest, roots):
            print(f"[conflict] Preserving external skill symlink: {dest}")
            continue
        place(src, dest, method, state, f"codex-skill:{name}", False, dry_run)
    wanted = set()
    for source_name in (resolve_roles(manifest, config) if enabled else []):
        overrides = local.get("agent_overrides", {})
        old_names = [old for old, new in RENAMED_ROLE_ALIASES.items() if new == source_name]
        settings = {**defaults.get("agents", {}).get(source_name, {}),
                    **{k: v for old in old_names for k, v in overrides.get(old, {}).items()},
                    **overrides.get(source_name, {})}
        if not settings:
            print(f"[warn] No Codex mapping for role: {source_name}")
            continue
        name = settings.get("name", "")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or Path(source_name).name != source_name:
            raise ValueError(f"Invalid Codex role name: {source_name} -> {name}")
        filename = name + ".toml"
        if filename in wanted:
            raise ValueError(f"Duplicate Codex role name: {name}")
        wanted.add(filename)
        found = layerset.resolve(f"agents/{source_name}.md")
        if found is None:
            print(f"[warn] Missing shared role: agents/{source_name}.md (layers: {', '.join(layerset.ids) or 'none'})")
            continue
        source = found[1]
        parts = source.read_text(encoding="utf-8").split("---", 2)
        if len(parts) != 3 or parts[0].strip():
            raise ValueError(f"Missing role YAML frontmatter: {source}")
        metadata, body = yaml.safe_load(parts[1]), parts[2].strip()
        settings["description"] = metadata["description"]
        settings["developer_instructions"] = body
        allowed = {"name", "description", "developer_instructions", "model", "model_reasoning_effort", "sandbox_mode"}
        if set(settings) - allowed or not all(isinstance(value, str) for value in settings.values()):
            raise ValueError(f"Unsupported Codex settings for {source_name}")
        content = "# Generated by skillsync from shared agents/*.md and codex.yaml.\n"
        content += "\n".join(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in settings.items()) + "\n"
        tomllib.loads(content)
        write_managed_text(project_dir / ".codex" / "agents" / filename, content, state, f"codex-agent:{filename}", dry_run)
    prune_managed(project_dir / ".codex" / "agents", "codex-agent", wanted, roots, state, dry_run)
    instruction_path = project_dir / ".codex" / "skillsync-instructions.md"
    instruction_key = "codex-instructions:skillsync-instructions.md"
    if enabled and mode == "agents":
        # The body lives in the AGENTS.md block; drop the managed copy, keep a locally edited one.
        if instruction_path.is_file() and not instruction_path.is_symlink():
            if instruction_key in state and tree_hash(instruction_path) == state[instruction_key]:
                if not dry_run:
                    instruction_path.unlink()
                    state.pop(instruction_key)
            else:
                print(f"[warn] Keeping locally edited {instruction_path}; instructions now live in AGENTS.md")
    elif enabled:
        instructions = manifest.get("instructions") or {}
        names = list(instructions.get("merge") or ["_base"])
        if instructions.get("template"):
            names.append(instructions["template"])
        parts = []
        for name in dict.fromkeys(names):
            text = read_instruction(layerset, name)
            if text is not None:
                parts.append(text)
            else:
                print(f"[warn] Missing instructions template: {missing_instruction(layerset, name)}")
        if not instructions.get("template") and (project_dir / ".github" / "copilot-instructions.md").exists():
            parts.append("Read `.github/copilot-instructions.md` for project-specific instructions.\n")
        content = apply_variables("\n\n".join(parts).rstrip() + "\n", manifest, config)
        write_managed_text(instruction_path, content, state, instruction_key, dry_run)
    elif instruction_key in state and instruction_path.is_file() and not instruction_path.is_symlink() and tree_hash(instruction_path) == state[instruction_key]:
        if not dry_run:
            instruction_path.unlink()
            state.pop(instruction_key)
    update_codex_config(project_dir, limit, enabled, dry_run)
    if mode != "agents":
        update_codex_entrypoint(project_dir, enabled, dry_run)
    if enabled and not dry_run:
        for directory, entries in ((project_dir / ".codex", ["config.toml", "config.toml.skillsync-backup", "skillsync-instructions.md"] + ["agents/" + name for name in sorted(wanted)]),
                                   (project_dir / ".agents", ["skills/" + name for name in skills])):
            directory.mkdir(parents=True, exist_ok=True)
            for entry in entries:
                ensure_root_gitignore(directory, entry=entry)
    save_state(project_dir, state, dry_run)


LINK_TASK_LABEL = "skillsync: link"


_LINK_TAIL = ["link", "${workspaceFolder}"]


def _managed_prefix_len(args: list) -> int | None:
    """Length of the self-command part of args for a task shape generated by skillsync, else None."""
    if not all(isinstance(a, str) for a in args):
        return None
    if args == _LINK_TAIL:
        return 0  # entry shape: command = the skillsync entry itself (name checked by `is_managed_link_task`)
    if len(args) == 3 and args[1:] == _LINK_TAIL and Path(args[0].replace("\\", "/")).name == "skillsync":
        return 1  # old shape: command = python, args = [wrapper, link, folder]
    if len(args) >= 3 and args[-2:] == _LINK_TAIL:
        head = args[:-2]
        if head[-3:] == ["-P", "-m", "skillsync_workflow"]:
            return len(head)
        if head and Path(head[-1].replace("\\", "/")).name == "skillsync":
            return len(head)
    return None


def is_managed_link_task(task: dict) -> bool:
    """True for the shapes `ensure_link_on_open` generates: the current ones (K6) and the legacy one."""
    args = task.get("args")
    if not (task.get("type") == "process" and isinstance(task.get("command"), str) and isinstance(args, list)
            and task.get("runOptions") == {"runOn": "folderOpen"}):
        return False
    prefix = _managed_prefix_len(args)
    if prefix is None:
        return False
    if prefix == 0:  # entry shape: the command must be a skillsync entry
        return Path(task["command"].replace("\\", "/")).name.lower() in ("skillsync", "skillsync.exe")
    return True


def ensure_link_on_open(project_dir: Path, manifest: dict, config: dict, dry_run: bool = False) -> None:
    """Bootstrap folder-open sync for feature projects; refresh the script path of our own task, never replace custom tasks."""
    if not has_feature(manifest, config) or not (claude_enabled(manifest) or codex_enabled(manifest, config)):
        return
    if (manifest.get("vscode") or {}).get("link_on_open") is False:
        return

    target = project_dir / ".vscode" / "tasks.json"
    original = target.read_text(encoding="utf-8") if target.exists() else ""
    try:
        data = json.loads(strip_jsonc(original)) if original else {"version": "2.0.0", "tasks": []}
    except json.JSONDecodeError:
        print(f"[warn] Invalid tasks JSONC, not overwriting: {target}")
        return
    if not isinstance(data, dict) or not isinstance(data.get("tasks", []), list):
        print(f"[warn] Invalid task structure, not overwriting: {target}")
        return
    tasks = data.get("tasks", [])
    from skillsync_workflow.selfcmd import link_task_argv
    argv = link_task_argv()
    existing = [task for task in tasks if isinstance(task, dict) and task.get("label") == LINK_TASK_LABEL]
    if existing:
        # Only the task shape generated by skillsync is refreshed; anything else is the user's own.
        managed = next((task for task in existing if is_managed_link_task(task)), None)
        if managed is None:
            return
        prefix = _managed_prefix_len(managed["args"])
        if managed["command"] == argv[0] and managed["args"][:prefix] == argv[1:]:
            return
        managed["command"] = argv[0]
        managed["args"] = argv[1:] + managed["args"][prefix:]
        message = f"[update] Refreshed folder-open sync path: {target}"
        dry_message = f"[dry-run] Would refresh folder-open sync path: {target}"
    else:
        tasks.append({
            "label": LINK_TASK_LABEL,
            "type": "process",
            "command": argv[0],
            "args": argv[1:] + _LINK_TAIL,
            "runOptions": {"runOn": "folderOpen"},
            "presentation": {"reveal": "silent", "panel": "shared"},
            "problemMatcher": [],
        })
        message = f"[update] Added folder-open sync: {target}"
        dry_message = f"[dry-run] Would add folder-open sync: {target}"
    data["tasks"] = tasks
    if dry_run:
        print(dry_message)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if original:
        write_text_lf(target.with_name("tasks.json.skillsync-backup"), original)
    write_text_lf(target, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(message)


def ensure_cursor_gitignore(cursor_rules_dir: Path, dry_run: bool = False) -> None:
    gitignore_path = cursor_rules_dir / ".gitignore"
    entries = ["*.mdc"]

    if gitignore_path.exists():
        existing = gitignore_path.read_text(encoding="utf-8").splitlines()
    else:
        existing = []

    updated = list(existing)
    for entry in entries:
        if entry not in existing:
            updated.append(entry)

    if updated == existing:
        return

    if dry_run:
        print(f"[dry-run] Would update: {gitignore_path}")
        return

    write_text_lf(gitignore_path, "\n".join(updated).rstrip() + "\n")


def build_cursor_rules(project_dir: Path, manifest: dict, config: dict, dry_run: bool = False) -> None:
    cursor_config = manifest.get("cursor") or {}
    if not cursor_config:
        return

    layerset = config_layers(config)
    cursor_rules_dir = project_dir / ".cursor" / "rules"
    if not dry_run:
        cursor_rules_dir.mkdir(parents=True, exist_ok=True)

    rules_config = cursor_config.get("rules") or {}

    # Generate base.mdc from base template (no merging - standalone file)
    base_template = rules_config.get("base")
    if base_template:
        text = read_instruction(layerset, base_template)
        if text is not None:
            dest = cursor_rules_dir / "base.mdc"
            if dry_run:
                print(f"[dry-run] Would write: {dest}")
            else:
                content = apply_variables(text, manifest, config)
                write_text_lf(dest, content)
        else:
            print(f"[warn] Missing cursor base template: {missing_instruction(layerset, base_template)}")

    # Generate project.mdc from project template (no merging - standalone file)
    project_template = rules_config.get("project")
    if project_template:
        text = read_instruction(layerset, project_template)
        if text is not None:
            dest = cursor_rules_dir / "project.mdc"
            if dry_run:
                print(f"[dry-run] Would write: {dest}")
            else:
                content = apply_variables(text, manifest, config)
                write_text_lf(dest, content)
        else:
            print(f"[warn] Missing cursor project template: {missing_instruction(layerset, project_template)}")

    # Link skills as .mdc files
    include_skills = cursor_config.get("skills", True)
    if include_skills:
        skills = project_skills(manifest, config)
        method = (manifest.get("sync") or {}).get("method", "symlink")

        # Remove obsolete .mdc skill symlinks
        managed_names = {f"{s}.mdc" for s in skills}
        reserved_names = {"base.mdc", "project.mdc", ".gitignore"}
        if cursor_rules_dir.exists():
            for existing in cursor_rules_dir.iterdir():
                if existing.name in reserved_names:
                    continue
                if existing.suffix == ".mdc" and existing.name not in managed_names:
                    if dry_run:
                        print(f"[dry-run] Would remove obsolete cursor rule: {existing.name}")
                        continue
                    existing.unlink()
                    print(f"[clean] Removed obsolete cursor rule: {existing.name}")

        for skill in skills:
            found = layerset.resolve(f"skills/{skill}/SKILL.md")
            if found is None:
                print(f"[warn] Missing SKILL.md for cursor skill: {skill}")
                continue
            src = found[1]
            dest = cursor_rules_dir / f"{skill}.mdc"
            if dest.exists() or dest.is_symlink():
                if dest.is_symlink() and dest.resolve() == src.resolve():
                    continue
                if dry_run:
                    print(f"[dry-run] Would replace: {dest}")
                    continue
                dest.unlink()
            if dry_run:
                print(f"[dry-run] Would create: {dest}")
                continue
            if method == "copy" or not try_symlink(src, dest):
                shutil.copy2(src, dest)

    ensure_cursor_gitignore(cursor_rules_dir, dry_run=dry_run)


_symlink_warned = False


def _normalized_bytes(data: bytes) -> bytes:
    if b"\0" in data[:8000]:
        return data
    return data.replace(b"\r\n", b"\n")


def tree_hash(path: Path) -> str:
    # CRLF/LF-insensitive so autocrlf on Windows does not look like a local edit.
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(_normalized_bytes(path.read_bytes()))
        return digest.hexdigest()
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = file.relative_to(path)
        if ".git" in rel.parts:
            continue
        digest.update(rel.as_posix().encode("utf-8") + b"\0")
        digest.update(_normalized_bytes(file.read_bytes()) + b"\0")
    return digest.hexdigest()


def files_hash(files: dict) -> str:
    """`tree_hash` of the directory that a copy of `files` ({relative posix path: file}) would give."""
    digest = hashlib.sha256()
    for rel in sorted(files, key=lambda item: Path(item)):
        if ".git" in Path(rel).parts:
            continue
        digest.update(Path(rel).as_posix().encode("utf-8") + b"\0")
        digest.update(_normalized_bytes(Path(files[rel]).read_bytes()) + b"\0")
    return digest.hexdigest()


class Source:
    """What `place` links or copies.

    `link`: symlink target (a layer file, the skill directory of its only layer, or a composite in the
    cache); None = copy only. `files`: {relative path: file} of a directory source, copied per file
    from `layers.layer_files` (never `copytree`/`rglob` over a layer, D73); None for a single file.
    `owners`: {relative path ('' for a single file): owning layer id} recorded in the copy state."""

    def __init__(self, link, files: dict | None = None, owners: dict | None = None):
        self.link = Path(link) if link is not None else None
        self.files = files
        self.owners = dict(owners or {})

    def file_map(self) -> dict:
        """{relative path ('' = the single file itself): source file}."""
        if self.files is None:
            return {"": self.link} if self.link is not None else {}
        return dict(self.files)

    def owner(self, rel: str):
        return self.owners.get(rel)

    @classmethod
    def from_path(cls, path) -> "Source":
        path = Path(path)
        if path.is_dir():
            layers = _layers_module()
            files = {rel: path / rel for rel in layers.layer_files(layers.Layer("source", "core", path))}
            return cls(path, files)
        return cls(path)

    def hash(self) -> str:
        return tree_hash(self.link) if self.files is None else files_hash(self.files)

    def copy_to(self, dest: Path) -> None:
        if dest.exists() or dest.is_symlink():
            remove_item(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if self.files is None:
            shutil.copy2(self.link, dest)
            return
        dest.mkdir()
        for rel, path in sorted(self.files.items()):
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


# Copy-mode state (K4): `files: {"<kind>:<name>[/<rel>]": {"owner": <layer id>, "base": <hash>}}`, one
# entry per copied file (`base` = content hash at copy time, the per-file merge base; None = local edit
# of unknown base after converting a legacy entry). Legacy entries `{"<kind>:<name>": <tree hash>}` are
# converted once when met (`convert_legacy_copy`). Derived files (`codex-agent:*`, `codex-instructions:*`)
# keep `{key: hash}`.
FILES_KEY = "files"
COPY_KINDS = ("github-skill", "claude-skill", "claude-agent", "codex-skill")


def _entry_key(key: str, rel: str) -> str:
    return f"{key}/{rel}" if rel else key


def copy_entries(state: dict, key: str) -> dict:
    """{relative path ('' = single file): entry} of the copy `key` in `state`."""
    entries = {}
    for name, entry in (state.get(FILES_KEY) or {}).items():
        if name == key:
            entries[""] = entry
        elif name.startswith(key + "/"):
            entries[name[len(key) + 1:]] = entry
    return entries


def set_copy_entry(state: dict, key: str, rel: str, owner, base, **extra) -> None:
    state.setdefault(FILES_KEY, {})[_entry_key(key, rel)] = {"owner": owner, "base": base, **extra}


OUTSIDE_SOURCE = "outside_source"  # entry flag: a file of a diverged legacy copy that the layers do not have


def drop_copy_entry(state: dict, key: str, rel: str) -> None:
    files = state.get(FILES_KEY) or {}
    files.pop(_entry_key(key, rel), None)
    if FILES_KEY in state and not files:
        state.pop(FILES_KEY)


def drop_copy(state: dict, key: str) -> None:
    """Forget the copy `key` (legacy entry and every per-file entry)."""
    state.pop(key, None)
    for rel in list(copy_entries(state, key)):
        drop_copy_entry(state, key, rel)


def copy_tracked(state: dict, key: str) -> bool:
    return isinstance(state.get(key), str) or bool(copy_entries(state, key))


def copy_keys(state: dict) -> list[str]:
    """Copy keys (`<kind>:<name>`) present in `state`, legacy or per file."""
    keys = {key for key, value in state.items() if isinstance(value, str) and key.partition(":")[0] in COPY_KINDS}
    for name in state.get(FILES_KEY) or {}:
        kind, _, rest = name.partition(":")
        if kind in COPY_KINDS and rest:
            keys.add(f"{kind}:{rest.split('/', 1)[0]}")
    return sorted(keys)


def copy_files(dest: Path) -> list[str]:
    """Relative posix paths of regular files of a copy (`''` for a single file), links and `.git` skipped."""
    if dest.is_file() and not dest.is_symlink():
        return [""]
    found = []
    for current, dirs, files in os.walk(dest, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d != ".git" and not (Path(current) / d).is_symlink())
        for name in files:
            path = Path(current) / name
            if not path.is_symlink() and path.is_file():
                found.append(path.relative_to(dest).as_posix())
    return sorted(found)


def _copy_path(dest: Path, rel: str) -> Path:
    return dest / rel if rel else dest


def convert_legacy_copy(state: dict, key: str, dest: Path, src: "Source") -> None:
    """One-time conversion of a legacy `{key: tree hash}` entry to per-file entries (K4).

    An untouched copy (tree hash equal to the legacy base) gets the exact base of every file (its
    current content); otherwise files equal to the current source get the source hash as base and
    the other ones are marked as local edits (`base: None`), never overwritten. In a diverged copy, files
    that are not in the source (maybe deleted in the layer) are marked `outside_source` with an `[info]`:
    `push` never adopts them (a file deleted in a layer is not restored). In an untouched copy such files
    were in the old source, so they keep their exact base and `link` removes them like any deleted file."""
    base = state.pop(key, None)
    if not isinstance(base, str) or not (dest.exists() and not dest.is_symlink()):
        return
    untouched = tree_hash(dest) == base
    sources = src.file_map()
    for rel in copy_files(dest):
        current = tree_hash(_copy_path(dest, rel))
        if untouched:
            set_copy_entry(state, key, rel, src.owner(rel), current)
        elif rel in sources and Path(sources[rel]).is_file() and tree_hash(Path(sources[rel])) == current:
            set_copy_entry(state, key, rel, src.owner(rel), current)
        elif rel in sources:
            set_copy_entry(state, key, rel, src.owner(rel), None)
        else:
            set_copy_entry(state, key, rel, None, None, **{OUTSIDE_SOURCE: True})
            print(f"[info] {_copy_path(dest, rel)} is not in the layers (state conversion); it is kept and never "
                  "pushed - add it to a layer by hand or delete it")


def record_copy(state: dict, key: str, src: "Source") -> None:
    """Per-file entries of a fresh copy of `src` (base = source content hash)."""
    drop_copy(state, key)
    for rel, path in sorted(src.file_map().items()):
        set_copy_entry(state, key, rel, src.owner(rel), tree_hash(Path(path)))


def copy_untouched(path: Path, state: dict, key: str) -> bool:
    """True when the managed copy `path` has no local change (safe to remove)."""
    legacy = state.get(key)
    if isinstance(legacy, str):
        return tree_hash(path) == legacy
    entries = copy_entries(state, key)
    if not entries:
        return False
    for rel, entry in entries.items():
        target = _copy_path(path, rel)
        if target.exists() and (entry.get("base") is None or tree_hash(target) != entry["base"]):
            return False
    return set(copy_files(path)) <= set(entries)


def unknown_base_message(project_dir: Path, path: Path) -> str:
    """Q3: a copy file of unknown merge base (`base: None` after converting a legacy entry) that differs
    from its owner's file is a conflict, resolved by hand."""
    return (f"{project_dir}: {path} (unknown merge base after state conversion); move the change into the "
            "layer by hand (git), or delete the local copy and run `skillsync link` to recreate it from the "
            "layer with a merge base")


def untracked_message(project_dir: Path, path: Path) -> str:
    """A copy file without a state entry that differs from its owner's file (e.g. created by hand where the
    layers also have it)."""
    return (f"{project_dir}: {path} is not tracked in the copy state and differs from the layer file; move "
            "the change into the layer by hand (git), or delete the local copy and run `skillsync link`")


def _project_of(dest: Path) -> Path:
    """Project directory of a managed copy (`<project>/.github|.claude|.agents/<kind>/<name>`)."""
    return dest.parent.parent.parent


def _remove_empty_parents(path: Path, stop: Path) -> None:
    current = path.parent
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def merge_copy(src: "Source", dest: Path, state: dict, key: str, dry_run: bool = False) -> None:
    """Three-way merge per file of a managed copy with its source (copy mode, K4)."""
    if isinstance(state.get(key), str):
        convert_legacy_copy(state, key, dest, src)
    sources = src.file_map()
    single = src.files is None
    if single != (dest.is_file() and not dest.is_symlink()):
        print(f"[conflict] {dest} and the library both changed; not overwriting - resolve manually")
        return
    entries = copy_entries(state, key)
    refreshed, local, conflict = False, False, False
    for rel in sorted(set(sources) | set(entries)):
        target = _copy_path(dest, rel)
        entry = entries.get(rel)
        base = entry.get("base") if entry else None
        source = sources.get(rel)
        if source is None:  # removed from the layers
            if entry is None or entry.get(OUTSIDE_SOURCE):
                continue  # `outside_source`: reported once at the conversion, kept, never pushed
            if not target.exists():
                drop_copy_entry(state, key, rel)
            elif base is not None and tree_hash(target) == base:
                if dry_run:
                    print(f"[dry-run] Would update copy: {dest}")
                    return
                target.unlink()
                _remove_empty_parents(target, dest)
                drop_copy_entry(state, key, rel)
                refreshed = True
            else:
                local = True
            continue
        source_hash = tree_hash(Path(source))
        if not target.exists():
            if entry is not None and (base is None or base == source_hash):
                local = True  # deleted locally; `push` never deletes layer files
                continue
            if entry is not None:
                conflict = True
                continue
            if dry_run:
                print(f"[dry-run] Would update copy: {dest}")
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            set_copy_entry(state, key, rel, src.owner(rel), source_hash)
            refreshed = True
            continue
        current = tree_hash(target)
        if current == source_hash:
            set_copy_entry(state, key, rel, src.owner(rel), source_hash)
        elif base is not None and current == base:
            if dry_run:
                print(f"[dry-run] Would update copy: {dest}")
                return
            shutil.copy2(source, target)
            set_copy_entry(state, key, rel, src.owner(rel), source_hash)
            refreshed = True
        elif entry is None:
            print(f"[warn] {untracked_message(_project_of(dest), target)}")
        elif base is None:
            print(f"[warn] {unknown_base_message(_project_of(dest), target)}")
        elif source_hash == base:
            local = True
        else:
            conflict = True
    if not single and set(copy_files(dest)) - set(sources) - set(copy_entries(state, key)):
        local = True  # new local files (adopted by `push`)
    if refreshed:
        print(f"[update] Refreshed copy: {dest}")
    if conflict:
        print(f"[conflict] {dest} and the library both changed; not overwriting - resolve manually")
    elif local:
        print(f"[info] Local edits in {dest} not pushed yet (run `skillsync push`)")


def load_state(project_dir: Path) -> dict:
    state_path = project_dir / STATE_FILE
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(project_dir: Path, state: dict, dry_run: bool = False) -> None:
    if dry_run or state == load_state(project_dir):
        return
    state_path = project_dir / STATE_FILE
    if not state and not state_path.exists():
        return
    state_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(state_path, json.dumps(state, indent=2, sort_keys=True) + "\n")
    gitignore_path = state_path.parent / ".gitignore"
    lines = gitignore_path.read_text(encoding="utf-8").splitlines() if gitignore_path.exists() else []
    if STATE_FILE.name not in lines:
        write_text_lf(gitignore_path, "\n".join(lines + [STATE_FILE.name]).rstrip() + "\n")


def remove_item(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def copy_item(src: Path, dest: Path) -> None:
    if dest.exists() or dest.is_symlink():
        remove_item(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dest)
    else:
        shutil.copy2(src, dest)


_TRUE_VALUES = ("1", "true", "yes", "on")
_FALSE_VALUES = ("", "0", "false", "no", "off")
_WARNED_NO_SYMLINK = set()


def symlinks_disabled() -> bool:
    """SKILLSYNC_NO_SYMLINK: 1/true/yes/on (any case) force copy mode; unset, empty, 0/false/no/off do not.

    Any other value warns once and counts as set: copy mode never creates a link, so it is the safe reading.
    """
    raw = os.environ.get("SKILLSYNC_NO_SYMLINK")
    if raw is None:
        return False
    value = raw.strip().lower()
    if value in _FALSE_VALUES:
        return False
    if value not in _TRUE_VALUES and value not in _WARNED_NO_SYMLINK:
        _WARNED_NO_SYMLINK.add(value)
        print("[warn] SKILLSYNC_NO_SYMLINK has an unrecognized value (use 1/true/yes/on or 0/false/no/off); "
              "treating it as set (copy mode)", file=sys.stderr)
    return True


def try_symlink(src: Path, dest: Path) -> bool:
    global _symlink_warned
    if symlinks_disabled():
        return False
    try:
        os.symlink(src, dest, target_is_directory=src.is_dir())
        return True
    except (OSError, NotImplementedError) as exc:
        if not _symlink_warned:
            print(f"[warn] Symlinks unavailable ({exc}); falling back to copy (Windows: enable Developer Mode)")
            _symlink_warned = True
        return False


def place(src, dest: Path, method: str, state: dict, key: str, owned: bool, dry_run: bool = False) -> None:
    """Link (or copy) a layer item to dest.

    `src` is a `Source` (a `Path` is wrapped). Copies are tracked in `state` (hash of the source
    content at copy time) so a local edit is never silently overwritten and `push` can send it back.
    `owned` means the whole target directory belongs to skillsync (.github/skills); otherwise (.claude/*)
    real files that skillsync did not create are left alone.
    """
    if not isinstance(src, Source):
        src = Source.from_path(src)
    if dest.is_symlink():
        if method != "copy" and src.link is not None and dest.resolve() == src.link.resolve():
            return
        if dry_run:
            print(f"[dry-run] Would replace: {dest}")
            return
        dest.unlink()
    elif dest.exists():
        if copy_tracked(state, key):
            merge_copy(src, dest, state, key, dry_run)  # per file `{owner, base}` (K4)
            return
        if not owned:
            print(f"[warn] Skipping {dest}: exists and is not managed by skillsync")
            return
        if dry_run:
            print(f"[dry-run] Would replace: {dest}")
            return
        remove_item(dest)

    if dry_run:
        print(f"[dry-run] Would create: {dest}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if method != "copy" and src.link is not None and try_symlink(src.link, dest):
        drop_copy(state, key)
        return
    src.copy_to(dest)
    record_copy(state, key, src)


def prune_managed(target_dir: Path, prefix: str, wanted: set, library_root, state: dict,
                  dry_run: bool = False, owned: bool = False) -> None:
    """Remove entries in target_dir that are no longer wanted.

    owned=True (.github/skills): anything unwanted goes. Otherwise only symlinks into a layer, the
    composite cache or `legacy_library` (`library_root`: a root or a list of roots, see
    `managed_roots`) and untouched managed copies are removed - hand-written files are never touched.
    """
    if not target_dir.exists():
        return
    for existing in target_dir.iterdir():
        if existing.name in wanted or existing.name == ".gitignore":
            continue
        key = f"{prefix}:{existing.name}"
        removable = False
        if owned:
            removable = existing.is_symlink() or existing.is_dir()
        elif is_library_symlink(existing, library_root):
            removable = True
        elif copy_tracked(state, key) and existing.exists() and not existing.is_symlink():
            removable = copy_untouched(existing, state, key)
        if not removable:
            continue
        if dry_run:
            print(f"[dry-run] Would remove obsolete: {existing.name}")
            continue
        remove_item(existing)
        drop_copy(state, key)
        print(f"[clean] Removed obsolete: {existing.name}")


def is_library_symlink(path: Path, library_root) -> bool:
    """True for a symlink into one of the managed roots (`library_root`: a path or a list of paths)."""
    if not path.is_symlink():
        return False
    roots = [library_root] if isinstance(library_root, (str, Path)) else list(library_root)
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return False
    for root in roots:
        try:
            resolved.relative_to(Path(root).expanduser().resolve())
            return True
        except (ValueError, OSError, RuntimeError):
            continue
    return False


def managed_roots(layerset) -> list[Path]:
    """Roots whose symlinks are generator-managed: every active and configured layer, the composite
    cache and `legacy_library` (old library, recognised only to clean up after migration)."""
    roots = [layer.root for layer in layerset.layers]
    roots += [layer.root for layer in layerset.config.layers.values()]
    roots.append(_layers_module().composed_root())
    if layerset.config.legacy_library:
        roots.append(Path(layerset.config.legacy_library))
    return list(dict.fromkeys(roots))


# ---------------------------------------------------------------- writes into layers (K4)

def status_code(*codes) -> int:
    """Combined exit code: BLOCKED (2) wins over FAIL (1) over PASS/NOT_RUN (0)."""
    return max([0, *[code or 0 for code in codes]])


def _detached_head(layer) -> bool:
    """True when the checkout of `layer` is a git repository on a detached HEAD (`ref` = tag/commit)."""
    layers = _layers_module()
    probe = layers._git_text(layer.path, "rev-parse", "--git-dir") if layer.path.is_dir() else None
    if probe is None or probe.returncode:
        return False
    branch = layers._git_text(layer.path, "symbolic-ref", "-q", "HEAD")
    return branch is not None and branch.returncode != 0


def layer_file_target(layer, rel: str) -> Path:
    """Path of `rel` in `layer` after the checks of a write: no escape (`..`, absolute) and no link on the
    path (D73). Raises LayerError (BLOCKED)."""
    layers = _layers_module()
    rel = layers._rel_strict(rel)
    target = layer.root / rel
    if not layer.root_clean or target.is_symlink() or not layers._clean(target if target.exists() else target.parent, layer.root):
        raise layers.LayerError(f"Layer {layer.id}: {rel} lies under a symlink/junction; not written",
                                "symlink-in-layer")
    current = layer.root
    for part in Path(rel).parent.parts:
        current = current / part
        if layers.is_link(current):
            raise layers.LayerError(f"Layer {layer.id}: {rel} lies under a symlink/junction; not written",
                                    "symlink-in-layer")
    return target


def write_layer_file(layer, rel: str, data: bytes) -> Path:
    """Atomic write of `rel` (relative to the layer root) in `layer`: temporary file + `os.replace`. Refuses
    paths escaping the layer and paths through links (D73)."""
    target = layer_file_target(layer, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.skillsync-{os.getpid()}.tmp")
    try:
        temporary.write_bytes(data)
        if target.exists():
            shutil.copymode(target, temporary)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


class LayerWriter:
    """Writes of one command into layers (K4). Planned writes go to the owner chosen by the caller;
    `commit` refuses read-only layers (`writable: false`), the core layer without
    `--to-layer <core>` (`[info]`, skipped), detached HEAD checkouts, and runs `leakcheck --target <layer>`
    on every planned file (content and name) before writing anything.

    One unit (a project of `push`, an `import`, a `skills add`) is all-or-nothing: a BLOCKED refusal or a
    leak writes none of its files. `soft=True` (adoption during `link`): refusals and leaks are warnings
    only and never change `status` (the caller keeps the file in `.orphans`)."""

    def __init__(self, layerset, to_layer=None, dry_run: bool = False, soft: bool = False):
        self.layerset = layerset
        self.to_layer = to_layer
        self.dry_run = dry_run
        self.soft = soft
        self.status = 0
        self.written = []
        self._guards = {}
        self._refusals = {}
        self._warned = set()
        self._reset()

    def _reset(self) -> None:
        self.planned = {}
        self.finalizers = []
        self.aborted = False

    @property
    def consent_core(self) -> bool:
        core = self.layerset.core
        return core is not None and self.to_layer == core.id

    def new_file_layer(self):
        """Layer of a new file without an owner: `--to-layer`, else personal (None = none active)."""
        return self.layerset.get(self.to_layer) if self.to_layer else self.layerset.personal

    def refusal(self, layer):
        """None when `layer` accepts writes, else (code, message); code 0 = skipped with `[info]`."""
        if layer.id not in self._refusals:
            result = None
            if layer.kind == "core" and not self.consent_core:
                result = (0, f"files of the core layer {layer.id} are written only with `--to-layer {layer.id}`")
            elif not layer.writable:
                result = (2, f"layer {layer.id} is read-only (writable: false)")
            elif _detached_head(layer):
                result = (2, f"layer {layer.id} is on a detached HEAD (ref {layer.ref or '-'}); switch it to a "
                             f"branch with `skillsync layers update {layer.id} --ref <branch>`")
            self._refusals[layer.id] = result
        return self._refusals[layer.id]

    def _note(self, level: str, label: str, message: str, code: int = 0) -> None:
        if self.soft:
            print(f"[warn] {label}: not adopted ({message})")
            return
        print(f"[{level}] {label}: {message}")
        if code:
            self.status = status_code(self.status, code)
            if code == 2:
                self.aborted = True

    def plan(self, layer, rel: str, data: bytes, label: str, on_done=None) -> bool:
        """Plan writing `data` to `rel` of `layer` (owner already chosen); False when refused/skipped."""
        if layer is None:
            self._note("blocked", label, "no personal layer is active; pass `--to-layer ID`", 2)
            return False
        refused = self.refusal(layer)
        if refused:
            code, message = refused
            self._note("blocked" if code else "info", label, message + ("" if code else "; skipped"), code)
            if self.soft:
                self.aborted = True
            return False
        try:  # path checks before any write of the unit (all or nothing)
            target = layer_file_target(layer, rel)
        except ValueError as exc:
            self._note("blocked", label, str(exc), 2)
            if self.soft:
                self.aborted = True
            return False
        key = (layer.id, rel)
        if key in self.planned:
            if self.planned[key][1] != data:
                self.conflict(f"{label}: {layer.id}:{rel} was also changed differently in another copy; "
                              "nothing written - make the copies equal or resolve manually")
                return False
            if on_done:
                self.planned[key][3].append(on_done)
            return True
        # Expected state of the owner's file (hash, or None = absent), compared again under the cache lock
        # in `commit`: a concurrent push that changed it meanwhile is a conflict, never overwritten.
        expected = tree_hash(target) if target.is_file() else None
        self.planned[key] = [layer, data, label, [on_done] if on_done else [], expected]
        return True

    def conflict(self, message: str) -> None:
        """A conflict that stops the whole unit (nothing written, BLOCKED): `[conflict] <message>`."""
        print(f"[conflict] {message}")
        if not self.soft:
            self.status = status_code(self.status, 2)
            self.aborted = True

    def finally_(self, callback) -> None:
        """Run `callback` at the end of `commit` (also when nothing was written), e.g. saving the copy state."""
        self.finalizers.append(callback)

    def _guard(self, layer):
        if layer.id not in self._guards:
            from skillsync_workflow import leakguard
            layers = _layers_module()
            config = self.layerset.config
            inputs = layers.leakguard_inputs(config, path=layer.root, target=layer.id)
            identity = list(inputs["identity_files"])
            for active in self.layerset.layers:
                found = active.identity_file()
                if found is not None and str(found) not in identity:
                    identity.append(str(found))
            prepared = leakguard.prepare(target=layer.id, targets=inputs["targets"], identity_files=identity,
                                         target_patterns=layers.target_patterns_file(config, layer.id),
                                         trailer_allow=inputs["trailer_allow"])
            for warning in list(inputs["warnings"]) + list(prepared.warnings):
                line = f"[warn] leakcheck {layer.id}: {warning}"
                if line not in self._warned:
                    self._warned.add(line)
                    print(line)
            self._guards[layer.id] = prepared
        return self._guards[layer.id]

    def _leaks(self):
        """[(layer id, finding)] of the planned writes, or None when the guard cannot run (BLOCKED)."""
        from skillsync_workflow import leakguard
        found = []
        groups = {}
        for (layer_id, rel), (layer, data, *_rest) in sorted(self.planned.items(), key=lambda item: item[0]):
            groups.setdefault(layer_id, (layer, []))[1].append((rel, data))
        for layer_id, (layer, items) in groups.items():
            try:
                prepared = self._guard(layer)
                texts = []
                for rel, data in items:
                    if b"\0" not in data[:8192]:
                        texts.append((rel, data.decode("utf-8", errors="replace")))
                reports = [leakguard.scan_texts(texts, prepared, "content"),
                           leakguard.scan_texts([(rel, "") for rel, _data in items], prepared, "filename")]
            except (ValueError, OSError) as exc:  # LayerError, LeakguardError, unreadable identity
                self._note("blocked", f"leakcheck {layer_id}", f"cannot check the target before writing: {exc}", 2)
                return None
            for report in reports:
                found.extend((layer_id, item) for item in report["findings"])
        return found

    def commit(self) -> int:
        """Leakcheck and write the planned files of one unit; returns the unit status (0/1/2)."""
        unit = 0
        try:
            if self.aborted:
                unit = 2 if not self.soft else 0
                if self.planned and not self.soft:
                    print(f"[blocked] nothing written ({len(self.planned)} planned file(s)): see the refusal above")
                return unit
            if not self.planned:
                return 0
            leaks = self._leaks()
            if leaks is None:
                return 0 if self.soft else 2
            if leaks:
                for layer_id, item in leaks:
                    line = f"{layer_id}:{item['path']} {item['location']} [{item['category']}]"
                    print(f"[warn] leak {line}; not adopted" if self.soft else f"[leak] {line}")
                if not self.soft:
                    print(f"[fail] leakcheck found a leak; nothing written ({len(self.planned)} planned file(s))")
                    self.status = status_code(self.status, 1)
                    unit = 1
                return unit
            items = sorted(self.planned.items(), key=lambda item: item[0])
            if self.dry_run:
                for (layer_id, rel), (layer, _data, label, *_rest) in items:
                    print(f"[dry-run] Would write: {layer.root / rel} (layer {layer_id}) from {label}")
                return 0
            locked_here = _composed_lock["handle"] is None
            acquire_composed_lock()  # concurrent pushes/adoptions: compare and write under one lock
            try:
                changed = [(layer_id, rel, label) for (layer_id, rel), (layer, _data, label, _callbacks, expected) in items
                           if ((tree_hash(layer.root / rel) if (layer.root / rel).is_file() else None) != expected)]
                if changed:
                    for layer_id, rel, label in changed:
                        message = (f"{label}: {layer_id}:{rel} changed in the layer since it was read (another push?); "
                                   "nothing written - run `skillsync link` and push again")
                        print(f"[warn] {message}" if self.soft else f"[conflict] {message}")
                    if not self.soft:
                        self.status = status_code(self.status, 2)
                        return 2
                    return 0
                for (layer_id, rel), (layer, data, label, callbacks, _expected) in items:
                    target = write_layer_file(layer, rel, data)
                    self.written.append(target)
                    print(f"{'Adopted' if self.soft else 'Pushed'}: {label} -> {layer_id}:{rel}")
                    for callback in callbacks:
                        callback()
            finally:
                if locked_here:
                    release_composed_lock()
            return 0
        finally:
            for callback in self.finalizers:
                callback()
            self._reset()


# ---------------------------------------------------------------- composite skills (K4)

COMPOSED_REGISTRY = ".projects.json"
COMPOSED_LOCK = ".lock"
LOCK_TIMEOUT_SECONDS = 60.0  # override: SKILLSYNC_LOCK_TIMEOUT
_composite_warned = False
_composed_lock = {"handle": None}
_layer_links_cache: dict = {}


def _lock_timeout() -> float:
    try:
        return max(0.0, float(os.environ.get("SKILLSYNC_LOCK_TIMEOUT") or LOCK_TIMEOUT_SECONDS))
    except ValueError:
        return LOCK_TIMEOUT_SECONDS


def acquire_composed_lock() -> None:
    """Exclusive inter-process lock of the composite cache (`<composed_root>/.lock`; `fcntl.flock` on
    POSIX, `msvcrt.locking` on Windows). Held by this process until `release_composed_lock`, so building
    composites, the registry and the collection of one `link` are atomic for other processes. A
    timeout raises LayerError `lock-timeout` (BLOCKED)."""
    if _composed_lock["handle"] is not None:
        return
    root = _layers_module().composed_root()
    root.mkdir(parents=True, exist_ok=True)
    handle = open(root / COMPOSED_LOCK, "a+b")
    deadline = time.monotonic() + _lock_timeout()
    while True:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic() >= deadline:
                handle.close()
                raise _layers_module().LayerError(
                    f"Composite cache {root} is locked by another skillsync process for more than "
                    f"{_lock_timeout():g}s; try again when it finishes (or raise SKILLSYNC_LOCK_TIMEOUT)",
                    "lock-timeout") from None
            time.sleep(0.05)
    _composed_lock["handle"] = handle


def release_composed_lock() -> None:
    handle = _composed_lock["handle"]
    if handle is None:
        return
    _composed_lock["handle"] = None
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    handle.close()


def skill_has_links(layer, name: str) -> bool:
    """True when `layer_links` (D73) reports a link inside `skills/<name>` of `layer` (cached per link run)."""
    key = (layer.id, str(layer.path), layer.subdir)
    if key not in _layer_links_cache:
        _layer_links_cache[key] = _layers_module().layer_links(layer)
    prefix = (f"{layer.subdir}/" if layer.subdir else "") + f"skills/{name}"
    return any(rel == prefix or rel.startswith(prefix + "/") for rel in _layer_links_cache[key])


def composite_hash(files: dict) -> str:
    """SHA-256 of the sorted list (relative path, layer id, realpath of the source file)."""
    rows = sorted((rel, layer.id, os.path.realpath(path)) for rel, (layer, path) in files.items())
    digest = hashlib.sha256()
    for rel, layer_id, real in rows:
        digest.update(f"{rel}\0{layer_id}\0{real}\n".encode("utf-8", "surrogateescape"))
    return digest.hexdigest()


def _link_text(value: str) -> str:
    """Comparable symlink target (Windows may report `\\\\?\\` paths from `os.readlink`)."""
    if os.name == "nt" and value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(value)


def _composite_entries(directory: Path) -> tuple[dict, list, list]:
    """(symlinks {rel: target}, regular files [rel], linked directories [rel]) of a composite directory."""
    links, regular, linked_dirs = {}, [], []
    for current, dirs, files in os.walk(directory, followlinks=False):
        base = Path(current)
        for name in list(dirs):
            if (base / name).is_symlink():
                linked_dirs.append((base / name).relative_to(directory).as_posix())
                dirs.remove(name)
        for name in files:
            path = base / name
            rel = path.relative_to(directory).as_posix()
            if path.is_symlink():
                links[rel] = _link_text(os.readlink(path))
            else:
                regular.append(rel)
    return links, sorted(regular), sorted(linked_dirs)


def _orphan_target(orphans: Path, rel: str) -> Path:
    target = orphans / rel
    counter = 1
    while target.exists() or target.is_symlink():
        target = orphans / f"{rel}.{counter}"
        counter += 1
    return target


def _sweep_composite(directory: Path, expected: dict, owners: dict) -> None:
    """Make `directory` safe to (re)build or remove: wrong links go; a regular file (a new file or an
    editor's atomic save over a link) is never overwritten: unchanged copies of the owner's file are
    dropped, anything else moves to `<hash>.orphans/` with a warning (adoption to the owner: 2.2b)."""
    orphans = directory.with_name(directory.name + ".orphans")
    links, regular, linked_dirs = _composite_entries(directory)
    for rel in linked_dirs:
        (directory / rel).unlink()
    for rel, target in links.items():
        if expected.get(rel) != target:
            (directory / rel).unlink()
    for rel in regular:
        path = directory / rel
        owner = owners.get(rel)
        try:
            same = owner is not None and path.read_bytes() == Path(owner[1]).read_bytes()
        except OSError:
            same = False
        if same:
            path.unlink()
            continue
        target = _orphan_target(orphans, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
        hint = f" (owner layer: {owner[0].id})" if owner else ""
        print(f"[warn] Composite {directory.parent.name}: regular file {rel} moved to {target}{hint}; "
              "it was not adopted - move the change to the owning layer or delete it")
    for current, dirs, _files in sorted(os.walk(directory, followlinks=False), key=lambda item: -len(item[0])):
        if Path(current) != directory and not any(Path(current).iterdir()):
            Path(current).rmdir()


def build_composite(name: str, files: dict) -> Path | None:
    """Composite of skill `name` in the cache: one symlink per file to its source in the owning layer
    (`files` from `LayerSet.skill_files`). Rebuilt when it differs; None when symlinks are unavailable."""
    global _composite_warned
    root = _layers_module().composed_root()
    directory = root / name / composite_hash(files)
    if not directory.resolve().is_relative_to(root.resolve()) or directory.parent.parent != root:
        raise ValueError(f"Unsafe skill name: {name}")
    acquire_composed_lock()
    targets = {rel: os.path.abspath(path) for rel, (_layer, path) in files.items()}
    expected = {rel: _link_text(target) for rel, target in targets.items()}
    if directory.is_dir():
        links, regular, linked_dirs = _composite_entries(directory)
        if links == expected and not regular and not linked_dirs:
            return directory
        _sweep_composite(directory, expected, files)
    root.mkdir(parents=True, exist_ok=True)  # independent of the lock file side effect
    created_dirs, created_links = [], []
    try:
        for parent in reversed([directory, *directory.parents]):
            if parent == root or root in parent.parents:
                if not parent.exists():
                    parent.mkdir()
                    created_dirs.append(parent)
        for rel, target in sorted(targets.items()):
            entry = directory / rel
            if entry.is_symlink():
                continue  # kept by the sweep: already points to `target`
            for parent in reversed(entry.relative_to(directory).parents):
                folder = directory / parent
                if not folder.exists():
                    folder.mkdir()
                    created_dirs.append(folder)
            try:
                os.symlink(target, entry)
                created_links.append(entry)
            except FileExistsError:
                # Built by another process meanwhile: fine when it points where it should.
                if not (entry.is_symlink() and _link_text(os.readlink(entry)) == expected[rel]):
                    raise
    except (OSError, NotImplementedError) as exc:
        if not _composite_warned:
            print(f"[warn] Cannot build composite skills with symlinks ({exc}); falling back to copies")
            _composite_warned = True
        # Undo only what this call created; the shared composite directory is never swept here.
        for entry in reversed(created_links):
            try:
                entry.unlink()
            except OSError:
                pass
        for folder in reversed(created_dirs):
            try:
                folder.rmdir()
            except OSError:
                pass
        try:
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            pass
        return None
    return directory


def skill_source(config: dict, name: str, method: str, dry_run: bool = False) -> Source | None:
    """Source of skill `name`: the skill directory of its only layer (symlinked as today) or, with two
    or more owning layers, a composite in the cache. Copy mode copies per file. None = missing skill."""
    layerset = config_layers(config)
    if not dry_run and method != "copy" and not symlinks_disabled():
        adopt_composite_files(layerset, name)  # K4: regular files in the composite go to their owner first
    files = layerset.skill_files(name)
    owners = {layer.id: layer for layer, _path in files.values()}
    plain = {rel: path for rel, (_layer, path) in files.items()}
    file_owners = {rel: layer.id for rel, (layer, _path) in files.items()}
    if not files:
        providers = layerset.skill_providers(name)
        return Source(providers[-1].root / "skills" / name, {}) if providers else None
    if len(owners) == 1:
        layer = next(iter(owners.values()))
        if not skill_has_links(layer, name):
            return Source(layer.root / "skills" / name, plain, file_owners)
        # D73: a link inside the skill directory must not reach the host through a directory symlink;
        # use a composite of the regular files (symlink mode) or per-file copies (copy mode).
    link = None
    if method != "copy" and not symlinks_disabled():
        if dry_run:
            link = _layers_module().composed_root() / name / composite_hash(files)
        else:
            link = build_composite(name, files)
        if link is not None and COMPOSITES_KEY in config:
            config[COMPOSITES_KEY][name] = link.name
    return Source(link, plain, file_owners)


def _registered_hashes(project_dir) -> dict:
    """{skill: hash} recorded for a project in the composite registry (empty without one)."""
    if project_dir is None:
        return {}
    registry = _layers_module().composed_root() / COMPOSED_REGISTRY
    if not registry.is_file():
        return {}
    return _read_registry(registry).get(str(Path(project_dir).resolve()), {})


def plan_composite_adoption(layerset, name: str, directory: Path, writer: "LayerWriter", rels=None) -> int:
    """Plan writes of the regular files of one composite directory of skill `name` (K4): a regular file
    where the owner's file exists (an editor's atomic save over a link) is a modification of the owner;
    a new file goes to the last provider of `SKILL.md` (else personal, or `--to-layer`). A written file
    becomes a link to its new source again. Returns the number of planned writes."""
    if not directory.is_dir() or directory.is_symlink():
        return 0
    _links, regular, _dirs = _composite_entries(directory)
    if not regular:
        return 0
    files = layerset.skill_files(name)
    planned = 0
    for rel in regular if rels is None else [item for item in regular if item in rels]:
        path = directory / rel
        owner = files.get(rel)
        try:
            data = path.read_bytes()
            if owner is not None and Path(owner[1]).read_bytes() == data:
                continue  # unchanged copy of the owner's file: the sweep relinks it
        except OSError:
            continue
        if owner is not None:
            layer = owner[0]
        elif writer.to_layer:
            layer = layerset.get(writer.to_layer)
        else:
            layer = layerset.skill_new_file_owner(name)

        def relink(path=path, layer=layer, rel=rel):
            target = os.path.abspath(layer.root / "skills" / name / rel)
            path.unlink()
            os.symlink(target, path)

        if writer.plan(layer, f"skills/{name}/{rel}", data, f"composite {name}/{directory.name}/{rel}", relink):
            planned += 1
    return planned


def adopt_composite_files(layerset, name: str) -> None:
    """Rebuild-time adoption (`link`/`watch`, K4): regular files in this project's previous composite of
    `name` and in the current one are written to their owner after leakcheck, one file at a time; what
    cannot be adopted (leak, read-only or core owner, detached HEAD) is moved to `.orphans` by the sweep."""
    root = _layers_module().composed_root()
    directories = []
    previous = _registered_hashes(layerset.project).get(name)
    if previous:
        directories.append(root / name / previous)
    try:
        files = layerset.skill_files(name)
    except ValueError:
        return
    if len({layer.id for layer, _path in files.values()}) > 1:
        directories.append(root / name / composite_hash(files))
    directories = [item for item in dict.fromkeys(directories) if item.is_dir() and not item.is_symlink()]
    if not any(_composite_entries(item)[1] for item in directories):
        return
    acquire_composed_lock()
    writer = LayerWriter(layerset, soft=True)
    for directory in directories:
        for rel in _composite_entries(directory)[1]:
            plan_composite_adoption(layerset, name, directory, writer, rels={rel})
            writer.commit()  # per file: one leak does not orphan the clean files


def _read_registry(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: value for key, value in data.items() if isinstance(value, dict)} if isinstance(data, dict) else {}


def update_composites(project_dir: Path, used: dict, dry_run: bool = False, collect: bool = True) -> None:
    """Record the composites of a project (`<cache>/composed/.projects.json`) and, after `link`
    (`collect`), remove every `<hash>` no recorded project uses; `.orphans` are never removed. Nothing is
    written to the cache when no composite is involved."""
    if dry_run:
        return
    root = _layers_module().composed_root()
    registry_path = root / COMPOSED_REGISTRY
    if not used and (not registry_path.exists() or str(project_dir.resolve()) not in _read_registry(registry_path)):
        # No composite and no registry entry: nothing to record or drop, so no lock (a foreign lock must
        # not turn a fully linked project into BLOCKED); unused hashes are collected by projects using the cache.
        return
    acquire_composed_lock()
    try:
        _update_registry(root, registry_path, project_dir, used, collect)
    finally:
        release_composed_lock()


def _update_registry(root: Path, registry_path: Path, project_dir: Path, used: dict, collect: bool) -> None:
    registry = _read_registry(registry_path)
    key = str(project_dir.resolve())
    if collect:
        if used:
            registry[key] = dict(sorted(used.items()))
        else:
            registry.pop(key, None)
    elif used:
        registry[key] = dict(sorted({**registry.get(key, {}), **used}.items()))
    registry = {path: value for path, value in sorted(registry.items()) if Path(path).is_dir()}
    if collect and root.is_dir():
        live = {}
        for value in registry.values():
            for skill, digest in value.items():
                live.setdefault(skill, set()).add(digest)
        for skill_dir in sorted(root.iterdir()):
            if skill_dir.name.startswith(".") or skill_dir.is_symlink() or not skill_dir.is_dir():
                continue
            for child in sorted(skill_dir.iterdir()):
                if child.is_symlink() or not child.is_dir() or child.name.endswith(".orphans"):
                    continue
                if child.name in live.get(skill_dir.name, ()):
                    continue
                _sweep_composite(child, {}, {})
                if not any(child.iterdir()):
                    child.rmdir()
            if not any(skill_dir.iterdir()):
                skill_dir.rmdir()
    text = json.dumps(registry, indent=2, sort_keys=True) + "\n"
    if registry_path.is_file() and registry_path.read_text(encoding="utf-8") == text:
        return
    root.mkdir(parents=True, exist_ok=True)
    temporary = registry_path.with_name(registry_path.name + f".tmp-{os.getpid()}")
    write_text_lf(temporary, text)
    os.replace(temporary, registry_path)


def build_claude_agents(project_dir: Path, manifest: dict, config: dict, dry_run: bool = False) -> None:
    # .claude/agents/ is NOT fully owned by skillsync (projects keep hand-written agents
    # there), so only library symlinks / untouched managed copies are cleaned or replaced.
    if not claude_enabled(manifest):
        return
    agent_names = resolve_roles(manifest, config)
    layerset = config_layers(config)
    agents_target = project_dir / ".claude" / "agents"
    if not agent_names and not agents_target.exists():
        return

    method = (manifest.get("sync") or {}).get("method", "symlink")
    state = load_state(project_dir)
    prune_managed(agents_target, "claude-agent", {f"{a}.md" for a in agent_names}, managed_roots(layerset), state, dry_run)

    for agent in agent_names:
        found = layerset.resolve(f"agents/{agent}.md")
        if found is None:
            print(f"[warn] Missing agent in library: {agent}")
            continue
        place(Source(found[1], owners={"": found[0].id}), agents_target / f"{agent}.md", method, state,
              f"claude-agent:{agent}.md", False, dry_run)
    save_state(project_dir, state, dry_run)


def build_claude_skills(project_dir: Path, manifest: dict, config: dict, dry_run: bool = False) -> None:
    # Same ownership rule as build_claude_agents: never touch hand-written skills.
    if not claude_enabled(manifest):
        return

    skills = project_skills(manifest, config)
    skills_target = project_dir / ".claude" / "skills"
    if not skills and not skills_target.exists():
        return

    method = (manifest.get("sync") or {}).get("method", "symlink")
    state = load_state(project_dir)
    prune_managed(skills_target, "claude-skill", set(skills), managed_roots(config_layers(config)), state, dry_run)

    for skill in skills:
        src = skill_source(config, skill, method, dry_run)
        if src is None:
            print(f"[warn] Missing skill in library: {skill}")
            continue
        place(src, skills_target / skill, method, state, f"claude-skill:{skill}", False, dry_run)
    save_state(project_dir, state, dry_run)


def build_claude_hooks(project_dir: Path, manifest: dict, config: dict, dry_run: bool = False) -> None:
    if not claude_enabled(manifest) or has_feature(manifest, config):
        return
    hooks_config = manifest.get("hooks") or {}
    if not hooks_config.get("stop_tests"):
        return

    found = config_layers(config).resolve("hooks/check-tests.py")  # hooks: core layer only
    if found is None:
        print("[warn] Missing hook script in library: hooks/check-tests.py (core layer)")
        return
    hook_src = found[1]

    dest = project_dir / ".claude" / "hooks" / "check-tests.py"

    if dry_run:
        print(f"[dry-run] Would write: {dest}")
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(hook_src, dest)
    dest.chmod(dest.stat().st_mode | 0o111)


def build_stop_hook_entry(config: dict, test_command: str) -> dict:
    python_command = config.get("python_command", "python3")
    return {
        "matcher": "",
        "hooks": [
            {
                "type": "command",
                "command": shlex.join([python_command, ".claude/hooks/check-tests.py", test_command]),
                "timeout": 300,
            }
        ],
    }


def is_skillsync_test_hook(hook: dict) -> bool:
    if not isinstance(hook, dict) or hook.get("type") != "command":
        return False
    try:
        args = shlex.split(hook.get("command", "")) + hook.get("args", [])
    except (ValueError, TypeError):
        return False
    return len(args) >= 2 and args[1] == ".claude/hooks/check-tests.py"


CLOSURE_HOOK = ".claude/hooks/check-closure.py"


def closure_wanted(manifest: dict, config: dict) -> bool:
    """Hook zamkniecia planu (K17): projekty z `feature`, wylaczany przez `hooks.stop_closure: false`."""
    hooks_config = manifest.get("hooks")
    disabled = isinstance(hooks_config, dict) and hooks_config.get("stop_closure") is False
    return claude_enabled(manifest) and has_feature(manifest, config) and not disabled


def copy_closure_script(project_dir: Path, config: dict, dry_run: bool = False) -> bool:
    """Kopiuje hook z biblioteki; True, gdy skrypt jest (w dry-run: bylby) na miejscu."""
    found = config_layers(config).resolve("hooks/check-closure.py")  # hooks: core layer only
    if found is None:
        print("[warn] Missing hook script in library: hooks/check-closure.py (core layer)")
        return False
    hook_src = found[1]
    dest = project_dir / CLOSURE_HOOK
    if dry_run:
        print(f"[dry-run] Would write: {dest}")
        return True
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(hook_src, dest)
        dest.chmod(dest.stat().st_mode | 0o111)
    except OSError as error:
        print(f"[warn] Nie udalo sie zapisac {dest}: {error}")
        return False
    return True


def remove_closure_script(project_dir: Path, dry_run: bool = False) -> None:
    # Kopia hooka jest nasza (osobna nazwa pliku w .claude/hooks); dowiazan nie ruszamy.
    dest = project_dir / CLOSURE_HOOK
    if dest.is_file() and not dest.is_symlink():
        if dry_run:
            print(f"[dry-run] Would remove: {dest}")
            return
        try:
            dest.unlink()
        except OSError as error:
            print(f"[warn] Nie udalo sie usunac {dest}: {error}")


def build_closure_hook_entry(config: dict) -> dict:
    # Korzen z git: $CLAUDE_PROJECT_DIR to katalog startu sesji, czesto podkatalog; brak skryptu konczy sie exit 0.
    python_command = shlex.quote(config.get("python_command", "python3"))
    command = (f'f="$(git -C "${{CLAUDE_PROJECT_DIR:-.}}" rev-parse --show-toplevel 2>/dev/null)/{CLOSURE_HOOK}"; '
               f'if [ -f "$f" ]; then {python_command} "$f"; fi')
    return {"matcher": "", "hooks": [{"type": "command", "command": command, "timeout": 10}]}


def closure_gitignore_entries(manifest: dict, config: dict) -> list[str]:
    if not closure_wanted(manifest, config):
        return []
    return ["hooks/check-closure.py", "settings.local.json", "settings.local.json.skillsync-backup"]


def is_closure_hook(hook: dict) -> bool:
    return (isinstance(hook, dict) and hook.get("type") == "command"
            and CLOSURE_HOOK in str(hook.get("command", "")))


def is_tracked(path: Path) -> bool:
    """Czy plik jest sledzony przez git; brak gita, blad lub timeout to 'nie'."""
    try:
        result = subprocess.run(["git", "-C", str(path.parent), "ls-files", "--error-unmatch", path.name],
                                capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def merge_closure_settings(project_dir: Path, manifest: dict, config: dict, want: bool,
                           dry_run: bool = False) -> bool:
    """Dopisuje (`want`) albo usuwa wpis Stop; True, gdy po wywolaniu nie zostaje w pliku nasz wpis.

    Wpis trafia do ignorowanego settings.local.json (settings.json bywa sledzony); cudze hooki i uprawnienia
    zostaja. Blad pliku to [warn], nigdy wyjatek, zeby `link` szedl dalej."""
    settings_path = project_dir / ".claude" / "settings.local.json"
    if not want and not settings_path.exists():
        return True

    existing = {}
    try:
        if settings_path.exists():
            existing = json.loads(settings_path.read_text(encoding="utf-8"))
    except ValueError:
        print(f"[warn] Nieprawidlowy JSON w {settings_path}, pomijam scalanie")
        return False
    except OSError as error:
        print(f"[warn] Nie udalo sie odczytac {settings_path}: {error}")
        return False
    if not isinstance(existing, dict) or not isinstance(existing.get("hooks", {}), dict):
        print(f"[warn] Invalid hook settings structure: {settings_path}")
        return False
    updated = dict(existing)
    hooks_section = dict(updated.get("hooks") or {})
    stop = hooks_section.get("Stop", [])
    if not isinstance(stop, list) or any(not isinstance(group, dict) or not isinstance(group.get("hooks", []), list) for group in stop):
        print(f"[warn] Invalid Stop settings structure: {settings_path}")
        return False
    entry = build_closure_hook_entry(config)
    present = [hook for group in stop for hook in group.get("hooks", []) if is_closure_hook(hook)]
    if want and present == entry["hooks"]:
        return False  # wpis jest aktualny; nie przestawiamy go ani nie nadpisujemy pliku
    kept = []
    for group in stop:
        if "hooks" not in group:
            kept.append(group)
            continue
        remaining = [hook for hook in group["hooks"] if not is_closure_hook(hook)]
        if remaining or not group["hooks"]:
            kept.append({**group, "hooks": remaining})
    if want:
        kept.append(entry)
    if kept:
        hooks_section["Stop"] = kept
    else:
        hooks_section.pop("Stop", None)
    if hooks_section or "hooks" in existing:
        updated["hooks"] = hooks_section

    if updated == existing:
        return not want
    if settings_path.exists() and is_tracked(settings_path):
        print(f"[warn] {settings_path} jest śledzony przez git, pomijam zapis")
        return False
    if dry_run:
        print(f"[dry-run] Would update: {settings_path}")
        return not want
    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        if settings_path.exists():
            backup_path = settings_path.with_name(settings_path.name + ".skillsync-backup")
            write_text_lf(backup_path, settings_path.read_text(encoding="utf-8"))
        write_text_lf(settings_path, json.dumps(updated, indent=2, ensure_ascii=False) + "\n")
    except OSError as error:
        print(f"[warn] Nie udalo sie zapisac {settings_path}: {error}")
        return False
    print(f"Updated: {settings_path}")
    return not want


def sync_closure_hook(project_dir: Path, manifest: dict, config: dict, dry_run: bool = False) -> None:
    """Skrypt i wpis sa spojne: wpis tylko przy skrypcie, a skrypt znika dopiero po usunieciu wpisu."""
    if not claude_enabled(manifest):
        return
    if closure_wanted(manifest, config):
        merge_closure_settings(project_dir, manifest, config, copy_closure_script(project_dir, config, dry_run),
                               dry_run)
    elif merge_closure_settings(project_dir, manifest, config, False, dry_run):
        remove_closure_script(project_dir, dry_run)


def merge_claude_settings(project_dir: Path, manifest: dict, config: dict, dry_run: bool = False) -> None:
    # Only our Stop command is managed; preserve custom commands, even in a shared group.
    if not claude_enabled(manifest):
        return
    hooks_config = manifest.get("hooks") or {}
    tests_config = manifest.get("tests") or {}
    test_command = tests_config.get("command")
    want_hook = hooks_config.get("stop_tests") and not has_feature(manifest, config)
    if want_hook and not test_command:
        print(f"[warn] hooks.stop_tests włączony, ale brak tests.command w manifeście: {project_dir}")
        return

    settings_path = project_dir / ".claude" / "settings.json"

    existing = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"[error] Nieprawidlowy JSON w {settings_path}, pomijam scalanie")
            return

    if not isinstance(existing, dict) or not isinstance(existing.get("hooks", {}), dict):
        print(f"[warn] Invalid hook settings structure: {settings_path}")
        return
    updated = dict(existing)
    hooks_section = dict(updated.get("hooks") or {})
    stop = hooks_section.get("Stop", [])
    if not isinstance(stop, list) or any(not isinstance(group, dict) or not isinstance(group.get("hooks", []), list) for group in stop):
        print(f"[warn] Invalid Stop settings structure: {settings_path}")
        return
    kept = []
    for group in stop:
        remaining = [hook for hook in group.get("hooks", []) if not is_skillsync_test_hook(hook)]
        if remaining or not group.get("hooks"):
            kept.append({**group, "hooks": remaining})
    if want_hook:
        kept.append(build_stop_hook_entry(config, test_command))
    if kept:
        hooks_section["Stop"] = kept
    else:
        hooks_section.pop("Stop", None)
    if hooks_section or "hooks" in existing:
        updated["hooks"] = hooks_section

    if updated == existing:
        return

    if dry_run:
        print(f"[dry-run] Would update: {settings_path}")
        return

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if settings_path.exists():
        backup_path = settings_path.with_name(settings_path.name + ".skillsync-backup")
        write_text_lf(backup_path, settings_path.read_text(encoding="utf-8"))

    write_text_lf(settings_path, json.dumps(updated, indent=2, ensure_ascii=False) + "\n")
    print(f"Updated: {settings_path}")


def mcp_enabled(manifest: dict) -> bool:
    mcp_cfg = manifest.get("mcp")
    if mcp_cfg is False:
        return False
    if isinstance(mcp_cfg, dict) and mcp_cfg.get("enabled") is False:
        return False
    return True


def strip_jsonc(text: str) -> str:
    """Drop // and /* */ comments and trailing commas (VS Code's mcp.json is JSONC)."""
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        elif ch in "}]":
            k = len(out) - 1
            while k >= 0 and out[k].isspace():
                k -= 1
            if k >= 0 and out[k] == ",":
                del out[k]
        out.append(ch)
        i += 1
    return "".join(out)


def vscode_server_to_claude(name: str, server: object) -> dict | None:
    """Translate one VS Code mcp.json server entry to the .mcp.json format, or None to skip."""
    if not isinstance(server, dict):
        print(f"[warn] .vscode/mcp.json: server '{name}' is not an object, skipping")
        return None
    if "${input:" in json.dumps(server):
        print(f"[warn] .vscode/mcp.json: server '{name}' uses ${{input:...}}, unsupported in .mcp.json - skipping")
        return None
    dropped = sorted(set(server) - set(MCP_SERVER_KEYS))
    if dropped:
        print(f"[info] .vscode/mcp.json: server '{name}': ignoring keys not supported in .mcp.json: {', '.join(dropped)}")
    converted = json.dumps({k: v for k, v in server.items() if k in MCP_SERVER_KEYS})
    # Claude Code starts stdio servers in the project root and expands ${VAR} from the environment.
    converted = converted.replace("${workspaceFolder}", ".")
    converted = re.sub(r"\$\{env:([^}]+)\}", r"${\1}", converted)
    return json.loads(converted)


def mcp_server_names(vscode_names: list[str]) -> dict[str, str]:
    """Map VS Code server ids (e.g. 'io.github.ChromeDevTools/chrome-devtools-mcp') to names
    usable in .mcp.json: last path segment, only [A-Za-z0-9_-]; the full sanitized id on collision."""
    def sanitize(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_-]", "-", value)

    short = {name: sanitize(name.rsplit("/", 1)[-1]) for name in vscode_names}
    counts: dict[str, int] = {}
    for value in short.values():
        counts[value] = counts.get(value, 0) + 1
    return {name: (value if counts[value] == 1 else sanitize(name)) for name, value in short.items()}


def desired_mcp_servers(project_dir: Path) -> tuple[dict, bool]:
    """Return (servers, authoritative). Servers come from .vscode/mcp.json when it defines
    any (authoritative: they override same-named .mcp.json entries); otherwise the defaults,
    which only fill in what is missing."""
    vscode_path = project_dir / ".vscode" / "mcp.json"
    if vscode_path.exists():
        try:
            data = json.loads(strip_jsonc(vscode_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[warn] Cannot read {vscode_path} ({exc}); falling back to default MCP servers")
        else:
            raw = (data.get("servers") or data.get("mcpServers") or {}) if isinstance(data, dict) else {}
            if isinstance(raw, dict) and raw:
                names = mcp_server_names(list(raw))
                converted = {names[name]: vscode_server_to_claude(name, server) for name, server in raw.items()}
                return {name: server for name, server in converted.items() if server is not None}, True
    return copy.deepcopy(DEFAULT_MCP_SERVERS), False


def build_claude_mcp(project_dir: Path, manifest: dict, config: dict, dry_run: bool = False) -> None:
    # Creates/updates .mcp.json (Claude Code project-scoped MCP servers). Only the servers
    # skillsync derives are touched; other servers and top-level keys stay as the project has
    # them. Servers dropped from .vscode/mcp.json are NOT removed - delete them by hand.
    if not claude_enabled(manifest) or not mcp_enabled(manifest):
        return

    wanted, authoritative = desired_mcp_servers(project_dir)
    target = project_dir / ".mcp.json"

    existing: dict = {}
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print(f"[error] Nieprawidlowy JSON w {target}, pomijam")
            return
        if not isinstance(existing, dict) or not isinstance(existing.get("mcpServers", {}), dict):
            print(f"[error] Nieoczekiwana struktura {target}, pomijam")
            return

    servers = dict(existing.get("mcpServers") or {})
    for name, server in wanted.items():
        if name in servers and not authoritative:
            continue
        if name not in servers and server in servers.values():
            continue  # identical config already present under another name - do not duplicate it
        servers[name] = server
    if not servers:
        return

    updated = dict(existing)
    updated["mcpServers"] = servers
    if updated != existing:
        if dry_run:
            print(f"[dry-run] Would write: {target}")
        else:
            write_text_lf(target, json.dumps(updated, indent=2, ensure_ascii=False) + "\n")
            print(f"Updated: {target}")
    ensure_root_gitignore(project_dir, dry_run=dry_run, entry=".mcp.json")


def claude_gitignore_entries(manifest: dict, config: dict) -> list[str]:
    # Per-entry (not whole-directory) so hand-written agents/skills stay visible to git.
    # No trailing slash on skills: git treats a symlink-to-dir as a file, not a directory.
    entries = [f"agents/{agent}.md" for agent in resolve_roles(manifest, config)]
    for skill in project_skills(manifest, config):
        entries.append(f"skills/{skill}")
    if (manifest.get("hooks") or {}).get("stop_tests"):
        entries.append("hooks/check-tests.py")
        entries.append("settings.json.skillsync-backup")
    return entries


def ensure_claude_gitignore(claude_dir: Path, entries: list[str], dry_run: bool = False) -> None:
    if not entries:
        return

    gitignore_path = claude_dir / ".gitignore"

    if gitignore_path.exists():
        existing = gitignore_path.read_text(encoding="utf-8").splitlines()
    else:
        existing = []

    updated = list(existing)
    for entry in entries:
        if entry not in existing:
            updated.append(entry)

    if updated == existing:
        return

    if dry_run:
        print(f"[dry-run] Would update: {gitignore_path}")
        return

    claude_dir.mkdir(parents=True, exist_ok=True)
    write_text_lf(gitignore_path, "\n".join(updated).rstrip() + "\n")


def link_project(manifest_path: Path, config: dict, dry_run: bool = False, layerset=None) -> None:
    """Generate the artefacts of one project from its LayerSet (`layerset`, else resolved from the
    machine config by the project path; `cmd_link` resolves and gates it first)."""
    manifest = load_yaml(manifest_path)
    project_dir = manifest_path.parent.parent
    if layerset is None:
        layerset = config.get(LAYERSET_KEY) or resolve_project_layers(project_dir, config)
    config = with_layers(config, layerset)
    _layer_links_cache.clear()
    try:
        _link_project(manifest, project_dir, config, dry_run)
    finally:
        release_composed_lock()


def uses_composed_cache(project_dir: Path, manifest: dict, config: dict, skills: list) -> bool:
    """True when `link` of this project will touch the composite cache: a skill with two or more owning
    layers or with a link inside (symlink mode), or a registry entry of the project (to update/drop)."""
    layers = _layers_module()
    registry = layers.composed_root() / COMPOSED_REGISTRY
    if registry.is_file() and str(project_dir.resolve()) in _read_registry(registry):
        return True
    if (manifest.get("sync") or {}).get("method", "symlink") == "copy" or symlinks_disabled():
        return False
    layerset = config_layers(config)
    for name in skills:
        owners = {layer.id: layer for layer, _path in layerset.skill_files(name).values()}
        if len(owners) > 1 or any(skill_has_links(layer, name) for layer in owners.values()):
            return True
    return False


def _link_project(manifest: dict, project_dir: Path, config: dict, dry_run: bool) -> None:
    layerset = config_layers(config)
    skills = project_skills(manifest, config)
    if not dry_run and uses_composed_cache(project_dir, manifest, config, skills):
        acquire_composed_lock()  # before the first write: a timeout leaves the project untouched

    github_dir = project_dir / ".github"
    skills_target = github_dir / "skills"
    if not dry_run:
        skills_target.mkdir(parents=True, exist_ok=True)

    method = (manifest.get("sync") or {}).get("method", "symlink")

    state = load_state(project_dir)
    prune_managed(skills_target, "github-skill", set(skills), managed_roots(layerset), state, dry_run, owned=True)

    for skill in skills:
        src = skill_source(config, skill, method, dry_run)
        if src is None:
            print(f"[warn] Missing skill in library: {skill}")
            continue
        place(src, skills_target / skill, method, state, f"github-skill:{skill}", True, dry_run)
    save_state(project_dir, state, dry_run)

    build_instructions(project_dir, manifest, config, dry_run=dry_run)
    build_cursor_rules(project_dir, manifest, config, dry_run=dry_run)
    mode = write_agents_block(project_dir, manifest, config, dry_run=dry_run)
    build_claude_md(project_dir, manifest, config, dry_run=dry_run, mode=mode)
    if claude_enabled(manifest):
        build_claude_agents(project_dir, manifest, config, dry_run=dry_run)
        build_claude_skills(project_dir, manifest, config, dry_run=dry_run)
        build_claude_hooks(project_dir, manifest, config, dry_run=dry_run)
        merge_claude_settings(project_dir, manifest, config, dry_run=dry_run)
        sync_closure_hook(project_dir, manifest, config, dry_run=dry_run)
        build_claude_mcp(project_dir, manifest, config, dry_run=dry_run)
        ensure_claude_gitignore(project_dir / ".claude", claude_gitignore_entries(manifest, config), dry_run=dry_run)
        ensure_claude_gitignore(project_dir / ".claude", closure_gitignore_entries(manifest, config), dry_run=dry_run)
    ensure_gitignore(github_dir, dry_run=dry_run)
    build_codex(project_dir, manifest, config, dry_run=dry_run, mode=mode)
    ensure_link_on_open(project_dir, manifest, config, dry_run=dry_run)
    if has_feature(manifest, config):
        ensure_root_gitignore(project_dir, entry=".skillsync/", dry_run=dry_run)
    update_composites(project_dir, config[COMPOSITES_KEY], dry_run)


def cmd_init(args: argparse.Namespace) -> int:
    """`init` = `layers init --kind personal` (skeleton, `git init`, hooks, target stub)."""
    layers = _layers_module()
    layer_id = getattr(args, "id", None)
    directory = getattr(args, "directory", None) or layers.default_layer_path(layer_id or "personal")
    return layers._print(layers.layers_init(directory, "personal", layer_id), getattr(args, "json", False), "init")


def project_files(directory: Path) -> dict:
    """{relative posix path: file} of regular files under `directory` (links and `.git` skipped, with a warning
    for links: layers must not contain links, D73)."""
    found = {}
    for current, dirs, files in os.walk(directory, followlinks=False):
        base = Path(current)
        for name in sorted(dirs):
            if name == ".git" or (base / name).is_symlink():
                if name != ".git":
                    print(f"[warn] Skipping linked directory {base / name} (links are not allowed in layers)")
                dirs.remove(name)
        for name in sorted(files):
            path = base / name
            if path.is_symlink():
                print(f"[warn] Skipping link {path} (links are not allowed in layers)")
            elif path.is_file():
                found[path.relative_to(directory).as_posix()] = path
    return found


def command_layers(args, project_dir=None):
    """(config, LayerSet) of a command: `--layer` overrides, else `layer_rules` for `project_dir` (None:
    the rule for the current directory, outside projects all layers). Raises LayerError (BLOCKED)."""
    overrides = layer_overrides(args)
    config = load_config(optional=bool(overrides))
    return config, resolve_project_layers(project_dir, config, overrides)


def target_layer_arg(writer: "LayerWriter", args) -> bool:
    """False (BLOCKED, printed) when `--to-layer` names a layer that is not active."""
    if writer.to_layer and writer.layerset.get(writer.to_layer) is None:
        print(f"[blocked] --to-layer {writer.to_layer}: not an active layer ({', '.join(writer.layerset.ids) or 'none'})")
        return False
    return True


def cmd_import(args: argparse.Namespace) -> int:
    """Import the skills and instructions of a project into a layer (`--to-layer`, default personal)."""
    project_dir = Path(args.path).expanduser().resolve()
    github_dir = project_dir / ".github"
    skills_dir = github_dir / "skills"
    instructions_file = github_dir / "copilot-instructions.md"

    if not github_dir.exists():
        print(f"[error] Missing .github in {project_dir}")
        return 1

    _config, layerset = command_layers(args)  # commands without a project context: rule for the cwd
    writer = LayerWriter(layerset, getattr(args, "to_layer", None), getattr(args, "dry_run", False))
    if not target_layer_arg(writer, args):
        return 2
    target = writer.new_file_layer()

    skill_names = []
    if skills_dir.exists():
        for skill_path in sorted(skills_dir.iterdir()):
            if skill_path.is_symlink():
                print(f"[info] Skipping {skill_path}: a link (managed by `skillsync link` or external)")
                continue
            if not skill_path.is_dir():
                continue
            if not safe_skill_name(skill_path.name):
                print(f"[warn] Skipping {skill_path}: unsafe skill name")
                continue
            for rel, path in sorted(project_files(skill_path).items()):
                writer.plan(target, f"skills/{skill_path.name}/{rel}", path.read_bytes(), str(path))
            skill_names.append(skill_path.name)

    template_name = None
    if instructions_file.exists():
        project_name = project_name_from_path(project_dir)
        template_name = slug_from_project_name(project_name)
        writer.plan(target, f"instructions/{template_name}.md", instructions_file.read_bytes(), str(instructions_file))

    status = writer.commit()
    if status:
        return status

    manifest_path = github_dir / ".skillsync.yaml"
    if not manifest_path.exists() and not writer.dry_run:
        manifest = {
            "version": 1,
            "project": {
                "name": project_name_from_path(project_dir),
                "type": "local",
            },
            "skills": sorted(skill_names),
            "instructions": {},
            "sync": {"method": "symlink"},
        }
        if template_name:
            manifest["instructions"]["template"] = template_name
        save_yaml(manifest_path, manifest)

    ensure_gitignore(github_dir, dry_run=writer.dry_run)
    print(f"Imported from {project_dir}")
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    """Link every selected project; BLOCKED projects (exit 2) are reported and skipped, the rest is linked."""
    dry_run = getattr(args, "dry_run", False)
    config, selected, status = gated_projects(args)
    for manifest, layerset in selected:
        try:
            link_project(manifest, config, dry_run=dry_run, layerset=layerset)
        except ValueError as exc:
            if getattr(exc, "code", None) != "lock-timeout":
                raise
            print(f"[blocked] {manifest.parent.parent}: {exc}")
            status = 2
    return status


def cmd_pull(args: argparse.Namespace) -> int:
    """`pull` = `layers update` (every configured layer) + `link`; exit code = the worse of both."""
    layers = _layers_module()
    update = layers._print(layers.layers_update(), False, "layers update")
    linked = cmd_link(argparse.Namespace(path=args.path, dry_run=False, layers=[]))
    return status_code(update, linked)


def snapshot_tree(root: Path) -> dict[str, float]:
    snapshot = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for filename in filenames:
            path = Path(dirpath) / filename
            try:
                snapshot[str(path)] = path.stat().st_mtime
            except FileNotFoundError:
                continue
    return snapshot


MEMORY_RETRY_SECONDS = 60
MEMORY_HOST_PATTERN = re.compile(r"^[A-Za-z0-9._@-]+$")


def memory_sync_settings(config: dict) -> tuple[list[Path], list[dict]]:
    """Machine-local `memory_sync:` section: source files and scp targets. Invalid entries are skipped with a warning."""
    section = config.get("memory_sync") or {}
    files = [Path(str(item)).expanduser() for item in section.get("files") or []]
    targets = []
    for target in section.get("targets") or []:
        host, directory = str(target.get("host", "")), str(target.get("dir", ""))
        if not MEMORY_HOST_PATTERN.match(host) or host.startswith("-") or not directory:
            print(f"[warn] memory_sync: skipping invalid target {target!r} (needs host and dir)")
            continue
        targets.append({"host": host, "dir": directory.rstrip("/\\") + "/"})
    return files, targets


def sync_memory(config: dict, state: dict, dry_run: bool = False, force: bool = False) -> list[str]:
    """One-way copy (scp) of memory files to remote hosts when their content changed.

    `state` maps (file, host) -> {"digest", "retry_at"}; a failed copy is retried after MEMORY_RETRY_SECONDS,
    never in a tight loop. Sources are only read, never overwritten.
    """
    files, targets = memory_sync_settings(config)
    messages = []
    for source in files:
        try:
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
        except OSError:
            continue
        for target in targets:
            key = (str(source), target["host"])
            entry = state.setdefault(key, {})
            if not force and entry.get("digest") == digest:
                continue
            if time.time() < entry.get("retry_at", 0):
                continue
            destination = f"{target['host']}:{target['dir']}{source.name}"
            if dry_run:
                messages.append(f"[dry-run] Would copy: {source} -> {destination}")
                continue
            command = ["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", str(source), destination]
            failure = None
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=60)
                if result.returncode:
                    failure = result.stderr.strip() or f"scp exit {result.returncode}"
            except (OSError, subprocess.TimeoutExpired) as exc:
                failure = str(exc)
            if failure is not None:
                entry["retry_at"] = time.time() + MEMORY_RETRY_SECONDS
                messages.append(f"[warn] memory_sync: {source.name} -> {target['host']} failed ({failure}); retry in {MEMORY_RETRY_SECONDS}s")
            else:
                entry.update(digest=digest, retry_at=0)
                messages.append(f"[update] memory_sync: {source.name} -> {destination}")
    return messages


def cmd_memory_sync(args: argparse.Namespace) -> None:
    config = load_config()
    files, targets = memory_sync_settings(config)
    if not files or not targets:
        print("[info] memory_sync: no files/targets configured in config.yaml")
        return
    for message in sync_memory(config, {}, dry_run=args.dry_run, force=True):
        print(message)


def watch_roots(machine) -> list[Path]:
    """Roots watched by `watch`: every configured layer present on the machine and the composite cache
    (new files written into a composite are adopted at the next rebuild)."""
    roots = [layer.root for layer in machine.ordered_layers() if layer.present]
    composed = _layers_module().composed_root()
    return roots + ([composed] if composed.is_dir() else [])


def watch_snapshot(machine) -> dict:
    snapshot = {}
    composed = _layers_module().composed_root()
    for root in watch_roots(machine):
        for path, mtime in snapshot_tree(root).items():
            if root == composed and (any(part.endswith(".orphans") for part in Path(path).relative_to(root).parts)
                                     or Path(path).name.startswith(".")):
                continue
            snapshot[path] = mtime
    return snapshot


def cmd_watch(args: argparse.Namespace) -> int:
    """Link after any change in any configured layer (or a composite); returns the last `link` status
    when interrupted."""
    config = load_config()
    machine = machine_config(config)
    if not machine.layers:
        try:
            resolve_project_layers(None, config, machine=machine)
        except ValueError as exc:
            print(f"[blocked] {exc}")
            return 2
    interval = args.interval

    last_snapshot = watch_snapshot(machine)
    memory_state: dict = {}
    status = 0
    print(f"Watching layers {', '.join(layer.id for layer in machine.ordered_layers() if layer.present)} "
          f"(interval={interval}s)")
    for message in sync_memory(config, memory_state):
        print(message)

    try:
        while True:
            time.sleep(interval)
            current_snapshot = watch_snapshot(machine)
            if current_snapshot != last_snapshot:
                print("Detected changes, linking...")
                status = cmd_link(argparse.Namespace(path=args.path, dry_run=False, layers=[]))
                # After link: its own rebuild of composites must not trigger the next cycle.
                current_snapshot = watch_snapshot(machine)
                last_snapshot = current_snapshot
            for message in sync_memory(config, memory_state):
                print(message)
    except KeyboardInterrupt:
        pass
    return status


def cmd_list(args: argparse.Namespace) -> None:
    """Skills of the active layers (rule for the current directory, else all layers) with a layer column."""
    overrides = layer_overrides(args)
    config = load_config(optional=bool(overrides))
    layerset = resolve_project_layers(None, config, overrides)
    skills = available_skills(layerset)

    if not skills:
        print("No skills found in library")
        return

    print(f"Available skills ({len(skills)}):")
    for skill in skills:
        owners = ",".join(layer.id for layer in layerset.skill_providers(skill))
        found = layerset.resolve(f"skills/{skill}/SKILL.md")
        if found is not None:
            skill_md = found[1]
            first_line = skill_md.read_text(encoding="utf-8").splitlines()[0] if skill_md.stat().st_size > 0 else ""
            print(f"  • {skill:30s} [{owners}] {first_line[:60]}")
        else:
            print(f"  • {skill:30s} [{owners}]")


def cmd_add(args: argparse.Namespace) -> int:
    project_path = Path(args.project).expanduser().resolve()
    manifest_path = project_path / ".github" / ".skillsync.yaml"

    if not manifest_path.exists():
        print(f"[error] No manifest found at {manifest_path}")
        return 1

    manifest = load_yaml(manifest_path)
    skills_list = manifest.get("skills", [])

    skill_name = args.skill
    if skill_name in skills_list:
        print(f"[warn] Skill '{skill_name}' already in manifest")
        return 0

    _config, layerset = command_layers(args, project_path)
    available = available_skills(layerset)
    if skill_name not in available:
        print(f"[error] Skill '{skill_name}' not found in layers {', '.join(layerset.ids) or '-'}")
        print(f"Available: {', '.join(available)}")
        return 1

    skills_list.append(skill_name)
    manifest["skills"] = sorted(skills_list)
    save_yaml(manifest_path, manifest)

    print(f"Added '{skill_name}' to {project_path.name}")

    return cmd_link(argparse.Namespace(path=str(project_path), dry_run=False, layers=layer_overrides(args)))


def cmd_skills_add(args: argparse.Namespace) -> int:
    """Create a skill skeleton in a layer (`--to-layer`, default personal), after leakcheck."""
    skill_name = args.name
    if not safe_skill_name(skill_name):
        print(f"[error] Unsafe skill name: {skill_name}")
        return 1
    _config, layerset = command_layers(args)
    writer = LayerWriter(layerset, getattr(args, "to_layer", None))
    if not target_layer_arg(writer, args):
        return 2
    providers = layerset.skill_providers(skill_name)
    if providers:
        print(f"[error] Skill '{skill_name}' already exists (layers: {', '.join(layer.id for layer in providers)})")
        return 1
    target = writer.new_file_layer()

    template = f"""---
name: {skill_name}
description: Brief description of this skill
---

# {skill_name.replace('-', ' ').title()}

## Overview

Brief overview of what this skill covers.

## Key Concepts

- Concept 1
- Concept 2
- Concept 3

## Examples

```python
# Example code
pass
```

## Best Practices

1. Practice 1
2. Practice 2
3. Practice 3

## Common Pitfalls

- Pitfall 1
- Pitfall 2

## References

- Documentation links
- Related resources
"""

    planned = writer.plan(target, f"skills/{skill_name}/SKILL.md", template.encode("utf-8"), f"skills add {skill_name}")
    status = writer.commit()
    if status or not planned:
        return status or 2
    skill_md = target.root / "skills" / skill_name / "SKILL.md"
    print(f"Created skill '{skill_name}' at {skill_md.parent} (layer {target.id})")
    print(f"Edit: {skill_md}")

    if args.add_to_project:
        add_args = argparse.Namespace(project=args.add_to_project, skill=skill_name, layers=layer_overrides(args))
        return cmd_add(add_args)
    return 0

def cmd_instructions(args: argparse.Namespace) -> int:
    dry_run = getattr(args, "dry_run", False)
    config, selected, status = gated_projects(args)
    for manifest, layerset in selected:
        project_config = with_layers(config, layerset)
        project_dir = manifest.parent.parent
        _layer_links_cache.clear()
        try:
            loaded = load_yaml(manifest)
            if not dry_run and uses_composed_cache(project_dir, loaded, project_config,
                                                   project_skills(loaded, project_config)):
                acquire_composed_lock()  # before the first write, as in `link`
            build_instruction_files(project_dir, loaded, project_config, dry_run=dry_run,
                                    codex_first=not getattr(args, "path", None))
            update_composites(project_dir, project_config[COMPOSITES_KEY], dry_run, collect=False)
        except ValueError as exc:
            if getattr(exc, "code", None) != "lock-timeout":
                raise
            print(f"[blocked] {project_dir}: {exc}")
            status = 2
        finally:
            release_composed_lock()
    return status


def build_instruction_files(project_dir: Path, manifest: dict, config: dict, dry_run: bool = False,
                            codex_first: bool = False) -> str:
    """Instruction files: the AGENTS.md block decides the mode before CLAUDE.md and Codex. `codex_first` keeps the
    historical order of `instructions` without a path in `inline` (it decides the order of .gitignore entries)."""
    build_instructions(project_dir, manifest, config, dry_run=dry_run)
    mode = write_agents_block(project_dir, manifest, config, dry_run=dry_run)
    if codex_first and mode == "inline":
        build_codex(project_dir, manifest, config, dry_run=dry_run, mode=mode)
        build_claude_md(project_dir, manifest, config, dry_run=dry_run, mode=mode)
        return mode
    build_claude_md(project_dir, manifest, config, dry_run=dry_run, mode=mode)
    build_codex(project_dir, manifest, config, dry_run=dry_run, mode=mode)
    return mode


def strip_merge_preamble(source_text: str, merge_parts: list[str]) -> tuple[str, int]:
    """Strip the merged base preamble (e.g. _base.md) from the start of source_text,
    leaving just the project-local part meant to live in the library template."""
    stripped_count = 0
    merge_prefix = "\n\n".join(merge_parts).rstrip() if merge_parts else ""
    merge_prefix_legacy = merge_prefix.replace(BASE_PREAMBLE_END_MARKER, "").rstrip() if merge_prefix else ""

    # Preferred mode: cut everything before the last base marker.
    if BASE_PREAMBLE_END_MARKER in source_text:
        source_text = source_text.rsplit(BASE_PREAMBLE_END_MARKER, 1)[1]
        source_text = source_text.lstrip("\n")
        stripped_count = 1
    # Fallback mode: legacy prefix stripping when marker is missing.
    elif merge_parts:
        while source_text.startswith(merge_prefix):
            source_text = source_text[len(merge_prefix):]
            if source_text.startswith("\n\n"):
                source_text = source_text[2:]
            elif source_text.startswith("\n"):
                source_text = source_text[1:]
            stripped_count += 1

    # Extra cleanup: if historical template already contained merged preamble,
    # remove it from the beginning of local part as well (with or without marker).
    while True:
        if merge_prefix and source_text.startswith(merge_prefix):
            source_text = source_text[len(merge_prefix):]
        elif merge_prefix_legacy and source_text.startswith(merge_prefix_legacy):
            source_text = source_text[len(merge_prefix_legacy):]
        else:
            break

        if source_text.startswith("\n\n"):
            source_text = source_text[2:]
        elif source_text.startswith("\n"):
            source_text = source_text[1:]
        stripped_count += 1

    return source_text.rstrip() + "\n", stripped_count


def load_merge_parts(merge_names: list[str], layerset, manifest: dict | None = None, config: dict | None = None) -> list[str]:
    """Merge parts as `link` renders them (per file across layers, `_base` with its `_base.d` fragments,
    variables applied): the preamble that `push` cuts from a generated file."""
    merge_parts = []
    for name in merge_names:
        text = read_instruction(layerset, name)
        if text is None:
            print(f"[warn] Missing merge template: {missing_instruction(layerset, name)}")
            continue
        text = text.replace("\r\n", "\n")
        merge_parts.append(apply_variables(text, manifest or {}, config or {}) if config is not None else text)
    return merge_parts


FRAGMENT_BLOCK = re.compile(r"<!-- SKILLSYNC:FRAGMENT begin ([a-z0-9][a-z0-9-]*)/([^\s/>]+) -->\n(.*?)"
                            r"<!-- SKILLSYNC:FRAGMENT end \1/\2 -->\n?", re.DOTALL)


VARIABLE_PATTERN = re.compile(r"\{\{\s*[^{}\s]+\s*\}\}")


def has_variables(text: str) -> bool:
    """True when a layer file uses `{{ var }}` (push would write the project's values in their place)."""
    return bool(VARIABLE_PATTERN.search(text))


def variables_warning(label: str, layer_id: str, rel: str) -> None:
    print(f"[warn] {label}: {layer_id}:{rel} uses {{{{ variables }}}}; the generated text has the project's "
          "values in their place, so it is not pushed - edit the layer file by hand")


def _blank_normalized(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n")).strip()


def push_base_region(source_text: str, manifest: dict, config: dict, writer: "LayerWriter", label: str) -> None:
    """Text before `SKILLSYNC:BASE-END` of a generated file: each `_base.d` fragment block that differs from
    its fragment goes to the fragment's layer; the core `_base` text outside the blocks goes to the core
    layer only with `--to-layer <core>` (otherwise `[info]`)."""
    instructions = manifest.get("instructions") or {}
    if "_base" not in (instructions.get("merge") or []) or BASE_PREAMBLE_END_MARKER not in source_text:
        return
    layerset = writer.layerset
    found = layerset.resolve("instructions/_base.md")
    if found is None:
        return
    core_text = found[1].read_text(encoding="utf-8").replace("\r\n", "\n")
    if BASE_PREAMBLE_END_MARKER not in core_text:
        return
    region = source_text.rsplit(BASE_PREAMBLE_END_MARKER, 1)[0]
    fragments = {(item["layer"], item["name"]): item for item in layerset.base_fragments()}
    for match in FRAGMENT_BLOCK.finditer(region):
        layer_id, name, body = match.group(1), match.group(2), match.group(3)
        fragment = fragments.get((layer_id, name))
        if fragment is None:
            print(f"[warn] {label}: fragment {layer_id}/{name} is not active in this project; not pushed")
            continue
        raw = fragment["path"].read_text(encoding="utf-8").replace("\r\n", "\n")
        current = apply_variables(raw, manifest, config)
        if body.rstrip("\n") == current.rstrip("\n"):
            continue
        if has_variables(raw):
            variables_warning(f"{label} (fragment {layer_id}/{name})", layer_id, f"instructions/_base.d/{name}")
            continue
        text = body if body.endswith("\n") else body + "\n"
        writer.plan(layerset.get(layer_id), f"{_layers_module().FRAGMENTS_DIR}/{name}", text.encode("utf-8"),
                    f"{label} (fragment {layer_id}/{name})")
    outside = FRAGMENT_BLOCK.sub("", region)
    core_before, core_after = core_text.split(BASE_PREAMBLE_END_MARKER, 1)
    if _blank_normalized(outside) == _blank_normalized(apply_variables(core_before, manifest, config)):
        return
    if has_variables(core_text):
        variables_warning(f"{label} (base text)", found[0].id, "instructions/_base.md")
        return
    text = _blank_normalized(outside) + "\n" + BASE_PREAMBLE_END_MARKER + core_after
    writer.plan(found[0], "instructions/_base.md", text.encode("utf-8"), f"{label} (base text)")


def push_template_text(source: Path, source_text: str, manifest: dict, config: dict, writer: "LayerWriter",
                       kind: str) -> None:
    """Project part of a generated instructions file (after `BASE-END`) -> `instructions/<template>.md` of
    its owner (a new template: `--to-layer`, else personal). Unchanged text writes nothing."""
    instructions = manifest.get("instructions") or {}
    template_name = instructions.get("template")
    layerset = writer.layerset
    push_base_region(source_text, manifest, config, writer, str(source))
    merge_parts = load_merge_parts(list(instructions.get("merge") or []), layerset, manifest, config)
    local_text, stripped_count = strip_merge_preamble(source_text, merge_parts)
    rel = f"instructions/{template_name}.md"
    found = layerset.resolve(rel)
    if found is not None:
        raw = found[1].read_text(encoding="utf-8").replace("\r\n", "\n")
        current = apply_variables(raw, manifest, config)
        if current.rstrip() + "\n" == local_text:
            return
        if has_variables(raw):
            variables_warning(f"{kind} {source}", found[0].id, rel)
            return
        layer = found[0]
    else:
        layer = writer.new_file_layer()
    detail = f"stripped merge preamble x{stripped_count}" if stripped_count > 0 else "no merge preamble stripped"
    writer.plan(layer, rel, local_text.encode("utf-8"), f"{kind} ({detail}) {source}")


def _own_writer(config: dict, dry_run: bool, writer):
    """(writer, owned) for the `sync_*` helpers called alone (tests, scripts): own writer committed at the end."""
    if writer is not None:
        return writer, False
    return LayerWriter(config_layers(config), dry_run=dry_run), True


def sync_project_instructions_to_library(manifest: Path, config: dict, dry_run: bool = False, writer=None) -> int:
    if not manifest.exists():
        print(f"[warn] Missing manifest: {manifest}")
        return 0

    manifest_data = load_yaml(manifest)
    instructions = manifest_data.get("instructions") or {}
    template_name = instructions.get("template")
    if not template_name:
        print(f"[warn] Missing instructions.template in manifest: {manifest}")
        return 0

    project_dir = manifest.parent.parent
    source = project_dir / ".github" / "copilot-instructions.md"
    if not source.exists():
        print(f"[warn] Missing project instructions file: {source}")
        return 0

    writer, owned = _own_writer(config, dry_run, writer)
    source_text = source.read_text(encoding="utf-8").replace("\r\n", "\n")
    push_template_text(source, source_text, manifest_data, config, writer, "Synced instructions")
    return writer.commit() if owned else 0


def sync_claude_md_to_library(manifest: Path, config: dict, dry_run: bool = False, writer=None) -> int:
    if not manifest.exists():
        print(f"[warn] Missing manifest: {manifest}")
        return 0

    manifest_data = load_yaml(manifest)
    if not claude_enabled(manifest_data):
        return 0

    instructions = manifest_data.get("instructions") or {}
    template_name = instructions.get("template")
    if not template_name:
        return 0

    project_dir = manifest.parent.parent
    source = project_dir / "CLAUDE.md"
    if not source.exists():
        return 0

    source_text = source.read_text(encoding="utf-8").replace("\r\n", "\n")
    if source_text.startswith(CLAUDE_MD_HEADER):
        source_text = source_text[len(CLAUDE_MD_HEADER):]
    if source_text.strip() == CLAUDE_IMPORT.strip():
        source_text = agents_block_for_push(project_dir / "AGENTS.md")
        if source_text is None:
            return 0

    # Same template as copilot-instructions.md: CLAUDE.md and copilot-instructions.md
    # share one project-local source of truth in the layers.
    writer, owned = _own_writer(config, dry_run, writer)
    push_template_text(source, source_text, manifest_data, config, writer, "Synced CLAUDE.md")
    return writer.commit() if owned else 0


def agents_block_for_push(path: Path) -> str | None:
    """AGENTS.md block body without the Codex note, or None (with a warning) when it cannot be cut safely."""
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.is_file() else ""
    counts = [text.count(marker) for marker in (AGENTS_BEGIN, AGENTS_END, CODEX_NOTE_BEGIN, CODEX_NOTE_END)]
    if counts[:2] != [1, 1] or text.index(AGENTS_BEGIN) > text.index(AGENTS_END) or counts[2] != counts[3] or counts[2] > 1:
        print(f"[warn] Skipping push: no single skillsync block (or broken Codex note markers) in {path}")
        return None
    body = text.split(AGENTS_BEGIN, 1)[1].split(AGENTS_END, 1)[0]
    if body.count(CODEX_NOTE_BEGIN) != counts[2] or body.count(CODEX_NOTE_END) != counts[3]:
        print(f"[warn] Skipping push: Codex note markers outside the skillsync block in {path}")
        return None
    if counts[2]:
        before, rest = body.split(CODEX_NOTE_BEGIN, 1)
        if CODEX_NOTE_END not in rest or rest.split(CODEX_NOTE_END, 1)[1].strip():
            print(f"[warn] Skipping push: Codex note is not at the end of the skillsync block in {path}")
            return None
        body = before
    return body.strip("\n") + "\n"


def copy_source(layerset, kind: str, name: str) -> "Source | None":
    """Current source (with owners) of a copy key, or None when the layers no longer provide it."""
    if kind == "claude-agent":
        found = layerset.resolve(f"agents/{name}")
        return Source(found[1], owners={"": found[0].id}) if found else None
    try:
        files = layerset.skill_files(name)
    except ValueError:
        return None
    return Source(None, {rel: path for rel, (_layer, path) in files.items()},
                  {rel: layer.id for rel, (layer, _path) in files.items()})


def sync_copies_to_library(manifest: Path, config: dict, dry_run: bool = False, writer=None) -> int:
    """Send local edits of copy-mode skills/agents (Windows fallback) back to their owner layers, per file.

    The state entry `{owner, base}` of each file is its merge base: a local-only change goes to the layer
    that owns the file now (after leakcheck), a layer-only change is left for `link`, a change on both
    sides is a conflict and never overwritten. A new file of a skill goes to the last provider of its
    `SKILL.md` (else personal, or `--to-layer`). Legacy state entries are converted first.
    """
    project_dir = manifest.parent.parent
    state = load_state(project_dir)
    if not state:
        return 0
    writer, owned = _own_writer(config, dry_run, writer)
    layerset = writer.layerset
    layout = {
        "github-skill": project_dir / ".github" / "skills",
        "claude-skill": project_dir / ".claude" / "skills",
        "claude-agent": project_dir / ".claude" / "agents",
        "codex-skill": project_dir / ".agents" / "skills",
    }
    for key in copy_keys(state):
        kind, _, name = key.partition(":")
        dest = layout[kind] / name
        if not dest.exists() or dest.is_symlink():
            continue
        src = copy_source(layerset, kind, name)
        if src is None:
            continue
        if isinstance(state.get(key), str):
            convert_legacy_copy(state, key, dest, src)
        sources = src.file_map()
        entries = copy_entries(state, key)
        for rel in copy_files(dest) if kind != "claude-agent" else [""]:
            target = _copy_path(dest, rel)
            if not target.is_file():
                continue
            current = tree_hash(target)
            entry = entries.get(rel)
            source = sources.get(rel)
            source_hash = tree_hash(Path(source)) if source is not None else None
            if entry is not None and current == entry.get("base"):
                continue
            if source_hash == current:
                set_copy_entry(state, key, rel, src.owner(rel), current)
                continue
            # Every conflict stops the whole push of the project (nothing written, exit 2), as Q3.
            if entry is not None and entry.get(OUTSIDE_SOURCE):
                print(f"[info] {target} is not in the layers (state conversion); not pushed - add it to a layer "
                      "by hand or delete it")
                continue
            if source is not None and entry is None:
                writer.conflict(untracked_message(project_dir, target))
                continue
            if source is not None and entry.get("base") is None:
                writer.conflict(unknown_base_message(project_dir, target))
                continue
            if source is not None and source_hash != entry["base"]:
                writer.conflict(f"{project_dir}: {target} and the layer file both changed; nothing written - "
                                "merge the change into the layer by hand (git), then run `skillsync link`")
                continue
            if source is not None:
                layer = layerset.get(src.owner(rel))
                layer_rel = f"agents/{name}" if kind == "claude-agent" else f"skills/{name}/{rel}"
            elif entry is not None:
                writer.conflict(f"{project_dir}: {target} was removed from the layers but edited locally; nothing "
                                "written - restore it in a layer by hand or delete the local copy")
                continue
            else:
                layer = writer.new_file_layer() if writer.to_layer else layerset.skill_new_file_owner(name)
                layer_rel = f"skills/{name}/{rel}"

            def adopted(key=key, rel=rel, layer=layer, current=current):
                set_copy_entry(state, key, rel, layer.id if layer else None, current)

            writer.plan(layer, layer_rel, target.read_bytes(), f"local edits {target}", adopted)
    writer.finally_(lambda: save_state(project_dir, state, dry_run))
    return writer.commit() if owned else 0


def adopt_project_composites(project_dir: Path, writer: "LayerWriter") -> None:
    """`push` in symlink mode: regular files in the composites of this project are planned for adoption."""
    root = _layers_module().composed_root()
    for name, digest in sorted(_registered_hashes(project_dir).items()):
        if not safe_skill_name(name) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            continue
        plan_composite_adoption(writer.layerset, name, root / name / digest, writer)


def cmd_push(args: argparse.Namespace) -> int:
    """Push local changes of projects to the layers that own them (K4); exit 0/1 (leak)/2 (BLOCKED)."""
    dry_run = getattr(args, "dry_run", False)
    overrides = layer_overrides(args)
    config = load_config(optional=bool(overrides))
    machine = machine_config(config)
    if not overrides and not machine.layers:
        try:
            resolve_project_layers(None, config, machine=machine)
        except ValueError as exc:
            print(f"[blocked] {exc}")
            return 2
    gate = LayerGate()
    status = 0
    for manifest in project_manifests(config, getattr(args, "path", None)):
        if not manifest.exists():
            print(f"[warn] Missing manifest: {manifest}")
            continue
        project_dir = manifest.parent.parent
        try:
            layerset = resolve_project_layers(project_dir, config, overrides, machine)
        except ValueError as exc:
            print(f"[blocked] {project_dir}: {exc}")
            status = 2
            continue
        if gate.blocked(project_dir, layerset, "pushed"):
            status = 2
            continue
        project_config = with_layers(config, layerset)
        writer = LayerWriter(layerset, getattr(args, "to_layer", None), dry_run)
        if not target_layer_arg(writer, args):
            status = 2
            continue
        registered = bool(_registered_hashes(project_dir))
        try:
            if registered and not dry_run:
                acquire_composed_lock()
            sync_project_instructions_to_library(manifest, project_config, dry_run, writer)
            sync_claude_md_to_library(manifest, project_config, dry_run, writer)
            sync_copies_to_library(manifest, project_config, dry_run, writer)
            if registered:  # `--dry-run`: planned adoptions are shown, nothing is written
                adopt_project_composites(project_dir, writer)
            status = status_code(status, writer.commit())
        except ValueError as exc:
            if getattr(exc, "code", None) not in ("lock-timeout", "symlink-in-layer"):
                raise
            print(f"[blocked] {project_dir}: {exc}")
            status = 2
        finally:
            release_composed_lock()
    return status


def cmd_skills_list(args) -> int:
    from skillsync_workflow.catalog import _cmd_skills_list
    return _cmd_skills_list(args)


def cmd_skills_remove(args) -> int:
    """Remove an explicitly listed skill from the project manifest (mirror of `add`); companions/tags/profiles cannot be removed here."""
    import json as _json
    project_path = Path(args.project).expanduser().resolve()
    manifest_path = project_path / ".github" / ".skillsync.yaml"

    def finish(report: dict, code: int) -> int:
        if args.json:
            print(_json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(report.get("reason") or f"Removed '{args.skill}' from {project_path.name}")
        return code

    if not manifest_path.exists():
        return finish({"schema": 1, "status": "BLOCKED", "reason": f"No manifest found at {manifest_path}"}, 2)
    manifest = load_yaml(manifest_path)
    skills_list = list(manifest.get("skills") or [])
    if args.skill not in skills_list:
        try:
            from skillsync_workflow.catalog import skills_list as catalog_skills
            from skillsync_workflow.common import load_layer_project
            root, loaded, layerset = load_layer_project(str(project_path), layer_overrides(args))
            row = next((item for item in catalog_skills(root, loaded, layerset)["skills"] if item["name"] == args.skill), None)
        except (ValueError, OSError, yaml.YAMLError):
            row = None
        if row and row["status"] in ("active", "companion"):
            reason = f"Skill '{args.skill}' is part of the project via {row['origin']}, not an explicit manifest entry"
        else:
            reason = f"Skill '{args.skill}' is not in the manifest skills list"
        return finish({"schema": 1, "status": "BLOCKED", "reason": reason}, 2)
    manifest["skills"] = sorted(item for item in skills_list if item != args.skill)
    save_yaml(manifest_path, manifest)
    link_status = "NOT_RUN"
    if not args.no_link:
        # With --json the link output goes to stderr, so stdout stays one JSON report.
        import contextlib
        with contextlib.redirect_stdout(sys.stderr) if args.json else contextlib.nullcontext():
            code = cmd_link(argparse.Namespace(path=str(project_path), dry_run=False, layers=layer_overrides(args)))
        link_status = {0: "PASS", 1: "FAIL"}.get(code or 0, "BLOCKED")
    report = {"schema": 1, "status": "PASS", "project": project_path.name, "removed": args.skill,
              "skills": manifest["skills"], "linked": link_status == "PASS", "link_status": link_status}
    if link_status in ("BLOCKED", "FAIL"):
        report["status"] = link_status
        report["reason"] = (f"Removed '{args.skill}' from the manifest of {project_path.name}, but `link` was "
                            f"{link_status} and the project artefacts were not regenerated; fix the layers "
                            "(see the link output) and run `skillsync link`")
        return finish(report, 2 if link_status == "BLOCKED" else 1)
    return finish(report, 0)


def cmd_skills_index(args) -> int:
    from skillsync_workflow.catalog import cmd_index
    return cmd_index(args)


def main() -> None:
    from skillsync_workflow import __version__
    parser = argparse.ArgumentParser(prog="skillsync")
    parser.add_argument("--version", action="version", version=f"skillsync {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_to_layer_flag(command_parser, what: str) -> None:
        command_parser.add_argument("--to-layer", dest="to_layer", metavar="ID",
                                    help=f"Layer receiving {what} (writing to the core layer requires --to-layer <core>)")

    init_parser = subparsers.add_parser("init", help="Create a personal layer (= layers init --kind personal)")
    init_parser.add_argument("directory", nargs="?", help="Layer directory (default: per-user layers dir)/<id>")
    init_parser.add_argument("--id", help="Layer id (default: directory name, or 'personal')")
    init_parser.add_argument("--json", action="store_true")
    init_parser.set_defaults(func=cmd_init)

    import_parser = subparsers.add_parser("import", help="Import skills and instructions of a project into a layer")
    import_parser.add_argument("path", help="Project path")
    import_parser.add_argument("--dry-run", action="store_true", help="Show what would be written without writing")
    add_to_layer_flag(import_parser, "the imported files (default: personal)")
    add_layer_flag(import_parser)
    import_parser.set_defaults(func=cmd_import)

    link_parser = subparsers.add_parser("link", help="Link skills and instructions")
    link_parser.add_argument("path", nargs="?", help="Project path or .skillsync.yaml")
    link_parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing anything")
    add_layer_flag(link_parser)
    link_parser.set_defaults(func=cmd_link)

    instr_parser = subparsers.add_parser("instructions", help="Build instructions")
    instr_parser.add_argument("path", nargs="?", help="Project path or .skillsync.yaml")
    instr_parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing anything")
    add_layer_flag(instr_parser)
    instr_parser.set_defaults(func=cmd_instructions)

    pull_parser = subparsers.add_parser("pull", help="Update every layer (layers update) and link")
    pull_parser.add_argument("path", nargs="?", help="Project path or .skillsync.yaml")
    pull_parser.set_defaults(func=cmd_pull)

    push_parser = subparsers.add_parser("push", help="Send project changes back to the layers that own them")
    push_parser.add_argument("path", nargs="?", help="Project path or .skillsync.yaml")
    push_parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing anything")
    add_to_layer_flag(push_parser, "new files (default: personal; also the consent to write core files)")
    add_layer_flag(push_parser)
    push_parser.set_defaults(func=cmd_push)

    watch_parser = subparsers.add_parser("watch", help="Watch every layer and link on changes")
    watch_parser.add_argument("path", nargs="?", help="Project path or .skillsync.yaml")
    watch_parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds")
    watch_parser.set_defaults(func=cmd_watch)

    memory_parser = subparsers.add_parser("memory-sync", help="Copy configured memory files to remote hosts (config memory_sync:)")
    memory_parser.add_argument("--dry-run", action="store_true", help="Show what would be copied")
    memory_parser.set_defaults(func=cmd_memory_sync)

    list_parser = subparsers.add_parser("list", help="List available skills (with the providing layers)")
    add_layer_flag(list_parser)
    list_parser.set_defaults(func=cmd_list)

    add_parser = subparsers.add_parser("add", help="Add skill to project")
    add_parser.add_argument("project", help="Project path")
    add_parser.add_argument("skill", help="Skill name to add")
    add_layer_flag(add_parser)
    add_parser.set_defaults(func=cmd_add)

    skills_parser = subparsers.add_parser("skills", help="Manage skills")
    skills_subparsers = skills_parser.add_subparsers(dest="skills_command", required=True)

    skills_add_parser = skills_subparsers.add_parser("add", help="Create new skill")
    skills_add_parser.add_argument("name", help="Skill name (e.g., kubernetes-patterns)")
    skills_add_parser.add_argument("--add-to-project", help="Also add to project manifest")
    add_to_layer_flag(skills_add_parser, "the new skill (default: personal)")
    add_layer_flag(skills_add_parser)
    skills_add_parser.set_defaults(func=cmd_skills_add)

    skills_list_parser = skills_subparsers.add_parser("list", help="List project skills with status and metadata (read-only)")
    skills_list_parser.add_argument("path", nargs="?", default=".", help="Project path or .skillsync.yaml")
    add_layer_flag(skills_list_parser)
    skills_list_parser.add_argument("--status", help="Comma list: active,companion,suggested,available (default: all)")
    skills_list_parser.add_argument("--json", action="store_true", help="JSON output (the only format)")
    skills_list_parser.set_defaults(func=cmd_skills_list)
    skills_remove_parser = skills_subparsers.add_parser("remove", help="Remove an explicit skill from the project manifest")
    skills_remove_parser.add_argument("project", help="Project path")
    skills_remove_parser.add_argument("skill", help="Skill name to remove")
    skills_remove_parser.add_argument("--no-link", action="store_true", help="Edit the manifest without running link")
    skills_remove_parser.add_argument("--json", action="store_true", help="JSON report")
    add_layer_flag(skills_remove_parser)
    skills_remove_parser.set_defaults(func=cmd_skills_remove)
    skills_index_parser = skills_subparsers.add_parser(
        "index", help="Generate the skill index from the merged skill catalog into the cache (never into a layer)")
    add_layer_flag(skills_index_parser)
    skills_index_parser.add_argument("--check", action="store_true", help="Exit 1 when the cached index is stale")
    skills_index_parser.add_argument("--stdout", action="store_true", help="Print the index instead of writing the cache")
    skills_index_parser.set_defaults(func=cmd_skills_index)

    try:
        from skillsync_workflow import register_subcommands
        register_subcommands(subparsers)
    except ModuleNotFoundError as exc:
        if exc.name != "skillsync_workflow":
            raise
        # Older Windows installations can still run legacy sync commands.
        pass

    args = parser.parse_args()
    try:
        result = args.func(args)
    except (ValueError, OSError, yaml.YAMLError) as exc:
        parser.exit(2, f"skillsync: {exc}\n")
    if result:
        sys.exit(result)


def run() -> None:
    """Console entry point (`skillsync`, wrapper, `python -m skillsync_workflow`)."""
    sys.exit(main())


if __name__ == "__main__":
    run()
