// Jedno gardlo wyjscia: maskowanie sekretow i zapis plikow/strumieni tylko pod root.
import path from 'node:path';
import { assertRealInside, resolveInside } from './policy.mjs';

const MASK = '***';

function variantsOf(secret) {
  const b64 = Buffer.from(secret).toString('base64');
  const set = new Set([
    secret,
    encodeURIComponent(secret),
    JSON.stringify(secret).slice(1, -1),
    b64,
    b64.replace(/=+$/, ''),
    Buffer.from(secret).toString('base64url'),
  ]);
  return [...set].filter((v) => v.length >= 4);
}

export function createMasker({ secrets = [], extra = [] } = {}) {
  const valid = [...new Set((secrets || []).filter((s) => typeof s === 'string' && s.length >= 4))];
  const variants = [...new Set(valid.flatMap(variantsOf))].sort((a, b) => b.length - a.length);
  const extras = (extra || []).map((r) => (r.global ? r : new RegExp(r.source, r.flags + 'g')));

  const mask = (text) => {
    let s = String(text);
    for (const v of variants) s = s.split(v).join(MASK);
    for (const r of extras) { r.lastIndex = 0; s = s.replace(r, MASK); }
    return s;
  };

  const seen = new WeakMap();
  const maskValue = (v) => {
    if (typeof v === 'string') return mask(v);
    if (v === null || typeof v !== 'object') return v;
    if (ArrayBuffer.isView(v)) return v;
    if (seen.has(v)) return seen.get(v);
    if (v instanceof Error) {
      const e = new Error(mask(v.message));
      seen.set(v, e);
      e.name = v.name;
      e.stack = mask(v.stack ?? '');
      if (v.code !== undefined) e.code = maskValue(v.code);
      return e;
    }
    if (Array.isArray(v)) {
      const arr = [];
      seen.set(v, arr);
      for (const x of v) arr.push(maskValue(x));
      return arr;
    }
    const o = {};
    seen.set(v, o);
    for (const [k, x] of Object.entries(v)) o[mask(k)] = maskValue(x);
    return o;
  };

  return { mask, maskValue, secrets: valid.length };
}

// Wspolny masker dla handlera globalnego (installGuards): domyslnie wartosci zmiennych z UI_VERIFY_SECRET_ENV
// w process.env, plus masker ustawiony przez skrypt (sekrety z konfiguracji).
let guardMasker = null;

export function setGuardMasker(masker) {
  guardMasker = masker && typeof masker.mask === 'function' ? masker : null;
}

export function guardMask(text) {
  const names = String(process.env.UI_VERIFY_SECRET_ENV || '').split(',').map((n) => n.trim()).filter(Boolean);
  let out = createMasker({ secrets: collectSecretsFromEnv(process.env, names) }).mask(text);
  if (guardMasker) out = guardMasker.mask(out);
  return out;
}

export function createSink({ masker, fs, root, stdout, stderr }) {
  const streams = { stdout, stderr };
  return {
    // opts.mode (np. 0o600): tryb pliku przy tworzeniu, chmod PRZED zapisem pliku, ktory juz istnial (zeby nowa tresc nie trafila do pliku czytelnego dla innych),
    // i chmod po zapisie; opts.flag (np. 'wx': nie nadpisuj) idzie do zapisu. Bez opcji zapis jak dotad.
    async writeFile(rel, data, opts = {}) {
      const abs = resolveInside(root, masker.mask(rel));
      assertRealInside(root, abs, fs);
      const body = typeof data === 'string' ? masker.mask(data) : data;
      const { mode, flag } = opts;
      const writeOpts = mode == null && flag == null ? undefined : { ...(mode == null ? {} : { mode }), ...(flag == null ? {} : { flag }) };
      const absent = (e) => e && e.code === 'ENOENT';
      if (fs.promises) {
        await fs.promises.mkdir(path.dirname(abs), { recursive: true });
        if (mode != null) await fs.promises.chmod(abs, mode).catch((e) => { if (!absent(e)) throw e; });
        await fs.promises.writeFile(abs, body, writeOpts);
        if (mode != null) await fs.promises.chmod(abs, mode);
      } else {
        fs.mkdirSync(path.dirname(abs), { recursive: true });
        if (mode != null) { try { fs.chmodSync(abs, mode); } catch (e) { if (!absent(e)) throw e; } }
        fs.writeFileSync(abs, body, writeOpts);
        if (mode != null) fs.chmodSync(abs, mode);
      }
      return abs;
    },
    async write(stream, text) {
      const target = streams[stream];
      if (!target) throw new Error(`unknown stream: ${stream}`);
      target.write(masker.mask(text));
    },
    async error(err) {
      const e = masker.maskValue(err instanceof Error ? err : new Error(String(err)));
      stderr.write(masker.mask(`${e.stack || e.message}\n`));
    },
  };
}

export function collectSecretsFromEnv(env, names) {
  return (names || []).map((n) => env?.[n]).filter((v) => typeof v === 'string' && v !== '');
}
