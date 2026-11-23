// Kompilacja SCSS (LibSass przez sass.js) w pamieci: pliki *.scss pierwszego poziomu katalogu trafiaja do wirtualnego fs Sass.
// Symlinki nie sa podazane (lstat); komunikaty bez sciezek bezwzglednych.
import nodeFs from 'node:fs';
import path from 'node:path';

const NAME_RE = /^[A-Za-z0-9._-]+\.scss$/;
const MAX_ERROR = 4000;
const USAGE_CODES = new Set(['ENOENT', 'EACCES', 'ENOTDIR', 'EPERM']);

const clip = (s) => {
  const t = String(s);
  return t.length > MAX_ERROR ? t.slice(0, MAX_ERROR) : t;
};
const fail = (error, files = 0, warnings = []) => ({ ok: false, css: null, error: clip(error), files, warnings });
// Blad uzycia (brak/niedostepny katalog lub plik wejsciowy): CLI zamienia go na kod 2.
const usageFail = (error, warnings = []) => ({ ...fail(error, 0, warnings), usage: true });
const describe = (e) => `${(e && e.name) || 'Error'}: ${e && e.message ? e.message : String(e)}`;

export async function compileScss({ Sass, dir, entry, fs = nodeFs, maxFiles = 500, maxBytes = 2000000, maxTotalBytes = 16000000 }) {
  const warnings = [];
  try {
    let dirStat;
    try {
      dirStat = fs.lstatSync(dir);
    } catch (e) {
      if (e && USAGE_CODES.has(e.code)) return usageFail(`cannot read the SCSS directory (${e.code})`);
      throw e;
    }
    if (dirStat.isSymbolicLink()) return fail('the SCSS directory is a symbolic link (symlinks are not followed)');
    if (!dirStat.isDirectory()) return usageFail('the SCSS directory path is not a directory');

    let names;
    try {
      names = fs.readdirSync(dir);
    } catch (e) {
      if (e && USAGE_CODES.has(e.code)) return usageFail(`cannot read the SCSS directory (${e.code})`);
      throw e;
    }
    const accepted = [];
    for (const name of names) {
      const full = path.join(dir, name);
      let st = null;
      try {
        st = fs.lstatSync(full);
      } catch {
        st = null;
      }
      if (NAME_RE.test(name)) {
        if (st && st.isSymbolicLink()) return fail(`${name} is a symbolic link (symlinks are not followed)`, 0, warnings);
        if (st && st.isFile()) accepted.push({ name, full, size: st.size });
        continue;
      }
      if (!(st && st.isDirectory())) warnings.push(`skipped ${name}`);
    }
    if (typeof entry !== 'string' || !accepted.some((a) => a.name === entry)) {
      return usageFail(`entry file ${entry} is not a top-level .scss file in the SCSS directory`, warnings);
    }
    if (accepted.length > maxFiles) return fail(`too many SCSS files (${accepted.length} > ${maxFiles})`, accepted.length, warnings);
    let total = 0;
    for (const a of accepted) {
      if (a.size > maxBytes) return fail(`file ${a.name} is too large (${a.size} > ${maxBytes} bytes)`, accepted.length, warnings);
      total += a.size;
    }
    if (total > maxTotalBytes) return fail(`SCSS files are too large in total (${total} > ${maxTotalBytes} bytes)`, accepted.length, warnings);
    const loaded = [];
    for (const a of accepted) loaded.push({ name: a.name, content: String(fs.readFileSync(a.full)) });
    for (const l of loaded) {
      await new Promise((resolve) => {
        const ret = Sass.writeFile(l.name, l.content, () => resolve());
        if (ret === undefined || ret === true || ret === false) setImmediate(resolve);
      });
    }
    const r = await new Promise((resolve, reject) => {
      try {
        Sass.compileFile(entry, { style: Sass.style.expanded }, resolve);
      } catch (e) {
        reject(e);
      }
    });
    if (r && r.status === 0) return { ok: true, css: r.text == null ? '' : String(r.text), error: null, files: loaded.length, warnings };
    return fail((r && (r.formatted || r.message)) || 'SCSS compilation failed', loaded.length, warnings);
  } catch (e) {
    return fail(describe(e), 0, warnings);
  }
}
