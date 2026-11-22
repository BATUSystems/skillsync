// Srodowisko uruchomieniowe narzedzi obrazowych: katalog cache, ladowanie pngjs/pixelmatch, kodowanie PNG, flagger pixelmatch.
import path from 'node:path';
import nodeFs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { cacheDirFor, computeHash, defaultCacheRoot } from './paths.mjs';
import { createLoader, DepsError } from './deps.mjs';

// Katalog skilla = rodzic katalogu `scripts` (plik skryptu lezy w scripts/).
export function skillDirOf(importMetaUrl) {
  return path.dirname(path.dirname(fileURLToPath(importMetaUrl)));
}

export function resolveCacheDir({ skillDir, env = {}, platform = process.platform, homedir, fs = nodeFs }) {
  const contents = ['package.json', 'package-lock.json'].map((name) => {
    try {
      return String(fs.readFileSync(path.join(skillDir, name)));
    } catch {
      throw new DepsError('not-installed', `brak pliku skilla ${name}`, 'Zainstaluj skill ponownie (brak package.json lub package-lock.json).');
    }
  });
  return cacheDirFor(defaultCacheRoot({ platform, env, homedir }), computeHash(contents), platform);
}

export async function loadImaging({ cacheDir, env = {}, fs = nodeFs, requireFactory, importer }) {
  const opts = { cacheDir, env, fs };
  if (requireFactory) opts.requireFactory = requireFactory;
  if (importer) opts.importer = importer;
  const loader = createLoader(opts);
  const pngMod = await loader.load('pngjs');
  const pmMod = await loader.load('pixelmatch');
  const PNG = pngMod && pngMod.PNG ? pngMod.PNG : pngMod;
  const pixelmatch = pmMod && typeof pmMod.default === 'function' ? pmMod.default : pmMod;
  return { PNG, pixelmatch };
}

export function decodePng(PNG, buffer) {
  const p = PNG.sync.read(buffer);
  return { width: p.width, height: p.height, data: new Uint8ClampedArray(p.data) };
}

export function encodePng(PNG, img) {
  return Buffer.from(PNG.sync.write({ width: img.width, height: img.height, data: Buffer.from(img.data) }));
}

// Wektor 0/1: piksel rozny, gdy pixelmatch zapisal w wyjsciu dokladnie diffColor (255,0,0); AA (255,255,0) to nie roznica.
export function makePixelmatchFlagger(pixelmatch, { threshold = 0.1, includeAA = false } = {}) {
  return (a, b) => {
    const { width, height } = a;
    const output = new Uint8ClampedArray(width * height * 4);
    pixelmatch(a.data, b.data, output, width, height, {
      threshold,
      includeAA,
      alpha: 0,
      diffColor: [255, 0, 0],
      aaColor: [255, 255, 0],
    });
    const flags = new Uint8Array(width * height);
    for (let i = 0; i < flags.length; i++) {
      const o = i * 4;
      if (output[o] === 255 && output[o + 1] === 0 && output[o + 2] === 0) flags[i] = 1;
    }
    return flags;
  };
}
