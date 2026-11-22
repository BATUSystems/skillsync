// Regression (Windows): the selftest of css-scope compared isMain() with `new URL(import.meta.url).pathname`, which on Windows is `/C:/...`
// while isMain() uses fileURLToPath (`C:\...`); the case failed there although the code was right. A file path must come from fileURLToPath.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { isMain } from '../../library/skills/ui-verify/scripts/lib/verdict.mjs';

const SCRIPT = new URL('../../library/skills/ui-verify/scripts/css-scope.mjs', import.meta.url);

test('css-scope.mjs does not derive a file path from import.meta.url with .pathname', () => {
  const source = fs.readFileSync(SCRIPT, 'utf8');
  assert.equal(/import\.meta\.url\)\.pathname/.test(source), false, 'use fileURLToPath(import.meta.url) for a file path');
});

test('isMain accepts the path made by fileURLToPath and refuses a missing argv (fake realpath)', () => {
  const fake = { realpathSync: (s) => s };
  assert.equal(isMain(SCRIPT.href, fileURLToPath(SCRIPT), fake), true);
  assert.equal(isMain(SCRIPT.href, undefined, fake), false);
});

// CRLF files (a real overlay checked out on Windows): comments, strings, a stray brace line and the counters behave as with LF.
import { scan, countImportant } from '../../library/skills/ui-verify/scripts/lib/css-parse.mjs';
import { checkScope } from '../../library/skills/ui-verify/scripts/lib/css-scope-core.mjs';

const CFG = { root: '.r', exceptions: [] };

test('CRLF: braces in comments and strings, rule count, !important and the line of a stray brace', () => {
  const css = '/* a {\r\n b } */\r\n.r a {\r\n  color: red !important;\r\n  content: "x\\\r\ny";\r\n}\r\n';
  assert.equal(checkScope(css, CFG).ok, true);
  assert.equal(checkScope(css, CFG).rules, 1);
  assert.equal(countImportant(css), 1);
  assert.throws(() => scan('.r a {\r\n}\r\n}\r\n'), (error) => error.line === 3);
  assert.equal(checkScope('.r a {\r\n color: red;\r\n}\r\nbody {\r\n margin: 0;\r\n}\r\n', CFG).ok, false);
});
