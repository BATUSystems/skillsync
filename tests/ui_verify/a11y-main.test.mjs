// Process-level tests of a11y.mjs: main() with fake streams, --selftest as a child process.
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { main } from '../../library/skills/ui-verify/scripts/a11y.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SCRIPT = path.resolve(HERE, '..', '..', 'library', 'skills', 'ui-verify', 'scripts', 'a11y.mjs');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'uv-a11y-main-'));
after(() => fs.rmSync(tmp, { recursive: true, force: true }));

const GOOD = { version: 1, pages: [{ id: 'home', url: 'page.html', assert: { selector: 'main' } }] };
const secretValue = ['fake', 'secret', String(process.pid), 'mmmm'].join('-');
let n = 0;
function project(files = {}) {
  const dir = path.join(tmp, `p${n++}`);
  fs.mkdirSync(path.join(dir, 'cfg'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'cfg', 'page.html'), '<html></html>');
  fs.writeFileSync(path.join(dir, 'cfg', 'c.json'), JSON.stringify(GOOD));
  fs.writeFileSync(path.join(dir, 'outside.json'), JSON.stringify({ version: 1 }));
  for (const [name, text] of Object.entries(files)) fs.writeFileSync(path.join(dir, 'cfg', name), text);
  return dir;
}
async function run(dir, argv, env = {}) {
  let out = '';
  let err = '';
  const code = await main(argv, env, { cwd: path.join(dir, 'cfg'), stdout: { write: (t) => { out += t; } }, stderr: { write: (t) => { err += t; } } });
  return { code, out, err };
}

test('selftest przez main: kod 0, brak bledow', async () => {
  const r = await run(project(), ['--selftest']);
  assert.equal(r.code, 0, r.out + r.err);
  assert.match(r.out, /selftest a11y: \d+\/\d+ passed/);
});

test('selftest jako proces potomny: kod 0', () => {
  const p = spawnSync(process.execPath, [SCRIPT, '--selftest'], { encoding: 'utf8', timeout: 60000 });
  assert.equal(p.status, 0, p.stdout + p.stderr);
});

test('poprawna konfiguracja bez zaleznosci i bez Chrome: kod 3 z lista sprawdzonych miejsc', async () => {
  const dir = project();
  const env = { UI_VERIFY_CACHE_DIR: path.join(dir, 'no-cache'), UI_VERIFY_CHROME: path.join(dir, 'no-chrome'), PATH: '' };
  const r = await run(dir, ['--config', 'c.json', 'out'], env);
  assert.equal(r.code, 3, r.err);
  assert.match(r.err, /kod 3, NOT_RUN/);
  assert.ok(!fs.existsSync(path.join(dir, 'cfg', 'out')), 'nothing is written without a run');
});

test('login: brak zmiennej srodowiska wymaganej przez krok fill to kod 2', async () => {
  const cfg = { ...GOOD, login: { url: 'page.html', steps: [{ fill: { selector: '#u', valueEnv: 'A11Y_TEST_MISSING' } }] } };
  const r = await run(project({ 'login.json': JSON.stringify(cfg) }), ['--config', 'login.json', 'out']);
  assert.equal(r.code, 2);
  assert.match(r.err, /A11Y_TEST_MISSING/);
});

test('outDir poza katalogiem konfiguracji: kod 2', async () => {
  const r = await run(project(), ['--config', 'c.json', '../out']);
  assert.equal(r.code, 2);
  assert.match(r.err, /outside the configuration directory/);
});

test('--exceptions i --expect poza katalogiem konfiguracji: kod 2', async () => {
  const dir = project();
  const a = await run(dir, ['--config', 'c.json', '--exceptions', '../outside.json', 'out']);
  assert.equal(a.code, 2);
  assert.match(a.err, /outside the configuration directory/);
  const b = await run(dir, ['--config', 'c.json', '--expect', '../outside.json', 'out']);
  assert.equal(b.code, 2);
  assert.match(b.err, /outside the configuration directory/);
});

test('--now: zly format i zla data kalendarzowa to kod 2', async () => {
  const dir = project();
  for (const bad of ['2026-1-1', 'tomorrow', '2026-02-30', '2026-13-01']) {
    const r = await run(dir, ['--config', 'c.json', '--now', bad, 'out']);
    assert.equal(r.code, 2, bad);
    assert.match(r.err, /--now/);
  }
  const env = { UI_VERIFY_CACHE_DIR: path.join(dir, 'no-cache'), UI_VERIFY_CHROME: path.join(dir, 'no-chrome'), PATH: '' };
  assert.equal((await run(dir, ['--config', 'c.json', '--now', '2026-11-22', 'out'], env)).code, 3);
});

test('blad JSON konfiguracji nie cytuje tresci, wartosc sekretu nie wycieka', async () => {
  const dir = project({ 'bad.json': `{"password": "${secretValue}", oops}` });
  const r = await run(dir, ['--config', 'bad.json', 'out'], { A11Y_TEST_SECRET: secretValue, UI_VERIFY_SECRET_ENV: 'A11Y_TEST_SECRET' });
  assert.equal(r.code, 2);
  assert.ok(!(r.out + r.err).includes(secretValue));
  assert.ok(!(r.out + r.err).includes('password'));
});

test('blad konfiguracji z literalem fill.value: kod 2 bez cytowania wartosci', async () => {
  const cfg = { ...GOOD, login: { url: 'page.html', steps: [{ fill: { selector: '#u', value: secretValue } }] } };
  const r = await run(project({ 'lit.json': JSON.stringify(cfg) }), ['--config', 'lit.json', 'out']);
  assert.equal(r.code, 2);
  assert.ok(!(r.out + r.err).includes(secretValue));
});

test('zly plik wyjatkow i zly expected.json: kod 2', async () => {
  const dir = project({ 'ex.json': '{ nope', 'exp.json': '{ nope', 'noreason.json': JSON.stringify({ version: 1, exceptions: [{ rule: 'contrast', selector: '#a' }] }) });
  assert.equal((await run(dir, ['--config', 'c.json', '--exceptions', 'ex.json', 'out'])).code, 2);
  assert.equal((await run(dir, ['--config', 'c.json', '--exceptions', 'noreason.json', 'out'])).code, 2);
  assert.equal((await run(dir, ['--config', 'c.json', '--expect', 'exp.json', 'out'])).code, 2);
});

test('uzycie: brak --config, nieznana flaga, dwa outDir: kod 2', async () => {
  const dir = project();
  assert.equal((await run(dir, ['out'])).code, 2);
  assert.equal((await run(dir, ['--bogus', '--config', 'c.json', 'out'])).code, 2);
  assert.equal((await run(dir, ['--config', 'c.json', 'a', 'b'])).code, 2);
});
