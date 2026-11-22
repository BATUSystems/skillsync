// Contract of the T1 exports the accessibility audit relies on: a change in the shared modules must not break it silently.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as runtime from '../../library/skills/ui-verify/scripts/lib/runtime.mjs';
import * as pixels from '../../library/skills/ui-verify/scripts/lib/pixels.mjs';
import * as adapter from '../../library/skills/ui-verify/scripts/lib/playwright-adapter.mjs';
import * as policy from '../../library/skills/ui-verify/scripts/lib/policy.mjs';
import * as out from '../../library/skills/ui-verify/scripts/lib/out.mjs';
import * as verdict from '../../library/skills/ui-verify/scripts/lib/verdict.mjs';
import * as cli from '../../library/skills/ui-verify/scripts/lib/cli.mjs';

const FUNCTIONS = {
  runtime: [runtime, ['makePixelmatchFlagger', 'decodePng', 'loadImaging', 'resolveCacheDir', 'skillDirOf']],
  pixels: [pixels, ['flagDifferences', 'diffImages']],
  adapter: [adapter, ['loadPlaywright', 'launchChromium']],
  policy: [policy, ['checkOrigin', 'resolveInside', 'safeName']],
  out: [out, ['createMasker', 'createSink', 'collectSecretsFromEnv']],
  verdict: [verdict, ['combine', 'exitFor', 'installGuards']],
  cli: [cli, ['parseArgs', 'runSelftest']],
};

for (const [name, [mod, names]] of Object.entries(FUNCTIONS)) {
  for (const fn of names) {
    test(`kontrakt T1: ${name}.${fn} jest funkcja`, () => {
      assert.equal(typeof mod[fn], 'function');
    });
  }
}

test('kontrakt T1: EXIT ma kody 0/1/2/3', () => {
  assert.deepEqual({ ...verdict.EXIT }, { PASS: 0, FAIL: 1, ERROR: 2, NOT_RUN: 3 });
});

test('kontrakt T1: PolicyError jest klasa bledu', () => {
  assert.equal(typeof policy.PolicyError, 'function');
  assert.ok(new policy.PolicyError('x', 'y') instanceof Error);
});

test('kontrakt T1: makePixelmatchFlagger zwraca flagger z progiem pixelmatch 0,1', () => {
  let seen = null;
  const flagger = runtime.makePixelmatchFlagger((a, b, o, w, h, opts) => { seen = opts; }, { threshold: 0.1 });
  const img = { width: 2, height: 1, data: new Uint8ClampedArray(8) };
  const flags = flagger(img, img);
  assert.equal(flags.length, 2);
  assert.equal(seen.threshold, 0.1);
});

test('kontrakt T1: flagDifferences i diffImages dzialaja na prostych obrazach', () => {
  const a = { width: 1, height: 1, data: new Uint8ClampedArray([0, 0, 0, 255]) };
  const b = { width: 1, height: 1, data: new Uint8ClampedArray([255, 255, 255, 255]) };
  assert.deepEqual([...pixels.flagDifferences(a, b)], [1]);
  assert.equal(pixels.diffImages(a, a).verdict, 'PASS');
});

test('kontrakt T1: combine i exitFor tworza zgodny lancuch', () => {
  assert.equal(verdict.exitFor(verdict.combine(['PASS', 'UNVERIFIED'])), 3);
});

test('kontrakt T1: createMasker maskuje sekret zlozony w czasie wykonania', () => {
  const secret = ['fake', 'secret', String(process.pid), 'abcd'].join('-');
  assert.equal(out.createMasker({ secrets: [secret] }).mask(`x ${secret} y`), 'x *** y');
});
