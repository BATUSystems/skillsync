// Artefakty wrazliwe `capture --allow-trace` (trace.zip, storageState.json). Narzedzie ich NIE maskuje i niczego nie gwarantuje: trace Playwrighta
// niesie wartosci pol formularza (takze hasla z logowania), naglowki i tresci sieci, ciasteczka i tokeny, migawki DOM i klatki ekranu, a maska
// wyjscia zna tylko wartosci wskazanych zmiennych. Zamiast maskowania: tryb 0600, .gitignore w outDir (tylko gdy go nie bylo) i konkretne ostrzezenie.
import path from 'node:path';

export const SENSITIVE_MODE = 0o600;
export const GITIGNORE_BODY = 'trace.zip\nstorageState.json\n';

const hasBytes = (v) => v != null && v.length > 0;

function gitignoreNote(state, where) {
  if (state === 'created') return `Created ${where}${path.sep}.gitignore listing both files.`;
  if (state === 'exists') return `${where}${path.sep}.gitignore already exists and was not changed: make sure it lists trace.zip and storageState.json.`;
  return 'The output directory is the configuration directory, so no .gitignore was created: add trace.zip and storageState.json to .gitignore.';
}

// Zapisuje dostepne artefakty (art.trace, art.storageState) z trybem 0600 i zwraca { written, gitignore: 'none'|'created'|'exists'|'skipped', warning }.
// `.gitignore` powstaje tylko w podkatalogu konfiguracji (outRel niepusty) i tylko gdy nie istnieje (takze jako dowiazanie); istniejacego nie ruszamy.
export async function writeSensitiveArtifacts({ sink, fs, root, outRel, art }) {
  const written = [];
  let gitignore = 'skipped';
  try {
    for (const [name, data] of [['trace.zip', art?.trace], ['storageState.json', art?.storageState]]) {
      if (!hasBytes(data)) continue;
      await sink.writeFile(path.join(outRel, name), data, { mode: SENSITIVE_MODE });
      written.push(name);
    }
    if (written.length && outRel) {
      let exists = true;
      try {
        fs.lstatSync(path.join(root, outRel, '.gitignore'));
      } catch (e) {
        if (e.code !== 'ENOENT') throw e;
        exists = false;
      }
      if (exists) gitignore = 'exists';
      else {
        try {
          await sink.writeFile(path.join(outRel, '.gitignore'), GITIGNORE_BODY, { flag: 'wx' }); // 'wx': nigdy nie nadpisuj (wyscig z lstat)
          gitignore = 'created';
        } catch (e) {
          if (e.code !== 'EEXIST') throw e;
          gitignore = 'exists';
        }
      }
    }
  } catch (e) {
    // Blad po zapisie pierwszego pliku (takze przy zakladaniu .gitignore): wrazliwy plik juz lezy na dysku, a wyjatek skonczy bieg kodem 2 bez ostrzezenia: ostrzez przed rzuceniem dalej.
    if (written.length) await sink.write('stderr', `capture: warning: ${written.join(' and ')} was already written to ${outRel || '.'} before an error stopped --allow-trace; it can contain credentials and page data (see the warning text of a normal run). Do not commit or share it.\n`);
    throw e;
  }
  if (!written.length) return { written, gitignore: 'none', warning: null };
  const where = outRel || '.';
  const warning = `capture: warning: --allow-trace wrote ${written.join(' and ')} to ${where}. They can contain form field values typed during the run (including passwords), `
    + 'request and response headers and bodies, cookies and tokens, DOM snapshots and screen frames. The output mask does NOT cover them, and the absence of a known secret value '
    + `proves nothing. Do not commit or share them. ${gitignoreNote(gitignore, where)} Owner-only mode 0600 was requested (POSIX; not enforced on Windows).\n`;
  return { written, gitignore, warning };
}
