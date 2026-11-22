// Czysta logika polecenia `diff`: porownanie dwoch zestawow zrzutow (manifest + PNG) z wstrzykiwanym odczytem i porownaniem obrazow.
// SAME tylko gdy OBA zrzuty maja asercje pozytywna PASS i obrazy sa zgodne; brak dowodu to UNVERIFIED, nigdy SAME.
import { combine, exitFor } from './verdict.mjs';

export class DiffError extends Error {
  constructor(message, code, cause) {
    super(message);
    this.name = 'DiffError';
    this.code = code;
    if (cause !== undefined) this.cause = cause;
  }
}

const ENTRY_VERDICT = { SAME: 'PASS', DIFFERENT: 'FAIL', MISSING: 'FAIL', UNVERIFIED: 'UNVERIFIED' };

function finish(entries, extra = {}) {
  const verdict = combine(entries.filter((e) => e.verdict !== 'ADDED').map((e) => ENTRY_VERDICT[e.verdict]));
  return { verdict, entries, exit: exitFor(verdict), ...extra };
}

function compareOne(name, b, a, { before, after, diffFn, readPng, maxPct, masks }) {
  if (b.assertion !== 'PASS' || a.assertion !== 'PASS') {
    return { name, verdict: 'UNVERIFIED', reason: 'no-positive-assertion', assertion: { before: b.assertion, after: a.assertion } };
  }
  const imgB = readPng(before, name);
  const imgA = readPng(after, name);
  if (imgB.width !== imgA.width || imgB.height !== imgA.height) {
    return { name, verdict: 'DIFFERENT', reason: 'size-mismatch', pct: 100, size: { before: [imgB.width, imgB.height], after: [imgA.width, imgA.height] } };
  }
  const r = diffFn(imgB, imgA, { cropToCommon: false, maxPct, masks: masks || [] });
  return {
    name,
    verdict: r.verdict === 'PASS' ? 'SAME' : 'DIFFERENT',
    pct: r.pct,
    diffCount: r.diffCount,
    total: r.total,
  };
}

export function compareShotSets({ before, after, diffFn, readManifest, readPng, maxPct, masks }) {
  const mb = readManifest(before);
  const ma = readManifest(after);
  if (!mb || !ma) {
    return finish([], { reason: 'no-manifest', missingManifest: [!mb ? 'before' : null, !ma ? 'after' : null].filter(Boolean), verdict: 'UNVERIFIED', exit: exitFor('UNVERIFIED') });
  }
  const byB = new Map(mb.shots.map((s) => [s.name, s]));
  const byA = new Map(ma.shots.map((s) => [s.name, s]));
  const entries = [];
  for (const [name, b] of [...byB].sort((x, y) => (x[0] < y[0] ? -1 : 1))) {
    const a = byA.get(name);
    if (!a) entries.push({ name, verdict: 'MISSING' });
    else entries.push(compareOne(name, b, a, { before, after, diffFn, readPng, maxPct, masks }));
  }
  for (const name of [...byA.keys()].sort()) if (!byB.has(name)) entries.push({ name, verdict: 'ADDED' });
  const out = finish(entries);
  if (entries.every((e) => e.verdict === 'ADDED')) out.reason = 'no-shots';
  return out;
}

const fmt = (v) => String(Number(Number(v).toFixed(4)));

export function formatDiffReport(result, masker) {
  const lines = [];
  if (result.reason === 'no-manifest') lines.push(`UNVERIFIED no-manifest (missing manifest.json in: ${(result.missingManifest || []).join(', ')})`);
  for (const e of result.entries) {
    const parts = [e.verdict, e.name];
    if (e.reason) parts.push(e.reason);
    if (e.pct !== undefined && e.reason !== 'size-mismatch') parts.push(`${fmt(e.pct)}%`);
    lines.push(parts.join(' '));
  }
  if (result.reason === 'no-shots') lines.push('UNVERIFIED no-shots (nothing to compare)');
  lines.push(`result ${result.verdict} (exit ${result.exit})`);
  return masker.mask(`${lines.join('\n')}\n`);
}

export function toJson(result, masker) {
  return masker.maskValue(JSON.parse(JSON.stringify({
    verdict: result.verdict,
    exit: result.exit,
    ...(result.reason ? { reason: result.reason } : {}),
    entries: result.entries,
  })));
}
