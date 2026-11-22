// Project exceptions and manual confirmations (file { version: 1, exceptions, confirmations }), finding keys and expiry.
// The current date is always injected as `now` ("YYYY-MM-DD"); nothing here reads the clock.
import { createHash } from 'node:crypto';
import { CONFIRMABLE_REASONS } from './a11y-schema.mjs';

export class ExceptionsError extends Error {
  constructor(message, errors = []) {
    super(message);
    this.name = 'ExceptionsError';
    this.errors = errors;
  }
}

const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const isText = (v) => typeof v === 'string' && v.trim() !== '';
const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
export const MIN_MESSAGE_FRAGMENT = 8;
const TOP = new Set(['version', 'exceptions', 'confirmations']);
const EXC_KEYS = new Set(['rule', 'page', 'selector', 'key', 'messageContains', 'reason', 'expires']);
const CONF_KEYS = new Set(['rule', 'page', 'selector', 'key', 'result', 'checkedBy', 'reason', 'expires']);

export function isValidDate(text) {
  const m = DATE_RE.exec(String(text));
  if (!m) return false;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const dt = new Date(Date.UTC(y, mo - 1, d));
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === mo - 1 && dt.getUTCDate() === d;
}

// "YYYY-MM-DD" plus whole months, day clamped to the end of the target month.
export function addMonths(date, months) {
  const m = DATE_RE.exec(date);
  const y = Number(m[1]);
  const mo = Number(m[2]) - 1 + months;
  const ty = y + Math.floor(mo / 12);
  const tm = ((mo % 12) + 12) % 12;
  const last = new Date(Date.UTC(ty, tm + 1, 0)).getUTCDate();
  const day = Math.min(Number(m[3]), last);
  return `${String(ty).padStart(4, '0')}-${String(tm + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

// Numbers and long hex runs become "#" so that volatile values do not change the key.
export function normalizeMessage(message) {
  return String(message ?? '')
    .replace(/\b0x[0-9a-f]+\b|\b[0-9a-f]{8,}\b/gi, '#')
    .replace(/\d+(?:\.\d+)?/g, '#');
}

export function digest(text) {
  return createHash('sha256').update(String(text), 'utf8').digest('hex').slice(0, 12);
}

// finding: { rule, selector?, hashBasis?, message? }. Without a selector the key part is a digest.
export function findingKey({ rule, page, selector, hashBasis, message }) {
  const part = selector !== undefined && selector !== null && selector !== ''
    ? selector
    : digest(hashBasis !== undefined ? hashBasis : normalizeMessage(message));
  return `${rule}|${page}|${part}`;
}

function checkEntry(e, i, kind, keys, errors, now, isConfirmation) {
  const w = `${kind}[${i}]`;
  if (!isObj(e)) { errors.push(`${w}: expected object`); return; }
  for (const k of Object.keys(e)) if (!keys.has(k)) errors.push(`${w}.${k}: unknown key`);
  if (!isText(e.reason)) errors.push(`${w}.reason: required non-empty text`);
  if (e.rule !== undefined && !isText(e.rule)) errors.push(`${w}.rule: expected text`);
  if (e.expires !== undefined && !isValidDate(e.expires)) errors.push(`${w}.expires: expected a real date YYYY-MM-DD`);
  const hasKey = isText(e.key);
  const hasSel = isText(e.rule) && isText(e.selector);
  const hasMsg = !isConfirmation && e.rule === 'console' && isText(e.messageContains);
  if (!isConfirmation && e.messageContains !== undefined && (typeof e.messageContains !== 'string' || e.messageContains.length < MIN_MESSAGE_FRAGMENT)) {
    errors.push(`${w}.messageContains: expected text of at least ${MIN_MESSAGE_FRAGMENT} characters`);
  }
  if (!hasKey && !hasSel && !hasMsg) errors.push(`${w}: needs "key", or "rule" with "selector", or (console) "messageContains"`);
  if (isConfirmation) {
    if (e.result !== 'PASS') errors.push(`${w}.result: only "PASS" is accepted (report a real FAIL by fixing the code)`);
    if (!isText(e.checkedBy)) errors.push(`${w}.checkedBy: required non-empty text`);
    if (e.expires === undefined) errors.push(`${w}.expires: required for a confirmation`);
    else if (isValidDate(e.expires) && isValidDate(now) && e.expires > addMonths(now, 12)) errors.push(`${w}.expires: at most 12 months from the run date`);
  }
}

// Returns { ok, errors, data: { exceptions, confirmations } }; messages never quote the file content.
export function parseExceptionsFile(obj, { now } = {}) {
  const errors = [];
  if (!isValidDate(now)) throw new ExceptionsError('now must be a date YYYY-MM-DD');
  if (!isObj(obj)) return { ok: false, errors: ['exceptions file: expected a JSON object'], data: null };
  for (const k of Object.keys(obj)) if (!TOP.has(k)) errors.push(`unknown key "${k}"`);
  if (obj.version !== 1) errors.push('version: expected 1');
  const exceptions = obj.exceptions === undefined ? [] : obj.exceptions;
  const confirmations = obj.confirmations === undefined ? [] : obj.confirmations;
  if (!Array.isArray(exceptions)) errors.push('exceptions: expected a list');
  else exceptions.forEach((e, i) => checkEntry(e, i, 'exceptions', EXC_KEYS, errors, now, false));
  if (!Array.isArray(confirmations)) errors.push('confirmations: expected a list');
  else confirmations.forEach((e, i) => checkEntry(e, i, 'confirmations', CONF_KEYS, errors, now, true));
  return { ok: errors.length === 0, errors, data: errors.length ? null : { exceptions: [...exceptions], confirmations: [...confirmations] } };
}

function matches(entry, f, withMessage) {
  if (isText(entry.key)) return entry.key === f.key;
  if (entry.page !== undefined && entry.page !== f.page) return false;
  if (isText(entry.rule) && isText(entry.selector)) return entry.rule === f.rule && entry.selector === f.selector;
  if (withMessage && entry.rule === 'console' && isText(entry.messageContains)) {
    return f.rule === 'console' && String(f.message ?? '').includes(entry.messageContains);
  }
  return false;
}

const expired = (entry, now) => entry.expires !== undefined && entry.expires < now;

/**
 * applyExceptions(findings, data, { now }) -> { findings, exceptions, confirmations, warnings }
 * findings: { rule, page, selector?, key, verdict, reason?, message? }. FAIL + exception = EXCEPTED;
 * UNVERIFIED with a confirmable reason + confirmation = CONFIRMED. Limits and errors never get a way out.
 */
export function applyExceptions(findings, data, { now }) {
  if (!isValidDate(now)) throw new ExceptionsError('now must be a date YYYY-MM-DD');
  const exceptions = (data && data.exceptions) || [];
  const confirmations = (data && data.confirmations) || [];
  const warnings = [];
  const used = new Set();
  const expiredWarned = new Set();
  const warnExpired = (entry, kind) => {
    if (expiredWarned.has(entry)) return;
    expiredWarned.add(entry);
    warnings.push({ code: 'exception-expired', level: 'warning', message: `${kind} expired on ${entry.expires} and is ignored: ${entry.key || `${entry.rule}|${entry.selector ?? entry.messageContains}`}` });
  };
  const out = findings.map((f) => {
    if (f.verdict === 'FAIL') {
      for (const e of exceptions) {
        if (!matches(e, f, true)) continue;
        if (expired(e, now)) { warnExpired(e, 'exception'); continue; }
        used.add(e);
        return { ...f, verdict: 'EXCEPTED', exception: { reason: e.reason, ...(e.expires ? { expires: e.expires } : {}) } };
      }
    } else if (f.verdict === 'UNVERIFIED' && CONFIRMABLE_REASONS.includes(f.reason)) {
      for (const e of confirmations) {
        if (!matches(e, f, false)) continue;
        if (expired(e, now)) { warnExpired(e, 'confirmation'); continue; }
        used.add(e);
        return { ...f, verdict: 'CONFIRMED', confirmation: { checkedBy: e.checkedBy, reason: e.reason, expires: e.expires } };
      }
    }
    return f;
  });
  for (const [list, kind] of [[exceptions, 'exception'], [confirmations, 'confirmation']]) {
    for (const e of list) {
      if (used.has(e) || expiredWarned.has(e) || expired(e, now)) {
        if (!used.has(e) && !expiredWarned.has(e)) warnExpired(e, kind);
        continue;
      }
      warnings.push({ code: 'exception-unused', level: 'info', message: `${kind} matched nothing: ${e.key || `${e.rule}|${e.selector ?? e.messageContains}`}` });
    }
  }
  const mark = (list, kind) => list.map((e) => ({ ...e, status: used.has(e) ? 'used' : (expired(e, now) ? 'expired' : 'unused'), kind }));
  return { findings: out, exceptions: mark(exceptions, 'exception'), confirmations: mark(confirmations, 'confirmation'), warnings };
}
