// Zakładka „Testy” bez sesji i z sesją: ostrzeżenie o surowych wynikach (`tests.publicRaw`) i pole polecenia biegu.
// ctx: { page, masked: bool (manifest ma tests.mask: true), absolute: {nazwa: ścieżka bezwzględna z polecenia}, inside: ścieżka w projekcie }
import { Suite, connect } from '../prototype/lib.mjs';
import { ctx, J, sleep, newPage, loginPage, sessionOf } from './common.mjs';

const suite = new Suite('anonymous_tests');
suite.setup(async () => ({ c: await connect() }));
suite.teardown(async (s) => { if (s) await s.c.closeAll(); });

// Otwiera zakładkę „Testy”, rozwija sekcje i czeka na polecenie biegu: dowód, że zakładka naprawdę coś narysowała.
async function testsTab(p) {
  await p.go(ctx.page + '/', 1800);
  const shown = await p.waitFor(`!!document.getElementById('tab-tests') && !document.getElementById('tab-tests').hidden`, 10000);
  if (!shown.ok) throw new Error('brak zakładki „Testy”');
  await p.domClick(`document.getElementById('tab-tests')`); await sleep(800);
  const expand = () => p.evaluate(`(() => { const v = document.getElementById('view-tests'); for (const d of v.querySelectorAll('details')) d.open = true; for (const b of [...v.querySelectorAll('[aria-expanded="false"]')].slice(0, 40)) b.click(); return true; })()`);
  if (!(await p.evaluate(`!!document.querySelector('#view-tests .cmd')`))) { await expand(); await sleep(1000); }
  const drawn = await p.waitFor(`!!document.querySelector('#view-tests .cmd')`, 8000);
  if (!drawn.ok) throw new Error('zakładka „Testy” bez polecenia biegu');
  await sleep(2500);   // jeden cykl odświeżenia: ostrzeżenie nie znika ani nie dochodzi po czasie
  return p.evaluate(`(() => { const v = document.getElementById('view-tests');
    const notes = [...document.querySelectorAll('[data-tests-raw]')];
    return { viewShown: !v.hidden && v.getClientRects().length > 0,
      warnings: notes.map((n) => ({ inTests: v.contains(n), visible: n.getClientRects().length > 0, text: n.textContent })),
      commands: [...v.querySelectorAll('.cmd')].map((e) => e.textContent + ' ' + (e.getAttribute('title') || '')),
      html: v.outerHTML }; })()`);
}
const saysRaw = (text) => text.includes('wyniki testów są surowe') && text.includes('tests.mask: on');
const named = (texts) => Object.entries(ctx.absolute).filter(([, value]) => texts.some((t) => t.includes(value))).map(([name]) => name);

if (!ctx.masked) {
  suite.add('ANON-4A', 'bez sesji: zakładka „Testy” pokazuje jedno widoczne ostrzeżenie, że wyniki są surowe', async (s) => {
    const p = await newPage(s.c, 'anon'); const seen = await testsTab(p); s.anon = seen;
    const user = (await sessionOf(p)).user || null;
    const w = seen.warnings;
    return { ok: !user && seen.viewShown && w.length === 1 && w[0].inTests && w[0].visible && saysRaw(w[0].text), user, warnings: w };
  });
  suite.add('ANON-4B', 'bez sesji: polecenie biegu w zakładce nie zawiera ścieżki bezwzględnej, a ścieżkę w projekcie pokazuje względnie', async ({ anon }) => {
    const leaked = named([...anon.commands, anon.html]);
    return { ok: anon.commands.length > 0 && leaked.length === 0 && anon.commands.every((c) => c.includes(ctx.inside)), commands: anon.commands, leaked };
  });
  suite.add('ANON-4C', 'zalogowany: bez ostrzeżenia; kontrola: to samo polecenie jest surowe, ze ścieżkami bezwzględnymi', async (s) => {
    const p = await newPage(s.c, 'approver'); await loginPage(p, ctx.page, 'approver');
    const seen = await testsTab(p);
    const found = named(seen.commands);
    return { ok: seen.viewShown && seen.warnings.length === 0 && found.length === Object.keys(ctx.absolute).length, warnings: seen.warnings, commands: seen.commands, found };
  });
} else {
  suite.add('ANON-4D', 'tests.mask: true: czytelnik bez sesji widzi zakładkę „Testy” bez ostrzeżenia, polecenie nadal bez ścieżek bezwzględnych', async (s) => {
    const p = await newPage(s.c, 'anon-masked'); const seen = await testsTab(p);
    const user = (await sessionOf(p)).user || null;
    const leaked = named([...seen.commands, seen.html]);
    return { ok: !user && seen.viewShown && seen.commands.length > 0 && seen.warnings.length === 0 && leaked.length === 0, user, warnings: seen.warnings, commands: seen.commands, leaked };
  });
}

await suite.main();
