/**
 * Purpose: Capture screenshots of a matrix of pages x widths x color modes with masks for volatile elements and a positive assertion per page; prints PASS, FAIL or UNVERIFIED per screenshot.
 * Usage: node capture.mjs [--selftest] [--json] [--allow-origin <pattern>]... [--allow-trace] --config <file.json> <outDir>
 * Inputs: JSON configuration (version 1: viewport, widths 200..4000, modes light|dark, secretEnv, optional login { url, steps of fill/click } whose fill values come ONLY from environment variables (valueEnv),
 *   pages with id, url, waitFor, assert { selector, text }, masks (selector or rectangle) and fullPage); url is http(s) or a path relative to the configuration directory;
 *   outDir is relative to the configuration directory; env UI_VERIFY_CACHE_DIR, UI_VERIFY_CHROME, UI_VERIFY_NO_SANDBOX, UI_VERIFY_SECRET_ENV.
 * Outputs: <outDir>/<id>__<width>-<mode>.png per combination, <outDir>/manifest.json (name, pageId, width, mode, assertion PASS|FAIL|NONE), <outDir>/capture.txt;
 *   with --allow-trace also <outDir>/trace.zip (and <outDir>/storageState.json when a login is configured); stdout carries the text report (or the JSON with --json).
 * Exit codes: 0 PASS (every page asserted), 1 FAIL (asserted element missing), 2 usage/configuration/adapter/internal error, 3 missing dependencies or Chrome, or UNVERIFIED (page without a positive assertion).
 * Dependencies: playwright-core 1.65.0, pngjs 7.0.0, pixelmatch 8.0.0 (from the skill cache, see lib/deps.mjs), a Chrome/Chromium browser; Node >= 20.11 (tested on 22).
 * Limits: one browser context per run, whole images in memory; selector masks paint the bounding box of every match measured just before the screenshot; animations are disabled and reduced motion is requested; a page without assert never gives PASS.
 * Security: only localhost, files under the configuration directory and --allow-origin hosts are opened; writes only under outDir inside the configuration directory; output is masked for secretEnv and login valueEnv values;
 *   trace and storageState (they can hold credentials) are written only with --allow-trace, with a warning to keep outDir out of version control (.gitignore).
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { parseArgs, runSelftest } from './lib/cli.mjs';
import { CaptureError, formatCaptureReport, loginEnvNames, manifest, missingLoginEnv, runCapture, shotName, toJson, validateCaptureConfig } from './lib/capture-core.mjs';
import { chromeMissing, findChrome } from './lib/chrome.mjs';
import { DepsError, explainMissing } from './lib/deps.mjs';
import { createMasker, createSink, collectSecretsFromEnv, setGuardMasker } from './lib/out.mjs';
import { createCaptureAdapter, launchChromium, makeWhich } from './lib/playwright-adapter.mjs';
import { PolicyError, resolveInside } from './lib/policy.mjs';
import { loadImaging, resolveCacheDir, skillDirOf } from './lib/runtime.mjs';
import { EXIT, installGuards, isMain } from './lib/verdict.mjs';

const SPEC = {
  string: ['config'],
  repeatable: ['allow-origin'],
  boolean: ['selftest', 'json', 'allow-trace'],
};
const USAGE = 'usage: node capture.mjs [--selftest] [--json] [--allow-origin <pattern>]... [--allow-trace] --config <file.json> <outDir>';

class UsageError extends Error {}

// --- selftest: atrapa adaptera (bez przegladarki, PNG i cache zaleznosci) ---

function selftestCases() {
  const configDir = path.join(path.sep, 'tmp', 'uv-capture-selftest');
  const check = (cond, msg) => { if (!cond) throw new Error(msg); };
  const page = (over = {}) => ({ id: 'home', url: 'http://localhost/home', assert: { selector: 'h1' }, ...over });
  const mkConfig = (top = {}, pages = [page()]) => {
    const v = validateCaptureConfig({ version: 1, pages, ...top }, { configDir, allowOrigins: [] });
    check(v.ok, `config: ${v.errors}`);
    return v.config;
  };
  const adapter = (found = true) => ({ async capture() { return { png: Buffer.from([137, 80, 78, 71]), assertionFound: found }; } });
  const run = (config, found) => runCapture({ config, adapter: adapter(found), env: {} });

  return [
    { name: 'asserted page PASS, exit 0', fn: async () => {
      const r = await run(mkConfig(), true);
      check(r.verdict === 'PASS' && r.exit === 0 && r.shots[0].assertion === 'PASS', `got ${r.verdict}`);
    } },
    { name: 'page without assert is UNVERIFIED (exit 3), never PASS', fn: async () => {
      const r = await run(mkConfig({}, [page({ assert: undefined })]), true);
      check(r.shots[0].assertion === 'NONE' && r.verdict === 'UNVERIFIED' && r.exit === 3, `got ${r.verdict}/${r.exit}`);
    } },
    { name: 'asserted element missing is FAIL (exit 1)', fn: async () => {
      const r = await run(mkConfig(), false);
      check(r.shots[0].assertion === 'FAIL' && r.verdict === 'FAIL' && r.exit === 1, `got ${r.verdict}/${r.exit}`);
    } },
    { name: 'screenshot names cover widths x modes', fn: async () => {
      check(shotName('home', 375, 'dark') === 'home__375-dark.png', 'shotName');
      const r = await run(mkConfig({ widths: [375, 1280], modes: ['light', 'dark'] }), true);
      check(r.shots.length === 4 && new Set(r.shots.map((s) => s.name)).size === 4, `got ${r.shots.length}`);
    } },
    { name: 'literal fill value is a validation error without echoing it', fn: async () => {
      const literal = ['fake', 'literal', String(process.pid), 'wxyz'].join('-');
      const v = validateCaptureConfig({ version: 1, login: { url: 'http://localhost/', steps: [{ fill: { selector: '#u', value: literal } }] }, pages: [page()] }, { configDir, allowOrigins: [] });
      check(!v.ok && v.errors.some((e) => /value/.test(e)) && !v.errors.join(' ').includes(literal), 'literal value accepted or echoed');
    } },
    { name: 'disallowed origin in the configuration is a validation error', fn: async () => {
      const cfg = { version: 1, pages: [page({ url: 'https://app.example.test/' })] };
      const bad = validateCaptureConfig(cfg, { configDir, allowOrigins: [] });
      const good = validateCaptureConfig(cfg, { configDir, allowOrigins: ['app.example.test'] });
      check(!bad.ok && good.ok, `bad.ok=${bad.ok} good.ok=${good.ok}`);
    } },
    { name: 'secret composed at run time is masked in report and JSON', fn: async () => {
      const secret = ['fake', 'secret', String(process.pid), 'wxyz'].join('-');
      const r = await run(mkConfig({}, [page({ id: `p-${secret}` })]), true);
      const masker = createMasker({ secrets: [secret] });
      const text = formatCaptureReport(r, masker);
      const json = JSON.stringify(toJson(r, masker));
      check(!text.includes(secret) && !json.includes(secret), 'secret leaked');
      check(formatCaptureReport(r, createMasker({})).includes(secret), 'control: unmasked report lacks the value');
    } },
    { name: 'manifest carries no PNG bytes', fn: async () => {
      const m = manifest(await run(mkConfig(), true));
      check(m.version === 1 && m.shots.length === 1 && !JSON.stringify(m).includes('"png"'), 'bad manifest');
    } },
  ];
}

// --- konfiguracja i wynik ---

function readConfig(configPath) {
  let text;
  try {
    text = fs.readFileSync(configPath, 'utf8');
  } catch (e) {
    throw new UsageError(`cannot read configuration ${configPath}: ${e.code || e.message}`);
  }
  try {
    return JSON.parse(text);
  } catch (e) {
    throw new UsageError(`configuration ${configPath} is not valid JSON: ${e.message}`);
  }
}

export async function main(argv, env, io) {
  const envNames = String(env.UI_VERIFY_SECRET_ENV || '').split(',').map((s) => s.trim()).filter(Boolean);
  let masker = createMasker({ secrets: collectSecretsFromEnv(env, envNames) });
  setGuardMasker(masker);
  let sink = createSink({ masker, fs, root: io.cwd, stdout: io.stdout, stderr: io.stderr });
  let browser = null;
  let adapter = null;
  try {
    const { values, positionals, errors } = parseArgs(argv, SPEC);
    const skillDir = skillDirOf(import.meta.url);

    if (values.selftest) {
      if (errors.length) throw new UsageError(errors.join('; '));
      return await runSelftest('capture', selftestCases(), { stdout: io.stdout, stderr: io.stderr, masker });
    }

    if (errors.length) throw new UsageError(errors.join('; '));
    if (values.config === undefined) throw new UsageError('--config <file.json> is required');
    if (positionals.length !== 1) throw new UsageError(`expected exactly one outDir, got ${positionals.length}`);

    const configPath = path.resolve(io.cwd, values.config);
    const configDir = path.dirname(configPath);
    const raw = readConfig(configPath);
    const declared = [...(raw && Array.isArray(raw.secretEnv) ? raw.secretEnv.filter((n) => typeof n === 'string') : []), ...loginEnvNames(raw)];
    masker = createMasker({ secrets: collectSecretsFromEnv(env, [...envNames, ...declared]) });
    setGuardMasker(masker);
    sink = createSink({ masker, fs, root: io.cwd, stdout: io.stdout, stderr: io.stderr });

    const allowOrigins = values['allow-origin'] || [];
    const v = validateCaptureConfig(raw, { configDir, allowOrigins, env });
    if (!v.ok) throw new UsageError(`invalid configuration:\n  ${v.errors.join('\n  ')}`);
    const config = v.config;
    const missing = missingLoginEnv(config, env);
    if (missing.length) throw new UsageError(`invalid configuration: environment variable(s) required by login are not set: ${missing.join(', ')}`);

    let outDir;
    try {
      outDir = resolveInside(configDir, positionals[0]);
    } catch (e) {
      if (e instanceof PolicyError) throw new UsageError(`outDir "${positionals[0]}" is outside the configuration directory`);
      throw e;
    }
    const outRel = path.relative(configDir, outDir);
    sink = createSink({ masker, fs, root: configDir, stdout: io.stdout, stderr: io.stderr });

    const cacheDir = resolveCacheDir({ skillDir, env, platform: process.platform, homedir: os.homedir() });
    const { PNG } = await loadImaging({ cacheDir, env });
    const found = findChrome({ platform: process.platform, env, fs, which: makeWhich({ env, fs, platform: process.platform }) });
    if (!found.path) {
      await sink.write('stderr', `${chromeMissing(found).lines.join('\n')}\n`);
      return EXIT.NOT_RUN;
    }
    browser = await launchChromium({ cacheDir, chromePath: found.path, env, fs });

    const allowTrace = values['allow-trace'] === true;
    adapter = createCaptureAdapter({ browser, PNG, config, configDir, allowOrigins, allowTrace });
    const result = await runCapture({ config, adapter, env });

    for (const s of result.shots) await sink.writeFile(path.join(outRel, shotName(s.pageId, s.width, s.mode)), Buffer.from(s.png));
    await sink.writeFile(path.join(outRel, 'manifest.json'), `${JSON.stringify(manifest(result), null, 2)}\n`);
    const report = formatCaptureReport(result, masker);
    await sink.writeFile(path.join(outRel, 'capture.txt'), report);

    if (allowTrace) {
      const art = await adapter.finish();
      const written = [];
      if (art.trace) {
        await sink.writeFile(path.join(outRel, 'trace.zip'), art.trace);
        written.push('trace.zip');
      }
      if (art.storageState) {
        await sink.writeFile(path.join(outRel, 'storageState.json'), art.storageState);
        written.push('storageState.json');
      }
      if (written.length) {
        await sink.write('stderr', `capture: warning: --allow-trace wrote ${written.join(' and ')} to ${outRel || '.'}; they can contain credentials and page data. Keep the output directory out of version control (add it to .gitignore).\n`);
      }
    }

    if (values.json) await sink.write('stdout', `${JSON.stringify(toJson(result, masker))}\n`);
    else await sink.write('stdout', report);
    return result.exit;
  } catch (e) {
    if (e instanceof DepsError) {
      const ex = explainMissing(e);
      await sink.write('stderr', `${ex.lines.join('\n')}\n`);
      return ex.exit;
    }
    if (e instanceof UsageError || e instanceof PolicyError) {
      await sink.write('stderr', `capture: ${e.message}\n${USAGE}\n`);
      return EXIT.ERROR;
    }
    if (e instanceof CaptureError) {
      await sink.write('stderr', `capture: ${e.message}\n`);
      return EXIT.ERROR;
    }
    await sink.error(e);
    return EXIT.ERROR;
  } finally {
    if (adapter) {
      try {
        await adapter.close();
      } catch { /* kontekst mogl juz zniknac */ }
    }
    if (browser) {
      try {
        await browser.close();
      } catch { /* przegladarka mogla juz zniknac */ }
    }
  }
}

if (isMain(import.meta.url, process.argv[1])) {
  installGuards(process);
  const code = await main(process.argv.slice(2), process.env, { cwd: process.cwd(), stdout: process.stdout, stderr: process.stderr });
  process.exitCode = code;
}
