# Prototype theme check: theme_check.py

The theme check is the single source for checking the theme of prototypes in the `prototype` skill. Instead of three copies with different quality (the skill guard, a copy in a project, a project script) there is one script, `scripts/theme_check.py`, which does what its JSON configuration describes. The CSS parser is shared (`scripts/css_tokens.py`), colours are computed by `scripts/theme_colors.py`, style rules are in `scripts/theme_proto.py`. The script uses only the standard library (Python 3.11+) and writes nothing.

The check covers the theme of a project, not the skill. The guard of the skill is `scripts/check_tokens.py` (see [style.md](style.md)).

## What it is for

- coverage of the list of required tokens in the light block of the theme;
- consistency of the dark blocks with the chosen mode (`separate`, `mirror`, `none`);
- a ban on constructs in token files (`color-scheme`, `prefers-color-scheme`);
- consistency of file copies with their source (the source can be a project file or a skill file);
- contrast of colour pairs (WCAG 2.x) computed from the light block;
- a ban on literals in prototype styles (`project.css`, `index.html`): colours, fonts, radii, weights, line heights, sizes and `z-index`.

Each check is enabled by its own configuration key. A missing key means the check does not exist; it is never skipped silently. Missing input gives exit code 2 or UNVERIFIED, never PASS.

## Invocation

    python3 <skill>/scripts/theme_check.py --config docs/design/theme-check.json --project .
    python3 <skill>/scripts/theme_check.py --config docs/design/theme-check.json --project . --only tokens,dark
    python3 <skill>/scripts/theme_check.py --config docs/design/theme-check.json --project . --json
    python3 <skill>/scripts/theme_check.py --config - --project . < theme-check.json
    python3 <skill>/scripts/theme_check.py --selftest

- `--config <file.json>` is the configuration. `--config -` reads it from standard input and requires `--project`.
- `--project <directory>` is the root of the configuration paths. By default it is the directory where the configuration file is, not the current directory; pass it explicitly.
- `--skill-root <directory>` is the directory of skills for `skill:` sources. Without it the script searches the project (see "Paths and security").
- `--only <check>[,...]` runs the chosen checks (`tokens`, `dark`, `forbid`, `copies`, `copyDirs`, `contrast`, `prototype`). The name must be in the configuration, otherwise exit code 2. Skipped checks are reported as INFO.
- `--json` prints the result as JSON instead of text lines (see "Output and exit codes").
- `--selftest` runs fixtures in memory: each check must be able to fail. It does not read a project.

Programmatic input, for wrappers that build the configuration in memory:

    import sys
    sys.path.insert(0, "<skill>/scripts")
    import theme_check
    res = theme_check.run(config, project, skill_root=None, only=None)
    res.code, res.status, res.findings, res.error

`config` is a dictionary, `project` is a path (string or `Path`). `run` does not raise on configuration errors or on an unexpected error: it returns `error` and code 2.

## Configuration

A JSON file with `"version": 1`. An unknown key at any level is exit code 2 with a message that does not quote the file. Paths in the configuration are relative to `--project`.

    {
      "version": 1,
      "tokens": {"files": ["docs/design/tokens.css"], "required": "skill:prototype/assets/theme/required-tokens.txt", "assets": null},
      "dark": "separate",
      "forbid": ["color-scheme", "prefers-color-scheme"],
      "copies": [{"source": "skill:<skill>/assets/theme/default.css", "copy": "docs/design/theme.css", "mode": "auto"}],
      "copyDirs": [{"source": "skill:<skill>/assets/fonts", "copy": "docs/prototypes/fonts", "include": ["*.woff2", "LICENSE"]}],
      "contrast": {
        "pairs": [{"name": "text on background", "fg": "--foreground", "bg": "--background", "min": 4.5, "deviation": "D12"}],
        "pairsFrom": {"file": "docs/design/palette.json", "path": "contrastPairs"},
        "deviations": {"file": "docs/domain/deviations.md", "pattern": "^###\\s+(D\\d+)\\b"}
      },
      "prototype": {
        "dirs": ["docs/prototypes/*"], "dirMarker": "index.html", "files": ["project.css", "index.html"],
        "knownTokensFrom": ["tokens.css", "theme.css"], "onMissingFile": "fail",
        "rules": {"color": true, "font": true, "radius": {"allow": ["0", "50%", "999px", "inherit"]}, "weight": true, "lineHeight": true, "size": false, "zIndex": "tokens"}
      }
    }

The example shows all keys. A real configuration has only the checks it needs. The names `docs/design/...`, `docs/prototypes/*` and `<skill>` are examples.

| Key | Value | Default | Notes |
|---|---|---|---|
| `version` | `1` | required | any other value is exit code 2 |
| `tokens.files` | list of project paths | required in `tokens` | a missing file is FAIL `„brak pliku tokenów”` |
| `tokens.required` | path of the list (`skill:…` or a project path) or `null` | required in `tokens` | a missing list is exit code 2; `null` turns coverage off and always gives a WARN |
| `tokens.assets` | directory of template assets or `null` | `null` | requires `required` other than `null` |
| `dark` | `separate`, `mirror` or `none` | no default; the value is required when the key is present | requires `tokens` |
| `forbid` | non-empty list: `color-scheme`, `prefers-color-scheme` | none | requires `tokens` |
| `copies` | non-empty list of objects `source`, `copy`, `mode` | none | `mode`: `auto`, `text` or `bytes` |
| `copyDirs` | non-empty list of objects `source`, `copy`, `include` | none | `include` is required, a non-empty list of patterns |
| `contrast.pairs` | list of pairs | `[]` | a pair needs `fg` and `bg`; `name`, `min`, `deviation` are optional |
| `contrast.pairs[].name` | string | `<fg> on <bg>` | the name used in messages |
| `contrast.pairs[].min` | positive number | `4.5` | the contrast threshold |
| `contrast.pairsFrom` | `{file, path}` | `null` | a JSON file; `path` uses dots, numeric index for lists; the value under the path is a list of pairs with the same keys |
| `contrast.deviations` | `{file, pattern}` | `null` | `pattern` is a regex up to 200 characters; group 1, or the whole match when there is no group, is the identifier |
| `prototype.dirs` | non-empty list of directory patterns | required | `fnmatch` on each path segment, no `**` |
| `prototype.dirMarker` | relative path inside a directory | `index.html` | a directory without the marker is not a prototype |
| `prototype.files` | non-empty list of relative paths | `project.css`, `index.html` | style files to check |
| `prototype.knownTokensFrom` | list of relative paths inside a prototype directory | `[]` | tokens from them and from `tokens.files` form the set of known tokens |
| `prototype.onMissingFile` | `fail` or `skip` | `fail` | `skip` reports INFO instead of FAIL |
| `prototype.rules` | object of rules | `{}` (all off) | see the `prototype` check |

## Paths and security

- A project path is relative to `--project`: no `..`, no drive letter, no separator at the start (`/` or `\`). After `realpath` it must be under `realpath(--project)`; a symlink leading outside the project is exit code 2. All files that are read (tokens, pairs, deviations, copies, prototype files) are checked the same way. A file larger than 2 MiB or not in UTF-8 is exit code 2.
- A `skill:<name>/<path>` source: `<name>` is one skill name matching `[a-z0-9][a-z0-9-]*` (no `/` and no `..`), and the path is relative with no `..`.
- `--skill-root` is explicit: the skill is `<skill-root>/<name>`. Without it the script searches the project in the order `.claude/skills`, `.agents/skills`, `.github/skills` and picks the first directory that contains the skill (separately for each skill). The script does not compute the skills directory through `resolve()` of its own file, nor from user directories. A missing skill is exit code 2.
- A symlink of a skill to the library is a normal state. `skill:` sources are not checked with `realpath` inside the project.
- Absolute paths (`/x`, `\x`, `C:x`) are rejected by the script's own check; the `isabs` guard of the generator scans only the generator.
- A deviation pattern is at most 200 characters. An invalid regex is exit code 2, without quoting the pattern. Lines of the deviations file longer than 500 characters are skipped with a WARN.
- CSS nesting deeper than 200 levels is exit code 2.

## Checks

### tokens

Coverage of the list of required tokens in the light block (`:root`, `@theme`, `@theme inline`). A token defined only in a dark block is a FAIL `„token … zdefiniowany tylko w bloku ciemnym”` with its line number. A token defined nowhere is a FAIL `„brak definicji wymaganego tokenu”`. No light block at all is a FAIL. An empty list is UNVERIFIED.

When `assets` is given, the set of tokens used by the template assets (`var(--x)`) must equal the list. A missing and an extra token are both FAILs.

### dark

Comparison of the dark blocks according to the mode (block definitions: see "Block definitions"):

- `separate`: the dark blocks have the same tokens with the same values (after normalising whitespace). A token present in one block and missing in the other is a FAIL, and different values are a FAIL. One dark block gives PASS with a note, because there is nothing to compare it with. No dark block gives UNVERIFIED. The mode does not require both families of blocks (`.dark`/`[data-theme]` and `@media`); it compares the ones that exist.
- `mirror`: the dark block must be identical to the light one: the same tokens with the same values. No dark block is a FAIL `„brak bloku ciemnego”`. The mode fits a project with the dark theme switched off (`prototype.dark_theme: false` in the manifest).
- `none`: a dark block must not exist, so each such block is a FAIL. Declarations other than tokens in a dark block are a WARN.

A selector containing the word `dark` that the engine does not recognise (for example `html[data-theme=dark]` or `body.dark`) gives a WARN.

### forbid

Searches the files from `tokens.files` for forbidden constructs, skipping comments: `color-scheme:` and every occurrence of `prefers-color-scheme`, including in `@media (prefers-color-scheme: …)`. Each occurrence is a FAIL with its line number.

### copies and copyDirs

`copies`: the copy (`copy`) must equal the source. A missing source is exit code 2, a missing copy is a FAIL `„brak kopii”`, and a different content is a FAIL. Text files (`.css`, `.html`, `.htm`, `.js`, `.mjs`, `.json`, `.md`, `.txt`, `.svg`, `.xml`, `.yaml`, `.yml`, `.csv`) are compared after normalisation: no BOM, line endings converted to `\n`, one final `\n`. `mode: "bytes"` compares bytes, `mode: "text"` forces text, and `auto` chooses by extension. Other files (for example fonts) are compared byte for byte.

`copyDirs`: compares the files of the source directory selected by `include`. It is an explicit list of `fnmatchcase` patterns: a pattern without `/` matches the file name, a pattern with `/` matches the relative path. Patterns select the files, so list them explicitly (for example `*.woff2`, `LICENSE`) rather than everything at once. When no file matches, the result is UNVERIFIED. A missing source directory is exit code 2, a missing file in the copy is a FAIL.

### contrast

Colour pairs from the light block. A colour of a pair is a token name (`--x`), an alias `var(--x)` (up to 5 levels) or a colour literal.

- Supported formats: `#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa`, `rgb()`, `rgba()`, `hsl()`, `hsla()`, `white`, `black`, `transparent`, and `oklch()`, `oklab()`, `lab()`, `lch()`. The OKLab, Lab and LCH spaces are converted to sRGB as in CSS Color 4. A result outside the sRGB gamut is clipped and the finding gets `outOfGamut: true` (in the text: „kolor poza gamutem sRGB przycięty”).
- Alpha: a `fg` colour with alpha is composited over `bg`, and a `bg` with alpha is composited over white.
- Other formats (`color()`, `color-mix()`, `hwb()`, wide-gamut spaces, named colours, `calc()`) are UNVERIFIED with the field `reason: color-format`. An alias cycle or a chain longer than 5 levels is UNVERIFIED with `reason: alias`. Never an exception, never a PASS.
- The WCAG threshold is `min` (default 4.5). The verdict uses the unrounded ratio, and the luminance threshold is 0.04045.
- A token of a pair that is not defined in the light block is a FAIL with `reason: missing-token`. Pairs with tokens require `tokens` in the configuration (exit code 2 without it).
- A pair with `deviation`: if the identifier is in the deviations file, falling below the minimum gives a WARN; if it is not there, a FAIL. A pair without `deviation` below the minimum is a FAIL. A pair with `deviation` but no `deviations` in the configuration is exit code 2.
- Pairs from `pairsFrom` are read from a JSON file at the given path; a missing file is exit code 2.

**Note:** contrast is computed from the LIGHT block only. A pair that passes in the light theme and fails in the dark theme gives PASS. The contrast of the dark theme needs a separate check outside this script.

### prototype

Style rules for the files from `prototype.files` in each prototype directory. A prototype directory matches `dirs` and has `dirMarker`. Zero such directories gives UNVERIFIED. The CSS files and the `<style>` blocks and `style` attributes in HTML are checked; `url()` and strings are masked.

Known tokens are `knownTokensFrom` from the prototype directory plus the tokens from `tokens.files`. A `var(--x)` outside this set is a FAIL `„nieznany token”`. An empty set of known tokens is a FAIL. A fallback `var(--x, value)` is scanned like any other value.

Rules from `rules` (a key that is present means the rule is on):

- `color` (`true`): a ban on colour literals: colour functions (`rgb`, `hsl`, `hwb`, `lab`, `lch`, `oklab`, `oklch`, `color`, `color-mix`, `light-dark` and others), hex values, CSS named colours and system colours. `transparent` and `currentcolor` are allowed.
- `font` (`true`): `font-family` only through `var()` or `inherit`. A list such as `var(--a), serif` is a FAIL, because a literal stands outside `var()`.
- `radius` (`true` or `{"allow": […]}`): in `border*radius` (also with a vendor prefix), outside `allow` only `var()` is allowed. `true` means no allowed values.
- `weight` (`true`): `font-weight` only through `var()`; the words `normal`, `bold` and numbers are a FAIL.
- `lineHeight` (`true`): `line-height` only through `var()`.
- `size` (`true`): in `font` and `font-size` a ban on size literals: `px`, `rem`, `em`, `pt`, `pc`, `cm`, `mm`, `in`, `vw`, `vh`, `vmin`, `vmax`, `ch`, `ex`, `%`. It does not apply to `font-weight`.
- `zIndex`: `tokens` (only `var(--z-index-*)`), `forbid` (every `z-index` is a FAIL), `allow` (not checked; the default).

`onMissingFile`: `fail` (default) treats a missing file from `files` as a FAIL; `skip` reports INFO `„pominięto brak pliku”`.

## Block definitions

As in the prototype engine (`extract_tokens` in the generator; `tests/test_skillsync_css_tokens.py` checks the consistency):

- Light block: the selector exactly `:root` (also in a comma-separated list), `@theme` or `@theme inline`. It may sit in `@layer` at any depth, but not in `@media`, `@supports` or another block.
- Dark block: the selector exactly `.dark`, `[data-theme="dark"]` or `:root[data-theme="dark"]` (quotes are normalised). A selector containing the word `dark` in another meaning (`.card-dark`, `.dark .card`) is not a dark block.
- `@media (prefers-color-scheme: dark)` is recognised separately. It takes part in the `dark` mode and in `forbid`. The engine skips it with a warning.
- The value of a token is its last definition in source order (in a file the larger line number, between files the later file).

## Output and exit codes

Each result is a line `STATUS check: message [file:line]`, for example:

    FAIL forbid: „zakazana konstrukcja” color-scheme [docs/design/tokens.css:3]

The last line is `theme_check: <STATUS> („kod” N): X PASS, Y FAIL, Z WARN, W UNVERIFIED` (the program prints the word `„kod”` in Polish). `STATUS` is `PASS`, `FAIL`, `UNVERIFIED` or `ERROR` (code 2).

`--json` prints an object `{"status", "code", "error", "findings": [...]}`. Each finding has `status`, `check`, `message`, `file`, `line` and extra fields: `reason` (contrast), `ratio`, `min`, `outOfGamut`. A usage error goes to stderr as `theme_check: „BŁĄD”: <message>`, and in JSON mode also as an object with `status: "ERROR"` on stdout.

Exit codes:

- `0`: PASS (also PASS with WARN and INFO).
- `1`: at least one FAIL.
- `2`: usage error, configuration error or missing source (a list, a skill, a copy directory, a pairs file, a deviations file); a path leaving the project; an internal error.
- `3`: UNVERIFIED without FAIL, meaning nothing was measured: an empty configuration, zero files, zero prototype directories, no dark block in `separate`, no file matching `include`, no pairs.

Priority: 2, then 1, then 3. `--selftest` returns 0 when each fixture gives the expected result (`theme_check: selftest OK („N przypadków”)`), and 1 with the list of mismatches on stderr when one does not.

## Safety net

- A configuration error (`ConfigError`) and any unexpected exception end with exit code 2. An unexpected exception gives the message `„błąd wewnętrzny” (<type>)` without the contents of files.
- Error messages do not quote the configuration, the files or the deviation pattern.
- The script writes nothing.
- The configuration file may start with a BOM.

## Notes

- `separate` does not require both families of dark blocks: it compares the ones that exist. Zero blocks is UNVERIFIED, one block is PASS with a note.
- A selector with `dark` that the engine does not recognise gives a WARN (neither PASS nor FAIL).
- `fnmatchcase` (in `include` and in `dirs`) is case-sensitive, also on Windows. `SOURCE.txt` or `LICENSE` must be listed in `include` explicitly.
- Text normalisation ends the file with exactly one `\n`.
- `tokens.required: null` turns coverage off and always gives a WARN. `--only tokens` with `null` is exit code 2.
- A pair with `deviation` requires `contrast.deviations`; without it, exit code 2.
- The `dark` key requires a value (there is no default value).

## Migrating from your own theme check scripts

A recipe for a project that has its own theme check script.

1. List the checks of the old script and assign each one to a configuration key (the table in "Configuration"). General checks (list coverage, dark blocks, forbidden constructs, copies, pair contrast, prototype styles) move to the configuration.
2. What depends on the project stays in the project: values from design tools, the generator of the token file and its byte equality, the content of the deviations file, sheet rules that need project knowledge, font embedding. This part is a separate project script that runs next to `theme_check.py`; both must give code 0.
3. Choose the `dark` mode: `separate` when both blocks must have the same values; `mirror` when the project switches the dark theme off; `none` when a dark block must not exist.
4. Set the list of required tokens (`required`) and check that the old script sees the same list. A missing list is exit code 2, not a silent skip.
5. Run the new check next to the old one on the current theme and compare the results. Switch when the results agree or the differences are described.
6. Remove the old script in a separate step.

Stricter rules compared with typical project scripts (the new check may find more; this is intended):

- `font-family` with `var()` plus a literal (for example `var(--font), serif`) is a FAIL; a list of `var()` alone is allowed.
- `size` covers `%`, `vw`, `vh`, `ch` and the other units in the list (`vmin`, `vmax`, `ex`); it does not apply to `font-weight`, which is the separate `weight` rule.
- `zIndex` in the `tokens` mode requires only `var(--z-index-*)`; `forbid` bans everything; `allow` does not check.
- `radius: true` means no allowed values: every radius literal is a FAIL.
- Radius rules cover vendor prefixes (`-webkit-border-radius`, `-moz-border-radius`).
- `var(--x)` outside the set of known tokens is a FAIL, also in a fallback `var(--x, value)`.
- A dark block with a declaration other than a token in the `none` mode is a WARN.
- A directory matching `dirs` without `dirMarker` is not a prototype. A prototype without a file from `files` is a FAIL (or INFO with `skip`).
- Contrast: formats outside the list and pairs without a result are UNVERIFIED, not skipped.
- A symlink leading outside the project is exit code 2, not a silent read.
