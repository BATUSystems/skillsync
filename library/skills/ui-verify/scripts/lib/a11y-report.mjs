// Report assembly (JSON schema 1 and Markdown) for the accessibility audit. Page-derived text is cleaned, cut and masked.
import { combine, exitFor } from './verdict.mjs';
import { LIMITS, cleanText } from './a11y-schema.mjs';
import { findingKey } from './a11y-exceptions.mjs';

export const REPORT_TOOL = 'ui-verify a11y';
const NEUTRAL = new Set(['EXEMPT', 'EXCEPTED', 'INFO']);

// Strings anywhere inside structured values (measured, exception, confirmation) are page-derived as well.
function cleanDeep(v, masker) {
  if (typeof v === 'string') return cleanText(v, LIMITS.messageChars, masker);
  if (Array.isArray(v)) return v.map((x) => cleanDeep(x, masker));
  if (v !== null && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, cleanDeep(x, masker)]));
  return v;
}

function cleanFinding(f, page, masker, index) {
  const out = {
    id: `${f.rule}-${index + 1}`,
    key: cleanText(f.key || findingKey({ rule: f.rule, page, selector: f.selector, hashBasis: f.hashBasis, message: f.message }), 400, masker),
    rule: f.rule,
    selector: f.selector === undefined || f.selector === null ? null : cleanText(f.selector, LIMITS.selectorChars, masker),
    verdict: f.verdict,
    measured: f.measured === undefined ? null : cleanDeep(f.measured, masker),
    required: f.required === undefined ? null : f.required,
    basis: f.basis || 'wcag',
    note: f.note ? cleanText(f.note, LIMITS.messageChars, masker) : null,
    pageData: true,
  };
  if (f.reason) out.reason = f.reason;
  if (f.message) out.message = cleanText(f.message, LIMITS.messageChars, masker);
  if (f.exception) out.exception = cleanDeep(f.exception, masker);
  if (f.confirmation) out.confirmation = cleanDeep(f.confirmation, masker);
  return out;
}

// pages: [{ id, url, assertion: 'PASS'|'FAIL'|'NONE', checks: [..], findings: [..] }]
export function nowOverrideWarning(now) {
  return { code: 'now-overridden', level: 'warning', message: `run date overridden with --now ${now}; expiry of exceptions is judged against it` };
}

const EXPECT_RESULT = { 0: 'PASS', 1: 'FAIL', 3: 'UNVERIFIED' };

// In mode "expect" the exit code and result come from the comparison with the expected file; the plain audit outcome
// stays in auditResult and auditExit. `nowOverridden` (a date string) adds the now-overridden warning.
export function buildReport({ version = 1, mode = 'audit', configDigest = null, pages = [], exceptions = [], confirmations = [], warnings: inWarnings = [], masker = null, expectResult = null, nowOverridden = null } = {}) {
  const warnings = nowOverridden ? [...inWarnings, nowOverrideWarning(nowOverridden)] : inWarnings;
  const outPages = pages.map((p) => {
    const raw = [...(p.findings || [])];
    if (p.assertion === 'FAIL') raw.unshift({ rule: 'assertion-failed', verdict: 'FAIL', message: 'asserted element missing or text differs', selector: undefined, hashBasis: 'assertion' });
    else if (p.assertion !== 'PASS') raw.unshift({ rule: 'assertion-failed', verdict: 'UNVERIFIED', reason: 'no-assertion', message: 'page has no positive assertion', selector: undefined, hashBasis: 'no-assertion' });
    if (Array.isArray(p.checks) && p.checks.length === 0) raw.push({ rule: 'checks', verdict: 'UNVERIFIED', reason: 'no-checks', message: 'no checks ran', selector: undefined, hashBasis: 'no-checks' });
    const findings = raw.map((f, i) => cleanFinding(f, p.id, masker, i));
    return { id: p.id, url: String(p.url || '').split(/[?#]/)[0], assertion: p.assertion || 'NONE', ...(p.sequence ? { sequence: p.sequence.map((s) => cleanText(s, LIMITS.selectorChars, masker)) } : {}), findings };
  });
  const all = outPages.flatMap((p) => p.findings);
  const counted = all.filter((f) => !NEUTRAL.has(f.verdict)).map((f) => (f.verdict === 'CONFIRMED' ? 'PASS' : f.verdict));
  const result = combine(counted);
  const byRule = {};
  for (const f of all) {
    const r = (byRule[f.rule] ||= {});
    r[f.verdict] = (r[f.verdict] || 0) + 1;
  }
  const report = {
    tool: REPORT_TOOL,
    version,
    mode,
    config: configDigest,
    pages: outPages,
    exceptions: cleanDeep(exceptions, masker),
    confirmations: cleanDeep(confirmations, masker),
    warnings: warnings.map((w) => ({ ...w, code: cleanText(w.code, LIMITS.textChars, masker), ...(w.selector !== undefined ? { selector: cleanText(w.selector, LIMITS.selectorChars, masker) } : {}), message: cleanText(w.message, LIMITS.messageChars, masker) })).slice(0, LIMITS.messages),
    summary: { byRule, total: all.length },
    result,
    exit: exitFor(result),
  };
  if (expectResult) {
    report.auditResult = report.result;
    report.auditExit = report.exit;
    report.expectResult = cleanDeep(expectResult, masker);
    report.exit = expectResult.exit;
    report.result = EXPECT_RESULT[expectResult.exit] || 'UNVERIFIED';
  }
  return report;
}

const cell = (s) => String(s ?? '').replace(/\|/g, '\\|');
const VERDICT_COLS = ['PASS', 'FAIL', 'EXCEPTED', 'CONFIRMED', 'UNVERIFIED', 'EXEMPT'];

function measuredText(m) {
  if (m === null || m === undefined) return '';
  if (typeof m !== 'object') return String(m);
  return Object.entries(m).filter(([, v]) => typeof v !== 'object').map(([k, v]) => `${k}=${v}`).join(', ');
}

export function renderMarkdown(report) {
  const L = [];
  L.push('# Accessibility audit', '');
  L.push(`Result: **${report.result}** (exit ${report.exit}). Mode: ${report.mode}. Config: ${report.config ?? 'n/a'}.`, '');
  L.push('Text in selectors, messages and notes is page data (untrusted), cleaned and shortened.', '');
  L.push('## Summary by rule', '', `| rule | ${VERDICT_COLS.join(' | ')} |`, `| --- | ${VERDICT_COLS.map(() => '---').join(' | ')} |`);
  for (const [rule, counts] of Object.entries(report.summary.byRule)) {
    L.push(`| ${cell(rule)} | ${VERDICT_COLS.map((v) => counts[v] || 0).join(' | ')} |`);
  }
  L.push('');
  const fails = report.pages.flatMap((p) => p.findings.filter((f) => f.verdict === 'FAIL').map((f) => ({ page: p.id, f })));
  L.push('## Failures', '');
  if (fails.length === 0) L.push('None.');
  for (const { page, f } of fails) {
    L.push(`- [${cell(f.rule)}] page ${cell(page)}: \`${cell(f.selector ?? f.key)}\` measured ${cell(measuredText(f.measured))}${f.required !== null ? `, required ${f.required}` : ''}${f.message ? `: ${cell(f.message)}` : ''}`);
  }
  L.push('');
  const unv = report.pages.flatMap((p) => p.findings.filter((f) => f.verdict === 'UNVERIFIED').map((f) => ({ page: p.id, f })));
  if (unv.length) {
    L.push('## Unverified', '');
    for (const { page, f } of unv) L.push(`- [${cell(f.rule)}] page ${cell(page)}: \`${cell(f.selector ?? f.key)}\` (${cell(f.reason)})`);
    L.push('');
  }
  const excepted = report.pages.flatMap((p) => p.findings.filter((f) => f.verdict === 'EXCEPTED').map((f) => ({ page: p.id, f })));
  if (excepted.length) {
    L.push('## Exceptions', '');
    for (const { page, f } of excepted) L.push(`- [${cell(f.rule)}] page ${cell(page)}: \`${cell(f.selector ?? f.key)}\`: ${cell(f.exception && f.exception.reason)}`);
    L.push('');
  }
  const confirmed = report.pages.flatMap((p) => p.findings.filter((f) => f.verdict === 'CONFIRMED').map((f) => ({ page: p.id, f })));
  if (confirmed.length) {
    L.push('## Confirmed manually', '');
    for (const { page, f } of confirmed) L.push(`- [${cell(f.rule)}] page ${cell(page)}: \`${cell(f.selector ?? f.key)}\` by ${cell(f.confirmation && f.confirmation.checkedBy)}: ${cell(f.confirmation && f.confirmation.reason)}`);
    L.push('');
  }
  if (report.warnings.length) {
    L.push('## Warnings', '');
    for (const w of report.warnings) L.push(`- ${cell(w.level)} ${cell(w.code)}: ${cell(w.message)}`);
    L.push('');
  }
  return `${L.join('\n')}\n`;
}
