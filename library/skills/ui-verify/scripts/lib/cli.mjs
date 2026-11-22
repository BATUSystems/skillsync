// Minimalny parser argumentow CLI i runner selftestu.

export function parseArgs(argv, spec = {}) {
  const str = new Set([...(spec.string || []), ...(spec.repeatable || [])]);
  const bool = new Set(spec.boolean || []);
  const rep = new Set(spec.repeatable || []);
  const values = {};
  const positionals = [];
  const errors = [];
  const args = [...(argv || [])];
  for (let i = 0; i < args.length; i++) {
    const tok = args[i];
    if (tok === '--') { positionals.push(...args.slice(i + 1)); break; }
    if (!tok.startsWith('--')) { positionals.push(tok); continue; }
    const eq = tok.indexOf('=');
    const name = eq === -1 ? tok.slice(2) : tok.slice(2, eq);
    if (bool.has(name)) { values[name] = true; continue; }
    if (!str.has(name)) { errors.push(`unknown flag --${name}`); continue; }
    let val;
    if (eq !== -1) val = tok.slice(eq + 1);
    else if (i + 1 < args.length && !args[i + 1].startsWith('--')) val = args[++i];
    else { errors.push(`flag --${name} requires a value`); continue; }
    if (rep.has(name)) (values[name] ||= []).push(val);
    else values[name] = val;
  }
  return { values, positionals, errors };
}

// Zwraca 0 (wszystko PASS), 1 (jakis FAIL), 2 (brak przypadkow).
export async function runSelftest(name, cases, io) {
  const mask = io.masker ? (t) => io.masker.mask(String(t)) : (t) => String(t);
  if (!Array.isArray(cases) || cases.length === 0) {
    io.stderr.write(mask(`selftest ${name}: no cases\n`));
    return 2;
  }
  let failed = 0;
  for (const c of cases) {
    try {
      await c.fn();
      io.stdout.write(mask(`PASS ${c.name}\n`));
    } catch (err) {
      failed++;
      io.stdout.write(mask(`FAIL ${c.name}: ${err && err.message ? err.message : err}\n`));
    }
  }
  io.stdout.write(mask(`selftest ${name}: ${cases.length - failed}/${cases.length} passed\n`));
  return failed === 0 ? 0 : 1;
}
