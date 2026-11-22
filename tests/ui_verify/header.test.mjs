import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { HEADER_SECTIONS, parseHeader } from '../../library/skills/ui-verify/scripts/lib/header.mjs';

const SCRIPTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../library/skills/ui-verify/scripts');

function header(sections) {
  return `#!/usr/bin/env node\n/**\n${sections.map((s) => ` * ${s}: tekst ${s}`).join('\n')}\n */\nexport {};\n`;
}

test('HEADER_SECTIONS: osiem sekcji w ustalonej kolejnosci', () => {
  assert.deepEqual([...HEADER_SECTIONS], ['Purpose', 'Usage', 'Inputs', 'Outputs', 'Exit codes', 'Dependencies', 'Limits', 'Security']);
});

test('parseHeader: poprawny naglowek', () => {
  const r = parseHeader(header(HEADER_SECTIONS));
  assert.equal(r.ok, true);
  assert.deepEqual(r.sections, [...HEADER_SECTIONS]);
  assert.deepEqual(r.missing, []);
  assert.equal(r.order, true);
});

test('parseHeader: brakujaca sekcja', () => {
  const r = parseHeader(header(HEADER_SECTIONS.filter((s) => s !== 'Limits')));
  assert.equal(r.ok, false);
  assert.deepEqual(r.missing, ['Limits']);
  assert.ok(!r.sections.includes('Limits'));
});

test('parseHeader: zla kolejnosc', () => {
  const swapped = [...HEADER_SECTIONS];
  [swapped[1], swapped[2]] = [swapped[2], swapped[1]];
  const r = parseHeader(header(swapped));
  assert.equal(r.ok, false);
  assert.equal(r.order, false);
  assert.deepEqual(r.missing, []);
});

test('parseHeader: brak komentarza naglowkowego', () => {
  const r = parseHeader('export const x = 1;\n');
  assert.equal(r.ok, false);
  assert.deepEqual(r.missing, [...HEADER_SECTIONS]);
});

test('parseHeader: sekcje spoza pierwszego bloku komentarza sie nie licza', () => {
  const src = `/**\n * Purpose: x\n */\n/**\n${HEADER_SECTIONS.slice(1).map((s) => ` * ${s}: y`).join('\n')}\n */\n`;
  assert.equal(parseHeader(src).ok, false);
});

test('KAZDY scripts/*.mjs ma poprawny naglowek (pusty zbior dozwolony)', () => {
  const files = fs.existsSync(SCRIPTS)
    ? fs.readdirSync(SCRIPTS).filter((f) => f.endsWith('.mjs'))
    : [];
  for (const f of files) {
    const r = parseHeader(fs.readFileSync(path.join(SCRIPTS, f), 'utf8'));
    assert.equal(r.ok, true, `${f}: brakuje ${r.missing.join(', ')}; kolejnosc=${r.order}`);
  }
});
