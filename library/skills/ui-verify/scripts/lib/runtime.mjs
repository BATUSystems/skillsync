// Srodowisko uruchomieniowe narzedzi obrazowych: katalog cache, ladowanie pngjs/pixelmatch, kodowanie PNG, flagger pixelmatch.
import path from 'node:path';
import nodeFs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { cacheDirFor, computeHash, defaultCacheRoot } from './paths.mjs';
import { createLoader, DepsError } from './deps.mjs';
import { checkCacheSafety } from './install-core.mjs';

// Katalog skilla = rodzic katalogu `scripts` (plik skryptu lezy w scripts/).
export function skillDirOf(importMetaUrl) {
  return path.dirname(path.dirname(fileURLToPath(importMetaUrl)));
}

// Ta sama kontrola co przy instalacji (checkCacheSafety), zanim kod z cache zostanie zaladowany; blad to DepsError z kodem 2.
export function assertCacheSafe({ root, skillDir, platform = process.platform, fs = nodeFs }) {
  try {
    checkCacheSafety({ root, skillDir, platform, fs });
  } catch (e) {
    if (e && e.name === 'InstallError') {
      throw new DepsError('unsafe-cache', e.message, 'Wskaz katalog cache nalezacy do biezacego uzytkownika, bez zapisu dla grupy i innych, poza katalogiem skilla.', 2);
    }
    throw e;
  }
}

export function resolveCacheDir({ skillDir, env = {}, platform = process.platform, homedir, fs = nodeFs }) {
  const override = typeof env.UI_VERIFY_CACHE_DIR === 'string' ? env.UI_VERIFY_CACHE_DIR : '';
  if (override !== '' && !(platform === 'win32' ? path.win32 : path.posix).isAbsolute(override)) {
    throw new DepsError('unsafe-cache', 'UI_VERIFY_CACHE_DIR must be an absolute path', 'Ustaw UI_VERIFY_CACHE_DIR na sciezke bezwzgledna.', 2);
  }
  const contents = ['package.json', 'package-lock.json'].map((name) => {
    try {
      return String(fs.readFileSync(path.join(skillDir, name)));
    } catch {
      throw new DepsError('not-installed', `brak pliku skilla ${name}`, 'Zainstaluj skill ponownie (brak package.json lub package-lock.json).');
    }
  });
  const root = defaultCacheRoot({ platform, env, homedir });
  assertCacheSafe({ root, skillDir, platform, fs });
  return cacheDirFor(root, computeHash(contents), platform);
}

export async function loadImaging({ cacheDir, env = {}, fs = nodeFs, requireFactory, importer, skillDir, platform = process.platform }) {
  assertCacheSafe({ root: (platform === 'win32' ? path.win32 : path.posix).dirname(cacheDir), skillDir, platform, fs });
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

// sass.js (LibSass) zawsze z cache zestawu, po tej samej kontroli bezpieczenstwa co loadImaging.
export async function loadSass({ cacheDir, env = {}, fs = nodeFs, requireFactory, importer, skillDir, platform = process.platform }) {
  assertCacheSafe({ root: (platform === 'win32' ? path.win32 : path.posix).dirname(cacheDir), skillDir, platform, fs });
  const opts = { cacheDir, env, fs };
  if (requireFactory) opts.requireFactory = requireFactory;
  if (importer) opts.importer = importer;
  return createLoader(opts).load('sass.js');
}

// Limity dekodera PNG (bomba dekompresyjna): pngjs wylicza rozmiar danych z naglowka IHDR (a przy przeplocie Adam7 rozpakowuje bez zadnego limitu),
// wiec naglowki trzeba sprawdzic PRZED dekodowaniem. 16384 na bok to limit tekstury Chrome; 50 M px to ok. 200 MB bitmapy RGBA 8-bit (przy glebi
// 16 bit szczyt pamieci pngjs jest kilkukrotnie wiekszy: rozpakowane dane, dane po filtrach, bitmapa i kopia w Uint8ClampedArray, razem okolo 1 GB).
export const MAX_PNG_BYTES = 64 * 1024 * 1024;
export const MAX_PNG_DIMENSION = 16384;
export const MAX_PNG_PIXELS = 50_000_000;

// Odmowa PRZED dekodowaniem: rozmiar, wymiary albo ksztalt pliku, ktorego dekoder nie moze bezpiecznie przetworzyc.
export class ImageLimitError extends Error {
  constructor(message) {
    super(message);
    this.name = 'ImageLimitError';
  }
}

const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const TYPE_IHDR = 0x49484452;
const TYPE_IEND = 0x49454e44;

// Plik wiekszy niz limit bajtow nie jest nawet wczytywany: wolane z rozmiaru pliku (fs.statSync) przed readFileSync.
export function assertPngFileSize(size, { maxBytes = MAX_PNG_BYTES } = {}) {
  if (size > maxBytes) throw new ImageLimitError(`PNG is ${size} bytes, over the limit of ${maxBytes} bytes; refusing to read it`);
}

// Przechodzi po naglowkach chunkow (8 B na chunk, bez dekodowania) az do IEND albo konca bufora i odmawia, gdy:
//  - bufor jest za duzy, pierwszy chunk nie jest IHDR o dlugosci 13, pojawia sie drugi IHDR (pngjs przyjmuje je i bierze OSTATNI), wymiar to 0,
//    bok przekracza limit albo liczba pikseli przekracza limit, albo IHDR deklaruje przeplot (Adam7: pngjs rozpakowuje wtedy bez limitu).
// Bufor bez sygnatury PNG idzie do pngjs, ktory odrzuca go przed jakimkolwiek przydzialem (parser: _parseSignature). Chunk urwany przez koniec bufora
// konczy przejscie: pngjs nie dostanie z niego danych, wiec i nic nie przydzieli.
function assertPngWithinLimits(buffer, { maxBytes, maxDimension, maxPixels }) {
  if (!Buffer.isBuffer(buffer)) buffer = Buffer.from(buffer.buffer, buffer.byteOffset, buffer.byteLength); // Uint8Array bez kopii
  if (buffer.length > maxBytes) throw new ImageLimitError(`PNG is ${buffer.length} bytes, over the limit of ${maxBytes} bytes; refusing to decode`);
  if (buffer.length < 8 || !buffer.subarray(0, 8).equals(PNG_SIGNATURE)) return;
  let pos = 8;
  let ihdrSeen = false;
  while (pos + 8 <= buffer.length) {
    const length = buffer.readUInt32BE(pos);
    const type = buffer.readUInt32BE(pos + 4);
    if (!ihdrSeen && (type !== TYPE_IHDR || length !== 13)) {
      throw new ImageLimitError('PNG does not start with a standard IHDR chunk (length 13); refusing to decode');
    }
    if (type === TYPE_IHDR) {
      if (ihdrSeen) throw new ImageLimitError('PNG has more than one IHDR chunk; refusing to decode');
      if (pos + 8 + 13 > buffer.length) return; // IHDR urwany: nie ma z czego odczytac wymiarow, a pngjs niczego nie przydzieli
      ihdrSeen = true;
      const width = buffer.readUInt32BE(pos + 8);
      const height = buffer.readUInt32BE(pos + 12);
      if (width === 0 || height === 0) throw new ImageLimitError(`PNG header declares ${width}x${height} pixels; refusing to decode`);
      if (width > maxDimension || height > maxDimension) {
        throw new ImageLimitError(`PNG header declares ${width}x${height} pixels, over the limit of ${maxDimension} pixels per side; refusing to decode`);
      }
      if (width * height > maxPixels) {
        throw new ImageLimitError(`PNG header declares ${width}x${height} pixels (${width * height}), over the limit of ${maxPixels} pixels; refusing to decode`);
      }
      if (buffer[pos + 8 + 12] !== 0) throw new ImageLimitError('PNG is interlaced (Adam7), which the decoder cannot bound; refusing to decode');
    }
    if (type === TYPE_IEND) return;
    const next = pos + 12 + length; // dlugosc + typ + dane + CRC
    if (next > buffer.length) return;
    pos = next;
  }
}

export function decodePng(PNG, buffer, limits = {}) {
  assertPngWithinLimits(buffer, {
    maxBytes: limits.maxBytes ?? MAX_PNG_BYTES,
    maxDimension: limits.maxDimension ?? MAX_PNG_DIMENSION,
    maxPixels: limits.maxPixels ?? MAX_PNG_PIXELS,
  });
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
