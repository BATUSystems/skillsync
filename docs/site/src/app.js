// skillsync — agent flow docs: motyw, spis treści, kopiowanie, diagramy mermaid.
// MERMAID_URL wstawia build.py (przypięta wersja).
const THEME_KEY = "skillsync-docs-theme";
const root = document.documentElement;

function readTheme() {
  try { return localStorage.getItem(THEME_KEY) || "auto"; } catch { return "auto"; }
}
function saveTheme(value) {
  try { localStorage.setItem(THEME_KEY, value); } catch { /* prywatne okno: bez zapamiętania */ }
}
function isDark() {
  const set = root.getAttribute("data-theme");
  if (set === "dark") return true;
  if (set === "light") return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}
function applyTheme(value) {
  if (value === "auto") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", value);
  document.querySelectorAll("[data-theme-set]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.themeSet === value));
  });
}

applyTheme(readTheme());
document.querySelectorAll("[data-theme-set]").forEach((button) => {
  button.addEventListener("click", () => {
    const value = button.dataset.themeSet;
    saveTheme(value);
    applyTheme(value);
    renderDiagrams();
  });
});
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (!root.hasAttribute("data-theme")) renderDiagrams();
});

// Spis treści: podświetlenie bieżącej sekcji.
const tocLinks = new Map();
document.querySelectorAll(".toc a[href^='#']").forEach((link) => {
  tocLinks.set(decodeURIComponent(link.getAttribute("href").slice(1)), link);
});
const headings = [...document.querySelectorAll(".content h2[id], .content h3[id]")];
function markActive() {
  const offset = parseInt(getComputedStyle(root).scrollPaddingTop, 10) || 80;
  let current = headings[0];
  for (const heading of headings) {
    if (heading.getBoundingClientRect().top - offset <= 4) current = heading;
    else break;
  }
  tocLinks.forEach((link) => link.classList.remove("is-active"));
  const active = current && tocLinks.get(current.id);
  if (active) {
    active.classList.add("is-active");
    const parent = active.closest(".toc-sub")?.previousElementSibling;
    if (parent) parent.classList.add("is-active");
  }
}
let ticking = false;
window.addEventListener("scroll", () => {
  if (!ticking) {
    ticking = true;
    requestAnimationFrame(() => { markActive(); ticking = false; });
  }
}, { passive: true });
markActive();

// Na wąskim ekranie spis zwija się po wyborze pozycji.
const toc = document.querySelector(".toc");
document.querySelectorAll(".toc a").forEach((link) => {
  link.addEventListener("click", () => {
    if (window.matchMedia("(max-width: 960px)").matches && toc) toc.open = false;
  });
});
if (toc && window.matchMedia("(max-width: 960px)").matches) toc.open = false;

// Kopiowanie bloków kodu.
document.querySelectorAll(".content pre:not(.mermaid)").forEach((pre) => {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "copy";
  button.textContent = "Kopiuj";
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(pre.innerText.replace(/\nKopiuj$|^Kopiuj\n?/, ""));
      button.textContent = "Skopiowano";
    } catch {
      button.textContent = "Nie udało się";
    }
    setTimeout(() => { button.textContent = "Kopiuj"; }, 1600);
  });
  pre.appendChild(button);
});

document.querySelectorAll("[data-top]").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
});

// Diagramy: kolory z tokenów motywu, ponowne rysowanie po zmianie motywu.
const diagrams = [...document.querySelectorAll("pre.mermaid")];
diagrams.forEach((node) => { node.dataset.source = node.textContent; });
let mermaidModule = null;

function token(name) {
  return getComputedStyle(root).getPropertyValue(name).trim();
}

async function renderDiagrams() {
  if (!diagrams.length) return;
  try {
    if (!mermaidModule) mermaidModule = (await import(MERMAID_URL)).default;
  } catch {
    diagrams.forEach((node) => node.setAttribute("title", "Diagram w postaci tekstu: brak dostępu do biblioteki mermaid"));
    return;
  }
  const dark = isDark();
  mermaidModule.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: "base",
    fontFamily: "Inter, system-ui, sans-serif",
    flowchart: { curve: "basis", htmlLabels: true, useMaxWidth: true },
    themeVariables: {
      darkMode: dark,
      background: token("--surface"),
      primaryColor: token("--act-1-bg"),
      primaryBorderColor: token("--act-1"),
      primaryTextColor: token("--ink"),
      secondaryColor: token("--act-3-bg"),
      secondaryBorderColor: token("--act-3"),
      tertiaryColor: token("--act-2-bg"),
      tertiaryBorderColor: token("--act-2"),
      lineColor: token("--ink-3"),
      textColor: token("--ink"),
      mainBkg: token("--act-1-bg"),
      nodeBorder: token("--act-1"),
      clusterBkg: token("--bg-2"),
      edgeLabelBackground: token("--surface"),
      actorBkg: token("--act-3-bg"),
      actorBorder: token("--act-3"),
      actorTextColor: token("--ink"),
      signalColor: token("--ink-2"),
      signalTextColor: token("--ink"),
      labelBoxBkgColor: token("--act-2-bg"),
      labelBoxBorderColor: token("--act-2"),
      noteBkgColor: token("--act-2-bg"),
      noteBorderColor: token("--act-2"),
      fontSize: "15px",
    },
  });
  diagrams.forEach((node) => {
    node.removeAttribute("data-processed");
    node.textContent = node.dataset.source;
  });
  try {
    await mermaidModule.run({ nodes: diagrams });
  } catch (error) {
    console.error("mermaid", error);
  }
}

renderDiagrams();
