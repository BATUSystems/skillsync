/**
 * Purpose: Measure element boxes and computed styles of reference/candidate page pairs and check the rendered shape (pixel diff of the element screenshot); prints PASS, FAIL or UNVERIFIED per pair.
 * Usage: node measure.mjs [--selftest] [--json] [--allow-origin <pattern>]... [--allow-trace] --config <file.json> <outDir>
 * Inputs: JSON configuration (version 1: viewport, tolerancePx, sizeTolerancePx, colorTolerance, maxPct, shiftPx, shapeAlign, styleProps, secretEnv, pairs with reference/candidate url+selector, deviations with a mandatory reason, masks/masksReference/masksCandidate, acceptShape with a reason);
 *   url is http(s) or a path relative to the configuration directory; outDir is relative to the configuration directory; env UI_VERIFY_CACHE_DIR, UI_VERIFY_CHROME, UI_VERIFY_NO_SANDBOX, UI_VERIFY_SECRET_ENV.
 * Outputs: <outDir>/measure.json, <outDir>/measure.txt and <outDir>/<id>.diff.png for pairs whose shape FAILs; stdout carries the text report (or the JSON with --json).
 * Exit codes: 0 PASS, 1 FAIL (box, style or shape difference), 2 usage/configuration/adapter/internal error, 3 missing dependencies or Chrome, or UNVERIFIED (element not found, shape skipped, page-state-mismatch).
 * Dependencies: playwright-core 1.65.0, pngjs 7.0.0, pixelmatch 8.0.0 (from the skill cache, see lib/deps.mjs), a Chrome/Chromium browser; Node >= 20.11 (tested on 22).
 * Limits: one element per side (first match), whole images in memory (a PNG whose header declares over 16384 px per side or 50 M px, or whose file is over 64 MiB, is refused with exit 2 before decoding (so is an interlaced PNG, a non-standard or repeated IHDR, and a file over 64 MiB is not even read)); shape is checked only when box sizes agree within sizeTolerancePx; --allow-trace is accepted but measure stores no trace.
 * Security: only localhost, files under the configuration directory and --allow-origin hosts are opened; writes only under outDir inside the configuration directory; output is masked for listed secrets.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { parseArgs, runSelftest } from './lib/cli.mjs';
import { chromeMissing, findChrome } from './lib/chrome.mjs';
import { DepsError, explainMissing } from './lib/deps.mjs';
import { formatReport, MeasureError, runMeasure, toJson, validateConfig } from './lib/measure-core.mjs';
import { createMasker, createSink, collectSecretsFromEnv, setGuardMasker } from './lib/out.mjs';
import { createPlaywrightAdapter, launchChromium, makeWhich } from './lib/playwright-adapter.mjs';
import { cropToCommon, diffImages } from './lib/pixels.mjs';
import { PolicyError, resolveInside } from './lib/policy.mjs';
import { decodePng, encodePng, loadImaging, makePixelmatchFlagger, resolveCacheDir, skillDirOf } from './lib/runtime.mjs';
import { EXIT, installGuards, isMain } from './lib/verdict.mjs';

const SPEC = {
  string: ['config'],
  repeatable: ['allow-origin'],
  boolean: ['selftest', 'json', 'allow-trace'],
};
const USAGE = 'usage: node measure.mjs [--selftest] [--json] [--allow-origin <pattern>]... [--allow-trace] --config <file.json> <outDir>';

class UsageError extends Error {}

function solid(w, h, fn) {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) data.set(fn(x, y), (y * w + x) * 4);
  return { width: w, height: h, data };
}

function diffPng(a, flags) {
  const data = new Uint8ClampedArray(a.width * a.height * 4);
  for (let i = 0; i < flags.length; i++) {
    const o = i * 4;
    if (flags[i]) {
      data.set([255, 0, 0, 255], o);
    } else {
      for (let c = 0; c < 3; c++) data[o + c] = Math.round(a.data[o + c] * 0.25 + 255 * 0.75);
      data[o + 3] = 255;
    }
  }
  return { width: a.width, height: a.height, data };
}

// --- selftest: atrapa adaptera i syntetyczne obrazy (bez PNG, przegladarki i cache zaleznosci) ---

function selftestCases() {
  const WHITE = [255, 255, 255, 255];
  const BLACK = [0, 0, 0, 255];
  const block = (x0, w = 40, h = 40) => solid(w, h, (x, y) => (x >= x0 && x < x0 + 12 && y >= 4 && y < h - 4 ? BLACK : WHITE));
  const STYLES = { color: 'rgb(0, 0, 0)', backgroundColor: 'rgb(255, 255, 255)', borderTopColor: 'rgb(0, 0, 0)', borderTopLeftRadius: '4px', fontSize: '12px', fontFamily: 'Arial' };
  const side = (over = {}) => ({ found: true, box: { x: 10, y: 10, width: 40, height: 40 }, styles: { ...STYLES }, image: block(4), ...over });
  const missing = () => side({ found: false, box: null, styles: {}, image: null });
  const configDir = path.join(path.sep, 'tmp', 'uv-measure-selftest');
  const check = (cond, msg) => { if (!cond) throw new Error(msg); };
  const pairCfg = (over = {}) => ({ id: 'p', reference: { url: 'http://localhost/ref', selector: '#t' }, candidate: { url: 'http://localhost/cand', selector: '#t' }, ...over });
  const mkConfig = (over = {}, pairOver = {}) => {
    const v = validateConfig({ version: 1, pairs: [pairCfg(pairOver)], ...over }, { configDir, allowOrigins: [] });
    check(v.ok, `config: ${v.errors}`);
    return v.config;
  };
  const adapterFor = (ref, cand) => {
    const shots = new Map();
    return {
      async measure(s) {
        const d = s.url.endsWith('/ref') ? ref : cand;
        shots.set(s, d.image);
        return { found: d.found, box: d.box, styles: d.styles };
      },
      async screenshot(s) { return shots.get(s); },
    };
  };
  const run = (ref, cand, over, pairOver) => runMeasure({ config: mkConfig(over, pairOver), adapter: adapterFor(ref, cand), diffFn: (a, b, o) => diffImages(a, b, o) });

  return [
    { name: 'identical pair PASS', fn: async () => {
      const r = await run(side(), side());
      check(r.verdict === 'PASS' && r.exit === 0, `got ${r.verdict}`);
    } },
    { name: 'box shift within tolerancePx PASS, above FAIL', fn: async () => {
      const ok = await run(side(), side({ box: { x: 11, y: 10, width: 40, height: 40 } }));
      const bad = await run(side(), side({ box: { x: 14, y: 10, width: 40, height: 40 } }));
      check(ok.verdict === 'PASS', `1 px: ${ok.verdict}`);
      check(bad.verdict === 'FAIL' && bad.exit === 1, `4 px: ${bad.verdict}`);
    } },
    { name: 'deviation with a reason approves, without a reason is a validation error', fn: async () => {
      const cand = side({ styles: { ...STYLES, fontSize: '14px' } });
      const r = await run(side(), cand, {}, { deviations: [{ prop: 'fontSize', reason: 'approved by design' }] });
      check(r.verdict === 'PASS' && r.pairs[0].measure.approved.length === 1, `got ${r.verdict}`);
      const v = validateConfig({ version: 1, pairs: [pairCfg({ deviations: [{ prop: 'fontSize' }] })] }, { configDir, allowOrigins: [] });
      check(!v.ok && v.errors.some((e) => /reason/.test(e)), 'missing reason accepted');
    } },
    { name: 'shape FAIL for identical boxes and styles (mirrored image)', fn: async () => {
      const r = await run(side({ image: block(4) }), side({ image: block(24) }));
      const p = r.pairs[0];
      check(p.measure.verdict === 'PASS' && p.shape.verdict === 'FAIL' && r.verdict === 'FAIL' && r.exit === 1, `got ${p.measure.verdict}/${p.shape.verdict}/${r.verdict}`);
    } },
    { name: 'cropped true when boxes differ within sizeTolerancePx', fn: async () => {
      const r = await run(side({ image: block(4, 40, 40) }), side({ box: { x: 10, y: 10, width: 41, height: 40 }, image: block(4, 41, 40) }));
      check(r.pairs[0].shape.cropped === true && r.verdict === 'PASS', `got ${r.verdict} cropped=${r.pairs[0].shape.cropped}`);
    } },
    { name: 'box mismatch skips the shape check', fn: async () => {
      const r = await run(side(), side({ box: { x: 10, y: 10, width: 60, height: 40 }, image: block(4, 60, 40) }));
      const s = r.pairs[0].shape;
      check(s.verdict === 'SKIPPED' && s.reason === 'box-mismatch' && r.verdict === 'FAIL', `got ${s.verdict}/${s.reason}/${r.verdict}`);
    } },
    { name: 'missing element is UNVERIFIED with exit 3', fn: async () => {
      const r = await run(side(), missing());
      check(r.pairs[0].verdict === 'UNVERIFIED' && r.verdict === 'UNVERIFIED' && r.exit === 3, `got ${r.verdict}/${r.exit}`);
    } },
    { name: 'secret from secretEnv is masked in report and JSON', fn: async () => {
      const secret = ['fake', 'secret', String(process.pid), 'wxyz'].join('-');
      const r = await run(side(), side({ styles: { ...STYLES, fontFamily: `Font ${secret}` } }), {}, { id: `id-${secret}` });
      const masker = createMasker({ secrets: [secret] });
      const text = formatReport(r, masker);
      const json = JSON.stringify(toJson(r, masker));
      check(!text.includes(secret) && !json.includes(secret), 'secret leaked');
      check(formatReport(r, createMasker({})).includes(secret), 'control: unmasked report lacks the value');
    } },
    { name: 'disallowed origin in the configuration is a validation error', fn: async () => {
      const bad = validateConfig({ version: 1, pairs: [pairCfg({ reference: { url: 'https://app.example.test/', selector: '#t' } })] }, { configDir, allowOrigins: [] });
      const good = validateConfig({ version: 1, pairs: [pairCfg({ reference: { url: 'https://app.example.test/', selector: '#t' } })] }, { configDir, allowOrigins: ['app.example.test'] });
      check(!bad.ok && good.ok, `bad.ok=${bad.ok} good.ok=${good.ok}`);
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

async function writeOutputs({ result, masker, sink, outRel, PNG, config }) {
  const json = `${JSON.stringify(toJson(result, masker), null, 2)}\n`;
  await sink.writeFile(path.join(outRel, 'measure.json'), json);
  await sink.writeFile(path.join(outRel, 'measure.txt'), formatReport(result, masker));
  for (const p of result.pairs) {
    if (p.shape.verdict !== 'FAIL' || !p.diff || !p.diff.flags) continue;
    const { a } = cropToCommon(p.diff.reference, p.diff.candidate, { align: config.shapeAlign });
    await sink.writeFile(path.join(outRel, `${p.id}.diff.png`), encodePng(PNG, diffPng(a, p.diff.flags)));
  }
}

export async function main(argv, env, io) {
  const envNames = String(env.UI_VERIFY_SECRET_ENV || '').split(',').map((s) => s.trim()).filter(Boolean);
  let masker = createMasker({ secrets: collectSecretsFromEnv(env, envNames) });
  setGuardMasker(masker);
  let sink = createSink({ masker, fs, root: io.cwd, stdout: io.stdout, stderr: io.stderr });
  let browser = null;
  try {
    const { values, positionals, errors } = parseArgs(argv, SPEC);
    const skillDir = skillDirOf(import.meta.url);

    if (values.selftest) {
      if (errors.length) throw new UsageError(errors.join('; '));
      return await runSelftest('measure', selftestCases(), { stdout: io.stdout, stderr: io.stderr, masker });
    }

    if (errors.length) throw new UsageError(errors.join('; '));
    if (values.config === undefined) throw new UsageError('--config <file.json> is required');
    if (positionals.length !== 1) throw new UsageError(`expected exactly one outDir, got ${positionals.length}`);

    const configPath = path.resolve(io.cwd, values.config);
    const configDir = path.dirname(configPath);
    const raw = readConfig(configPath);
    if (raw && Array.isArray(raw.secretEnv)) {
      const names = raw.secretEnv.filter((n) => typeof n === 'string');
      masker = createMasker({ secrets: collectSecretsFromEnv(env, [...envNames, ...names]) });
      setGuardMasker(masker);
      sink = createSink({ masker, fs, root: io.cwd, stdout: io.stdout, stderr: io.stderr });
    }
    const allowOrigins = values['allow-origin'] || [];
    const v = validateConfig(raw, { configDir, allowOrigins });
    if (!v.ok) throw new UsageError(`invalid configuration:\n  ${v.errors.join('\n  ')}`);
    const config = v.config;
    const outDir = resolveInside(configDir, positionals[0]);
    const outRel = path.relative(configDir, outDir);
    sink = createSink({ masker, fs, root: configDir, stdout: io.stdout, stderr: io.stderr });

    const cacheDir = resolveCacheDir({ skillDir, env, platform: process.platform, homedir: os.homedir() });
    const { PNG, pixelmatch } = await loadImaging({ cacheDir, env });
    const found = findChrome({ platform: process.platform, env, fs, which: makeWhich({ env, fs, platform: process.platform }) });
    if (!found.path) {
      await sink.write('stderr', `${chromeMissing(found).lines.join('\n')}\n`);
      return EXIT.NOT_RUN;
    }
    browser = await launchChromium({ cacheDir, chromePath: found.path, env, fs });

    const adapter = createPlaywrightAdapter({ browser, PNG, config, configDir, allowOrigins });
    const flagger = makePixelmatchFlagger(pixelmatch, { threshold: 0.1, includeAA: false });
    const result = await runMeasure({ config, adapter, diffFn: (a, b, o) => diffImages(a, b, { ...o, flagger }) });

    await writeOutputs({ result, masker, sink, outRel, PNG, config });
    if (values.json) await sink.write('stdout', `${JSON.stringify(toJson(result, masker))}\n`);
    else await sink.write('stdout', formatReport(result, masker));
    return result.exit;
  } catch (e) {
    if (e instanceof DepsError) {
      const ex = explainMissing(e);
      await sink.write('stderr', `${ex.lines.join('\n')}\n`);
      return ex.exit;
    }
    if (e instanceof UsageError || e instanceof PolicyError) {
      await sink.write('stderr', `measure: ${e.message}\n${USAGE}\n`);
      return EXIT.ERROR;
    }
    if (e instanceof MeasureError) {
      await sink.write('stderr', `measure: ${e.message}\n`);
      return EXIT.ERROR;
    }
    await sink.error(e);
    return EXIT.ERROR;
  } finally {
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
