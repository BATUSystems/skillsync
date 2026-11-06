# Wersja HTML dokumentacji workflow

`build.py` buduje z [../workflow.md](../workflow.md) i [../workflow-details.md](../workflow-details.md) dwie strony HTML
do otwarcia w przeglądarce: z przełącznikiem motywu (auto, jasny, ciemny), spisem treści z podświetlaniem bieżącej
sekcji, kolorowymi statusami, przyciskiem „Kopiuj” przy blokach kodu i diagramami mermaid w kolorach motywu.

Źródłem treści są pliki Markdown. Stron HTML nie edytuje się ręcznie i nie trzyma w gicie: `out/` jest w `.gitignore`,
a w publicznym repo zbuduje je CI do GitHub Pages.

## Budowanie

Wymagania: Python 3.10 lub nowszy i python-markdown 3.5 lub nowszy (sprawdzone na 3.5.2).

```bash
python3 -m pip install "markdown>=3.5"   # tylko do budowania dokumentacji, nie do działania skillsync
python3 docs/site/build.py               # wynik: docs/site/out/workflow.html, docs/site/out/workflow-details.html
python3 docs/site/build.py --out /inny/katalog
python3 docs/site/build.py --require-denylist   # przed publikacją: bez listy lokalnej strażnika przerwij
```

Skrypt działa na Linuksie, macOS i Windowsie: ścieżki przez `pathlib`, zapis w UTF-8 z końcami linii LF, komunikaty na
stdout w UTF-8 także przy przekierowaniu. Brak pakietu `markdown`, za stara wersja albo brak listy lokalnej przy
`--require-denylist` kończą się komunikatem i kodem 2, a problem z treścią kodem 1 (wtedy nic nie jest zapisywane).

Strony są samodzielnymi plikami: styl i skrypt są wbudowane z `src/style.css` i `src/app.js`. Z sieci pobierane są
tylko fonty (Google Fonts) i biblioteka mermaid w przypiętej wersji (`MERMAID_URL` w `build.py`). Bez sieci strona działa
z fontami systemowymi, a diagramy pozostają czytelnym tekstem źródłowym.

## Kontrole przed zapisem

- **Strażnik treści**: sprawdza oba dokumenty i pliki źródłowe z `docs/site/` (`.py`, `.md`, `.css`, `.js`). Wbudowane
  są tylko wzorce ogólne: lokalne ścieżki użytkownika (katalog domowy, prywatne katalogi narzędzi; publiczne
  `~/.config/skillsync/` jest dozwolone). Wzorce zależne od organizacji (nazwy klientów, projektów, persony) trzyma lista
  lokalna poza repo: plik wskazany zmienną `SKILLSYNC_DOCS_DENYLIST` albo `~/.config/skillsync/docs-denylist.txt`, jedno
  wyrażenie regularne na linię, `#` to komentarz. Trafienie podaje plik, linię i numer wzorca, bez cytowania treści. Brak
  listy lokalnej to ostrzeżenie na stderr przed budową, a z `--require-denylist` błąd.
- **Kotwice**: każdy odnośnik `#…` w obrębie stron musi trafiać w istniejący nagłówek (identyfikatory jak na GitHubie,
  z polskimi znakami).
- **Diagramy**: każdy blok ` ```mermaid ` trafia na swoje miejsce w treści.

Odnośniki do innych dokumentów z `docs/` prowadzą do plików Markdown (`../../<plik>.md` względem `out/`).

## Po zmianie

Zmiana `workflow.md`, `workflow-details.md`, `src/style.css` albo `src/app.js`: uruchom `build.py` i obejrzyj obie strony
w przeglądarce na szerokim i wąskim oknie, w jasnym i ciemnym motywie.
