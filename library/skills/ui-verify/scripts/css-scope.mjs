/**
 * Purpose: Guard the CSS scope of an overlay stylesheet: every selector must sit under one root class (or a declared exception), the overlay may only add rules, and SCSS is compiled with LibSass for the check.
 * Usage: node css-scope.mjs [--json] [--config <config.json>] [--root <.class>] [--out <file.css>] check <file.css> | diff <with.css> <without.css> | compile | run | selftest; node css-scope.mjs --selftest
 * Inputs: CSS files (<= 20 MB); a JSON config (version, root, scssDir, entry, without, out, exceptions, selftest, important, diff) whose paths are relative to the config directory;
 *   env UI_VERIFY_CACHE_DIR (cache root with sass.js), UI_VERIFY_SECRET_ENV (comma-separated names of env vars whose values are masked in output).
 * Outputs: stdout lines "PASS|FAIL|INFO|WARN <check>: <detail>" (run ends with "RESULT: PASS|FAIL|NOT_RUN"), lists of up to 50 offending selectors and 10 diff examples;
 *   with --json one object { command, verdict, exit, checks, counts }; compile and run write CSS only to --out or config.out under the config directory.
 * Exit codes: 0 PASS, 1 FAIL (scope violation, removed or changed rule, addition outside the root, SCSS error, !important above max, selftest mismatch), 2 usage/configuration/CSS parse/path/internal error, 3 missing sass.js (NOT_RUN).
 * Dependencies: sass.js 0.11.1 (from the skill cache, only for compile and run; see lib/deps.mjs); Node >= 20.11 (tested on 22).
 * Limits: LibSass 3.6 via sass.js, not the Generator build (gulp-sass); !important is counted, not judged; no CSS rewriting; only top-level *.scss files of scssDir are compiled; CSS files up to 20 MB.
 * Security: reads only the given files and scssDir; writes only the compiled CSS under the config directory (no symlinks, no ..); output is masked for listed secrets; no network.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { parseArgs, runSelftest } from './lib/cli.mjs';
import { CssParseError, countImportant, scan, splitSelectors } from './lib/css-parse.mjs';
import { checkScope } from './lib/css-scope-core.mjs';
import { builtinCases, builtinConfig, parseConfig, runCases, toScopeConfig, validateConfig } from './lib/css-config.mjs';
import { diffCss } from './lib/css-diff.mjs';
import { compileScss } from './lib/css-compile.mjs';
import { createMasker, createSink, collectSecretsFromEnv, setGuardMasker } from './lib/out.mjs';
import { PolicyError, resolveInside } from './lib/policy.mjs';
import { EXIT, exitFor, installGuards, isMain } from './lib/verdict.mjs';
import { DepsError, explainMissing } from './lib/deps.mjs';
import { loadSass, resolveCacheDir, skillDirOf } from './lib/runtime.mjs';

const SPEC = {
  string: ['config', 'root', 'out'],
  boolean: ['selftest', 'json'],
};
const USAGE = 'usage: node css-scope.mjs [--json] [--config <config.json>] [--root <.class>] [--out <file.css>] check <file.css> | diff <with.css> <without.css> | compile | run | selftest; node css-scope.mjs --selftest';
const MAX_CSS_BYTES = 20 * 1024 * 1024;
const MAX_BAD = 50;
const ROOT_RE = /^\.[A-Za-z_][A-Za-z0-9_-]*$/;
const COMMANDS = { check: 1, diff: 2, compile: 0, run: 0, selftest: 0 };

class UsageError extends Error {}

// --- pliki i konfiguracja ---

function readCssFile(cwd, name) {
  const abs = path.resolve(cwd, name);
  let st;
  try {
    st = fs.statSync(abs);
  } catch (e) {
    throw new UsageError(`cannot read ${name}: ${e && e.code === 'ENOENT' ? 'file not found' : (e && e.code) || 'error'}`);
  }
  if (!st.isFile()) throw new UsageError(`cannot read ${name}: not a regular file`);
  if (st.size > MAX_CSS_BYTES) throw new UsageError(`${name}: file larger than 20 MB`);
  return fs.readFileSync(abs, 'utf8');
}

function loadConfig(cwd, name) {
  const abs = path.resolve(cwd, name);
  let text;
  try {
    const st = fs.statSync(abs);
    if (!st.isFile()) throw new UsageError(`cannot read config ${name}: not a regular file`);
    text = fs.readFileSync(abs, 'utf8');
  } catch (e) {
    if (e instanceof UsageError) throw e;
    throw new UsageError(`cannot read config ${name}: ${e && e.code === 'ENOENT' ? 'file not found' : (e && e.code) || 'error'}`);
  }
  const configDir = path.dirname(abs);
  const r = parseConfig(text, { configDir });
  if (!r.ok) throw new UsageError(`bad config ${name}: ${r.errors.join('; ')}`);
  return { config: r.config, configDir };
}

// --- kontrole jako dane: { name, verdict, detail, lines?, examples? } ---

const chk = (name, verdict, detail, extra = {}) => ({ name, verdict, detail, ...extra });

function scopeChecks(css, scopeConfig, config) {
  const r = checkScope(css, scopeConfig);
  const nExc = (scopeConfig.exceptions || []).length;
  const out = [];
  if (r.ok) {
    out.push(chk('scope', 'PASS', `scope OK: ${r.rules} rules, all under ${scopeConfig.root} (+ ${nExc} exceptions), !important: ${r.important}`));
  } else {
    const shown = r.bad.slice(0, MAX_BAD);
    const examples = shown.map((b) => `  ${b}`);
    if (r.bad.length > MAX_BAD) examples.push(`  ... and ${r.bad.length - MAX_BAD} more`);
    out.push(chk('scope', 'FAIL', `${r.bad.length} selectors outside scope ${scopeConfig.root} (${r.rules} rules)`, { examples }));
  }
  for (const w of r.warnings) out.push(chk('scope', 'WARN', w));
  return { r, out };
}

function importantCheck(count, config, standalone) {
  const max = config && config.important && config.important.max;
  const out = [];
  if (standalone) out.push(chk('important-count', 'INFO', String(count)));
  if (typeof max === 'number') {
    if (count > max) out.push(chk('important', 'FAIL', `${count} > ${max}`));
    else if (!standalone) out.push(chk('important', 'PASS', `${count} <= ${max}`));
  } else if (!standalone) {
    out.push(chk('important', 'INFO', `count ${count} (no limit configured)`));
  }
  return out;
}

function diffChecks(withCss, withoutCss, scopeConfig, config) {
  const r = diffCss(withCss, withoutCss, scopeConfig, { allowAtRules: config.diff.allowAtRules });
  const c = r.counts;
  const detail = `with ${c.with}, without ${c.without}; added ${c.added} (allowed ${c.addedAllowed}), removed ${c.removed}`;
  return { r, check: chk('diff', r.ok ? 'PASS' : 'FAIL', detail, { examples: r.examples }) };
}

function caseChecks(results) {
  return results.map((c) => chk(c.name, c.pass ? 'PASS' : 'FAIL', `expected ${c.expect}, got ${c.got}`, { plain: true }));
}

function selftestCaseResults(config) {
  const res = runCases(builtinCases(), builtinConfig());
  if (config) res.push(...runCases(config.selftest || [], toScopeConfig(config)));
  return res;
}

// --- wynik i render ---

function render(result, json) {
  if (json) {
    const checks = result.checks.map(({ name, verdict, detail, examples }) => (examples && examples.length ? { name, verdict, detail, examples } : { name, verdict, detail }));
    return `${JSON.stringify({ command: result.command, verdict: result.verdict, exit: result.exit, checks, counts: result.counts })}\n`;
  }
  const lines = [];
  for (const c of result.checks) {
    if (c.plain) {
      lines.push(`${c.verdict} ${c.name} (${c.detail})`);
      continue;
    }
    lines.push(`${c.verdict} ${c.name}: ${c.detail}`);
    for (const e of c.examples || []) lines.push(e);
  }
  if (result.summary) lines.push(result.summary);
  return `${lines.join('\n')}\n`;
}

function finish(command, checks, counts, { summary, verdictOverride } = {}) {
  const verdict = verdictOverride || (checks.some((c) => c.verdict === 'FAIL') ? 'FAIL' : 'PASS');
  return { command, verdict, exit: exitFor(verdict), checks, counts, summary };
}

// --- polecenia ---

async function cmdCheck(ctx) {
  const { values, positionals, cwd } = ctx;
  let config = null;
  let scopeConfig;
  if (values.config !== undefined) {
    if (values.root !== undefined) throw new UsageError('use either --config or --root, not both');
    config = loadConfig(cwd, values.config).config;
    scopeConfig = toScopeConfig(config);
  } else if (values.root !== undefined) {
    if (!ROOT_RE.test(values.root)) throw new UsageError(`bad --root "${values.root}" (expected a class like ".name")`);
    scopeConfig = { root: values.root, exceptions: [] };
  } else {
    throw new UsageError('check needs --config <config.json> or --root <.class>');
  }
  const css = readCssFile(cwd, positionals[1]);
  const { r, out } = scopeChecks(css, scopeConfig, config);
  const imp = importantCheck(r.important, config, true);
  const checks = [...out.filter((c) => c.verdict !== 'WARN'), ...imp, ...out.filter((c) => c.verdict === 'WARN')];
  return finish('check', checks, { rules: r.rules, important: r.important, bad: r.bad.length });
}

async function cmdDiff(ctx) {
  const { values, positionals, cwd } = ctx;
  if (values.config === undefined) throw new UsageError('diff needs --config <config.json>');
  const { config } = loadConfig(cwd, values.config);
  const withCss = readCssFile(cwd, positionals[1]);
  const withoutCss = readCssFile(cwd, positionals[2]);
  const { r, check } = diffChecks(withCss, withoutCss, toScopeConfig(config), config);
  return finish('diff', [check], { ...r.counts });
}

async function cmdSelftest(ctx) {
  const { values, cwd } = ctx;
  const config = values.config !== undefined ? loadConfig(cwd, values.config).config : null;
  const results = selftestCaseResults(config);
  const passed = results.filter((c) => c.pass).length;
  return finish('selftest', caseChecks(results), { passed, total: results.length }, { summary: `selftest: ${passed}/${results.length} PASS` });
}

function needScss(config) {
  if (typeof config.scssDir !== 'string' || typeof config.entry !== 'string') throw new UsageError('compile and run need scssDir and entry in --config');
}

async function loadSassOrNull(ctx) {
  const { env, skillDir } = ctx;
  const cacheDir = resolveCacheDir({ skillDir, env, platform: process.platform, homedir: os.homedir() });
  try {
    return { Sass: await loadSass({ cacheDir, env, skillDir }) };
  } catch (e) {
    if (e instanceof DepsError && e.exit === 3) return { missing: e };
    throw e;
  }
}

function outTarget(ctx, config) {
  const rel = ctx.values.out !== undefined ? ctx.values.out : config.out;
  if (rel === undefined) return undefined;
  try {
    resolveInside(ctx.configDir, rel);
  } catch (e) {
    if (e instanceof PolicyError) throw new PolicyError(e.code, `output path ${rel}: ${e.message}`);
    throw e;
  }
  return rel;
}

async function compileOne(ctx, Sass, dir, entry) {
  return compileScss({ Sass, dir, entry, fs });
}

async function notRun(ctx, command, checks, missing) {
  const ex = explainMissing(missing);
  await ctx.sink.write('stderr', `${ex.lines.join('\n')}\n`);
  checks.push(chk('compile', 'INFO', 'not run: sass.js is not installed (node scripts/install.mjs)'));
  const res = finish(command, checks, {}, { verdictOverride: 'NOT_RUN', summary: command === 'run' ? 'RESULT: NOT_RUN' : undefined });
  return res;
}

async function cmdCompile(ctx) {
  const { values, cwd } = ctx;
  if (values.config === undefined) throw new UsageError('compile needs --config <config.json>');
  const { config, configDir } = loadConfig(cwd, values.config);
  ctx.configDir = configDir;
  needScss(config);
  const out = outTarget(ctx, config);
  const dir = resolveInside(configDir, config.scssDir);
  const loaded = await loadSassOrNull(ctx);
  if (loaded.missing) return notRun(ctx, 'compile', [], loaded.missing);
  const r = await compileOne(ctx, loaded.Sass, dir, config.entry);
  if (!r.ok) return finish('compile', [chk('compile', 'FAIL', `${config.entry}: ${r.error}`)], { files: r.files });
  if (out !== undefined) await createSink({ masker: ctx.masker, fs, root: configDir, stdout: ctx.stdout, stderr: ctx.stderr }).writeFile(out, r.css);
  return finish('compile', [chk('compile', 'PASS', `${config.entry} -> ${r.css.length} chars`)], { files: r.files, chars: r.css.length });
}

async function cmdRun(ctx) {
  const { values, cwd } = ctx;
  if (values.config === undefined) throw new UsageError('run needs --config <config.json>');
  const { config, configDir } = loadConfig(cwd, values.config);
  ctx.configDir = configDir;
  needScss(config);
  const out = outTarget(ctx, config);
  const dir = resolveInside(configDir, config.scssDir);
  const scopeConfig = toScopeConfig(config);
  const checks = [];
  const counts = {};

  const cases = selftestCaseResults(config);
  const bad = cases.filter((c) => !c.pass);
  counts.selftest = { passed: cases.length - bad.length, total: cases.length };
  if (bad.length === 0) checks.push(chk('selftest', 'PASS', `${cases.length}/${cases.length} cases`));
  else checks.push(chk('selftest', 'FAIL', bad.map((c) => `${c.name} (expected ${c.expect}, got ${c.got})`).join('; ')));

  const loaded = await loadSassOrNull(ctx);
  if (loaded.missing) return notRun(ctx, 'run', checks, loaded.missing);

  const compiled = await compileOne(ctx, loaded.Sass, dir, config.entry);
  if (!compiled.ok) {
    checks.push(chk('compile', 'FAIL', `${config.entry}: ${compiled.error}`));
    return finish('run', checks, counts, { summary: 'RESULT: FAIL' });
  }
  checks.push(chk('compile', 'PASS', `${config.entry} -> ${compiled.css.length} chars`));
  counts.chars = compiled.css.length;
  if (out !== undefined) await createSink({ masker: ctx.masker, fs, root: configDir, stdout: ctx.stdout, stderr: ctx.stderr }).writeFile(out, compiled.css);

  const { r, out: scopeOut } = scopeChecks(compiled.css, scopeConfig, config);
  checks.push(...scopeOut.filter((c) => c.verdict !== 'WARN'));
  counts.rules = r.rules;
  counts.important = r.important;
  checks.push(...importantCheck(r.important, config, false));

  if (config.without !== undefined) {
    const base = await compileOne(ctx, loaded.Sass, dir, config.without);
    if (!base.ok) checks.push(chk('compile-without', 'FAIL', `${config.without}: ${base.error}`));
    else {
      const d = diffChecks(compiled.css, base.css, scopeConfig, config);
      checks.push(d.check);
      Object.assign(counts, d.r.counts);
    }
  }
  checks.push(...scopeOut.filter((c) => c.verdict === 'WARN'));
  const res = finish('run', checks, counts);
  res.summary = `RESULT: ${res.verdict}`;
  return res;
}

// --- --selftest (wewnetrzny, na atrapach) ---

function internalCases() {
  const need = (cond, msg) => { if (!cond) throw new Error(msg); };
  const cfg = { root: '.r', exceptions: [
    { id: 'pre', kind: 'prefix', value: '.host > .item', reason: 'x' },
    { id: 'ali', kind: 'root-alias', value: '.r-theme', onlyPropertyPrefix: '--t-', reason: 'x' },
    { id: 'ex', kind: 'exact-rule', selector: '.r-theme main:focus', declarations: ['outline:0'], reason: 'x' },
  ] };
  const accepted = (css) => checkScope(css, cfg).ok;
  const goodConfig = { version: 1, root: '.r', exceptions: [{ id: 'a', kind: 'prefix', value: '.x > .y', reason: 'why' }] };
  return [
    { name: 'parser ignores braces in comments', fn: () => need(checkScope('/* a { b } */ .r a { color: red }', cfg).rules === 1, 'rules != 1') },
    { name: 'parser ignores braces and !important in strings', fn: () => {
      const r = checkScope('.r a { content: "} { !important" }', cfg);
      need(r.ok && r.rules === 1 && r.important === 0, 'string handled wrong');
    } },
    { name: 'parser keeps :where(a, b) as one selector', fn: () => need(splitSelectors('.r :where(a, b), .r c').length === 2, 'split wrong') },
    { name: 'parser reports line of a stray brace', fn: () => {
      try { scan('.r a { }\n\n}'); } catch (e) { need(e instanceof CssParseError && e.line === 3, `line ${e && e.line}`); return; }
      throw new Error('no error');
    } },
    { name: 'important counter skips comments', fn: () => need(countImportant('/* !important */ .r a { color: red !important }') === 1, 'count != 1') },
    { name: 'scope: rule under root accepted', fn: () => need(accepted('.r a { color: red }'), 'rejected') },
    { name: 'scope: element outside root rejected', fn: () => need(!accepted('a { color: red }'), 'accepted') },
    { name: 'scope: prefix exception accepted', fn: () => need(accepted('.host > .item a { color: red }'), 'rejected') },
    { name: 'scope: root-alias exception only for token properties', fn: () => need(accepted('.r-theme { --t-a: 1 }') && !accepted('.r-theme { color: red }'), 'alias wrong') },
    { name: 'scope: exact-rule exception requires exact declarations', fn: () => need(accepted('.r-theme main:focus { outline: 0 }') && !accepted('.r-theme main:focus { outline: 0; color: red }'), 'exact wrong') },
    { name: 'diff: removed rule detected', fn: () => need(!diffCss('.r a { color: blue }', '.r a { color: red }', cfg).ok, 'ok') },
    { name: 'diff: added rule outside root detected', fn: () => need(!diffCss('.r a { color: red } b { x: y }', '.r a { color: red }', cfg).ok, 'ok') },
    { name: 'diff: added rule under root allowed', fn: () => need(diffCss('.r a { color: red } .r b { x: y }', '.r a { color: red }', cfg).ok, 'not ok') },
    { name: 'config: valid config accepted', fn: () => need(validateConfig(goodConfig, { configDir: '/x' }).ok, 'rejected') },
    { name: 'config: unknown key rejected', fn: () => need(!validateConfig({ ...goodConfig, surprise: 1 }, { configDir: '/x' }).ok, 'accepted') },
    { name: 'config: exception without reason rejected', fn: () => {
      const ex = [{ id: 'a', kind: 'prefix', value: '.x > .y' }];
      need(!validateConfig({ ...goodConfig, exceptions: ex }, { configDir: '/x' }).ok, 'accepted');
    } },
    { name: 'path policy: .. is rejected', fn: () => {
      try { resolveInside('/base', '../x'); } catch (e) { need(e instanceof PolicyError, 'wrong error'); return; }
      throw new Error('not rejected');
    } },
    { name: 'isMain: same realpath true, no argv false', fn: () => {
      const fake = { realpathSync: (s) => s };
      need(isMain(import.meta.url, new URL(import.meta.url).pathname, fake) === true, 'not main');
      need(isMain(import.meta.url, undefined, fake) === false, 'main without argv');
    } },
    { name: 'exit codes: FAIL 1, NOT_RUN 3, unknown 2', fn: () => need(exitFor('FAIL') === EXIT.FAIL && exitFor('NOT_RUN') === EXIT.NOT_RUN && exitFor('?') === EXIT.ERROR, 'codes') },
    { name: 'masker hides declared secrets', fn: () => need(createMasker({ secrets: ['topsecret-value'] }).mask('a topsecret-value b') === 'a *** b', 'not masked') },
    { name: 'builtin cases all agree', fn: () => need(runCases(builtinCases(), builtinConfig()).every((c) => c.pass), 'mismatch') },
  ];
}

// --- main ---

export async function main(argv, env, io) {
  const secrets = collectSecretsFromEnv(env, String(env.UI_VERIFY_SECRET_ENV || '').split(',').map((s) => s.trim()).filter(Boolean));
  const masker = createMasker({ secrets });
  setGuardMasker(masker);
  const sink = createSink({ masker, fs, root: io.cwd, stdout: io.stdout, stderr: io.stderr });
  try {
    const { values, positionals, errors } = parseArgs(argv, SPEC);
    if (values.selftest) {
      if (errors.length) throw new UsageError(errors.join('; '));
      return await runSelftest('css-scope', internalCases(), { stdout: io.stdout, stderr: io.stderr, masker });
    }
    if (errors.length) throw new UsageError(errors.join('; '));
    const command = positionals[0];
    if (command === undefined) throw new UsageError('missing command');
    if (!Object.prototype.hasOwnProperty.call(COMMANDS, command)) throw new UsageError(`unknown command ${command}`);
    if (positionals.length !== COMMANDS[command] + 1) throw new UsageError(`${command}: expected ${COMMANDS[command]} file argument(s), got ${positionals.length - 1}`);

    const ctx = { values, positionals, cwd: io.cwd, env, masker, sink, stdout: io.stdout, stderr: io.stderr, skillDir: skillDirOf(import.meta.url), configDir: null };
    const handlers = { check: cmdCheck, diff: cmdDiff, compile: cmdCompile, run: cmdRun, selftest: cmdSelftest };
    const result = await handlers[command](ctx);
    await sink.write('stdout', render(result, values.json === true));
    return result.exit;
  } catch (e) {
    if (e instanceof DepsError) {
      const ex = explainMissing(e);
      await sink.write('stderr', `${ex.lines.join('\n')}\n`);
      return ex.exit;
    }
    if (e instanceof UsageError) {
      await sink.write('stderr', `css-scope: ${e.message}\n${USAGE}\n`);
      return EXIT.ERROR;
    }
    if (e instanceof CssParseError) {
      const msg = /line \d+/.test(e.message) ? e.message : `${e.message} (line ${e.line})`;
      await sink.write('stderr', `css-scope: CSS parse error: ${msg}\n`);
      return EXIT.ERROR;
    }
    if (e instanceof PolicyError) {
      await sink.write('stderr', `css-scope: ${e.message}\n`);
      return EXIT.ERROR;
    }
    await sink.error(e);
    return EXIT.ERROR;
  }
}

if (isMain(import.meta.url, process.argv[1])) {
  installGuards(process);
  const code = await main(process.argv.slice(2), process.env, { cwd: process.cwd(), stdout: process.stdout, stderr: process.stderr });
  process.exitCode = code;
}
