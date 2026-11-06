// Pomocnik testu różnicowego Node <-> Python dla silnika v2 (testy Skillsync).
// Wywołanie: node diff.cjs <silnik.js> <wejście.json> <wynik.json>
//   wejście: {"folds": [[operacja, ...], ...], "labels": [{"logins": [...], "names": {login: nazwa}}, ...]}
//   wynik:   {"folds": [{"fold": {threads, currentIteration, removed, acceptance}, "ser": napis, "people": {...}}, ...],
//             "labels": [...]}
'use strict';
const fs = require('fs');
const [enginePath, inputPath, outputPath] = process.argv.slice(2);
const engine = require(enginePath);
const request = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const plainObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

function foldEntry(operations) {
  const folded = engine.foldDocument(operations);
  return {
    fold: {
      threads: folded.threads,
      currentIteration: folded.currentIteration,
      removed: folded.removed,
      acceptance: folded.acceptance,
    },
    ser: engine.serializeCommentsDocument('demo', operations.filter(plainObject)),
    people: engine.peopleFromOperations(operations),
  };
}

const output = {
  folds: request.folds.map(foldEntry),
  labels: request.labels.map((entry) => engine.computeLabels(entry.logins, entry.names || {})),
};
fs.writeFileSync(outputPath, JSON.stringify(output));
