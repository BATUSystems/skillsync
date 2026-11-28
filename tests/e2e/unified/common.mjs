// Wspólne narzędzia scenariuszy przeglądarkowych CORE-2000-UNIFIED (U-7). Sterownik CDP: ../prototype/lib.mjs.
// Kontekst (adresy, oczekiwane znaczniki) przychodzi w SKILLSYNC_UNIFIED_CTX od harness.py.
import { env, sleep, secret, mail, errors } from '../prototype/lib.mjs';

export const ctx = JSON.parse(process.env.SKILLSYNC_UNIFIED_CTX || '{}');
export const J = JSON.stringify;

// Detektor wykonania: ładunek woła window.__pwn('pole'). Skrypt stoi przed każdym dokumentem karty (także w ramkach), więc CSP go nie dotyczy.
const PROBE = `window.__hits = window.__hits || []; window.__pwn = (id) => { window.__hits.push(String(id)); };
window.__violations = []; document.addEventListener('securitypolicyviolation', (e) => window.__violations.push(e.effectiveDirective));`;

export async function newPage(c, name, { bypassCSP = false, shared } = {}) {
  const p = await c.page(name, { shared });
  await p.send('Page.addScriptToEvaluateOnNewDocument', { source: PROBE });
  if (bypassCSP) await p.send('Page.setBypassCSP', { enabled: true });
  return p;
}

// Wywołanie z gestem użytkownika (okna potomne).
export async function gesture(p, expression) {
  const r = await p.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true, userGesture: true });
  if (r.exceptionDetails) throw new Error('eval: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text).slice(0, 300));
  return r.result.value;
}

export const hits = (p) => p.evaluate('window.__hits || []');

// Co wstrzyknięty ładunek zostawiłby w DOM, gdyby trafił do niego jako HTML (także bez wykonania).
export const domAudit = (p) => p.evaluate(`(() => {
  const bad = [];
  for (const el of document.querySelectorAll('*')) {
    for (const a of el.attributes) {
      if (/^on/i.test(a.name)) bad.push(el.tagName + ' ' + a.name + '=' + a.value.slice(0, 60));
      if (['href', 'src', 'action', 'formaction', 'xlink:href'].includes(a.name) && /^\\s*(javascript|data|vbscript):/i.test(a.value) && !(a.value.startsWith('data:image/') || a.value === 'data:,')) bad.push(el.tagName + ' ' + a.name + '=' + a.value.slice(0, 60));
    }
    if ((el.tagName === 'IMG' || el.tagName === 'IFRAME') && /__pwn|^x$/.test(el.getAttribute('src') || '')) bad.push(el.tagName + ' src=' + el.getAttribute('src'));
    if (el.tagName === 'SCRIPT' && /__pwn/.test(el.textContent || '')) bad.push('SCRIPT ' + el.textContent.slice(0, 60));
  }
  return bad;
})()`);

// Znaczniki KAN-<pole>- widoczne w tekście dokumentu (z atrybutami podpowiedzi): dowód, że pole w ogóle dotarło do strony.
export const markers = (p) => p.evaluate(`(() => {
  let text = document.body ? document.body.textContent : '';
  for (const el of document.querySelectorAll('[title],[aria-label],[data-desc-extra],[data-tip],input,textarea')) text += ' ' + ['title', 'aria-label', 'data-desc-extra', 'data-tip'].map((n) => el.getAttribute(n) || '').join(' ') + ' ' + (el.value || '');
  return [...new Set([...text.matchAll(/KAN-([a-z0-9]+)-/g)].map((m) => m[1]))];
})()`);

export async function signInOnLoginPage(p, who) {
  const ready = await p.waitFor(`!!document.querySelector('#login-form #password') && document.querySelector('#login').offsetParent !== null`, 8000);
  if (!ready.ok) throw new Error('brak formularza logowania');
  await sleep(300);
  await p.evaluate(`document.querySelector('#login').focus(); true`); await p.type(mail(who));
  await p.evaluate(`document.querySelector('#password').focus(); true`); await p.type(secret(who));
  await p.key('Enter');
}

export async function loginPage(p, base, who) {
  await p.go(base + '/_login', 500);
  await signInOnLoginPage(p, who);
  const ok = await p.waitFor(`fetch('/_api/v1/session').then((r) => r.json()).then((s) => !!s.user)`, 10000, 300);
  if (!ok.ok) throw new Error('logowanie na stronie nieudane: ' + who);
  await sleep(500);
}

export const sessionOf = (p) => p.evaluate(`fetch('/_api/v1/session').then((r) => r.json())`);
export { env, sleep, secret, mail, errors };
