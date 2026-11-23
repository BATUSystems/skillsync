/**
 * Purpose: Compare two PNG screenshots pixel by pixel (pixelmatch) with masks and 1 px tolerance; prints PASS or FAIL.
 * Usage: node pixel-diff.mjs [--selftest] [--max-pct <n>] [--mask <x,y,w,h>]... [--crop] [--diff-out <file.png>] [--json] [--no-tolerate-1px] <reference.png> <candidate.png>
 * Inputs: two PNG files; --max-pct threshold in percent (default 0.5); --mask regions in px from the top-left corner (repeatable);
 *   env UI_VERIFY_CACHE_DIR (cache root), UI_VERIFY_SECRET_ENV (comma-separated names of env vars whose values are masked in output).
 * Outputs: stdout line "PASS|FAIL pixel-diff <pct>% (<diffCount>/<total>)" or "FAIL pixel-diff size-mismatch (<wA>x<hA> vs <wB>x<hB>)";
 *   with --json one object { verdict, pct, diffCount, total, sizeMismatch, cropped }; --diff-out writes a diff PNG under the current directory.
 * Exit codes: 0 PASS, 1 FAIL (difference above threshold or size mismatch), 2 usage/configuration/internal error, 3 missing dependencies.
 * Dependencies: pngjs 7.0.0, pixelmatch 8.0.0 (from the skill cache, see lib/deps.mjs); Node >= 20.11 (tested on 22).
 * Limits: whole images in memory (a PNG whose header declares over 16384 px per side or 50 M px, or whose file is over 64 MiB, is refused with exit 2 before decoding (so is an interlaced PNG, a non-standard or repeated IHDR, and a file over 64 MiB is not even read)); sizes must match unless --crop; anti-aliased pixels are not counted as differences.
 * Security: reads only the given PNG files; writes only under the current directory (--diff-out); output is masked for listed secrets.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { parseArgs, runSelftest } from './lib/cli.mjs';
import { cropToCommon, diffImages } from './lib/pixels.mjs';
import { createMasker, createSink, collectSecretsFromEnv, setGuardMasker } from './lib/out.mjs';
import { PolicyError, resolveInside } from './lib/policy.mjs';
import { EXIT, exitFor, installGuards, isMain } from './lib/verdict.mjs';
import { DepsError, explainMissing } from './lib/deps.mjs';
import { assertPngFileSize, decodePng, encodePng, ImageLimitError, loadImaging, makePixelmatchFlagger, resolveCacheDir, skillDirOf } from './lib/runtime.mjs';

const SPEC = {
  string: ['max-pct', 'diff-out'],
  repeatable: ['mask'],
  boolean: ['selftest', 'crop', 'json', 'no-tolerate-1px'],
};
const USAGE = 'usage: node pixel-diff.mjs [--selftest] [--max-pct <n>] [--mask <x,y,w,h>]... [--crop] [--diff-out <file.png>] [--json] [--no-tolerate-1px] <reference.png> <candidate.png>';

class UsageError extends Error {}

function parseMask(text) {
  const parts = String(text).split(',');
  const nums = parts.map((s) => (s.trim() === '' ? NaN : Number(s)));
  if (parts.length !== 4 || !nums.every(Number.isFinite) || nums[2] < 0 || nums[3] < 0) {
    throw new UsageError(`bad --mask "${text}" (expected x,y,w,h in px, w and h >= 0)`);
  }
  return { x: nums[0], y: nums[1], width: nums[2], height: nums[3] };
}

function parseMaxPct(text) {
  const n = text === undefined ? 0.5 : (String(text).trim() === '' ? NaN : Number(text));
  if (!Number.isFinite(n) || n < 0 || n > 100) throw new UsageError(`bad --max-pct "${text}" (expected 0..100)`);
  return n;
}

function fmtPct(pct) {
  return String(Number(pct.toFixed(4)));
}

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

function runDiff(a, b, { flagger, maxPct, masks, tolerate1px }) {
  return diffImages(a, b, { flagger, maxPct, masks, tolerate1px });
}

function selftestCases({ PNG, pixelmatch, skillDir }) {
  const flagger = makePixelmatchFlagger(pixelmatch, {});
  const WHITE = [255, 255, 255, 255];
  const BLACK = [0, 0, 0, 255];
  const bar = (x0) => (x, y) => (x >= x0 && x < x0 + 3 ? BLACK : WHITE);
  const img = (x0, w = 40, h = 40) => solid(w, h, bar(x0));
  const check = (cond, msg) => { if (!cond) throw new Error(msg); };
  const cases = [
    { name: 'identical synthetic PNG', fn: () => {
      const a = decodePng(PNG, encodePng(PNG, img(10)));
      const r = runDiff(a, a, { flagger, maxPct: 0.5, tolerate1px: true });
      check(r.verdict === 'PASS' && r.diffCount === 0, `got ${r.verdict} ${r.diffCount}`);
    } },
    { name: '1 px shift passes thanks to the 1 px shift tolerance', fn: () => {
      const r = runDiff(img(10), img(11), { flagger, maxPct: 0.5, tolerate1px: true });
      check(r.verdict === 'PASS', `got ${r.verdict} ${r.pct}`);
    } },
    { name: '4 px shift fails', fn: () => {
      const r = runDiff(img(10), img(14), { flagger, maxPct: 0.5, tolerate1px: true });
      check(r.verdict === 'FAIL', `got ${r.verdict} ${r.pct}`);
    } },
    { name: 'size mismatch fails', fn: () => {
      const r = runDiff(img(10), img(10, 40, 30), { flagger, maxPct: 0.5, tolerate1px: true });
      check(r.verdict === 'FAIL' && r.sizeMismatch === true, `got ${r.verdict}`);
    } },
    { name: 'mask covering the difference passes', fn: () => {
      const r = runDiff(img(10), img(14), { flagger, maxPct: 0.5, masks: [{ x: 8, y: 0, width: 10, height: 40 }], tolerate1px: true });
      check(r.verdict === 'PASS', `got ${r.verdict} ${r.pct}`);
    } },
    { name: 'fixtures from cases.json', fn: () => {
      const dir = path.join(skillDir, 'fixtures');
      const cfg = JSON.parse(fs.readFileSync(path.join(dir, 'cases.json'), 'utf8'));
      for (const c of cfg.cases) {
        const a = decodePng(PNG, fs.readFileSync(path.join(dir, c.id, 'reference.png')));
        const b = decodePng(PNG, fs.readFileSync(path.join(dir, c.id, 'candidate.png')));
        const r = runDiff(a, b, { flagger: makePixelmatchFlagger(pixelmatch, { threshold: cfg.pixelDiff.threshold, includeAA: cfg.pixelDiff.includeAA }), maxPct: cfg.pixelDiff.maxPct, tolerate1px: true });
        const [lo, hi] = c.expected.pixelDiffPctRange;
        check(r.verdict === c.expected.pixelDiff, `${c.id}: verdict ${r.verdict}`);
        check(r.pct >= lo && r.pct <= hi, `${c.id}: pct ${r.pct} outside [${lo}, ${hi}]`);
      }
    } },
  ];
  return cases;
}

function pureLogicCase() {
  return {
    name: 'pure logic (raw images, channel flagger)',
    fn: () => {
      const WHITE = [255, 255, 255, 255];
      const BLACK = [0, 0, 0, 255];
      const bar = (x0) => (x, y) => (x >= x0 && x < x0 + 3 ? BLACK : WHITE);
      const same = diffImages(solid(20, 20, bar(5)), solid(20, 20, bar(5)), { maxPct: 0.5 });
      if (same.verdict !== 'PASS' || same.diffCount !== 0) throw new Error('identical should PASS');
      const shifted = diffImages(solid(20, 20, bar(5)), solid(20, 20, bar(6)), { maxPct: 0.5 });
      if (shifted.verdict !== 'PASS') throw new Error('1 px shift should PASS');
      const far = diffImages(solid(20, 20, bar(5)), solid(20, 20, bar(9)), { maxPct: 0.5 });
      if (far.verdict !== 'FAIL') throw new Error('4 px shift should FAIL');
    },
  };
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
      let imaging;
      try {
        imaging = await loadImaging({ cacheDir: resolveCacheDir({ skillDir, env, platform: process.platform, homedir: os.homedir() }), env });
      } catch (e) {
        if (!(e instanceof DepsError)) throw e;
        await runSelftest('pixel-diff', [pureLogicCase()], { stdout: io.stdout, stderr: io.stderr, masker });
        const ex = explainMissing(e);
        await sink.write('stderr', `${ex.lines.join('\n')}\n`);
        return ex.exit;
      }
      const cases = [...selftestCases({ ...imaging, skillDir }), pureLogicCase()];
      return await runSelftest('pixel-diff', cases, { stdout: io.stdout, stderr: io.stderr, masker });
    }

    if (errors.length) throw new UsageError(errors.join('; '));
    if (positionals.length !== 2) throw new UsageError(`expected 2 PNG files, got ${positionals.length}`);
    const masks = (values.mask || []).map(parseMask);
    const maxPct = parseMaxPct(values['max-pct']);
    const diffOut = values['diff-out'];
    if (diffOut !== undefined) resolveInside(io.cwd, diffOut);

    const cacheDir = resolveCacheDir({ skillDir, env, platform: process.platform, homedir: os.homedir() });
    const { PNG, pixelmatch } = await loadImaging({ cacheDir, env });
    const readImage = (rel) => {
      const file = path.resolve(io.cwd, rel);
      assertPngFileSize(fs.statSync(file).size);
      return decodePng(PNG, fs.readFileSync(file));
    };
    const a = readImage(positionals[0]);
    const b = readImage(positionals[1]);
    const common = values.crop === true ? cropToCommon(a, b) : { a, b, cropped: false };
    const r = runDiff(common.a, common.b, {
      flagger: makePixelmatchFlagger(pixelmatch, { threshold: 0.1, includeAA: false }),
      maxPct,
      masks,
      tolerate1px: values['no-tolerate-1px'] !== true,
    });
    r.cropped = common.cropped;
    if (diffOut !== undefined && r.flags) await sink.writeFile(diffOut, encodePng(PNG, diffPng(common.a, r.flags)));

    if (values.json) {
      const { verdict, pct, diffCount, total, sizeMismatch, cropped } = r;
      await sink.write('stdout', `${JSON.stringify({ verdict, pct, diffCount, total, sizeMismatch, cropped })}\n`);
    } else if (r.sizeMismatch) {
      await sink.write('stdout', `FAIL pixel-diff size-mismatch (${a.width}x${a.height} vs ${b.width}x${b.height})\n`);
    } else {
      await sink.write('stdout', `${r.verdict} pixel-diff ${fmtPct(r.pct)}% (${r.diffCount}/${r.total})\n`);
    }
    return exitFor(r.verdict);
  } catch (e) {
    if (e instanceof DepsError) {
      const ex = explainMissing(e);
      await sink.write('stderr', `${ex.lines.join('\n')}\n`);
      return ex.exit;
    }
    if (e instanceof UsageError) {
      await sink.write('stderr', `pixel-diff: ${e.message}\n${USAGE}\n`);
      return EXIT.ERROR;
    }
    if (e instanceof PolicyError || e instanceof ImageLimitError) {
      await sink.write('stderr', `pixel-diff: ${e.message}\n`);
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
