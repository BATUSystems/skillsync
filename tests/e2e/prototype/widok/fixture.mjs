// Dane prototypu testowego dla widok.mjs (D28b, B7a). Prototyp powstaje w katalogu roboczym biegu przez CLI `init`;
// skrypt zamienia ekran przykładowy szablonu na s0, dopisuje poniższe ekrany i style projektu. Treść jest fikcyjna.
//
// Ekrany (sekcja 14 scenariusze-e2e.md: co najmniej 3 ekrany z ramką, jeden bez ramki):
//   s0 — ekran przykładowy szablonu (data-prototype-example), z ramką;
//   s1 — ramka z [data-test-mobile-only] / [data-test-desktop-only] (media query < 600 px w project.css) i polem formularza;
//   s2 — ramka z blokiem `min-height: 100vh` (wysokość ramki bez pętli wzrostu) i przejściem data-goto;
//   s3 — niska ramka (jedna linia; K14: okienko komentarza wyższe niż ramka);
//   s4 — ekran BEZ ramki (K2: brak przełącznika, widok zawsze desktop).
//   s5 — powłoka szablonu `.app` > `.main` > `.page` z tabelą dużo wyższą niż stała wysokość `.app` i okno
//        (K3: wysokość według treści, bez przewijania w środku; D243: przewijać nie może się też powłoka).

export const SLUG = 'widok';
export const TITLE = 'Widok desktop i mobile';
export const REQUIREMENTS_PATH = 'docs/wymagania/widok.md';
export const REQUIREMENTS = '# Widok makiety\n\nPrototyp testowy E2E: widok desktop i mobile (D28b).\n';

export const PROJECT_CSS = `
/* Style projektu prototypu testowego widok (D28b, B7a). */
[data-test-mobile-only] { display: none; }
[data-test-desktop-only] { display: block; }
.wk-cols { display: flex; gap: 1rem; }
@media (max-width: 599px) {
  [data-test-mobile-only] { display: block; }
  [data-test-desktop-only] { display: none; }
  .wk-cols { flex-direction: column; }
}
.wk-tall { min-height: 100vh; border: 1px dashed var(--border); padding: 1rem; }
.wk-low { padding: 0.25rem 0.5rem; }
.wk-more[hidden] { display: none; }
.wk-more { height: 240px; }
`;

const meta = (n, title, desc) => `
    <header class="screen-meta">
      <h2 id="s${n}-h">${n}. ${title}</h2>
      <p>${desc}</p>
    </header>`;

const frame = (url, body) => `
    <div class="frame">
      <div class="frame-bar">
        <span class="frame-dots" aria-hidden="true"><span></span><span></span><span></span></span>
        <span class="frame-url">${url}</span>
      </div>
${body}
    </div>`;

export const SCREENS = `
  <section class="screen" id="s1" aria-labelledby="s1-h">${meta(1, 'Formularz', 'Układ zmienia się poniżej 600 px.')}${frame('https://szkola.example/plan', `
      <div class="page stack-4">
        <p data-test-mobile-only id="wk-m">Układ telefonu</p>
        <p data-test-desktop-only id="wk-d">Układ komputera</p>
        <div class="wk-cols">
          <label class="field">Nazwa <input class="input" id="wk-input" value="Plan zajęć"></label>
          <button type="button" class="btn btn-primary" data-goto="s2">Dalej</button>
        </div>
        <div class="wk-more" id="wk-more" hidden><p>Rozwinięta treść.</p></div>
      </div>`)}
    <ul class="notes"><li>Uwaga do ekranu 1: notatka zostaje szeroka w widoku mobile.</li></ul>
  </section>

  <section class="screen" id="s2" aria-labelledby="s2-h">${meta(2, 'Długa treść', 'Blok o wysokości 100vh.')}${frame('https://szkola.example/plan/dlugi', `
      <div class="page">
        <div class="wk-tall" id="wk-tall"><p>Treść wysoka na cały ekran.</p><button type="button" class="btn" data-goto="s1">Wróć</button></div>
      </div>`)}
  </section>

  <section class="screen" id="s3" aria-labelledby="s3-h">${meta(3, 'Niska ramka', 'Jedna linia treści.')}${frame('https://szkola.example/plan/niski', `
      <div class="page wk-low"><button type="button" class="btn" id="wk-low">Krótko</button></div>`)}
  </section>

  <section class="screen" id="s4" aria-labelledby="s4-h">${meta(4, 'Bez ramki', 'Ekran opisowy bez makiety.')}
    <ul class="notes"><li>Ten ekran nie ma ramki makiety.</li></ul>
  </section>

  <section class="screen" id="s5" aria-labelledby="s5-h">${meta(5, 'Długa lista', 'Treść ramki dużo wyższa niż okno.')}${frame('https://szkola.example/plan/lista', `
      <div class="app">
        <div class="main">
          <header class="topbar"><div class="topbar-left"><span class="crumbs">Zajęcia</span></div></header>
          <main class="page">
            <h3>Zajęcia</h3>
            <table class="table" id="wk-long">
              <thead><tr><th>Nr</th><th>Przedmiot</th><th>Sala</th></tr></thead>
              <tbody>
${Array.from({ length: 60 }, (_, i) => `                <tr><td>${i + 1}</td><td>Przedmiot ${i + 1}</td><td>Sala ${100 + i}</td></tr>`).join('\n')}
              </tbody>
            </table>
          </main>
        </div>
      </div>`)}
    <ul class="notes"><li>Ramka mobile ma wysokość całej tabeli, bez paska przewijania w środku.</li></ul>
  </section>
`;

export const NAV = `
    <a href="#s1">1 Formularz</a>
    <a href="#s2">2 Długa treść</a>
    <a href="#s3">3 Niska ramka</a>
    <a href="#s4">4 Bez ramki</a>
    <a href="#s5">5 Długa lista</a>`;
