/**
 * Purpose: Compare two directories of screenshots made by capture (manifest.json + PNG) and report SAME, DIFFERENT, MISSING, ADDED or UNVERIFIED per screenshot.
 * Usage: node diff.mjs [--selftest] [--json] [--max-pct <n>] <before> <after>
 * Inputs: two directories under the current directory, each with the manifest.json and PNG files written by capture; --max-pct threshold in percent (default 0, 0..100);
 *   env UI_VERIFY_CACHE_DIR (cache root), UI_VERIFY_SECRET_ENV (comma-separated names of env vars whose values are masked in output).
 * Outputs: stdout lines "<SAME|DIFFERENT|MISSING|ADDED|UNVERIFIED> <name> [reason] [pct]" and "result <PASS|FAIL|UNVERIFIED> (exit <n>)" (or the JSON with --json); writes no files.
 * Exit codes: 0 PASS (every shot SAME), 1 FAIL (DIFFERENT, size mismatch or MISSING), 2 usage/manifest/internal error, 3 missing dependencies, or UNVERIFIED (missing manifest or a shot without a positive assertion on either side).
 * Dependencies: pngjs 7.0.0, pixelmatch 8.0.0 (from the skill cache, see lib/deps.mjs); Node >= 20.11 (tested on 22).
 * Limits: whole images in memory (a PNG whose header declares over 16384 px per side or 50 M px, or whose file is over 64 MiB, is refused with exit 2 before decoding (so is an interlaced PNG, a non-standard or repeated IHDR, and a file over 64 MiB is not even read)); a shot is SAME only when both sides have assertion PASS and equal dimensions; masks are baked into the PNG by capture, so diff applies none; ADDED shots do not affect the result.
 * Security: reads only manifest.json and the PNG files named in it (names validated) under the current directory; writes nothing; output is masked for listed secrets.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { parseManifest } from './lib/capture-core.mjs';
import { parseArgs, runSelftest } from './lib/cli.mjs';
import { DiffError, compareShotSets, formatDiffReport, toJson } from './lib/diff-core.mjs';
import { DepsError, explainMissing } from './lib/deps.mjs';
import { createMasker, createSink, collectSecretsFromEnv, setGuardMasker } from './lib/out.mjs';
import { diffImages } from './lib/pixels.mjs';
import { PolicyError, resolveInside } from './lib/policy.mjs';
import { assertPngFileSize, decodePng, loadImaging, makePixelmatchFlagger, resolveCacheDir, skillDirOf } from './lib/runtime.mjs';
import { EXIT, installGuards, isMain } from './lib/verdict.mjs';

const SPEC = {
  string: ['max-pct'],
  boolean: ['selftest', 'json'],
};
const USAGE = 'usage: node diff.mjs [--selftest] [--json] [--max-pct <n>] <before> <after>';
const DEFAULT_MAX_PCT = 0;

class UsageError extends Error {}

function parseMaxPct(text) {
  const n = text === undefined ? DEFAULT_MAX_PCT : (String(text).trim() === '' ? NaN : Number(text));
  if (!Number.isFinite(n) || n < 0 || n > 100) throw new UsageError(`bad --max-pct "${text}" (expected 0..100)`);
  return n;
}

// --- selftest: syntetyczne obrazy i zestawy w pamieci (bez PNG i cache zaleznosci) ---

function solid(w, h, fn) {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) data.set(fn(x, y), (y * w + x) * 4);
  return { width: w, height: h, data };
}

function selftestCases() {
  const WHITE = [255, 255, 255, 255];
  const BLACK = [0, 0, 0, 255];
  const blob = (x0) => solid(40, 40, (x, y) => (x >= x0 && x < x0 + 12 && y >= 4 && y < 36 ? BLACK : WHITE));
  const check = (cond, msg) => { if (!cond) throw new Error(msg); };
  const shot = (name, assertion = 'PASS') => ({ name, pageId: name.split('__')[0], width: 375, mode: 'light', assertion });
  const set = (shots, images) => ({ manifest: shots ? { version: 1, shots } : null, images });
  const run = (before, after, maxPct = 0) => compareShotSets({
    before: 'b',
    after: 'a',
    diffFn: diffImages,
    readManifest: (d) => (d === 'b' ? before : after).manifest,
    readPng: (d, n) => (d === 'b' ? before : after).images[n],
    maxPct,
  });
  const N = 'p__375-light.png';
  const verdictOf = (r, name = N) => r.entries.find((e) => e.name === name)?.verdict;

  return [
    { name: 'identical shots with PASS assertions on both sides are SAME, exit 0', fn: () => {
      const r = run(set([shot(N)], { [N]: blob(4) }), set([shot(N)], { [N]: blob(4) }));
      check(verdictOf(r) === 'SAME' && r.verdict === 'PASS' && r.exit === 0, `got ${verdictOf(r)}/${r.verdict}`);
    } },
    { name: 'changed shape is DIFFERENT, exit 1', fn: () => {
      const r = run(set([shot(N)], { [N]: blob(4) }), set([shot(N)], { [N]: blob(24) }));
      check(verdictOf(r) === 'DIFFERENT' && r.exit === 1, `got ${verdictOf(r)}/${r.exit}`);
    } },
    { name: 'identical images without a PASS assertion are UNVERIFIED (exit 3), never SAME', fn: () => {
      for (const [x, y] of [['NONE', 'PASS'], ['PASS', 'FAIL'], ['NONE', 'NONE']]) {
        const r = run(set([shot(N, x)], { [N]: blob(4) }), set([shot(N, y)], { [N]: blob(4) }));
        check(verdictOf(r) === 'UNVERIFIED' && r.exit === 3, `${x}/${y}: got ${verdictOf(r)}/${r.exit}`);
      }
    } },
    { name: 'size mismatch is DIFFERENT', fn: () => {
      const r = run(set([shot(N)], { [N]: solid(40, 40, () => WHITE) }), set([shot(N)], { [N]: solid(40, 60, () => WHITE) }));
      check(verdictOf(r) === 'DIFFERENT' && r.entries[0].reason === 'size-mismatch' && r.exit === 1, `got ${verdictOf(r)}`);
    } },
    { name: 'missing manifest is UNVERIFIED and no PNG is read', fn: () => {
      let reads = 0;
      const r = compareShotSets({ before: 'b', after: 'a', diffFn: diffImages, readManifest: (d) => (d === 'b' ? null : { version: 1, shots: [shot(N)] }), readPng: () => { reads++; return blob(4); }, maxPct: 0 });
      check(r.verdict === 'UNVERIFIED' && r.exit === 3 && r.reason === 'no-manifest' && reads === 0, `got ${r.verdict}/${r.reason}/${reads}`);
    } },
    { name: 'shot only before is MISSING (FAIL), only after is ADDED (neutral)', fn: () => {
      const both = { [N]: blob(4) };
      const gone = run(set([shot(N), shot('g__375-light.png')], { ...both, 'g__375-light.png': blob(4) }), set([shot(N)], both));
      check(verdictOf(gone, 'g__375-light.png') === 'MISSING' && gone.exit === 1, `missing: ${gone.verdict}`);
      const added = run(set([shot(N)], both), set([shot(N), shot('n__375-light.png')], { ...both, 'n__375-light.png': blob(4) }));
      check(verdictOf(added, 'n__375-light.png') === 'ADDED' && added.exit === 0, `added: ${added.verdict}`);
    } },
    { name: 'an empty set is not PASS', fn: () => {
      const r = run(set([], {}), set([], {}));
      check(r.verdict !== 'PASS' && r.exit !== 0, `got ${r.verdict}`);
    } },
    { name: 'secret composed at run time is masked in the report', fn: () => {
      const secret = ['fake', 'secret', String(process.pid), 'wxyz'].join('-');
      const name = `p-${secret}__375-light.png`;
      const r = run(set([shot(name)], { [name]: blob(4) }), set([shot(name)], { [name]: blob(24) }));
      check(!formatDiffReport(r, createMasker({ secrets: [secret] })).includes(secret), 'secret leaked');
      check(formatDiffReport(r, createMasker({})).includes(secret), 'control: unmasked report lacks the value');
    } },
  ];
}

// --- odczyt zestawow z dysku ---

function readManifestFrom(dir) {
  const file = path.join(dir, 'manifest.json');
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (e) {
    if (e && e.code === 'ENOENT') return null;
    throw new DiffError(`cannot read ${file}: ${e.code || e.message}`, 'manifest');
  }
  let obj;
  try {
    obj = JSON.parse(text);
  } catch (e) {
    throw new DiffError(`${file} is not valid JSON: ${e.message}`, 'manifest');
  }
  const p = parseManifest(obj);
  if (!p.ok) throw new DiffError(`${file}: ${p.errors.join('; ')}`, 'manifest');
  return p.manifest;
}

function resolveDir(cwd, rel) {
  try {
    return resolveInside(cwd, rel);
  } catch (e) {
    if (e instanceof PolicyError) throw new UsageError(`directory "${rel}" is outside the current directory`);
    throw e;
  }
}

export async function main(argv, env, io) {
  const secrets = collectSecretsFromEnv(env, String(env.UI_VERIFY_SECRET_ENV || '').split(',').map((s) => s.trim()).filter(Boolean));
  const masker = createMasker({ secrets });
  setGuardMasker(masker);
  const sink = createSink({ masker, fs, root: io.cwd, stdout: io.stdout, stderr: io.stderr });
  try {
    const { values, positionals, errors } = parseArgs(argv, SPEC);
    const skillDir = skillDirOf(import.meta.url);

    if (values.selftest) {
      if (errors.length) throw new UsageError(errors.join('; '));
      return await runSelftest('diff', selftestCases(), { stdout: io.stdout, stderr: io.stderr, masker });
    }

    if (errors.length) throw new UsageError(errors.join('; '));
    if (positionals.length !== 2) throw new UsageError(`expected two directories (before, after), got ${positionals.length}`);
    const maxPct = parseMaxPct(values['max-pct']);
    const before = resolveDir(io.cwd, positionals[0]);
    const after = resolveDir(io.cwd, positionals[1]);

    const cacheDir = resolveCacheDir({ skillDir, env, platform: process.platform, homedir: os.homedir() });
    const { PNG, pixelmatch } = await loadImaging({ cacheDir, env });
    const flagger = makePixelmatchFlagger(pixelmatch, { threshold: 0.1, includeAA: false });
    const result = compareShotSets({
      before,
      after,
      maxPct,
      diffFn: (a, b, o) => diffImages(a, b, { ...o, flagger }),
      readManifest: readManifestFrom,
      readPng: (dir, name) => {
        try {
          const file = path.join(dir, name);
          assertPngFileSize(fs.statSync(file).size);
          return decodePng(PNG, fs.readFileSync(file));
        } catch (e) {
          throw new DiffError(`cannot read screenshot ${name} in ${path.relative(io.cwd, dir)}: ${e.code || e.message}`, 'png', e);
        }
      },
    });

    if (values.json) await sink.write('stdout', `${JSON.stringify(toJson(result, masker))}\n`);
    else await sink.write('stdout', formatDiffReport(result, masker));
    return result.exit;
  } catch (e) {
    if (e instanceof DepsError) {
      const ex = explainMissing(e);
      await sink.write('stderr', `${ex.lines.join('\n')}\n`);
      return ex.exit;
    }
    if (e instanceof UsageError || e instanceof PolicyError) {
      await sink.write('stderr', `diff: ${e.message}\n${USAGE}\n`);
      return EXIT.ERROR;
    }
    if (e instanceof DiffError) {
      await sink.write('stderr', `diff: ${e.message}\n`);
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
