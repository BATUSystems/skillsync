// Pomocnik testów Skillsync: czyste funkcje silnika v2 dla kolejki offline, importu v1 i klasyfikacji zapisu.
// Wywołanie: node queue.cjs <silnik.js> <wejście.json> <wynik.json>
//   wejście: {"calls": [{"fn": "mergeQueueItems" | "classifyWriteResult" | "convertV1Operations", "args": [...]}]}
//   convertV1Operations: args = [operacje, {knownIds: [...], knownThreads: [...], deterministic: bool}];
//   nowe id mają postać <prefiks>-1, <prefiks>-2, …
'use strict';
const fs = require('fs');
const [enginePath, inputPath, outputPath] = process.argv.slice(2);
const engine = require(enginePath);
const request = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const asLookup = (keys) => Object.fromEntries((keys || []).map((key) => [key, true]));

function convertV1(operations, options) {
  let issued = 0;
  const context = {
    knownIds: asLookup(options.knownIds),
    knownThreads: asLookup(options.knownThreads),
    newId: (prefix) => `${prefix}-${++issued}`,
  };
  if (options.deterministic) context.idFor = engine.v1ImportId;
  return engine.convertV1Operations(operations, context);
}

const results = request.calls.map(({ fn, args }) =>
  fn === 'convertV1Operations' ? convertV1(args[0], args[1] || {}) : engine[fn](...args));
fs.writeFileSync(outputPath, JSON.stringify(results));
