// Sonda układu: jedna samodzielna deklaracja wstrzykiwana do strony prototypu.
// Potok: walidacja opcji -> zbieranie surowych rekordów (ramki jako [x, y, w, h]) -> tabela reguł -> złożenie przez report().
async function layoutProbe(options) {
  // ---- cienkie opakowania DOM (jedyne miejsce, które go dotyka) ----
  const dom = {
    first: (sel, root) => (root || document).querySelector(sel),
    many: (sel, root) => Array.from((root || document).querySelectorAll(sel)),
    attr: (el, name) => (el ? el.getAttribute(name) : null),
    text: (el) => ((el && el.textContent) || '').replace(/\s+/g, ' ').trim(),
    box: (el) => {
      const r = el.getBoundingClientRect();
      return [r.x, r.y, r.width, r.height];
    },
    topAt: (x, y) => document.elementFromPoint(x, y),
    scrollY: () => window.scrollY,
    win: () => [window.innerWidth, window.innerHeight],
    cssVar: (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim(),
    frame: () => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))),
  };

  // ---- drobne pomocnicze funkcje liczbowe ----
  const snap = (n, digits) => {
    const k = Math.pow(10, digits || 0);
    return Math.round(n * k) / k;
  };
  const bottomOf = (b) => b[1] + b[3];
  const rightOf = (b) => b[0] + b[2];
  const centerOf = (b) => [b[0] + b[2] / 2, b[1] + b[3] / 2];
  const hasArea = (b) => b[2] > 0 && b[3] > 0;
  const inWindow = (pt) => {
    const [w, h] = dom.win();
    return pt[0] >= 0 && pt[1] >= 0 && pt[0] < w && pt[1] < h;
  };
  const isInside = (hit, el) => !!hit && (hit === el || el.contains(hit));
  const fail = (error) => ({ ok: false, error });

  // ---- warstwa 1: walidacja opcji ----
  function readOptions(raw) {
    const o = raw === undefined || raw === null ? {} : raw;
    if (typeof o !== 'object' || Array.isArray(o)) return { problem: 'Opcje muszą być obiektem' };
    if (o.target !== undefined && typeof o.target !== 'string') return { problem: 'target musi być napisem (np. "s3")' };
    // Identyfikator ekranu może zaczynać się od cyfry, więc selektor budujemy z atrybutu, a nie z `#id`;
    // dozwolone są tylko znaki bezpieczne w napisie selektora, reszta to czytelny błąd zamiast cichego przycinania.
    if (typeof o.target === 'string' && o.target !== '' && !/^[\w-]+$/.test(o.target)) {
      return { problem: 'target może zawierać tylko litery, cyfry, "_" i "-" (np. "s3"), dostałem: ' + JSON.stringify(o.target) };
    }
    if (o.focusMargin !== undefined && !(typeof o.focusMargin === 'number' && isFinite(o.focusMargin))) {
      return { problem: 'focusMargin musi być liczbą' };
    }
    return {
      cfg: {
        target: o.target || null,
        current: o.measureCurrent === true,
        a1: o.a1 !== false,
        a2: o.a2 !== false,
        ring: o.focusRing === true,
        margin: o.focusMargin === undefined ? 4 : o.focusMargin,
        single: o.compareSingle === true,
      },
    };
  }

  // ---- warstwa 2: kolektory (surowe rekordy, bez werdyktów) ----
  function readBar() {
    const el = dom.first('.doc-toolbar');
    if (!el) return null;
    return { el, box: dom.box(el), height: el.offsetHeight };
  }

  function readHeading(screenEl) {
    const h2 = dom.first('.screen-meta h2', screenEl);
    return h2 ? dom.box(h2) : null;
  }

  function readScreenHeading(id, bar) {
    const screenEl = dom.first('[id="' + id + '"].screen');
    if (!screenEl) return { missing: true, id };
    const head = readHeading(screenEl);
    return { id, head, barBottom: bottomOf(bar.box), aria: dom.attr(screenEl, 'aria-current') };
  }

  function listWindows(screenEl) {
    const kinds = ['modal', 'drawer', 'toast'];
    const found = [];
    kinds.forEach((kind) => {
      dom.many('.' + kind, screenEl).forEach((el) => found.push({ kind, el }));
    });
    return found;
  }

  function readButtonsInWindow(win, barBox) {
    return dom
      .many('button, .btn', win.el)
      .map((el) => ({ el, box: dom.box(el) }))
      .filter((rec) => hasArea(rec.box))
      .map((rec) => {
        const c = centerOf(rec.box);
        const visible = inWindow(c);
        const top = visible ? dom.topAt(c[0], c[1]) : null;
        return {
          label: dom.text(rec.el) || dom.attr(rec.el, 'aria-label') || '',
          at: c,
          visible,
          reached: isInside(top, rec.el),
          underBar: rec.box[1] < bottomOf(barBox) && bottomOf(rec.box) > barBox[1],
        };
      });
  }

  function readPins(screenEl, index) {
    const pins = [];
    dom.many('.anno-pin', screenEl).forEach((el) => {
      const holder = el.closest('.modal, .drawer');
      const box = dom.box(el);
      if (!holder || !hasArea(box)) return;
      const body = dom.first('.modal-body, .drawer-body', holder);
      const c = centerOf(box);
      const visible = inWindow(c);
      pins.push({
        label: dom.attr(el, 'aria-label') || dom.text(el),
        index: pins.length + 1,
        scrolled: body ? body.scrollTop : 0,
        at: c,
        visible,
        reached: visible ? isInside(dom.topAt(c[0], c[1]), el) : true,
      });
    });
    return pins;
  }

  function readFocusSpace(bar) {
    const edge = bar.box;
    return dom
      .many('.proto-toolbar button')
      .map((el) => ({ el, box: dom.box(el) }))
      .filter((rec) => hasArea(rec.box))
      .map((rec) => ({
        label: dom.text(rec.el) || dom.attr(rec.el, 'aria-label') || '',
        gaps: [
          rec.box[0] - edge[0],
          rightOf(edge) - rightOf(rec.box),
          rec.box[1] - edge[1],
          bottomOf(edge) - bottomOf(rec.box),
        ],
      }));
  }

  function snapshotSizes() {
    const bar = readBar();
    return {
      height: bar ? bar.height : 0,
      screens: dom.many('.screen').length,
      links: dom.many('.screen-nav a').length,
    };
  }

  // ---- warstwa 3: reguły (tabela kontroli: surowy rekord -> oceniony rekord) ----
  const rules = {
    heading(rec) {
      if (rec.missing) return fail('Brak ekranu ' + rec.id);
      if (!rec.head) return fail('Brak nagłówka .screen-meta h2 w ' + rec.id);
      const top = rec.head[1];
      const bot = bottomOf(rec.head);
      const [, winH] = dom.win();
      return {
        ok: top >= rec.barBottom - 0.5 && bot <= winH,
        screen: rec.id,
        h2Top: snap(top),
        h2Bottom: snap(bot),
        toolbarBottom: snap(rec.barBottom),
        ariaCurrent: rec.aria,
      };
    },
    window(rec) {
      const buttons = rec.buttons.map((b) => ({
        ok: !b.underBar && (!b.visible || b.reached),
        label: b.label,
        x: snap(b.at[0]),
        y: snap(b.at[1]),
        hit: b.reached,
        overlaps: b.underBar,
      }));
      return { screen: rec.screen, element: rec.kind, ok: buttons.every((b) => b.ok), buttons };
    },
    pin(rec) {
      return {
        ok: rec.reached,
        screen: rec.screen,
        index: rec.index,
        label: rec.label,
        bodyScrollTop: snap(rec.scrolled),
        x: snap(rec.at[0]),
        y: snap(rec.at[1]),
        hit: rec.reached,
      };
    },
    ring(rec, margin) {
      const [left, right, top, bottom] = rec.gaps.map((g) => snap(g, 1));
      return {
        ok: [left, right, top, bottom].every((g) => g >= margin),
        label: rec.label,
        space: { left, right, top, bottom },
      };
    },
  };

  // wszystkie werdykty grupy są składane w jeden rekord
  const fold = (items, extra) => Object.assign({ ok: items.every((i) => i.ok !== false) }, extra);
  const verdictOf = (m) => (m && m.ok === false ? false : true);

  // ---- warstwa 4: jedno miejsce składania wyniku ----
  function report(parts) {
    const measures = parts.measures || {};
    const names = Object.keys(measures);
    const overall = parts.error ? false : names.every((n) => verdictOf(measures[n]));
    const out = { ok: overall };
    if (parts.error) out.error = parts.error;
    out.viewport = parts.viewport;
    out.toolbar = parts.toolbar;
    names.forEach((n) => {
      out[n] = measures[n];
    });
    return out;
  }

  function emptyView() {
    const [w, h] = dom.win();
    return {
      viewport: { width: w, height: h, theme: null, panelOpen: false },
      toolbar: { height: 0, bottom: 0, share: 0, cssVar: null, screens: 0 },
    };
  }

  function readView(bar) {
    const [w, h] = dom.win();
    const panel = dom.first('.anno-panel.open');
    const css = dom.cssVar('--proto-toolbar-height');
    return {
      viewport: {
        width: w,
        height: h,
        theme: dom.attr(document.documentElement, 'data-theme'),
        panelOpen: !!panel,
      },
      toolbar: {
        height: bar.height,
        bottom: snap(bottomOf(bar.box)),
        share: h > 0 ? snap(bar.height / h, 3) : 0,
        cssVar: css || null,
        screens: dom.many('.screen').length,
      },
    };
  }

  // ---- orkiestracja przebiegów pomiarowych ----
  function goToScreen(screenEl) {
    screenEl.scrollIntoView({ block: 'start' });
  }

  async function passWindows(bar) {
    const rows = [];
    for (const screenEl of dom.many('.screen')) {
      const wins = listWindows(screenEl);
      if (!wins.length) continue;
      goToScreen(screenEl);
      await dom.frame();
      wins.forEach((w) => {
        const buttons = readButtonsInWindow(w, readBar().box);
        const judged = rules.window({ screen: screenEl.id, kind: w.kind, buttons });
        if (!buttons.length) {
          judged.ok = null;
          judged.skipped = 'brak przycisków';
        }
        rows.push(judged);
      });
    }
    if (!rows.length) return { ok: null, screens: [], skipped: 'brak okien, szuflad i toastów' };
    return fold(rows, { screens: rows });
  }

  async function passPins() {
    const rows = [];
    for (const screenEl of dom.many('.screen')) {
      if (!dom.first('.anno-pin', screenEl)) continue;
      goToScreen(screenEl);
      await dom.frame();
      readPins(screenEl).forEach((p) => rows.push(rules.pin(Object.assign({ screen: screenEl.id }, p))));
    }
    if (!rows.length) return { ok: null, pins: [], skipped: 'brak pinezek w oknach i szufladach' };
    return fold(rows, { pins: rows });
  }

  async function passSingle() {
    const before = snapshotSizes();
    const all = dom.many('.screen');
    const dropped = all.slice(1).map((s) => s.id);
    all.slice(1).forEach((s) => s.remove());
    dom.many('.screen-nav a').forEach((a) => {
      const href = dom.attr(a, 'href') || '';
      if (dropped.indexOf(href.replace(/^#/, '')) >= 0) a.remove();
    });
    await dom.frame();
    const after = snapshotSizes();
    return {
      ok: before.height === after.height,
      before,
      after,
      diff: after.height - before.height,
      note: 'Usunięto ekrany poza pierwszym wraz z linkami; przeładuj stronę przed dalszymi pomiarami.',
    };
  }

  // ---- wejście ----
  try {
    const parsed = readOptions(options);
    if (parsed.problem) return report(Object.assign({ error: parsed.problem }, emptyView()));
    const cfg = parsed.cfg;
    const bar = readBar();
    if (!bar) return report(Object.assign({ error: 'Brak .doc-toolbar' }, emptyView()));

    const startY = dom.scrollY();
    const view = readView(bar);
    const measures = {};
    let targetEl = null;

    if (cfg.current) {
      const cur = dom.first('.screen.is-current');
      measures.current = cur ? rules.heading(readScreenHeading(cur.id, bar)) : fail('Brak .screen.is-current');
    }
    if (cfg.target) {
      targetEl = dom.first('[id="' + cfg.target + '"].screen');
      if (targetEl) {
        goToScreen(targetEl);
        await dom.frame();
      }
      measures.target = rules.heading(readScreenHeading(cfg.target, readBar()));
    }
    if (cfg.a1) measures.a1 = await passWindows(bar);
    if (cfg.a2) measures.a2 = await passPins();
    if (cfg.ring) {
      const rows = readFocusSpace(bar).map((r) => rules.ring(r, cfg.margin));
      measures.focusRing = fold(rows, { margin: cfg.margin, buttons: rows });
    }

    if (cfg.target && targetEl) {
      goToScreen(targetEl);
    } else if (!cfg.target) {
      window.scrollTo(0, startY);
    }
    if (cfg.single) measures.compareSingle = await passSingle();

    return report({ viewport: view.viewport, toolbar: view.toolbar, measures });
  } catch (err) {
    return report(Object.assign({ error: 'Błąd pomiaru: ' + ((err && err.message) || String(err)) }, emptyView()));
  }
}
