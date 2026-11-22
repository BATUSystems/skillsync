// Kompilacja SCSS (LibSass przez sass.js) w pamieci: pliki *.scss pierwszego poziomu katalogu trafiaja do wirtualnego fs Sass.
import nodeFs from 'node:fs';
import path from 'node:path';

const NAME_RE = /^[A-Za-z0-9._-]+\.scss$/;
const MAX_ERROR = 4000;

const clip = (s) => {
  const t = String(s);
  return t.length > MAX_ERROR ? t.slice(0, MAX_ERROR) : t;
};
const fail = (error, files = 0, warnings = []) => ({ ok: false, css: null, error: clip(error), files, warnings });
const describe = (e) => `${(e && e.name) || 'Error'}: ${e && e.message ? e.message : String(e)}`;

export async function compileScss({ Sass, dir, entry, fs = nodeFs, maxFiles = 500, maxBytes = 2000000 }) {
  const warnings = [];
  try {
    const accepted = [];
    for (const name of fs.readdirSync(dir)) {
      const full = path.join(dir, name);
      let isFile = false;
      if (NAME_RE.test(name)) {
        try {
          isFile = fs.statSync(full).isFile();
        } catch {
          isFile = false;
        }
        if (isFile) accepted.push({ name, full });
        continue;
      }
      let dirEntry = false;
      try {
        dirEntry = !fs.statSync(full).isFile();
      } catch {
        dirEntry = false;
      }
      if (!dirEntry) warnings.push(`pominieto plik o niedozwolonej nazwie: ${name}`);
    }
    if (typeof entry !== 'string' || !accepted.some((a) => a.name === entry)) {
      return fail(`plik wejsciowy ${entry} nie jest plikiem .scss pierwszego poziomu katalogu`, 0, warnings);
    }
    if (accepted.length > maxFiles) return fail(`za duzo plikow scss (${accepted.length} > ${maxFiles})`, accepted.length, warnings);
    const loaded = [];
    for (const a of accepted) {
      const size = fs.statSync(a.full).size;
      if (size > maxBytes) return fail(`plik ${a.name} jest za duzy (${size} > ${maxBytes} bajtow)`, accepted.length, warnings);
      loaded.push({ name: a.name, content: String(fs.readFileSync(a.full)) });
    }
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
    return fail((r && (r.formatted || r.message)) || 'kompilacja scss nie powiodla sie', loaded.length, warnings);
  } catch (e) {
    return fail(describe(e), 0, warnings);
  }
}
