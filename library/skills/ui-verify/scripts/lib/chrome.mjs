// Wykrywanie przegladarki Chrome/Chromium i opcje uruchomienia.
import path from 'node:path';

const LINUX_NAMES = ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser'];
const DARWIN_CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

function nonEmpty(v) {
  return typeof v === 'string' && v !== '' ? v : null;
}

export function findChrome({ platform, env = {}, fs, which }) {
  const tried = [];
  const found = (p, source) => ({ path: p, tried, source });

  for (const name of ['UI_VERIFY_CHROME', 'CHROME_EXECUTABLE_PATH']) {
    const v = nonEmpty(env[name]);
    if (!v) continue;
    tried.push(v);
    if (fs.existsSync(v)) return found(v, name);
  }

  if (platform === 'darwin') {
    tried.push(DARWIN_CHROME);
    if (fs.existsSync(DARWIN_CHROME)) return found(DARWIN_CHROME, 'system');
  } else if (platform === 'win32') {
    const w = path.win32;
    const chrome = (b) => w.join(b, 'Google', 'Chrome', 'Application', 'chrome.exe');
    const edge = (b) => w.join(b, 'Microsoft', 'Edge', 'Application', 'msedge.exe');
    const cands = [];
    for (const k of ['PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA']) {
      const b = nonEmpty(env[k]);
      if (b) cands.push(chrome(b));
    }
    for (const k of ['PROGRAMFILES(X86)', 'PROGRAMFILES']) {
      const b = nonEmpty(env[k]);
      if (b) cands.push(edge(b));
    }
    for (const c of cands) {
      tried.push(c);
      if (fs.existsSync(c)) return found(c, 'system');
    }
  } else {
    for (const name of LINUX_NAMES) {
      tried.push(name);
      const p = which(name);
      if (p) return found(p, 'system');
    }
  }
  return { path: null, tried, source: null };
}

export function launchOptions({ chromePath, env = {} }) {
  const noSandbox = env.UI_VERIFY_NO_SANDBOX === '1';
  return {
    executablePath: chromePath,
    chromiumSandbox: !noSandbox,
    headless: true,
    args: noSandbox ? ['--no-sandbox'] : [],
  };
}

export function chromeMissing(res) {
  const lines = ['Nie znaleziono przegladarki Chrome/Chromium (kod 3, NOT_RUN).'];
  const tried = (res && res.tried) || [];
  if (tried.length) {
    lines.push('Sprawdzone miejsca:');
    for (const t of tried) lines.push(`  - ${t}`);
  }
  lines.push('Wskaz przegladarke zmienna UI_VERIFY_CHROME (sciezka do pliku wykonywalnego).');
  return { exit: 3, lines };
}
