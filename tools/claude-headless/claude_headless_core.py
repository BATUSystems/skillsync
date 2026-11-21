"""Shared core of the wrappers that start the real `claude` (CLAUDE-4 `claude-eval`, CLAUDE-6 `claude-headless`).

Only what both wrappers need lives here: the validation of the arguments (cost limit, profile directory, model name), the clean environment of the child
and the strict reader of the frontmatter of a markdown file. Nothing here starts a process. The module name is unique on purpose (the directories of
the tools have a hyphen, so they are reached through `sys.path`, and a common name such as `common` would collide with `skillsync_workflow/common.py`)."""
import argparse
import os
import re
from pathlib import Path

import yaml

ENV_ALLOWLIST = ('PATH', 'HOME', 'LANG')
MAX_COST_CEILING_USD = 5.0
MODEL_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:\[\]-]*$')
HEADER_RE = re.compile(r'---[ \t]*\r?\n(.*?)^---[ \t]*(?:\r?\n|\Z)', re.S | re.M)


def positive_cost(text):
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f'not a number: {text}')
    if not 0 < value <= MAX_COST_CEILING_USD:
        raise argparse.ArgumentTypeError(f'must be above 0 and at most {MAX_COST_CEILING_USD} USD')
    return value


def absolute_dir(text):
    if not os.path.isabs(text) or '..' in Path(text).parts:
        raise argparse.ArgumentTypeError(f'must be an absolute path without "..": {text}')
    if not os.path.isdir(text):
        raise argparse.ArgumentTypeError(f'is not an existing directory: {text}')
    return text


def model_name(text):
    if not MODEL_RE.fullmatch(text):
        raise argparse.ArgumentTypeError(f'not a plain model identifier: {text!r}')
    return text


def build_env(environ, config_dir, home=None):
    """The environment of the child: only the allowlisted variables and `LC_*`, plus `CLAUDE_CONFIG_DIR`; `home` replaces `HOME` (a temporary one)."""
    env = {key: value for key, value in environ.items() if key in ENV_ALLOWLIST or key.startswith('LC_')}
    if home is not None:
        env['HOME'] = str(home)
    env['CLAUDE_CONFIG_DIR'] = config_dir
    return env


class UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate and non-string keys (a later key would silently win)."""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            if not isinstance(key, str):
                raise yaml.YAMLError(f'non-string key {key!r}')
            if key in seen:
                raise yaml.YAMLError(f'duplicate key {key!r}')
            seen.add(key)
        return super().construct_mapping(node, deep)


def frontmatter(path):
    """(dict, error) of the YAML header of a markdown file; the closing marker is a whole `---` line."""
    try:
        text = Path(path).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as error:
        return None, f'unreadable ({error.__class__.__name__})'
    found = HEADER_RE.match(text)
    if not found:
        return None, 'no frontmatter'
    try:
        data = yaml.load(found.group(1), Loader=UniqueKeyLoader) or {}
    except yaml.YAMLError as error:
        return None, f'invalid YAML ({str(error).splitlines()[0]})'
    return (data, None) if isinstance(data, dict) else (None, 'frontmatter is not a mapping')
