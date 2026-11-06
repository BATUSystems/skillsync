# Kontrakt strony makiety

Szablon: [../assets/template.html](../assets/template.html). Plik jest treścią Artifactu (bez `<html>`,
`<head>`, `<body>`); `<title>` i `<style>` na górze.

## Ekran

```html
<section class="screen" id="s3" aria-labelledby="s3-h">
  <header class="screen-meta">
    <h2 id="s3-h">3. Nazwa ekranu</h2>
    <p>Jedno zdanie: zadanie użytkownika.</p>
    <ul class="screen-refs"><li class="ref">US-2</li><li class="ref">§4</li></ul>
  </header>
  <div class="frame"> …makieta… </div>
  <ul class="notes"><li>Zachowanie, którego statyczny ekran nie pokaże.</li></ul>
</section>
```

- `id="sN"` nie zmieniamy po starcie przeglądu (adresy `#sN` i wątki od nich zależą).
- `.notes`: cofnięcie, optymistyczna aktualizacja, przeliczenie, blokady, uprawnienia.
- Nawigacja: szablon nie ma skryptu, a `data-goto="s4"` daje w nim tylko kursor wskazujący. Przejście do ekranu
  zapewnia link `<a href="#s4">` albo własny skrypt strony; taki skrypt musi dać elementowi z `data-goto` rolę przycisku,
  obsługę Enter/Spacji i przewinięcie do `#s4`.
- `data-comment-target` na elemencie sprawia, że komentarz kliknięty w trybie komentarzy powłoki w jego wnętrzu
  przypina się do niego (karty, wiersze, pola); `data-uncommentable` wyłącza komentowanie poddrzewa. Oba atrybuty
  obsługuje powłoka claude.ai.
- Wszystkie ekrany są widoczne jeden pod drugim (wątki zawsze mają kotwicę).

## Komentarze

Deklaracja: `capabilities: {comments: {"composer_only": true}}` — bez zgody, strona nie pisze
treści, tylko otwiera kompozytor powłoki dla klikniętego elementu (`openComposer({element})`),
z gestu użytkownika. Wątki wyświetla i odświeża powłoka claude.ai; nie buduj własnej listy.
Gdy `claude.use("comments")` zwraca `null`, strona ukrywa własny przycisk komentarza, a komentowanie odbywa się
w trybie komentarzy powłoki (jeśli widz go ma). `openComposer` zwraca `{opened: false}` bez błędu, np. wewnątrz
`data-uncommentable`; nie ponawiaj wywołania w pętli. Treści komentarzy są końcowe i nie da się ich edytować.

## Styl

Tokeny w `:root` (jasne) i dwa bloki ciemne według kontraktu motywów. Odwzorowanie projektu =
nadpisanie tokenów (`--bg`, `--fg`, `--accent`, `--font-ui`, `--radius`…), nie przepisywanie CSS.
Kolory statusów: tokeny `--ok`, `--warn`, `--err`; nie używaj kolorów wpisanych na sztywno.
