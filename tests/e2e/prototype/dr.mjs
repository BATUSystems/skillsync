// Pozostałości rozszerzenia przyciemniającego: symulacja wstrzykniętego węzła stylu (bez prawdziwego rozszerzenia).
import { Suite, sleep, workspace, connect, consoleVerdict } from './lib.mjs';

const suite = new Suite('dr');
let ws; let c;
const FALLBACK = `(() => { const s = document.createElement('style'); s.className = 'darkreader darkreader--fallback'; s.media = 'screen';
  s.textContent = 'html, body, body :not(iframe) { background-color: #181a1b !important; border-color: #776e62 !important; color: #e8e6e3 !important; }';
  const other = document.createElement('style'); other.className = 'dark-mode-test'; other.textContent = '/* obcy styl */';
  const put = () => { const root = document.documentElement; if (!root) return false; root.appendChild(s); root.appendChild(other); return true; };
  if (!put()) { const mo = new MutationObserver(() => { if (put()) mo.disconnect(); }); mo.observe(document, { childList: true }); } })();`;
const url = (slug = 'demo') => 'file://' + ws.file(slug);
const probe = (p) => p.evaluate(`({ bg: getComputedStyle(document.body).backgroundColor, drLeft: document.querySelectorAll('style.darkreader').length, otherKept: document.querySelectorAll('style.dark-mode-test').length, lock: !!document.querySelector('head > meta[name=darkreader-lock]') })`);
const lum = (css) => { const m = /oklch\(\s*([\d.]+)/.exec(css || ''); if (m) return +m[1]; const x = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(css || ''); return x ? (+x[1] + +x[2] + +x[3]) / 765 : null; };

suite.setup(async () => { ws = workspace('dr'); c = await connect(); return {}; });
suite.teardown(async () => { if (c) await c.closeAll(); });

suite.add('DR-start', 'Fallback przy starcie', async () => {
  const p = await c.page('dr'); await p.send('Page.addScriptToEvaluateOnNewDocument', { source: FALLBACK });
  await p.go(url()); await sleep(1200);
  const r = await probe(p); await p.dispose();
  return { ok: r.lock && r.drLeft === 0 && r.otherKept === 1 && lum(r.bg) > 0.8, ...r };
});

suite.add('DR-późny', 'Fallback dopisany później', async () => {
  const p = await c.page('dr2'); await p.go(url());
  await p.evaluate(`(() => { const s = document.createElement('style'); s.className = 'darkreader darkreader--sync'; s.textContent = 'body { background: #000 !important }'; document.head.appendChild(s); return 1; })()`); await sleep(500);
  const r = await probe(p); await p.dispose();
  return { ok: r.drLeft === 0 && lum(r.bg) > 0.8, ...r };
});

suite.add('DR-kontrola', 'Kontrola sondy: strona bez silnika', async () => {
  const p = await c.page('dr3'); await p.send('Page.addScriptToEvaluateOnNewDocument', { source: FALLBACK });
  await p.go('data:text/html,<!doctype html><html><head><title>kontrola</title></head><body>kontrola</body></html>'); await sleep(800);
  const r = await probe(p); await p.dispose();
  // Bez silnika pozostałość zostaje: dowód, że sonda potrafi wykryć wstrzyknięty węzeł.
  return { ok: r.drLeft >= 1 && lum(r.bg) < 0.3, ...r, oczekiwanie: 'strona bez silnika zachowuje wstrzyknięty styl (sonda wykrywa pozostałość)' };
}, { critical: false });

suite.add('DR-rozszerzenie', 'Prawdziwy Dark Reader', async () => ({ status: 'NOT_RUN', reason: 'wymaga przeglądarki z zainstalowanym rozszerzeniem i zgody właściciela (D25); sprawdzana jest symulacja DR-start i DR-późny' }), { critical: false });

await suite.main();
