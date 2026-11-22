/**
 * Purpose: Audit page accessibility with explicit verdicts: text contrast from rendered pixels, target size (WCAG 2.5.8), keyboard focus indicator, Tab order and console errors, with project exceptions kept in a file.
 * Usage: node a11y.mjs [--selftest] [--json] [--allow-origin <pattern>]... [--exceptions <file.json>] [--expect <expected.json>] [--expect-page <id>] [--now <YYYY-MM-DD>] --config <file.json> <outDir>
 * Inputs: JSON configuration (version 1: viewport, secretEnv, login { url, steps of fill/click } whose fill values come ONLY from environment variables (valueEnv), pages with id, url, assert { selector, text }, checks, waitFor,
 *   thresholds { normal, large, reason }, focus { minDiffPx, mouse { mode, selectors } }, tab { maxStops, positiveTabindex }, order { mode, tolerancePx }, exceptionsFile); optional exceptions file { version, exceptions, confirmations };
 *   optional expected.json for --expect; env UI_VERIFY_CACHE_DIR, UI_VERIFY_CHROME, UI_VERIFY_NO_SANDBOX, UI_VERIFY_SECRET_ENV.
 * Outputs: <outDir>/a11y.json (schema 1) and <outDir>/a11y.md; stdout carries the Markdown report (or the JSON with --json).
 * Exit codes: 0 PASS, 1 FAIL, 2 usage/configuration/internal error, 3 missing dependencies or Chrome, or UNVERIFIED (no assertion, no checks, unverifiable element, absent --expect element).
 * Dependencies: playwright-core 1.65.0, pngjs 7.0.0, pixelmatch 8.0.0 (from the skill cache, see lib/deps.mjs), a Chrome/Chromium browser; Node >= 20.11 (tested on 22). The pure logic and --selftest need none of them.
 * Limits: elements measured up to 5000 for contrast, 2000 targets, 500 Tab stops, 200 messages; text longer than 80 characters (messages 300) is cut; closed shadow DOM and cross-origin frames are not audited; whole-page screenshot up to 64000 px high and 40 million pixels (clips of 4000 px above 16000 px); navigation timeout 60 s, whole run 300 s (exit 2); one browser context per run.
 * Security: only localhost, files under the configuration directory and --allow-origin hosts are opened; input files and outDir stay under the configuration directory; text from the page is untrusted data, cleaned and masked for secretEnv and login valueEnv values.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs, runSelftest } from './lib/cli.mjs';
import { chromeMissing, findChrome } from './lib/chrome.mjs';
import { DepsError, explainMissing } from './lib/deps.mjs';
import { createMasker, createSink, collectSecretsFromEnv } from './lib/out.mjs';
import { launchChromium, makeWhich } from './lib/playwright-adapter.mjs';
import { PolicyError, resolveInside } from './lib/policy.mjs';
import { loadImaging, resolveCacheDir, skillDirOf } from './lib/runtime.mjs';
import { EXIT, installGuards } from './lib/verdict.mjs';
import { findingKey, isValidDate } from './lib/a11y-exceptions.mjs';
import { A11yConfigError, configDigest, loginEnvNames, missingLoginEnv, parseConfigText, validateA11yConfig } from './lib/a11y-config.mjs';
import { measureSample } from './lib/a11y-contrast.mjs';
import { applyExceptions, parseExceptionsFile } from './lib/a11y-exceptions.mjs';
import { ExpectError, compareExpected, parseExpected } from './lib/a11y-expect.mjs';
import { classifyKeyboard } from './lib/a11y-focus.mjs';
import { A11yLoginError } from './lib/a11y-login.mjs';
import { A11yPageError, classifyContrastRaw, createA11yRunner, planClips, redactUrlQueries } from './lib/a11y-page.mjs';
import { buildReport, renderMarkdown } from './lib/a11y-report.mjs';
import { analyzeTab } from './lib/a11y-tab.mjs';
import { evaluateTargets } from './lib/a11y-target.mjs';

const SPEC = {
  string: ['config', 'exceptions', 'expect', 'expect-page', 'now'],
  repeatable: ['allow-origin'],
  boolean: ['selftest', 'json'],
};
const USAGE = 'usage: node a11y.mjs [--selftest] [--json] [--allow-origin <pattern>]... [--exceptions <file.json>] [--expect <expected.json>] [--expect-page <id>] [--now <YYYY-MM-DD>] --config <file.json> <outDir>';

class UsageError extends Error {}
class RunTimeoutError extends Error {}

export const RUN_TIMEOUT_MS = 300000;

// Rejects with RunTimeoutError after `ms`; the browser is closed by the caller, which ends the pending work.
function withDeadline(promise, ms) {
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new RunTimeoutError(`run exceeded ${Math.round(ms / 1000)} s`)), ms);
  });
  promise.catch(() => {});
  return Promise.race([promise, deadline]).finally(() => clearTimeout(timer));
}

async function auditAll(runner, config) {
  await runner.login();
  const pages = [];
  for (const spec of config.pages) pages.push(await runner.auditPage(spec));
  return pages;
}

function selftestCases() {
  const check = (cond, msg) => { if (!cond) throw new Error(msg); };
  const white = { r: 255, g: 255, b: 255, a: 1 };
  const sample = (over) => ({ selector: '#x', fontSizePx: 16, fontWeight: 400, foreground: { r: 117, g: 117, b: 117, a: 1 }, opacity: 1, pixels: [[255, 255, 255]], ...over });
  const NOW = '2026-11-22';
  return [
    { name: 'contrast: 4.61 on white passes, 2.17 fails', fn: () => {
      const pass = measureSample(sample({}), undefined);
      check(pass.verdict === 'PASS' && Math.abs(pass.ratio - 4.61) < 0.01, `pass ${pass.verdict} ${pass.ratio}`);
      const fail = measureSample(sample({ foreground: { r: 176, g: 176, b: 176, a: 1 } }), undefined);
      check(fail.verdict === 'FAIL' && Math.abs(fail.ratio - 2.17) < 0.01, `fail ${fail.verdict} ${fail.ratio}`);
    } },
    { name: 'contrast: large text threshold, exempt and unverified precede measuring', fn: () => {
      const big = measureSample(sample({ foreground: { r: 138, g: 138, b: 138, a: 1 }, fontSizePx: 24 }), undefined);
      check(big.verdict === 'PASS' && big.required === 3, `large ${big.verdict}`);
      check(measureSample(sample({ exempt: 'inactive' }), undefined).verdict === 'EXEMPT', 'exempt');
      check(measureSample(sample({ unverified: 'occluded' }), undefined).verdict === 'UNVERIFIED', 'unverified');
    } },
    { name: 'target-size: small isolated target passes by spacing, neighbour at 2 px fails', fn: () => {
      const t = (selector, x, w) => ({ selector, box: { x, y: 0, width: w, height: w > 24 ? 40 : 16 } });
      const alone = evaluateTargets([t('#a', 0, 16)]);
      check(alone[0].verdict === 'PASS' && alone[0].exception === 'spacing', 'alone');
      const near = evaluateTargets([t('#a', 0, 16), t('#b', 18, 100)]);
      check(near[0].verdict === 'FAIL', 'near');
    } },
    { name: 'focus: no pixel difference fails the keyboard indicator', fn: () => {
      check(classifyKeyboard({ diffPx: 0 }).verdict === 'FAIL' && classifyKeyboard({ diffPx: 4 }).verdict === 'PASS', 'verdicts');
    } },
    { name: 'tab: positive tabindex fails and an unvisited target is unreachable', fn: () => {
      const step = (key, tabindex) => ({ key, selector: `#${key}`, tabindex, box: { x: 0, y: 0, width: 10, height: 10 } });
      const r = analyzeTab({ raw: [step('a', 3), step('b', null), step('a', 3)], candidates: [{ key: 'c', selector: '#c', tabindex: null }] });
      check(r.findings.some((f) => f.rule === 'tab-positive-tabindex') && r.findings.some((f) => f.rule === 'tab-unreachable'), JSON.stringify(r.findings));
    } },
    { name: 'exceptions: FAIL becomes EXCEPTED, an expired entry does not', fn: () => {
      const f = [{ rule: 'contrast', page: 'p', selector: '#x', key: 'contrast|p|#x', verdict: 'FAIL' }];
      const mk = (expires) => parseExceptionsFile({ version: 1, exceptions: [{ rule: 'contrast', selector: '#x', reason: 'logo', expires }] }, { now: NOW }).data;
      check(applyExceptions(f, mk('2027-01-31'), { now: NOW }).findings[0].verdict === 'EXCEPTED', 'valid');
      const late = applyExceptions(f, mk('2026-11-01'), { now: NOW });
      check(late.findings[0].verdict === 'FAIL' && late.warnings.some((w) => w.code === 'exception-expired'), 'expired');
    } },
    { name: 'report: UNVERIFIED never gives exit 0, a clean page with assertion does', fn: () => {
      const page = (findings, assertion = 'PASS') => ({ id: 'p', url: 'file:///x', assertion, checks: ['contrast'], findings });
      const ok = buildReport({ pages: [page([{ rule: 'contrast', verdict: 'PASS', selector: '#x' }])] });
      check(ok.exit === 0, `ok ${ok.exit}`);
      check(buildReport({ pages: [page([{ rule: 'contrast', verdict: 'PASS', selector: '#x' }], 'NONE')] }).exit === 3, 'no assertion');
      check(buildReport({ pages: [page([])] }).exit === 3, 'empty');
    } },
    { name: 'expect: matching element gives 0, missing element gives 3', fn: () => {
      const finding = { rule: 'contrast', selector: '#pass', verdict: 'PASS', required: 4.5, measured: { ratio: 4.61, fontSizePx: 16, foreground: { r: 117, g: 117, b: 117 }, effectiveBackground: white } };
      const exp = { elements: [{ selector: '#pass', fontSizePx: 16, ratio: 4.61, required: 4.5, verdict: 'PASS' }] };
      check(compareExpected(exp, [finding]).exit === 0, 'match');
      check(compareExpected(exp, []).exit === 3, 'missing');
    } },
    { name: 'adapter: page data is classified before measuring, tall pages are split, URL queries are redacted', fn: () => {
      const raw = (flags) => ({ rects: [[0, 0, 10, 10]], color: { r: 0, g: 0, b: 0, a: 1 }, flags });
      check(classifyContrastRaw(raw({ group: true })).unverified === 'opacity-group-with-paint', 'group');
      check(classifyContrastRaw({ ...raw({}), hidden: true }).exempt === 'not-rendered', 'hidden');
      check(classifyContrastRaw(raw({})).unverified === undefined, 'plain element is measurable');
      const tall = planClips({ width: 1000, height: 20000 });
      check(tall.ok && tall.chunked && tall.clips.length === 5 && planClips({ width: 1000, height: 70000 }).ok === false, 'plan');
      check(!redactUrlQueries('GET https://x.test/a?token=abc123 failed').includes('abc123'), 'redact');
    } },
    { name: 'config: unknown key and a literal fill value are errors that do not quote the input', fn: () => {
      const r = validateA11yConfig({ version: 1, bogus: 1, pages: [{ id: 'p', url: 'a.html' }], login: { url: 'a.html', steps: [{ fill: { selector: '#u', value: 'topsecretvalue' } }] } }, { configDir: '/tmp/cfg' });
      check(!r.ok && r.errors.some((e) => e.includes('bogus')), 'unknown key');
      check(!r.errors.join('\n').includes('topsecretvalue'), 'error quotes the value');
    } },
  ];
}

function readInside(root, rel, label) {
  let abs;
  try {
    abs = resolveInside(root, rel);
  } catch (e) {
    if (e instanceof PolicyError) throw new UsageError(`${label} "${rel}" is outside the configuration directory`);
    throw e;
  }
  try {
    return { abs, text: fs.readFileSync(abs, 'utf8') };
  } catch (e) {
    throw new UsageError(`cannot read ${label}: ${e.code || 'error'}`);
  }
}

function todayUtc(clock = new Date()) {
  return clock.toISOString().slice(0, 10);
}

// Page results (with raw findings) to the final report: exceptions and confirmations are applied per finding key,
// then, with an expected file, the exit code and result come from the comparison on the chosen page.
export function assembleReport({ pages, config, exceptionsData, now, digest, masker, nowOverridden = null, expected = null, expectPage }) {
  const flat = pages.flatMap((p) => p.findings.map((f) => ({ ...f, page: p.id, key: findingKey({ rule: f.rule, page: p.id, selector: f.selector, hashBasis: f.hashBasis, message: f.message }) })));
  const applied = applyExceptions(flat, exceptionsData, { now });
  let at = 0;
  const settled = pages.map((p) => ({ ...p, findings: applied.findings.slice(at, (at += p.findings.length)) }));
  const common = {
    configDigest: digest,
    pages: settled,
    exceptions: applied.exceptions,
    confirmations: applied.confirmations,
    warnings: [...config.warnings, ...pages.flatMap((p) => p.warnings || []), ...applied.warnings],
    masker,
    nowOverridden,
  };
  const report = buildReport(common);
  if (expected === null) return report;
  const target = report.pages.find((p) => p.id === (expectPage ?? config.pages[0].id));
  const expectResult = compareExpected(expected, target ? target.findings : [], { thresholds: config.thresholds });
  return buildReport({ ...common, mode: 'expect', expectResult });
}

export async function main(argv, env, io) {
  const envNames = String(env.UI_VERIFY_SECRET_ENV || '').split(',').map((s) => s.trim()).filter(Boolean);
  let masker = createMasker({ secrets: collectSecretsFromEnv(env, envNames) });
  let sink = createSink({ masker, fs, root: io.cwd, stdout: io.stdout, stderr: io.stderr });
  let browser = null;
  let runner = null;
  try {
    const { values, positionals, errors } = parseArgs(argv, SPEC);
    if (values.selftest) {
      if (errors.length) throw new UsageError(errors.join('; '));
      return await runSelftest('a11y', selftestCases(), { stdout: io.stdout, stderr: io.stderr, masker });
    }
    if (errors.length) throw new UsageError(errors.join('; '));
    if (values.config === undefined) throw new UsageError('--config <file.json> is required');
    if (positionals.length !== 1) throw new UsageError(`expected exactly one outDir, got ${positionals.length}`);
    const now = values.now === undefined ? todayUtc() : values.now;
    if (!isValidDate(now)) throw new UsageError('bad --now (expected a calendar date YYYY-MM-DD)');

    const configPath = path.resolve(io.cwd, values.config);
    const configDir = path.dirname(configPath);
    let rawText;
    try {
      rawText = fs.readFileSync(configPath, 'utf8');
    } catch (e) {
      throw new UsageError(`cannot read configuration: ${e.code || 'error'}`);
    }
    const raw = parseConfigText(rawText);
    const declared = [...(raw && Array.isArray(raw.secretEnv) ? raw.secretEnv.filter((n) => typeof n === 'string') : []), ...loginEnvNames(raw)];
    masker = createMasker({ secrets: collectSecretsFromEnv(env, [...envNames, ...declared]) });
    sink = createSink({ masker, fs, root: io.cwd, stdout: io.stdout, stderr: io.stderr });

    const v = validateA11yConfig(raw, { configDir, allowOrigins: values['allow-origin'] || [] });
    if (!v.ok) throw new UsageError(`invalid configuration:\n  ${v.errors.join('\n  ')}`);
    try {
      resolveInside(configDir, positionals[0]);
    } catch (e) {
      if (e instanceof PolicyError) throw new UsageError(`outDir "${positionals[0]}" is outside the configuration directory`);
      throw e;
    }
    const missing = missingLoginEnv(v.config, env);
    if (missing.length) throw new UsageError(`invalid configuration: environment variable(s) required by login are not set: ${missing.join(', ')}`);
    const exceptionsRel = values.exceptions !== undefined ? values.exceptions : (v.config.exceptionsFile ? path.relative(configDir, v.config.exceptionsFile) : null);
    let exceptionsData = { exceptions: [], confirmations: [] };
    if (exceptionsRel !== null) {
      const file = readInside(configDir, exceptionsRel, 'exceptions file');
      let obj;
      try {
        obj = JSON.parse(file.text);
      } catch {
        throw new UsageError('exceptions file is not valid JSON');
      }
      const ex = parseExceptionsFile(obj, { now });
      if (!ex.ok) throw new UsageError(`invalid exceptions file:\n  ${ex.errors.join('\n  ')}`);
      exceptionsData = ex.data;
    }
    let expected = null;
    if (values.expect !== undefined) {
      const file = readInside(configDir, values.expect, 'expected file');
      try {
        expected = JSON.parse(file.text);
        parseExpected(expected);
      } catch (e) {
        throw new UsageError(e instanceof ExpectError ? e.message : 'expected file is not valid JSON');
      }
      if (values['expect-page'] !== undefined && !v.config.pages.some((p) => p.id === values['expect-page'])) throw new UsageError('--expect-page does not name a page of the configuration');
    }
    const digest = configDigest(v.config, configDir);

    const skillDir = skillDirOf(import.meta.url);
    const cacheDir = resolveCacheDir({ skillDir, env, platform: process.platform, homedir: os.homedir() });
    const imaging = await loadImaging({ cacheDir, env });
    const found = findChrome({ platform: process.platform, env, fs, which: makeWhich({ env, fs, platform: process.platform }) });
    if (!found.path) {
      await sink.write('stderr', `${chromeMissing(found).lines.join('\n')}\n`);
      return EXIT.NOT_RUN;
    }
    browser = await launchChromium({ cacheDir, chromePath: found.path, env, fs });
    runner = createA11yRunner({ browser, imaging, config: v.config, configDir, allowOrigins: values['allow-origin'] || [], env });
    const pages = await withDeadline(auditAll(runner, v.config), io.runTimeoutMs ?? RUN_TIMEOUT_MS);

    const report = assembleReport({ pages, config: v.config, exceptionsData, now, digest, masker, nowOverridden: values.now !== undefined ? now : null, expected, expectPage: values['expect-page'] });
    const outRel = path.relative(configDir, resolveInside(configDir, positionals[0]));
    sink = createSink({ masker, fs, root: configDir, stdout: io.stdout, stderr: io.stderr });
    await sink.writeFile(path.join(outRel, 'a11y.json'), `${JSON.stringify(report, null, 2)}\n`);
    const markdown = renderMarkdown(report);
    await sink.writeFile(path.join(outRel, 'a11y.md'), markdown);
    await sink.write('stdout', values.json ? `${JSON.stringify(report)}\n` : markdown);
    return report.exit;
  } catch (e) {
    if (e instanceof DepsError) {
      await sink.write('stderr', `${explainMissing(e).lines.join('\n')}\n`);
      return EXIT.NOT_RUN;
    }
    if (e instanceof UsageError || e instanceof PolicyError) {
      await sink.write('stderr', `a11y: ${e.message}\n${USAGE}\n`);
      return EXIT.ERROR;
    }
    if (e instanceof A11yPageError || e instanceof A11yLoginError || e instanceof RunTimeoutError) {
      await sink.write('stderr', `a11y: ${e.message}\n`);
      return EXIT.ERROR;
    }
    if (e instanceof A11yConfigError) {
      await sink.write('stderr', `a11y: ${e.message}\n`);
      return EXIT.ERROR;
    }
    await sink.error(e);
    return EXIT.ERROR;
  } finally {
    if (runner) {
      try {
        await runner.close();
      } catch { /* the context may already be gone */ }
    }
    if (browser) {
      try {
        await browser.close();
      } catch { /* the browser may already be gone */ }
    }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  installGuards(process, { onError: (err) => process.stderr.write(`a11y: ${err && err.message ? err.message : err}\n`) });
  const code = await main(process.argv.slice(2), process.env, { cwd: process.cwd(), stdout: process.stdout, stderr: process.stderr });
  process.exitCode = code;
}
