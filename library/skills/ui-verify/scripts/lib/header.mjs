// Walidacja naglowka narzedzia: pierwszy blok komentarza z ustalonymi sekcjami w ustalonej kolejnosci.

export const HEADER_SECTIONS = ['Purpose', 'Usage', 'Inputs', 'Outputs', 'Exit codes', 'Dependencies', 'Limits', 'Security'];

export function parseHeader(sourceText) {
  const block = /\/\*[\s\S]*?\*\//.exec(String(sourceText ?? ''));
  const sections = [];
  if (block) {
    const re = new RegExp(`^\\s*\\*\\s*(${HEADER_SECTIONS.join('|')}):`, 'gm');
    let m;
    while ((m = re.exec(block[0])) !== null) sections.push(m[1]);
  }
  const missing = HEADER_SECTIONS.filter((s) => !sections.includes(s));
  const idx = sections.map((s) => HEADER_SECTIONS.indexOf(s));
  const order = idx.every((v, i) => i === 0 || v > idx[i - 1]);
  return { sections, ok: missing.length === 0 && order, missing, order };
}
