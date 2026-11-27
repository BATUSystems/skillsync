import test from 'node:test';
import assert from 'node:assert/strict';
import {
  addMonths, applyExceptions, digest, findingKey, isValidDate, normalizeMessage, parseExceptionsFile,
} from '../../library/skills/ui-verify/scripts/lib/a11y-exceptions.mjs';

const NOW = '2026-11-22';
const parse = (obj) => parseExceptionsFile({ version: 1, ...obj }, { now: NOW });
const fail = (over) => ({ rule: 'contrast', page: 'home', selector: '#brand', key: 'contrast|home|#brand', verdict: 'FAIL', ...over });
const unv = (over) => ({ rule: 'contrast', page: 'home', selector: '.hero h1', key: 'contrast|home|.hero h1', verdict: 'UNVERIFIED', reason: 'opacity-group-with-paint', ...over });

test('klucz findingu: regula|strona|selektor', () => {
  assert.equal(findingKey({ rule: 'contrast', page: 'home', selector: '#brand' }), 'contrast|home|#brand');
});

test('klucz bez selektora: skrot 12 znakow sha256 z komunikatu po zamianie liczb i hexow na #', () => {
  assert.equal(digest('abc').length, 12);
  assert.equal(digest('abc'), 'ba7816bf8f01');
  const a = findingKey({ rule: 'console', page: 'p', message: 'Failed to load 404 at 2026 id deadbeef12345678' });
  const b = findingKey({ rule: 'console', page: 'p', message: 'Failed to load 404 at 7 id 0123456789abcdef' });
  assert.equal(a, b);
  assert.match(a, /^console\|p\|[0-9a-f]{12}$/);
  assert.notEqual(a, findingKey({ rule: 'console', page: 'p', message: 'Other problem' }));
  assert.equal(normalizeMessage('x 1.5 y 0xFF z cafebabe99'), 'x # y # z #');
  // HTTP status codes are kept, so 404 and 500 have different keys
  assert.equal(normalizeMessage('GET /a 404 and 12'), 'GET /a 404 and #');
  assert.notEqual(findingKey({ rule: 'console', page: 'p', message: 'GET /a 404' }), findingKey({ rule: 'console', page: 'p', message: 'GET /a 500' }));
  // para selektorow kroku (tab-order-visual): skrot z hashBasis
  assert.equal(findingKey({ rule: 'tab-order-visual', page: 'p', hashBasis: '#a>#b' }), `tab-order-visual|p|${digest('#a>#b')}`);
});

test('daty: poprawnosc i dodawanie miesiecy', () => {
  assert.equal(isValidDate('2026-02-29'), false);
  assert.equal(isValidDate('2028-02-29'), true);
  assert.equal(isValidDate('2026-1-1'), false);
  assert.equal(addMonths('2026-11-22', 12), '2027-11-22');
  assert.equal(addMonths('2026-01-31', 1), '2026-02-28');
  assert.equal(addMonths('2026-11-30', 3), '2027-02-28');
});

test('plik: reason wymagany, nieznany klucz, zla wersja, brak dopasowania to bledy', () => {
  assert.equal(parse({ exceptions: [{ rule: 'contrast', selector: '#a' }] }).ok, false);
  assert.equal(parse({ exceptions: [{ rule: 'contrast', selector: '#a', reason: '  ' }] }).ok, false);
  assert.equal(parse({ exceptions: [{ rule: 'contrast', reason: 'x' }] }).ok, false);
  assert.equal(parse({ exceptions: [{ rule: 'contrast', selector: '#a', reason: 'x', bogus: 1 }] }).ok, false);
  assert.equal(parse({ bogus: [] }).ok, false);
  assert.equal(parseExceptionsFile({ version: 2 }, { now: NOW }).ok, false);
  assert.equal(parse({ exceptions: [{ key: 'contrast|home|#a', reason: 'logo' }] }).ok, true);
  assert.equal(parse({ exceptions: [{ rule: 'contrast', selector: '#a', reason: 'x', expires: '2026-13-01' }] }).ok, false);
  assert.equal(parse({}).ok, true);
  const bad = parse({ exceptions: [{ rule: 'contrast', selector: '#a', reason: '' }] });
  assert.ok(bad.errors.every((e) => !e.includes('#a')));
});

test('messageContains: tylko console i co najmniej 8 znakow', () => {
  assert.equal(parse({ exceptions: [{ rule: 'console', page: 'home', messageContains: 'favicon.ico', reason: 'x' }] }).ok, true);
  assert.equal(parse({ exceptions: [{ rule: 'console', page: 'home', messageContains: 'short', reason: 'x' }] }).ok, false);
  assert.equal(parse({ exceptions: [{ rule: 'contrast', page: 'home', messageContains: 'long enough text', reason: 'x' }] }).ok, false);
  // messageContains without page is rejected
  assert.equal(parse({ exceptions: [{ rule: 'console', messageContains: 'favicon.ico', reason: 'x' }] }).ok, false);
});

test('wyjatek zmienia FAIL w EXCEPTED z powodem; dopasowanie po key i po rule+selector', () => {
  for (const entry of [{ key: 'contrast|home|#brand', reason: 'logo' }, { rule: 'contrast', selector: '#brand', reason: 'logo' }, { rule: 'contrast', page: 'home', selector: '#brand', reason: 'logo' }]) {
    const out = applyExceptions([fail()], parse({ exceptions: [entry] }).data, { now: NOW });
    assert.equal(out.findings[0].verdict, 'EXCEPTED');
    assert.equal(out.findings[0].exception.reason, 'logo');
    assert.equal(out.exceptions[0].status, 'used');
  }
  const other = applyExceptions([fail()], parse({ exceptions: [{ rule: 'contrast', page: 'other', selector: '#brand', reason: 'x' }] }).data, { now: NOW });
  assert.equal(other.findings[0].verdict, 'FAIL');
});

test('console: dopasowanie przez messageContains', () => {
  const f = { rule: 'console', page: 'home', key: 'console|home|abc', verdict: 'FAIL', message: 'GET /favicon.ico 404 (Not Found)' };
  const out = applyExceptions([f], parse({ exceptions: [{ rule: 'console', page: 'home', messageContains: 'favicon.ico', reason: 'no icon yet' }] }).data, { now: NOW });
  assert.equal(out.findings[0].verdict, 'EXCEPTED');
});

test('wyjatek po terminie nie dziala i daje ostrzezenie exception-expired', () => {
  const data = parse({ exceptions: [{ rule: 'contrast', selector: '#brand', reason: 'logo', expires: '2026-11-21' }] }).data;
  const out = applyExceptions([fail()], data, { now: NOW });
  assert.equal(out.findings[0].verdict, 'FAIL');
  assert.equal(out.warnings.filter((w) => w.code === 'exception-expired').length, 1);
  assert.equal(out.exceptions[0].status, 'expired');
  // w dniu terminu jeszcze dziala
  const same = parse({ exceptions: [{ rule: 'contrast', selector: '#brand', reason: 'logo', expires: NOW }] }).data;
  assert.equal(applyExceptions([fail()], same, { now: NOW }).findings[0].verdict, 'EXCEPTED');
});

test('wpis niczego nie dopasowal: exception-unused jako info', () => {
  const data = parse({ exceptions: [{ rule: 'contrast', selector: '#gone', reason: 'old' }] }).data;
  const out = applyExceptions([fail()], data, { now: NOW });
  const w = out.warnings.find((x) => x.code === 'exception-unused');
  assert.equal(w.level, 'info');
  assert.equal(out.findings[0].verdict, 'FAIL');
});

test('potwierdzenie: result tylko PASS, checkedBy, reason i expires <= 12 miesiecy', () => {
  const ok = { rule: 'contrast', selector: '.hero h1', result: 'PASS', checkedBy: 'AB', reason: 'measured on the image', expires: '2027-01-31' };
  assert.equal(parse({ confirmations: [ok] }).ok, true);
  assert.equal(parse({ confirmations: [{ ...ok, result: 'FAIL' }] }).ok, false);
  assert.equal(parse({ confirmations: [{ ...ok, checkedBy: '' }] }).ok, false);
  assert.equal(parse({ confirmations: [{ ...ok, reason: '' }] }).ok, false);
  const noExp = { ...ok };
  delete noExp.expires;
  assert.equal(parse({ confirmations: [noExp] }).ok, false);
  assert.equal(parse({ confirmations: [{ ...ok, expires: '2027-11-22' }] }).ok, true);
  assert.equal(parse({ confirmations: [{ ...ok, expires: '2027-11-23' }] }).ok, false);
  assert.equal(parse({ confirmations: [{ ...ok, messageContains: 'long enough text' }] }).ok, false);
});

test('potwierdzenie zmienia UNVERIFIED o przyczynie pomiarowej w CONFIRMED', () => {
  const ok = { rule: 'contrast', selector: '.hero h1', result: 'PASS', checkedBy: 'AB', reason: 'measured', expires: '2027-01-31' };
  const data = parse({ confirmations: [ok] }).data;
  for (const reason of ['opacity-group-with-paint', 'background-clip-text', 'color-unconvertible', 'occluded', 'clipped', 'filter-or-blend', 'inline-important-color', 'autofill', 'media-background']) {
    const out = applyExceptions([unv({ reason })], data, { now: NOW });
    assert.equal(out.findings[0].verdict, 'CONFIRMED', reason);
    assert.equal(out.findings[0].confirmation.checkedBy, 'AB');
  }
});

test('UNVERIFIED z limitow nie ma sciezki wyjatku ani potwierdzenia', () => {
  const data = parse({
    exceptions: [{ rule: 'contrast', selector: '.hero h1', reason: 'x' }],
    confirmations: [{ rule: 'contrast', selector: '.hero h1', result: 'PASS', checkedBy: 'AB', reason: 'm', expires: '2027-01-31' }],
  }).data;
  for (const reason of ['page-too-tall', 'truncated', 'sequence-truncated', 'no-assertion', 'no-checks', 'no-samples']) {
    assert.equal(applyExceptions([unv({ reason })], data, { now: NOW }).findings[0].verdict, 'UNVERIFIED', reason);
  }
  // wyjatek nie ratuje UNVERIFIED, potwierdzenie nie ratuje FAIL
  assert.equal(applyExceptions([unv()], parse({ exceptions: [{ rule: 'contrast', selector: '.hero h1', reason: 'x' }] }).data, { now: NOW }).findings[0].verdict, 'UNVERIFIED');
  assert.equal(applyExceptions([fail({ selector: '.hero h1', key: 'k' })], parse({ confirmations: [{ rule: 'contrast', selector: '.hero h1', result: 'PASS', checkedBy: 'AB', reason: 'm', expires: '2027-01-31' }] }).data, { now: NOW }).findings[0].verdict, 'FAIL');
});

test('potwierdzenie po terminie nie dziala', () => {
  const data = parse({ confirmations: [{ rule: 'contrast', selector: '.hero h1', result: 'PASS', checkedBy: 'AB', reason: 'm', expires: '2026-11-01' }] }).data;
  const out = applyExceptions([unv()], data, { now: NOW });
  assert.equal(out.findings[0].verdict, 'UNVERIFIED');
  assert.equal(out.warnings.some((w) => w.code === 'exception-expired'), true);
});

test('data biegu jest wstrzykiwana: brak lub zly format to blad', () => {
  assert.throws(() => parseExceptionsFile({ version: 1 }, {}), /now/);
  assert.throws(() => applyExceptions([], null, { now: 'tomorrow' }), /now/);
  assert.deepEqual(applyExceptions([], null, { now: NOW }).findings, []);
});

test('wpis messageContains (nawet z page) trafiajacy wiecej niz jeden finding daje exception-broad', () => {
  const mk = (message) => ({ rule: 'console', page: 'p', key: `console|p|${message}`, verdict: 'FAIL', message });
  const data = { exceptions: [{ rule: 'console', page: 'p', messageContains: 'timeout while loading', reason: 'known' }], confirmations: [] };
  const broad = applyExceptions([mk('timeout while loading a.js'), mk('timeout while loading b.js')], data, { now: '2026-11-22' });
  assert.equal(broad.warnings.filter((w) => w.code === 'exception-broad').length, 1);
  assert.equal(applyExceptions([mk('timeout while loading a.js')], data, { now: '2026-11-22' }).warnings.some((w) => w.code === 'exception-broad'), false);
});

test('wpis bez page trafiajacy wiecej niz jeden finding daje ostrzezenie exception-broad', () => {
  const two = [fail(), fail({ page: 'other', key: 'contrast|other|#brand' })];
  const broad = applyExceptions(two, parse({ exceptions: [{ rule: 'contrast', selector: '#brand', reason: 'logo' }] }).data, { now: NOW });
  assert.equal(broad.findings.every((x) => x.verdict === 'EXCEPTED'), true);
  assert.equal(broad.warnings.filter((w) => w.code === 'exception-broad').length, 1);
  const narrow = applyExceptions(two, parse({ exceptions: [{ rule: 'contrast', page: 'home', selector: '#brand', reason: 'logo' }] }).data, { now: NOW });
  assert.equal(narrow.warnings.some((w) => w.code === 'exception-broad'), false);
  const single = applyExceptions([fail()], parse({ exceptions: [{ rule: 'contrast', selector: '#brand', reason: 'logo' }] }).data, { now: NOW });
  assert.equal(single.warnings.some((w) => w.code === 'exception-broad'), false);
});

test('reguly fokusu: wyjatek zdejmuje FAIL, potwierdzenie rozstrzyga UNVERIFIED clipped, INFO bez zmian', () => {
  for (const rule of ['focus-contrast', 'focus-appearance']) {
    const finding = (selector, verdict, extra = {}) => ({ rule, page: 'home', selector, key: findingKey({ rule, page: 'home', selector }), verdict, ...extra });
    const parsed = parse({
      exceptions: [
        { rule, page: 'home', selector: '#faint', reason: 'accepted' },
        { key: `${rule}|home|#keyed`, reason: 'accepted by key' },
        { rule, page: 'home', selector: '#info', reason: 'does not act on INFO' },
      ],
      confirmations: [{ rule, page: 'home', selector: '#edge', result: 'PASS', checkedBy: 'tester', reason: 'checked by hand', expires: '2027-06-30' }],
    });
    assert.equal(parsed.ok, true, parsed.errors.join('; '));
    const out = applyExceptions([
      finding('#faint', 'FAIL'), finding('#keyed', 'FAIL'), finding('#info', 'INFO'),
      finding('#edge', 'UNVERIFIED', { reason: 'clipped' }), finding('#other', 'FAIL'),
    ], parsed.data, { now: NOW });
    assert.deepEqual(out.findings.map((f) => f.verdict), ['EXCEPTED', 'EXCEPTED', 'INFO', 'CONFIRMED', 'FAIL'], rule);
    assert.deepEqual(out.warnings.map((w) => w.code), ['exception-unused'], rule);
  }
});
