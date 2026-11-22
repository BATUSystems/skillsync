import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../library/skills/ui-verify/scripts');
const load = () => import('../../library/skills/ui-verify/scripts/lib/paths.mjs');

function walk(dir) {
  const out = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...walk(p));
    else if (e.name.endsWith('.mjs')) out.push(p);
  }
  return out;
}

test('defaultCacheRoot linux: XDG_CACHE_HOME ma pierwszenstwo przed ~/.cache', async () => {
  const { defaultCacheRoot } = await load();
  assert.equal(defaultCacheRoot({ platform: 'linux', env: { XDG_CACHE_HOME: '/x/cache' }, homedir: '/h' }), '/x/cache/skillsync/ui-verify');
  assert.equal(defaultCacheRoot({ platform: 'linux', env: {}, homedir: '/h' }), '/h/.cache/skillsync/ui-verify');
  assert.equal(defaultCacheRoot({ platform: 'linux', env: { XDG_CACHE_HOME: '' }, homedir: '/h' }), '/h/.cache/skillsync/ui-verify');
});

test('defaultCacheRoot darwin: ~/Library/Caches', async () => {
  const { defaultCacheRoot } = await load();
  assert.equal(defaultCacheRoot({ platform: 'darwin', env: { XDG_CACHE_HOME: '/ignored' }, homedir: '/m' }), '/m/Library/Caches/skillsync/ui-verify');
});

test('defaultCacheRoot win32: LOCALAPPDATA i separatory backslash', async () => {
  const { defaultCacheRoot } = await load();
  assert.equal(defaultCacheRoot({ platform: 'win32', env: { LOCALAPPDATA: 'D:\\h\\AppData\\Local' }, homedir: 'D:\\h' }),
    'D:\\h\\AppData\\Local\\skillsync\\cache\\ui-verify');
  assert.equal(defaultCacheRoot({ platform: 'win32', env: {}, homedir: 'D:\\h' }),
    'D:\\h\\AppData\\Local\\skillsync\\cache\\ui-verify');
});

test('defaultCacheRoot: UI_VERIFY_CACHE_DIR wygrywa i jest zwracany bez zmian (kazda platforma)', async () => {
  const { defaultCacheRoot } = await load();
  for (const platform of ['linux', 'darwin', 'win32']) {
    const env = { UI_VERIFY_CACHE_DIR: 'D:\\mix/path', XDG_CACHE_HOME: '/x', LOCALAPPDATA: 'C:\\L' };
    assert.equal(defaultCacheRoot({ platform, env, homedir: '/h' }), 'D:\\mix/path');
  }
});

test('defaultCacheRoot: pusty UI_VERIFY_CACHE_DIR jest ignorowany; nieznana platforma = linux', async () => {
  const { defaultCacheRoot } = await load();
  assert.equal(defaultCacheRoot({ platform: 'linux', env: { UI_VERIFY_CACHE_DIR: '' }, homedir: '/h' }), '/h/.cache/skillsync/ui-verify');
  assert.equal(defaultCacheRoot({ platform: 'freebsd', env: {}, homedir: '/h' }), '/h/.cache/skillsync/ui-verify');
});

test('computeHash: sha256 (16 znakow hex) z zawartosci rozdzielonych \\0', async () => {
  const { computeHash } = await load();
  const expected = crypto.createHash('sha256').update('a\0b').digest('hex').slice(0, 16);
  assert.equal(computeHash(['a', 'b']), expected);
  assert.match(computeHash(['a', 'b']), /^[0-9a-f]{16}$/);
  assert.equal(computeHash([Buffer.from('a'), 'b']), expected);
});

test('computeHash: stabilny, wrazliwy na kolejnosc i na granice plikow', async () => {
  const { computeHash } = await load();
  assert.equal(computeHash(['x', 'y']), computeHash(['x', 'y']));
  assert.notEqual(computeHash(['x', 'y']), computeHash(['y', 'x']));
  assert.notEqual(computeHash(['ab', 'c']), computeHash(['a', 'bc']));
});

test('cacheDirFor: root + separator platformy + hash', async () => {
  const { cacheDirFor } = await load();
  assert.equal(cacheDirFor('/r/ui-verify', 'abc', 'linux'), '/r/ui-verify/abc');
  assert.equal(cacheDirFor('/r/ui-verify', 'abc', 'darwin'), '/r/ui-verify/abc');
  assert.equal(cacheDirFor('C:\\r\\ui-verify', 'abc', 'win32'), 'C:\\r\\ui-verify\\abc');
});

test('playwrightOverride: niepusta wartosc albo null', async () => {
  const { playwrightOverride } = await load();
  assert.equal(playwrightOverride({ PLAYWRIGHT_MODULE_PATH: '/p/node_modules' }), '/p/node_modules');
  assert.equal(playwrightOverride({ PLAYWRIGHT_MODULE_PATH: '' }), null);
  assert.equal(playwrightOverride({}), null);
});

test('zakaz golych specyfikatorow playwright-core|pngjs|pixelmatch w scripts/**/*.mjs', () => {
  const bare = /(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^\s*import\s*)(['"])(?:playwright-core|pngjs|pixelmatch)(?:\/[^'"]*)?\1/m;
  const files = walk(SCRIPTS);
  assert.ok(files.length > 0, 'brak plikow .mjs do sprawdzenia');
  for (const f of files) {
    assert.doesNotMatch(fs.readFileSync(f, 'utf8'), bare, `goly specyfikator w ${path.relative(SCRIPTS, f)}`);
  }
});

test('zakaz golych specyfikatorow: wzorzec wykrywa podlozone naruszenia', () => {
  const bare = /(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^\s*import\s*)(['"])(?:playwright-core|pngjs|pixelmatch)(?:\/[^'"]*)?\1/m;
  for (const bad of ["import { PNG } from 'pngjs';", "const p = require('pixelmatch');", 'await import("playwright-core")', "import 'pngjs';"]) {
    assert.match(bad, bare, bad);
  }
  assert.doesNotMatch("const spec = 'pngjs'; // tylko nazwa", bare);
});
