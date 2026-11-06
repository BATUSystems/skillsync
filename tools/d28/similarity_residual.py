#!/usr/bin/env python3
"""Podobienstwo rezydualne (D124): czesc wspolnych 5-gramow nowego i starego pliku, ktorej nie tlumacza zrodla kontraktu.

Strumien porownywany to identyfikatory, napisy i liczby (bez interpunkcji), wspolny dla JS, Pythona i Markdown, dzieki czemu
n-gramy ze zrodel kontraktu w innych jezykach (fold w Pythonie, testy, spec, docs kontraktu) mozna odjac od czesci wspolnej.

S_res = |G(N) & G(O) - G(K)| / |G(N)|, L_res = najdluzszy wspolny ciag N i O, w ktorym zaden 5-gram nie nalezy do G(K).
PASS, gdy S_res <= 0,10 i L_res < 15. Wynik w JSON (z surowymi S i L z similarity.py jako informacja).
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import similarity  # noqa: E402

NGRAM = 5
MAX_S = 0.10
MAX_L = 15
_TOKEN = re.compile(r"""[A-Za-z_$][A-Za-z0-9_$]*|'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"|`[^`]*`|\d+(?:\.\d+)?""")


def stream(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    out = []
    for tok in _TOKEN.findall(text):
        if tok[0] in "'\"`":
            tok = "'" + " ".join(tok[1:-1].split()) + "'"
        out.append(tok)
    return out


def grams(seq):
    return {tuple(seq[i:i + NGRAM]) for i in range(len(seq) - NGRAM + 1)}


def longest_outside(a, b, contract):
    """Najdluzszy wspolny ciag a i b (DP po pozycjach), ktorego zadne 5-gramowe okno nie jest w kontrakcie."""
    best = 0
    where = 0
    prev = {}
    index = {}
    for j, tok in enumerate(b):
        index.setdefault(tok, []).append(j)
    for i in range(len(a)):
        cur = {}
        for j in index.get(a[i], ()):
            run = prev.get(j - 1, 0) + 1
            if run >= NGRAM and tuple(a[i - NGRAM + 1:i + 1]) in contract:
                run = NGRAM - 1  # okno kontraktowe przerywa ciag; zostaje ogon krotszy od 5-gramu
            cur[j] = run
            if run > best:
                best, where = run, i
        prev = cur
    return best, a[where - best + 1:where + 1]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("new")
    ap.add_argument("old")
    ap.add_argument("--contract", nargs="+", required=True, help="pliki zrodel kontraktu (dozwolone do czytania)")
    args = ap.parse_args(argv)
    a, b = stream(args.new), stream(args.old)
    contract = set()
    for path in args.contract:
        contract |= grams(stream(path))
    ga, gb = grams(a), grams(b)
    shared = ga & gb
    residual = shared - contract
    s_res = len(residual) / len(ga) if ga else 0.0
    l_res, run = longest_outside(a, b, contract)
    raw = similarity.compare(args.new, args.old)
    result = {
        "new": args.new, "old": args.old, "S_res": round(s_res, 6), "L_res": l_res,
        "status": "PASS" if s_res <= MAX_S and l_res < MAX_L else "FAIL",
        "shared": len(shared), "residual": len(residual), "contract_explained": len(shared) - len(residual),
        "raw": {k: raw[k] for k in ("S", "L", "status") if k in raw},
        "longest_run_len": l_res,
    }
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
