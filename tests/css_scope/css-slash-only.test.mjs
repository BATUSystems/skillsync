// A path in a css-scope configuration or flag uses `/` only, on every host: the configuration is data shared between machines, so a backslash
// must not mean a separator on Windows and a plain character elsewhere (Windows 3.12 run: `a\b` passed the validation on a win32 host).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { resolveSlashOnly } from '../../library/skills/ui-verify/scripts/lib/css-paths.mjs';
import { PolicyError } from '../../library/skills/ui-verify/scripts/lib/policy.mjs';

const ROOT = new URL('../../library/skills/ui-verify/scripts/', import.meta.url);

test('resolveSlashOnly refuses a backslash for both platform rules, also in a nested or trailing position', () => {
  for (const platform of ['win32', 'posix']) {
    for (const bad of ['a\\b', 'a\\o.css', '\\a', 'a\\', 'a/b\\c', '..\\x', 'C:\\x']) {
      assert.throws(() => resolveSlashOnly('/base', bad, { platform }), PolicyError, `${platform} ${JSON.stringify(bad)}`);
    }
  }
});

test('resolveSlashOnly keeps the resolveInside rules for slash paths (.., absolute, NUL, type)', () => {
  for (const platform of ['win32', 'posix']) {
    for (const bad of ['../x', 'a/../../x', '/abs', '..', 'a\0b', 5, null]) {
      assert.throws(() => resolveSlashOnly('/base', bad, { platform }), PolicyError, `${platform} ${JSON.stringify(bad)}`);
    }
  }
  assert.ok(resolveSlashOnly('/base', 'a/b', { platform: 'posix' }).endsWith('b'));
  assert.ok(resolveSlashOnly('/base', 'scss', { platform: 'win32' }).length > 0);
});

test('css-config.mjs and css-scope.mjs resolve their paths only through resolveSlashOnly', () => {
  for (const name of ['css-scope.mjs', 'lib/css-config.mjs']) {
    const source = fs.readFileSync(new URL(name, ROOT), 'utf8');
    const calls = source.split('\n').filter((line) => /\bresolveInside\(/.test(line) && !/^\s*(\/\/|import)/.test(line));
    assert.deepEqual(calls.filter((line) => !/need\(|'\/base'/.test(line)), [], `${name} calls resolveInside directly`);
  }
});
