// K21: okno zgody zatwierdzającego pokazuje wrogi tytuł planu i nazwę gałęzi jako tekst. ctx: { page }
import { Suite, connect } from '../prototype/lib.mjs';
import { ctx, sleep, newPage, hits, domAudit, loginPage } from './common.mjs';

const suite = new Suite('xss_window');
suite.setup(async () => ({ c: await connect() }));
suite.teardown(async (s) => { if (s) await s.c.closeAll(); });

suite.add('XSS-P4', 'okno zgody zatwierdzającego: tytuł planu i nazwa gałęzi jako tekst, bez wykonania i bez zapisu', async (s) => {
  const p = await newPage(s.c, 'window');
  await loginPage(p, ctx.page, 'approver');
  await p.go(ctx.page + '/', 1800);
  const ready = await p.waitFor(`(() => { const b = document.querySelector('button[data-approve="cp"]'); return !!b && !b.disabled; })()`, 15000);
  if (!ready.ok) throw new Error('brak przycisku CP');
  await p.domClick(`document.querySelector('button[data-approve="cp"]')`);
  await p.waitFor(`!!document.querySelector('[data-dialog="consent"]')`, 4000);
  const text = await p.evaluate(`document.querySelector('[data-dialog="consent"]').textContent`);
  const traces = await domAudit(p); const executed = await hits(p);
  await p.key('Escape');
  const missing = ['plantitle', 'branch'].filter((m) => !text.includes(`KAN-${m}-`));
  return { ok: executed.length === 0 && traces.length === 0 && missing.length === 0, executed, domTraces: traces, missing };
});

await suite.main();
