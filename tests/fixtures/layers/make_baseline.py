#!/usr/bin/env python3
"""Generuje fixture bazowy K1 (rownowaznosc artefaktow `link`) na kodzie generatora.

Uzycie: python3 tests/fixtures/layers/make_baseline.py [--out KATALOG]
Wymaga stdlib + PyYAML. Uruchamiaj WYLACZNIE na kodzie sprzed zmian warstw.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent
DEFAULT_OUT = HERE / "baseline"

LAYER_YAML = "schema: 1\nid: core\nkind: core\nversion: 0.9.0\ncore_min: 0.9.0\n"

SKILLS = {
    "feature": "---\nname: feature\ndescription: Synthetic feature skill.\n---\n\n# feature\n\nSynthetic body.\n",
    "bugfix": "---\nname: bugfix\ndescription: Synthetic bugfix skill.\n---\n\n# bugfix\n\nSynthetic body.\n",
    "helper": "---\nname: helper\ndescription: Synthetic helper skill.\n---\n\n# helper\n\nSee [ref](references/ref.md).\n",
}
SKILL_EXTRA = {("helper", "references/ref.md"): "# ref\n\nSynthetic reference.\n"}

BASE_MD = "# Base\n\nShared rules for {{project_name}}.\n\n<!-- SKILLSYNC:BASE-END -->\n\nBase tail.\n"
TEMPLATE_MD = "# Template\n\nProject-specific rules.\n"
AGENT_MD = "---\nname: implementer\ndescription: Synthetic implementer role.\n---\n\nImplement the plan.\n"
CODEX_YAML = (
    "max_concurrent_threads_per_session: 3\n"
    "exclude_skills: []\n"
    "agents:\n"
    "  implementer:\n"
    "    name: implementer\n"
    "    model: model-x\n"
    "    model_reasoning_effort: medium\n"
    "    sandbox_mode: workspace-write\n"
)
DEFAULTS_YAML = "workflow:\n  version: 1\n"
MANIFEST = (
    "skills:\n  - feature\n  - bugfix\n  - helper\n"
    "instructions:\n  template: tpl\n  merge:\n    - _base\n"
    "variables:\n  project_name: Synthetic\n"
)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def build_library(lib: Path, with_layer: bool) -> None:
    for name, body in SKILLS.items():
        write(lib / "skills" / name / "SKILL.md", body)
    for (name, rel), body in SKILL_EXTRA.items():
        write(lib / "skills" / name / rel, body)
    write(lib / "agents" / "implementer.md", AGENT_MD)
    write(lib / "instructions" / "_base.md", BASE_MD)
    write(lib / "instructions" / "tpl.md", TEMPLATE_MD)
    write(lib / "hooks" / "check-closure.py", "# synthetic hook\n")
    write(lib / "codex.yaml", CODEX_YAML)
    write(lib / "workflow" / "defaults.yaml", DEFAULTS_YAML)
    if with_layer:
        write(lib / "layer.yaml", LAYER_YAML)


def normalize(text: str, tokens: list) -> str:
    for real, token in tokens:
        text = text.replace(real, token)
    return text


def describe(proj: Path, tokens: list) -> dict:
    files, links = {}, {}
    for root, dirs, names in os.walk(proj):
        root_p = Path(root)
        for name in sorted(dirs[:]):
            p = root_p / name
            if p.is_symlink():
                dirs.remove(name)
                links[p.relative_to(proj).as_posix()] = normalize(os.readlink(p), tokens)
        for name in names:
            p = root_p / name
            rel = p.relative_to(proj).as_posix()
            if p.is_symlink():
                links[rel] = normalize(os.readlink(p), tokens)
                continue
            data = p.read_bytes().replace(b"\r\n", b"\n")
            for real, token in tokens:
                data = data.replace(real.encode(), token.encode())
            files[rel] = hashlib.sha256(data).hexdigest()
    return {"files": dict(sorted(files.items())), "symlinks": dict(sorted(links.items()))}


def run_variant(variant: str, mode: str) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="skillsync-baseline-")).resolve()
    try:
        lib, proj, home = tmp / "library", tmp / "project", tmp / "home"
        (proj / ".github").mkdir(parents=True)
        home.mkdir()
        build_library(lib, with_layer=(variant == "with-layer-yaml"))
        write(proj / ".github" / ".skillsync.yaml", MANIFEST)
        write(tmp / "config.yaml", f"library: {lib}\ndiscover_paths: []\ntags: {{}}\npython_command: python3\n")
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_STATE_HOME": str(home / ".local" / "state"),
            "GIT_CONFIG_GLOBAL": str(tmp / "gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "SKILLSYNC_CONFIG": str(tmp / "config.yaml"),
            "LC_ALL": "C.UTF-8",
        }
        if mode == "copy":
            env["SKILLSYNC_NO_SYMLINK"] = "1"
        done = subprocess.run([sys.executable, str(REPO / "skillsync"), "link", str(proj)],
                              cwd=str(REPO), env=env, capture_output=True, text=True)
        if done.returncode != 0:
            raise SystemExit(f"link failed ({variant}/{mode}) rc={done.returncode}\n{done.stdout}\n{done.stderr}")
        tokens = [(str(lib), "<LIB>"), (str(proj), "<PROJ>"), (str(REPO), "<GEN>"), (str(home), "<HOME>")]
        result = describe(proj, tokens)
        # Symlinki wskazujace w biblioteke: adres wzgledny wobec korzenia biblioteki.
        result["symlinks"] = {k: (v[len("<LIB>/"):] if v.startswith("<LIB>/") else v) for k, v in result["symlinks"].items()}
        result["variant"], result["mode"] = variant, mode
        result["link_stdout"] = normalize(done.stdout, tokens)
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for variant in ("with-layer-yaml", "legacy"):
        for mode in ("symlink", "copy"):
            data = run_variant(variant, mode)
            path = args.out / f"{variant}.{mode}.json"
            path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
            print(f"wrote {path.relative_to(args.out.parent) if args.out.is_relative_to(args.out.parent) else path} "
                  f"({len(data['files'])} files, {len(data['symlinks'])} symlinks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
