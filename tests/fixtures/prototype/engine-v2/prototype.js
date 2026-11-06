/* Silnik prototypów skillsync, wersja 2.
 *
 * Jeden plik bez zależności. W przeglądarce uruchamia interfejs komentarzy, akceptacji i iteracji;
 * pod Node (`require`) eksportuje czyste funkcje (`module.exports`, kontrakt: 106 nazw), bez dotykania `window` i `document`.
 *
 * Układ pliku: logika podzielona na dziedziny (każda to obiekt z małymi funkcjami), eksport składany na końcu z mapy
 * nazwa -> implementacja. Kształty danych powstają w fabrykach (`Shapes`), nie w miejscu użycia.
 *   1. Kontrakt: stałe zgrupowane w zamrożonych obiektach
 *   2. Values: predykaty, porównania, tekst
 *   3. Shapes: fabryki kształtów (wątek, wiadomość, decyzja, osoba, wynik akceptacji ...)
 *   4. Appearance: blokada zewnętrznego trybu ciemnego
 *   5. Acceptance: akceptacja ekranów i projektu
 *   6. Iterations: iteracje, porównanie, przywracanie
 *   7. Switcher: przełącznik ekranów i skróty klawiszowe
 *   8. Outbox: kolejka offline, klasyfikacja zapisu, identyfikatory, import wpisów v1
 *   9. Views: widok wątku (desktop/mobile), pole `view` w `create`, obsługa widoków przez serwer
 *      Panel: filtry panelu (status i widok)
 *  10. People: etykiety, inicjały, odcień awatara
 *  11. Journal: dziennik operacji (normalizacja, kolejność, scalanie, serializacja)
 *  12. Fold: składanie stanu z dziennika (reducery według typu operacji) i pochodne
 *  13. Interfejs w przeglądarce (UI): pasek, przełącznik ekranów, tryby, nawigacja, klawiatura, komentowanie, panel wątków, kolejka zapisu,
 *      konto, banery, iteracje (zamrożona strona, porównanie, przywracanie) i akceptacja
 *  14. Eksporty i start
 */
(function () {
  'use strict';

  // ── 1. Kontrakt ───────────────────────────────────────────────────────────────────────────────────────────────

  const words = (text) => Object.freeze(text.trim().split(/\s+/));
  const NAMESPACE = 'skillsync-prototype';
  const versionedPrefix = (part, version) => NAMESPACE + '-' + part + ':v' + version + ':';

  const IDENTITY = Object.freeze({
    engine: 2, format: NAMESPACE + '-comments', formatVersion: 2, apiRoot: '/_api/v1'
  });
  const STORAGE = Object.freeze({
    queue: versionedPrefix('queue', 2), rejected: versionedPrefix('rejected', 2), seen: versionedPrefix('seen', 2),
    v1Comments: versionedPrefix('comments', 1), theme: NAMESPACE + '-theme', shortcuts: NAMESPACE + '-shortcuts'
  });
  const LIMITS = Object.freeze({ text: 4000, label: 200, restoreReason: 500 });

  const ENGINE_VERSION = IDENTITY.engine;
  const FORMAT_NAME = IDENTITY.format;
  const FORMAT_VERSION = IDENTITY.formatVersion;
  const API_ROOT = IDENTITY.apiRoot;
  const QUEUE_PREFIX = STORAGE.queue;
  const REJECTED_PREFIX = STORAGE.rejected;
  const SEEN_PREFIX = STORAGE.seen;
  const V1_COMMENTS_PREFIX = STORAGE.v1Comments;
  const THEME_KEY = STORAGE.theme;
  const MAX_TEXT = LIMITS.text;
  const MAX_LABEL = LIMITS.label;
  const MAX_RESTORE_REASON = LIMITS.restoreReason;

  const OPERATION_TYPES = words('create reply edit retract vote resolve reopen reanchor delete triage decision ' +
    'accept unaccept iteration restore');
  const PROTOTYPE_TYPES = words('accept unaccept iteration restore');
  const THREAD_TYPES = Object.freeze(OPERATION_TYPES.filter((type) => PROTOTYPE_TYPES.indexOf(type) < 0));
  const QUEUEABLE_TYPES = words('create reply edit retract vote resolve reopen reanchor');
  const STATUSES = words('open awaiting decided closed deferred');
  const OPEN_STATUSES = words('open awaiting decided');
  const FILTERS = words('open new agent lost all');
  const RESTORE_ROLES = words('acceptor developer');
  const ACCEPT_ROLES = words('acceptor developer');
  const OPERATION_KEYS = words('id type threadId at by cid payload');
  const VOTE_VALUES = words('up down none');
  const TRIAGE_CLASSES = words('clear disputed out_of_scope');
  const DECISION_KINDS = words('agent developer');
  const V1_IMPORT_TYPES = words('create reply resolve reopen reanchor');

  // Wynik decyzji -> [status wątku, powód statusu].
  const DECISION_STATUS = Object.freeze([
    ['apply', 'decided', null], ['variant', 'decided', null], ['keep', 'closed', 'kept'],
    ['out_of_scope', 'closed', 'out_of_scope'], ['defer', 'deferred', null]
  ].reduce((table, row) => Object.assign(table, { [row[0]]: Object.freeze([row[1], row[2]]) }), {}));

  const THEME_LOCK_METAS = Object.freeze([['color-scheme', 'light dark'], ['darkreader-lock', null]]
    .map((pair) => Object.freeze({ name: pair[0], content: pair[1] })));

  const TEXTS = Object.freeze({
    connecting: 'Łączenie z serwerem…',
    stale: 'Prototyp zmieniono od wczytania strony: przeładuj.',
    noVersion: 'Serwer nie podał wersji prototypu: przeładuj stronę.',
    noQueueRestore: 'Przywrócenie wymaga połączenia z serwerem i nie trafia do kolejki.',
    noQueueAccept: 'Akceptacja wymaga połączenia z serwerem i nie trafia do kolejki.'
  });

  // ── 2. Values ─────────────────────────────────────────────────────────────────────────────────────────────────

  const Values = (() => {
    const ownProperty = Object.prototype.hasOwnProperty;
    // Spacje jak `str.split()` w Pythonie (etykiety muszą być identyczne w obu językach).
    const SPACE_CLASS = '[\\t\\n\\u000b\\f\\r\\u001c-\\u0020\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]';
    const spaceMatcher = (flags, wrap) => new RegExp(wrap(SPACE_CLASS + '+'), flags);

    const api = {
      SPACE_RUN: spaceMatcher('', (core) => core),
      SPACE_RUNS: spaceMatcher('g', (core) => core),
      SPACE_EDGES: spaceMatcher('g', (core) => '^' + core + '|' + core + '$'),
      has: (object, key) => object !== null && object !== undefined && ownProperty.call(object, key),
      isObject: (value) => !!value && typeof value === 'object' && !Array.isArray(value),
      isStr: (value) => typeof value === 'string' && value.length > 0,
      isIntegral: (value) => typeof value === 'number' && isFinite(value) && Math.floor(value) === value,
      emptyMap: () => Object.create(null),
      asArray: (value) => (Array.isArray(value) ? value : []),
      oneOf: (list, value) => list.indexOf(value) >= 0,
      strOr: (value, fallback) => (typeof value === 'string' ? value : fallback),
      compareStrings: (a, b) => (a < b ? -1 : a > b ? 1 : 0),
      uniqueBy: (list, keyOf) => {
        const taken = new Set();
        return list.filter((item) => {
          const key = keyOf(item);
          if (taken.has(key)) return false;
          taken.add(key);
          return true;
        });
      },
      // Pierwsza reguła [test, wynik], której test przechodzi; wynik może być funkcją tych samych argumentów.
      firstRule: (rules, fallback, args) => {
        const hit = rules.find((rule) => rule[0].apply(null, args));
        const answer = hit ? hit[1] : fallback;
        return typeof answer === 'function' ? answer.apply(null, args) : answer;
      }
    };
    api.isInt = (value, minimum) => api.isIntegral(value) && value >= (minimum === undefined ? 1 : minimum);
    api.payloadOf = (operation) => (api.isObject(operation.payload) ? operation.payload : {});
    api.authorOf = (operation) => (api.isStr(operation.by) ? operation.by : null);
    api.firstCodePoint = (text) => {
      const first = Array.from(String(text))[0];
      return first === undefined ? '' : first;
    };
    api.upperFirst = (text) => api.firstCodePoint(text).toUpperCase() + String(text).slice(api.firstCodePoint(text).length);
    api.splitWords = (text) => {
      const trimmed = String(text).replace(api.SPACE_EDGES, '');
      return trimmed === '' ? [] : trimmed.split(api.SPACE_RUN);
    };
    api.truncateUtf16 = (text, limit) => {
      if (text.length <= limit) return text;
      const highSurrogate = text.charCodeAt(limit - 1);
      return text.slice(0, highSurrogate >= 0xd800 && highSurrogate <= 0xdbff ? limit - 1 : limit);
    };
    api.compareNatural = (a, b) => {
      const text = (value) => String(value === null || value === undefined ? '' : value);
      const split = (value) => /^(\D*)(\d+)(.*)$/.exec(value);
      const left = text(a);
      const right = text(b);
      const x = split(left);
      const y = split(right);
      if (x && y && x[1] === y[1] && Number(x[2]) !== Number(y[2])) return Number(x[2]) - Number(y[2]);
      return api.compareStrings(left, right);
    };
    api.isOpenStatus = (status) => typeof status === 'string' && OPEN_STATUSES.indexOf(status) >= 0;
    return Object.freeze(api);
  })();

  const {
    SPACE_RUNS, SPACE_EDGES, has, isObject, isStr, isIntegral, isInt, emptyMap, asArray, oneOf, strOr, compareStrings,
    uniqueBy, firstRule, payloadOf, authorOf, firstCodePoint, upperFirst, splitWords, truncateUtf16, compareNatural,
    isOpenStatus
  } = Values;

  // Selektor elementu po `data-proto-id`; wartość z danych (dziennik, wiadomość iframe) przechodzi przez CSS.escape.
  const protoIdSelector = (id) => '[data-proto-id="' + (typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(String(id)) : String(id).replace(/["\\]/g, '\\$&')) + '"]';

  // ── 3. Shapes ─────────────────────────────────────────────────────────────────────────────────────────────────

  // Fabryka: obiekt domyślny (świeży przy każdym wywołaniu) + nadpisania.
  const blueprint = (base) => (overrides) => Object.assign(base(), overrides);
  const gate = (visible, enabled, reason) => ({ visible: visible, enabled: enabled, reason: reason });

  const Shapes = Object.freeze({
    message: blueprint(() => ({
      id: null, text: '', by: null, at: null, opening: false, importedAuthor: null,
      edited: false, editedAt: null, retracted: false, votes: emptyMap()
    })),
    thread: blueprint(() => ({
      id: null, number: null, screen: '', anchor: null, label: '', hint: null, by: null, at: null,
      status: 'open', statusReason: null, triage: null, decisions: [], messages: [], lastAt: null, events: [],
      view: 'desktop'
    })),
    event: blueprint(() => ({ type: '', by: null, at: null })),
    triage: blueprint(() => ({ class: 'clear', note: null, at: null, by: null })),
    decision: blueprint(() => ({
      id: null, kind: 'agent', who: null, outcome: 'apply', text: '', iteration: null, at: null, by: null
    })),
    acceptEntry: blueprint(() => ({
      hash: '', by: null, who: null, at: null, iteration: null, scope: null, via: null, opId: null
    })),
    person: blueprint(() => ({ login: '', firstName: '', surname: '', fullName: '' })),
    label: blueprint(() => ({ firstName: '', fullName: '', label: '' })),
    foldState: blueprint(() => ({
      threads: [], currentIteration: 1, removed: emptyMap(), acceptance: emptyMap(), byId: emptyMap()
    })),
    queueItem: blueprint(() => ({ type: '', threadId: null, payload: {} })),
    outcome: blueprint(() => ({
      poll: false, markStale: false, auth: false, password: false, renew: false, message: ''
    })),
    summary: blueprint(() => ({ state: 'none', accepted: 0, stale: 0, known: false, text: '', title: '' })),
    projectItem: blueprint(() => ({
      slug: '', title: '', iteration: null, screens: 0, open: null, awaiting: null, version: null, format: null,
      target: false
    })),
    effects: blueprint(() => ({ frozen: null, created: null, back: null, lost: null, acceptanceLost: null })),
    iterationRow: blueprint(() => ({ number: 0, path: '', exists: null, at: null })),
    screenEntry: blueprint(() => ({ id: '', number: 0, name: '', label: '' })),
    availability: (visible, enabled, reason) => gate(visible, enabled, reason),
    submit: (enabled, reason) => ({ enabled: enabled, reason: reason }),
    screenAnchor: () => ({ kind: 'screen', x: 0.5, y: 0.5, iteration: 1 })
  });

  // ── 4. Appearance ─────────────────────────────────────────────────────────────────────────────────────────────

  const Appearance = (() => {
    const lockSelector = (name) => 'meta[name="' + name + '"]';
    const canQuery = (head) => !!head && typeof head.querySelector === 'function';

    const hasLock = (doc) => !!(doc && canQuery(doc.head) && doc.head.querySelector(lockSelector('darkreader-lock')));

    const ensureMetas = (doc) => {
      const lookupHead = () => (typeof doc.getElementsByTagName === 'function' ? doc.getElementsByTagName('head')[0] : null);
      const head = doc && (doc.head || lookupHead());
      if (!canQuery(head)) return 0;
      const reference = head.firstChild || null;
      const missing = THEME_LOCK_METAS.filter((spec) => !head.querySelector(lockSelector(spec.name)));
      missing.forEach((spec) => {
        const meta = doc.createElement('meta');
        meta.setAttribute('name', spec.name);
        if (spec.content) meta.setAttribute('content', spec.content);
        head.insertBefore(meta, reference);
      });
      return missing.length;
    };

    // Pozostałość rozszerzenia przyciemniającego: element `style` z klasą zaczynającą się od `darkreader`.
    const isLeftover = (node) => {
      const styleNode = !!node && node.nodeType === 1 && String(node.tagName).toLowerCase() === 'style' &&
        typeof node.getAttribute === 'function';
      if (!styleNode) return false;
      return String(node.getAttribute('class') || '').split(/\s+/).some((name) => name.indexOf('darkreader') === 0);
    };

    const sweep = (doc, parents) => {
      if (!hasLock(doc)) return 0;
      let swept = 0;
      asArray(parents).filter(Boolean).forEach((parent) => {
        const children = Array.prototype.slice.call(parent.childNodes || parent.children || []);
        children.filter(isLeftover).forEach((child) => {
          if (typeof parent.removeChild !== 'function') return;
          parent.removeChild(child);
          swept += 1;
        });
      });
      return swept;
    };

    return Object.freeze({ hasLock: hasLock, ensureMetas: ensureMetas, isLeftover: isLeftover, sweep: sweep });
  })();

  // ── 5. Acceptance ─────────────────────────────────────────────────────────────────────────────────────────────

  const Acceptance = (() => {
    const slugList = (list) => asArray(list).filter(isStr);

    const pluralPrototypes = (count) => {
      const lastTwo = count % 100;
      const last = count % 10;
      if (count === 1) return 'prototyp';
      return last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14) ? 'prototypy' : 'prototypów';
    };

    // Chip „Akceptacja k/n”. `acceptance` z serwera ({sN: {valid, who}}) albo null (tylko odczyt: wpis foldu bez aktualności).
    const summary = (ids, acceptance, folded) => {
      if (!Array.isArray(ids) || !ids.length) return null;
      const known = isObject(acceptance);
      const source = known ? acceptance : (isObject(folded) ? folded : {});
      const present = ids.filter((id) => has(source, id) && isObject(source[id]));
      const stale = known ? present.filter((id) => source[id].valid !== true) : [];
      const current = present.filter((id) => stale.indexOf(id) < 0);
      const who = [];
      current.forEach((id) => {
        if (isStr(source[id].who) && who.indexOf(source[id].who) < 0) who.push(source[id].who);
      });
      const text = 'Akceptacja ' + current.length + '/' + ids.length;
      const state = firstRule([
        [() => stale.length > 0, 'stale'],
        [() => current.length === ids.length, 'full'],
        [() => current.length > 0, 'partial']
      ], 'none', []);
      const title = text + ' ekranów' + (who.length ? ' (' + who.join(', ') + ')' : '') + '.' +
        (stale.length ? ' Akceptacja nieaktualna: zmieniono pliki ekranu po akceptacji (' + stale.length + ').' : '') +
        (known ? '' : ' Aktualność sprawdza serwer.');
      return Shapes.summary({
        state: state, accepted: current.length, stale: stale.length, known: known, text: text, title: title
      });
    };

    const LOAD_PROBLEMS = {
      missing: 'Plik komentarzy jest niedostępny: sprawdź, czy serwer prototypów działa.',
      needs_migration: 'Prototyp wymaga migracji do formatu v2: uruchom „skillsync prototype migrate”.',
      invalid_log: 'Plik comments.json jest uszkodzony: napraw go albo przywróć z gita.'
    };

    const loadReason = (problem) => {
      if (!isStr(problem)) return TEXTS.connecting;
      return has(LOAD_PROBLEMS, problem) ? LOAD_PROBLEMS[problem] : 'Nie udało się wczytać komentarzy (' + problem + ').';
    };

    const offline = (flag, onLine, serverOk) => flag === true || (onLine === false && serverOk !== true);

    // Wspólne blokady okna i przycisku: kolejność reguł = ważność komunikatu.
    const BLOCKS = [
      ['expired', () => 'Sesja wygasła: zaloguj się ponownie.'],
      ['mustChange', () => 'Najpierw ustaw własne hasło.'],
      ['stale', () => TEXTS.stale],
      ['offline', () => TEXTS.noQueueAccept],
      ['loading', (s) => loadReason(s.problem)]
    ];
    const blockReason = (s) => {
      const hit = BLOCKS.find((block) => s[block[0]]);
      return hit ? hit[1](s) : null;
    };

    const availability = (state) => {
      const s = isObject(state) ? state : {};
      const user = isObject(s.user) ? s.user : null;
      const hiddenByContext = s.mode !== 'api' || s.frozen || s.embed || s.example || !user || !oneOf(ACCEPT_ROLES, user.role);
      if (hiddenByContext) return Shapes.availability(false, false, '');
      const reason = blockReason(Object.assign({}, s, { mustChange: user.mustChange }));
      if (reason) return Shapes.availability(true, false, reason);
      if (!isStr(s.version) || !isStr(s.hash)) return Shapes.availability(true, false, TEXTS.noVersion);
      return Shapes.availability(true, true, '');
    };

    // Powód wyłączenia przycisku zatwierdzającego w zakresie projektu; null = można.
    const projectBlock = (s) => firstRule([
      [() => s.projectState === 'failed', 'Nie udało się pobrać listy prototypów.'],
      [() => s.projectState !== 'ready', 'Poczekaj na listę prototypów.'],
      [() => !(s.targets > 0), 'Brak prototypów v2 do zaakceptowania.']
    ], null, []);

    const submitState = (state) => {
      const s = isObject(state) ? state : {};
      const blocked = blockReason(s);
      if (blocked) return Shapes.submit(false, blocked);
      const project = s.scope === 'project';
      if (!isStr(s.version) || (!project && !isStr(s.hash))) return Shapes.submit(false, TEXTS.noVersion);
      const scopeReason = project ? projectBlock(s) : (s.accepted ? 'Ten ekran ma już aktualną akceptację.' : null);
      if (scopeReason) return Shapes.submit(false, scopeReason);
      return s.busy ? Shapes.submit(false, '') : Shapes.submit(true, '');
    };

    const canUnaccept = (user, entry) => {
      if (!isObject(user) || !isObject(entry) || !oneOf(ACCEPT_ROLES, user.role) || entry.valid !== true) return false;
      return user.role === 'developer' || (isStr(user.login) && entry.who === user.login);
    };

    const targetScreen = (current, ids) => {
      if (!Array.isArray(ids) || !ids.length) return null;
      return typeof current === 'string' && oneOf(ids, current) ? current : ids[0];
    };

    const escapeAllowed = (kind, mustChange) => !(kind === 'password' && mustChange === true);

    const panelEscape = (state) => {
      const s = isObject(state) ? state : {};
      const draft = s.anyDraft === true;
      const verdict = (action, confirm) => ({ action: action, confirm: confirm });
      if (s.inField === true) return s.fieldDraft === true ? verdict('blur', false) : verdict('close', draft);
      return s.threadView === true ? verdict('list', draft) : verdict('close', draft);
    };

    const tabTarget = (items, current, shift) => {
      if (!Array.isArray(items) || !items.length) return null;
      const lastIndex = items.length - 1;
      const index = items.indexOf(current);
      if (index < 0) return items[shift ? lastIndex : 0];
      if (shift && index === 0) return items[lastIndex];
      if (!shift && index === lastIndex) return items[0];
      return null;
    };

    const doneMessage = (kind, data, title) => {
      const name = strOr(title, '');
      if (kind === 'undo') return 'Cofnięto akceptację ekranu „' + name + '”.';
      if (kind !== 'project') return 'Zaakceptowano ekran „' + name + '”.';
      const source = isObject(data) ? data : {};
      const done = slugList(source.accepted).length;
      const skipped = slugList(source.skipped).length;
      return 'Zaakceptowano ' + done + ' ' + pluralPrototypes(done) + (skipped ? '; pominięto ' + skipped : '') + '.';
    };

    // Reakcja na wynik po zamknięciu okna: odświeżyć stan (`poll`), oznaczyć stronę jako nieaktualną, wymagane logowanie / hasło / token.
    const OUTCOMES = {
      done: (source, ask) => ({ poll: true, message: doneMessage(ask.kind, source, ask.title) }),
      partial: (source) => {
        const pending = slugList(source.pending);
        return {
          poll: true,
          message: 'Akceptacja zapisana częściowo' + (pending.length ? ' (czeka: ' + pending.join(', ') + ')' : '') +
            '; ponów, żeby dokończyć.'
        };
      },
      stale: (source, ask) => {
        const changed = ask.kind === 'project' ? slugList(source.stale) : [];
        const listed = 'Zmieniono prototypy: ' + changed.join(', ') + '. Przeładuj je i spróbuj ponownie. Nic nie zapisano.';
        return {
          markStale: changed.length === 0 || oneOf(changed, ask.slug),
          message: changed.length ? listed : 'Prototyp zmieniono od wczytania strony: przeładuj stronę i spróbuj ponownie. Nic nie zapisano.'
        };
      },
      not_accepted: () => ({ poll: true, message: 'Ten ekran nie ma już akceptacji.' }),
      auth: () => ({ auth: true, message: 'Sesja wygasła: zaloguj się i ponów akcję.' }),
      password: () => ({ password: true, message: 'Najpierw ustaw własne hasło, potem ponów akcję.' }),
      csrf: () => ({ renew: true, message: 'Nie udało się potwierdzić sesji: odśwież połączenie i ponów akcję.' }),
      busy: () => ({ message: 'Serwer jest zajęty: poczekaj chwilę i ponów akcję.' }),
      offline: () => ({ message: TEXTS.noQueueAccept })
    };
    const failedOutcome = (source, ask) => ({
      message: 'Nie udało się zapisać akceptacji' + (ask.reason ? ': ' + ask.reason : '') + '. Nic nie zapisano.'
    });

    const closedOutcome = (action, kind, data, title, currentSlug, reason) => {
      const ask = { kind: kind, title: title, slug: currentSlug, reason: typeof reason === 'string' ? reason : '' };
      const build = has(OUTCOMES, action) ? OUTCOMES[action] : failedOutcome;
      return Shapes.outcome(build(isObject(data) ? data : {}, ask));
    };

    const projectSummary = (data, currentSlug, currentVersion) => {
      const items = (isObject(data) ? asArray(data.prototypes) : [])
        .filter((entry) => isObject(entry) && isStr(entry.slug))
        .map((entry) => {
          const screens = isInt(entry.screens, 0) ? entry.screens : 0;
          return Shapes.projectItem({
            slug: entry.slug, title: strOr(entry.title, entry.slug), iteration: isInt(entry.iteration, 0) ? entry.iteration : null,
            screens: screens, open: isInt(entry.open, 0) ? entry.open : null,
            awaiting: isInt(entry.awaiting, 0) ? entry.awaiting : null, version: strOr(entry.version, null),
            format: entry.format, target: entry.format === 2 && screens > 0
          });
        });
      const v2 = items.filter((item) => item.format === 2);
      const skippedV1 = items.length - v2.length;
      const versions = {};
      v2.forEach((item) => {
        const version = item.slug === currentSlug ? currentVersion : item.version;
        if (isStr(version)) versions[item.slug] = version;
      });
      const total = (key) => v2.reduce((sum, item) => sum + (item[key] || 0), 0);
      return {
        items: items, v2: v2.length, v1: skippedV1, withScreens: items.filter((item) => item.target).length,
        versions: versions, open: total('open'), awaiting: total('awaiting'),
        optionText: skippedV1 > 0 ? 'Wszystkie prototypy v2 (pominięto ' + skippedV1 + ')' : 'Wszystkie prototypy w obecnej wersji'
      };
    };

    const screenBody = (cid, screen, hash, version) => ({
      cid: cid, type: 'accept', payload: { scope: 'screen', screens: { [screen]: hash }, version: version }
    });

    const unacceptBody = (cid, screen) => ({ cid: cid, type: 'unaccept', payload: { screens: [screen] } });

    const ACCEPT_RULES = [
      [(s) => s >= 200 && s < 300, 'done'],
      [(s, c) => s === 409 && c === 'stale_view', 'stale'],
      [(s, c) => s === 409 && c === 'partial', 'partial'],
      [(s, c) => s === 409 && c === 'not_accepted', 'not_accepted'],
      [(s, c) => (s === 500 && c === 'write_failed') || (s === 507 && c === 'file_too_large') ||
        (s === 409 && (c === 'invalid_log' || c === 'needs_migration')),
      (s, c, data) => (isObject(data) && slugList(data.accepted).length ? 'partial' : 'error')],
      [(s) => s === 503 || s === 429, 'busy'],
      [(s) => s === 401, 'auth'],
      [(s, c) => s === 403 && c === 'password_change_required', 'password'],
      [(s, c) => s === 403 && (c === 'invalid_csrf' || c === 'invalid_origin'), 'csrf'],
      [(s) => s === 403, 'forbidden'],
      [(s) => s === 400, 'invalid']
    ];
    const classifyResult = (status, code, data) => {
      if (typeof status !== 'number' || !status) return 'offline';
      return firstRule(ACCEPT_RULES, 'error', [status, code, data]);
    };

    const VIA_SUFFIX = { cli: ' (CLI)', ui: ' (UI)' };
    const acceptedLine = (entry, name, time) => {
      if (!isObject(entry)) return 'Zaakceptowano: nieznany';
      const who = isStr(name) ? name : (isStr(entry.who) ? entry.who : 'nieznany');
      const at = isStr(time) ? time : entry.at;
      return 'Zaakceptowano: ' + who + (isStr(at) ? ', ' + at : '') + (isInt(entry.iteration) ? ', iteracja ' + entry.iteration : '') +
        (has(VIA_SUFFIX, entry.via) ? VIA_SUFFIX[entry.via] : '');
    };

    return Object.freeze({
      slugList: slugList, summary: summary, loadReason: loadReason, offline: offline, availability: availability,
      submitState: submitState, canUnaccept: canUnaccept, targetScreen: targetScreen, escapeAllowed: escapeAllowed,
      panelEscape: panelEscape, tabTarget: tabTarget, doneMessage: doneMessage, closedOutcome: closedOutcome,
      projectSummary: projectSummary, screenBody: screenBody, unacceptBody: unacceptBody, classifyResult: classifyResult,
      acceptedLine: acceptedLine
    });
  })();

  // ── 6. Iterations ─────────────────────────────────────────────────────────────────────────────────────────────

  const Iterations = (() => {
    const pathOf = (number) => 'iterations/' + number + '/';

    // Pierwsza chwila zapisu iteracji o danym numerze według dziennika.
    const firstSeen = (operations) => {
      const found = new Map();
      Journal.sort(Journal.normalize(operations)).forEach((operation) => {
        const number = payloadOf(operation).number;
        if (operation.type === 'iteration' && isInt(number) && !found.has(number)) found.set(number, operation.at);
      });
      return found;
    };

    const list = (directories, operations) => {
      const at = firstSeen(operations);
      const row = (number, exists) => Shapes.iterationRow({
        number: number, path: pathOf(number), exists: exists, at: at.has(number) ? at.get(number) : null
      });
      const rows = Array.isArray(directories) ?
        uniqueBy(directories.filter((item) => isObject(item) && isInt(item.number)), (item) => item.number)
          .map((item) => row(item.number, item.exists === true)) :
        Array.from(at.keys()).map((number) => row(number, null));
      return rows.sort((a, b) => b.number - a.number);
    };

    // Najnowsza iteracja z uchwytem wątku. `handles`: {numer: {ekran: {protoId: true}}}; brak wpisu = trzeba wczytać (`need`).
    const forThread = (iterations, handles, thread, skip) => {
      const none = { number: null, need: null };
      if (!isObject(thread)) return none;
      const kind = Fold.anchorKind(thread.anchor);
      if (kind === 'lost') return none;
      const candidates = asArray(iterations)
        .filter((item) => isObject(item) && isInt(item.number) && item.exists !== false && item.number !== skip)
        .sort((a, b) => b.number - a.number);
      for (const candidate of candidates) {
        const key = String(candidate.number);
        if (!isObject(handles) || !has(handles, key) || !isObject(handles[key])) return { number: null, need: candidate.number };
        const screens = handles[key];
        if (!has(screens, thread.screen)) continue;
        const reachable = kind === 'screen' || (isObject(screens[thread.screen]) && has(screens[thread.screen], thread.anchor.protoId));
        if (reachable) return { number: candidate.number, need: null };
      }
      return none;
    };

    const shouldProbe = (item, result, running) => {
      if (!isObject(item) || running === true) return false;
      if (typeof item.exists === 'boolean') return false;
      return typeof result !== 'boolean';
    };

    const embedMode = (address) => {
      const question = typeof address === 'string' ? address.indexOf('?') : -1;
      if (question < 0) return '';
      return address.slice(question + 1).split('#')[0].split('&').some((pair) => pair === 'embed=compare') ? 'compare' : '';
    };

    const threadIdFromHash = (hash) => {
      const match = typeof hash === 'string' ? /^#thread=([A-Za-z0-9_-]{1,80})$/.exec(hash) : null;
      return match ? match[1] : null;
    };

    const unchangedText = (number) => (isInt(number) ? 'Ekran w iteracji ' + number + ' jest taki sam jak bieżący.' :
      'Ekran w wybranej iteracji jest taki sam jak bieżący.');

    const restoreAvailability = (state) => {
      const s = isObject(state) ? state : {};
      const user = isObject(s.user) ? s.user : null;
      const shown = s.mode === 'api' && !s.frozen && !s.embed && !!user && !user.mustChange && oneOf(RESTORE_ROLES, user.role) &&
        s.sourceWrites === true && isInt(s.iterations);
      if (!shown) return Shapes.availability(false, false, '');
      const reason = firstRule([
        [() => s.stale, TEXTS.stale],
        [() => s.version === null || s.version === undefined, TEXTS.noVersion],
        [() => s.offline || s.expired, TEXTS.noQueueRestore],
        [() => s.unchanged, () => unchangedText(s.from)]
      ], null, []);
      return reason === null ? Shapes.availability(true, true, '') : Shapes.availability(true, false, reason);
    };

    const restoreSubmitState = (state) => {
      const s = isObject(state) ? state : {};
      const reason = firstRule([
        [() => s.busy, ''],
        [() => s.stale, 'Prototyp zmieniono od wczytania strony: przeładuj stronę i spróbuj ponownie.'],
        [() => s.unchanged, () => unchangedText(s.from)],
        [() => s.previewFailed, 'Nie udało się pobrać podglądu skutków: użyj „Ponów podgląd” albo wybierz inną iterację.'],
        [() => !s.previewOk, 'Poczekaj na podgląd skutków przywrócenia.'],
        [() => typeof s.reason !== 'string' || s.reason.replace(SPACE_EDGES, '') === '', 'Podaj powód przywrócenia.']
      ], null, []);
      return reason === null ? Shapes.submit(true, '') : Shapes.submit(false, reason);
    };

    const RESTORE_RULES = [
      [(s) => s >= 200 && s < 300, (s, c, data) => (isObject(data) && data.unchanged === true ? 'unchanged' : 'done')],
      [(s, c) => s === 409 && c === 'stale_view', 'stale'],
      [(s) => s === 503 || s === 429, 'busy'],
      [(s) => s === 401, 'auth'],
      [(s, c) => s === 403 && c === 'password_change_required', 'password'],
      [(s, c) => s === 403 && (c === 'invalid_csrf' || c === 'invalid_origin'), 'csrf']
    ];
    const classifyResult = (status, code, data) => {
      if (typeof status !== 'number' || !status) return 'offline';
      return firstRule(RESTORE_RULES, 'error', [status, code, data]);
    };

    const effects = (preview, number) => {
      const p = isObject(preview) ? preview : {};
      const countOrNull = (value) => (isInt(value, 0) ? value : null);
      const frozen = isInt(number) ? number : null;
      return Shapes.effects({
        frozen: frozen, created: frozen === null ? null : frozen + 1, back: countOrNull(p.threadsBack),
        lost: countOrNull(p.threadsLost),
        acceptanceLost: p.acceptanceLost === undefined || p.acceptanceLost === null ? null : !!p.acceptanceLost
      });
    };

    return Object.freeze({
      pathOf: pathOf, list: list, forThread: forThread, shouldProbe: shouldProbe, embedMode: embedMode,
      threadIdFromHash: threadIdFromHash, unchangedText: unchangedText, restoreAvailability: restoreAvailability,
      restoreSubmitState: restoreSubmitState, classifyResult: classifyResult, effects: effects
    });
  })();

  // ── 7. Switcher ───────────────────────────────────────────────────────────────────────────────────────────────

  const Switcher = (() => {
    const FOCUSABLE_SELECTOR = 'a[href], button, input, select, textarea, [tabindex]';

    const entriesFromLinks = (links) => {
      const entries = [];
      asArray(links).forEach((link) => {
        const target = isObject(link) && typeof link.href === 'string' ? /^#(s\d+)$/.exec(link.href) : null;
        if (!target) return;
        const raw = link.text === undefined || link.text === null ? '' : link.text;
        const text = String(raw).replace(SPACE_RUNS, ' ').replace(SPACE_EDGES, '');
        const numbered = /^(\d+)[.)]?\s+(.+)$/.exec(text);
        const number = numbered ? Number(numbered[1]) : entries.length + 1;
        const name = numbered ? numbered[2] : text;
        entries.push(Shapes.screenEntry({ id: target[1], number: number, name: name, label: number + ' ' + name }));
      });
      return entries;
    };

    const normalizeSearch = (text) => {
      if (typeof text !== 'string') return '';
      return text.toLowerCase().replace(/ł/g, 'l').replace(/đ/g, 'd').normalize('NFD').replace(/[̀-ͯ]/g, '')
        .replace(SPACE_RUNS, ' ').replace(SPACE_EDGES, '');
    };

    // Zapytanie od cyfry to prefiks etykiety „numer nazwa”; inne zapytanie musi zawierać się w nazwie.
    const filterEntries = (entries, query) => {
      const needle = normalizeSearch(query);
      const all = asArray(entries);
      if (needle === '') return all.slice();
      const byPrefix = /^\d/.test(needle);
      return all.filter((entry) => (byPrefix ? normalizeSearch(entry.number + ' ' + entry.name).indexOf(needle) === 0 :
        normalizeSearch(entry.name).indexOf(needle) >= 0));
    };

    const adjacent = (entries, currentId, direction) => {
      const all = asArray(entries);
      const at = all.findIndex((entry) => entry.id === currentId);
      if (at < 0) return null;
      return all[at + (direction < 0 ? -1 : 1)] || null;
    };

    // Pierwszy ekran, którego dół leży poniżej progu; po przewinięciu poza wszystkie - ostatni.
    const visibleId = (rects, threshold) => {
      const all = asArray(rects);
      if (!all.length) return null;
      return (all.find((rect) => rect.bottom > threshold) || all[all.length - 1]).id;
    };

    const listMove = (index, key, count, columns) => {
      if (!(count > 0)) return -1;
      const last = count - 1;
      const step = Math.max(1, Math.floor(Number(columns)) || 1);
      const start = isIntegral(index) ? index : -1;
      const jumps = { Home: 0, End: last };
      if (has(jumps, key)) return jumps[key];
      const deltas = { ArrowDown: step, ArrowUp: -step, ArrowRight: 1, ArrowLeft: -1 };
      if (!has(deltas, key)) return start;
      if (start < 0) return deltas[key] > 0 ? 0 : last;
      const target = start + deltas[key];
      return target < 0 || target > last ? start : target;
    };

    const shortcut = (event) => {
      const e = isObject(event) ? event : {};
      if (e.typing || e.ctrlKey || e.metaKey || e.altKey || e.dialog) return null;
      if (e.key === 'g' || e.key === 'G') return 'open';
      if (e.neutralFocus === false) return null;
      const arrows = { ArrowLeft: 'prev', ArrowRight: 'next' };
      return has(arrows, e.key) ? arrows[e.key] : null;
    };

    const SEARCH_KEYS = { Escape: 'close', Enter: 'choose', ArrowDown: 'move', ArrowUp: 'move' };
    const CARET_KEYS = words('Home End ArrowLeft ArrowRight');
    const searchKeyAction = (event) => {
      const e = isObject(event) ? event : {};
      if (e.isComposing) return null;
      if (has(SEARCH_KEYS, e.key)) return SEARCH_KEYS[e.key];
      if (e.key === 'Tab') return e.shiftKey ? 'tab-back' : 'tab-forward';
      if (oneOf(CARET_KEYS, e.key)) return e.value ? null : 'move';
      return null;
    };

    const isFocusable = (node, excluded) => {
      if (node.disabled || (typeof node.tabIndex === 'number' && node.tabIndex < 0)) return false;
      if (excluded && typeof excluded.contains === 'function' && excluded.contains(node)) return false;
      return !(typeof node.getClientRects === 'function' && node.getClientRects().length === 0);
    };

    // Następny element fokusu po `element` w kolejności dokumentu; pomija wyłączone, ukryte, o tabIndex < 0 i wewnątrz `excluded`.
    const focusableAfter = (doc, element, excluded) => {
      if (!doc || typeof doc.querySelectorAll !== 'function') return null;
      const all = Array.prototype.slice.call(doc.querySelectorAll(FOCUSABLE_SELECTOR));
      const at = all.indexOf(element);
      if (at < 0) return null;
      return all.slice(at + 1).find((node) => isFocusable(node, excluded)) || null;
    };

    const searchTabTarget = (doc, mainButton, list, action) => {
      if (action === 'tab-back') return mainButton;
      return focusableAfter(doc, mainButton, list) || mainButton;
    };

    const withCounts = (entries, counts) => asArray(entries).map((entry) =>
      Object.assign({}, entry, { count: has(counts, entry.id) ? counts[entry.id] : 0 }));

    // Jeden wiersz paska, gdy mieści się tytuł (do minimum), przełącznik i narzędzia; wejście do jednego wiersza wymaga zapasu.
    const toolbarFitsOneRow = (measure) => {
      const m = isObject(measure) ? measure : {};
      const px = (value) => (typeof value === 'number' && isFinite(value) ? value : 0);
      const need = Math.min(px(m.title), px(m.titleMin)) + px(m.nav) + px(m.tools) + 2 * px(m.gap);
      return px(m.bar) >= need + (m.oneRow ? 0 : px(m.hysteresis));
    };

    return Object.freeze({
      entriesFromLinks: entriesFromLinks, normalizeSearch: normalizeSearch, filterEntries: filterEntries, adjacent: adjacent,
      visibleId: visibleId, listMove: listMove, shortcut: shortcut, searchKeyAction: searchKeyAction,
      focusableAfter: focusableAfter, searchTabTarget: searchTabTarget, withCounts: withCounts,
      toolbarFitsOneRow: toolbarFitsOneRow
    });
  })();

  // ── 8. Outbox ─────────────────────────────────────────────────────────────────────────────────────────────────

  const Outbox = (() => {
    let sequence = 0;

    const CSRF_CODES = words('invalid_csrf invalid_origin');
    const WRITE_RULES = [
      [(s) => s >= 200 && s < 300, 'done'],
      [(s) => s === 401, 'auth'],
      [(s, c) => s === 403 && oneOf(CSRF_CODES, c), 'csrf'],
      [(s, c) => s === 403 && c === 'password_change_required', 'password'],
      [(s) => s === 403, 'reject'],
      [(s, c) => (s === 409 && (c === 'needs_migration' || c === 'invalid_log')) || s === 507, 'blocked'],
      [(s) => s === 429 || s >= 500, 'retry']
    ];
    // Klasy: done, offline, auth, csrf, password, retry, blocked, reject.
    const classifyWrite = (status, code) => {
      if (typeof status !== 'number' || !status) return 'offline';
      return firstRule(WRITE_RULES, 'reject', [status, code]);
    };

    const entriesOf = (list) => uniqueBy(asArray(list).filter((item) => isObject(item) && isStr(item.cid)), (item) => item.cid);

    // Scalenie po `cid`: najpierw zapisane (localStorage, inne karty), potem tylko lokalne; usunięte znikają z obu.
    const mergeItems = (stored, local, removed) => {
      const gone = new Set(asArray(removed).filter((cid) => typeof cid === 'string'));
      return uniqueBy(entriesOf(stored).concat(entriesOf(local)), (item) => item.cid).filter((item) => !gone.has(item.cid));
    };

    const itemAccepted = (item) => isObject(item) && isStr(item.cid) && oneOf(QUEUEABLE_TYPES, item.type) && isObject(item.payload);
    const itemsAccepted = (items) => asArray(items).filter(itemAccepted);

    const pollDelay = (visible, errors) => {
      const failures = Math.min(30, Math.max(0, Math.floor(Number(errors) || 0)));
      return Math.min(300000, (visible ? 5000 : 30000) * Math.pow(2, failures));
    };

    const randomUnit = () => {
      const crypto = typeof globalThis !== 'undefined' ? globalThis.crypto : null;
      if (crypto && typeof crypto.getRandomValues === 'function') return crypto.getRandomValues(new Uint32Array(1))[0] / 4294967296;
      return Math.random();
    };

    // Identyfikator klienta: `<prefiks>-<czas base36>-<licznik><losowe>`; zgodny z wzorcami serwera (cid, th-, m-).
    const newClientId = (prefix, nowMs, random) => {
      const time = Math.floor(typeof nowMs === 'number' && isFinite(nowMs) ? nowMs : Date.now());
      const draw = typeof random === 'function' ? random : randomUnit;
      sequence = (sequence + 1) % 1296;
      const randomTail = Array.from({ length: 10 }, () => Math.floor(draw() * 36).toString(36).slice(-1)).join('');
      return String(isStr(prefix) ? prefix : 'c') + '-' + time.toString(36) + '-' + ('0' + sequence.toString(36)).slice(-2) + randomTail;
    };

    // Deterministyczny skrót 128 bitów (cyrb128) zapisany jako 26 znaków [0-9a-v].
    const DIGEST_SEED = [1779033703, 3144134277, 1013904242, 2773480762];
    const DIGEST_MULTIPLIERS = [597399067, 2869860233, 951274213, 2716044179];
    const DIGEST_SHIFTS = [18, 22, 17, 19];
    const digestOf = (text) => {
      const lanes = DIGEST_SEED.slice();
      for (let position = 0; position < text.length; position += 1) {
        const code = text.charCodeAt(position);
        lanes.forEach((lane, i) => {
          lanes[i] = lanes[(i + 1) % 4] ^ Math.imul(lane ^ code, DIGEST_MULTIPLIERS[i]);
        });
      }
      lanes.forEach((lane, i) => {
        lanes[i] = Math.imul(lanes[(i + 2) % 4] ^ (lane >>> DIGEST_SHIFTS[i]), DIGEST_MULTIPLIERS[i]);
      });
      lanes[0] ^= lanes[1] ^ lanes[2] ^ lanes[3];
      [1, 2, 3].forEach((i) => { lanes[i] ^= lanes[0]; });
      const bits = lanes.map((lane) => (lane >>> 0).toString(2).padStart(32, '0')).join('') + '00';
      return Array.from({ length: 26 }, (unused, i) => parseInt(bits.slice(i * 5, i * 5 + 5), 2).toString(32)).join('');
    };

    const V1_LABELS = { th: 'th-', m: 'm-' };
    const v1ImportId = (kind, source) =>
      (has(V1_LABELS, kind) ? V1_LABELS[kind] : 'c-v1-') + digestOf(String(kind) + ':' + String(source));

    // Operacje v1 z localStorage -> pozycje kolejki v2 (K31). Kontekst: knownIds, knownThreads (zbiory kluczy), newId(prefiks),
    // idFor(rodzaj, id v1) dla importu powtarzalnego (wtedy pozycje mają `cid`).
    const convertV1 = (operations, context) => {
      const ctx = isObject(context) ? context : {};
      const known = {
        ids: isObject(ctx.knownIds) ? ctx.knownIds : {},
        threads: isObject(ctx.knownThreads) ? ctx.knownThreads : {}
      };
      const idFor = typeof ctx.idFor === 'function' ? ctx.idFor : null;
      const mintId = typeof ctx.newId === 'function' ? ctx.newId : (prefix) => newClientId(prefix);
      const identify = (kind, source) => (idFor ? idFor(kind, source) : mintId(kind));
      const mapping = emptyMap();

      const wrapText = (message) => {
        const author = isStr(message.author) ? message.author : '';
        const head = 'Wpis z poprzedniej wersji prototypu' + (author ? ' (autor: ' + author + ')' : '') + ':\n';
        return truncateUtf16(head + strOr(message.text, ''), MAX_TEXT);
      };
      const messageOf = (operation, message) => ({ id: identify('m', operation.id), text: wrapText(message) });
      const anchoredPayload = (payload) => ({
        screen: payload.screen, anchor: Shapes.screenAnchor(), label: truncateUtf16(strOr(payload.label, ''), MAX_LABEL)
      });

      // Konwertery wg typu: zwracają { payload } albo null (pominięcie); wątek docelowy dostaje `threadId`.
      const converters = {
        reply: (operation, payload) => (isObject(payload.message) ? { message: messageOf(operation, payload.message) } : null),
        resolve: () => ({ reason: 'author' }),
        reopen: () => ({}),
        reanchor: (operation, payload) => (typeof payload.screen === 'string' ? anchoredPayload(payload) : null)
      };

      const convertOne = (operation) => {
        const payload = payloadOf(operation);
        if (operation.type === 'create') {
          if (has(known.threads, operation.threadId) || has(mapping, operation.threadId)) return null;
          if (!isObject(payload.message) || typeof payload.screen !== 'string') return null;
          const threadId = identify('th', operation.threadId);
          mapping[operation.threadId] = threadId;
          return Shapes.queueItem({
            type: 'create', threadId: threadId,
            payload: Object.assign({ message: messageOf(operation, payload.message) }, anchoredPayload(payload))
          });
        }
        let threadId = null;
        if (has(mapping, operation.threadId)) threadId = mapping[operation.threadId];
        else if (has(known.threads, operation.threadId)) threadId = operation.threadId;
        if (threadId === null) return null;
        const body = converters[operation.type](operation, payload);
        return body === null ? null : Shapes.queueItem({ type: operation.type, threadId: threadId, payload: body });
      };

      const seen = new Set();
      const usable = asArray(operations).filter((operation) => {
        const complete = isObject(operation) && isStr(operation.id) && isStr(operation.type) && isStr(operation.threadId) &&
          isStr(operation.at);
        if (!complete || seen.has(operation.id)) return false;
        seen.add(operation.id);
        return true;
      }).sort(Journal.compare);
      const deleted = new Set(usable.filter((operation) => operation.type === 'delete').map((operation) => operation.threadId));

      return usable.filter((operation) => oneOf(V1_IMPORT_TYPES, operation.type) && !deleted.has(operation.threadId) &&
        !has(known.ids, operation.id)).reduce((items, operation) => {
        const item = convertOne(operation);
        if (item === null) return items;
        if (idFor) item.cid = idFor('c', operation.id);
        return items.concat([item]);
      }, []);
    };

    return Object.freeze({
      classifyWrite: classifyWrite, mergeItems: mergeItems, itemAccepted: itemAccepted, itemsAccepted: itemsAccepted,
      pollDelay: pollDelay, newClientId: newClientId, v1ImportId: v1ImportId, convertV1: convertV1
    });
  })();

  // ── 9. Panel ──────────────────────────────────────────────────────────────────────────────────────────────────

  // Widok wątku: `view` w `create` (comments-format.md), flaga serwera `views`, filtr widoku w panelu.
  const Views = (() => {
    const DESKTOP = 'desktop';
    const MOBILE = 'mobile';
    const FILTER_ALL = 'all';
    const FEATURE = 'views';

    // Widok wątku z payloadu `create`: tylko dokładnie napis "mobile" daje mobile, wszystko inne desktop.
    const ofPayload = (payload) => (isObject(payload) && payload.view === MOBILE ? MOBILE : DESKTOP);
    const normalize = (view) => (view === MOBILE ? MOBILE : DESKTOP);
    // Pola payloadu `create` dla widoku ekranu: tylko mobile zapisuje `view`.
    const payloadFields = (view) => (view === MOBILE ? { view: MOBILE } : {});
    const serverSupports = (server) => isObject(server) && Array.isArray(server.features) && server.features.indexOf(FEATURE) >= 0;
    // Czy wolno tworzyć komentarz w danym widoku przy danym serwerze (server z sesji, null = nieznany).
    const createDecision = (view, server) => {
      if (normalize(view) !== MOBILE || serverSupports(server)) return { allowed: true, reason: null };
      return { allowed: false, reason: 'Ten serwer nie obsługuje komentarzy w widoku mobile — zaktualizuj Skillsync.' };
    };
    const normalizeFilter = (filter) => (filter === DESKTOP || filter === MOBILE ? filter : FILTER_ALL);
    const matchesFilter = (thread, filter) => {
      const wanted = normalizeFilter(filter);
      return wanted === FILTER_ALL || (isObject(thread) && normalize(thread.view) === wanted);
    };

    // Widok efektywny ekranu: widok lokalny (przełącznik przy ekranie); bez niego i dla ekranu bez ramki to desktop.
    const effective = (localView, hasFrame) => (hasFrame && localView === MOBILE ? MOBILE : DESKTOP);
    // Szerokość iframe widoku mobile: min(375, dostępna). Funkcja kontraktu (eksport `viewFrameWidth`); w UI tę samą regułę
    // stosuje CSS: `width: min(var(--proto-view-mobile-width), 100%)` w prototype.css — przy zmianie zmień oba miejsca.
    const MOBILE_WIDTH = 375;
    const frameWidth = (available) => {
      const room = typeof available === 'number' && isFinite(available) ? available : MOBILE_WIDTH;
      return Math.max(0, Math.min(MOBILE_WIDTH, room));
    };

    // Wiadomości strona <-> iframe widoku (`postMessage`). Kierunek 'toPage' (z iframe) i 'toFrame' (do iframe).
    const MESSAGE_PREFIX = 'skillsync-proto:';
    const MESSAGE_TYPES = Object.freeze({
      toPage: words('ready height pick pin-open navigate escape'),
      toFrame: words('state threads focus-return')
    });
    const finite = (value) => typeof value === 'number' && isFinite(value);
    const unit = (value) => (finite(value) ? Math.min(1, Math.max(0, value)) : 0.5);
    const shortText = (value, limit) => truncateUtf16(strOr(value, ''), limit);
    const cleanAnchor = (anchor) => {
      if (!isObject(anchor)) return null;
      const kind = anchor.kind === 'element' || anchor.kind === 'screen' ? anchor.kind : null;
      if (kind === null || (kind === 'element' && !isStr(anchor.protoId))) return null;
      const clean = { kind: kind, x: unit(anchor.x), y: unit(anchor.y) };
      if (kind === 'element') clean.protoId = truncateUtf16(anchor.protoId, 200);
      return clean;
    };
    const cleanFlags = (flags) => {
      const source = isObject(flags) ? flags : {};
      return { hotspots: source.hotspots === true, pinsHidden: source.pinsHidden === true, presentation: source.presentation === true };
    };
    const cleanThread = (thread) => {
      if (!isObject(thread) || !isStr(thread.id) || !isInt(thread.number) || !oneOf(STATUSES, thread.status)) return null;
      const anchor = cleanAnchor(thread.anchor);
      return anchor ? { id: truncateUtf16(thread.id, 200), number: thread.number, status: thread.status, anchor: anchor } : null;
    };
    const MESSAGE_FIELDS = Object.assign(emptyMap(), {
      ready: () => ({}),
      height: (data) => (finite(data.height) && data.height >= 0 && data.height <= 100000 ? { height: data.height } : null),
      pick: (data) => {
        const anchor = cleanAnchor(data.anchor);
        const rect = isObject(data.rect) ? data.rect : {};
        if (!anchor || anchor.kind !== 'element' || !['left', 'top', 'right', 'bottom'].every((key) => finite(rect[key]))) return null;
        return { anchor: anchor, label: shortText(data.label, MAX_LABEL), hint: shortText(data.hint, 500),
          rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom } };
      },
      'pin-open': (data) => (isStr(data.threadId) ? { threadId: truncateUtf16(data.threadId, 200) } : null),
      navigate: (data) => (typeof data.hash === 'string' && /^#s\d+$/.test(data.hash) ? { hash: data.hash } : null),
      escape: () => ({}),
      state: (data) => ({ theme: data.theme === 'dark' ? 'dark' : 'light', flags: cleanFlags(data.flags), commentMode: data.commentMode === true,
        thread: isStr(data.thread) ? truncateUtf16(data.thread, 200) : null }),
      threads: (data) => ({ screen: shortText(data.screen, 200),
        threads: asArray(data.threads).slice(0, 500).map(cleanThread).filter((thread) => thread !== null) }),
      'focus-return': (data) => ({ protoId: isStr(data.protoId) ? truncateUtf16(data.protoId, 200) : null })
    });
    // Zwraca { type, ...pola } (typ bez prefiksu) albo null, gdy to nie jest dozwolona wiadomość tego kierunku.
    const parseMessage = (data, direction) => {
      if (!isObject(data) || typeof data.type !== 'string' || data.type.indexOf(MESSAGE_PREFIX) !== 0) return null;
      const name = data.type.slice(MESSAGE_PREFIX.length);
      if (!has(MESSAGE_TYPES, direction) || !oneOf(MESSAGE_TYPES[direction], name)) return null;
      const fields = MESSAGE_FIELDS[name](data);
      return fields === null ? null : Object.assign({ type: name }, fields);
    };
    const composeMessage = (name, fields) => Object.assign({ type: MESSAGE_PREFIX + name }, fields);
    // Poza `file://` odbiorca wymaga zgodnego pochodzenia; przy `file://` pochodzenie jest nieprzejrzyste ("null").
    const originAllowed = (protocol, origin, own) => protocol === 'file:' || (isStr(origin) && origin === own);
    const targetOrigin = (protocol, own) => (protocol === 'file:' ? '*' : own);
    // Wątki mobile danego ekranu w kształcie potrzebnym do pinezek (bez treści wiadomości).
    const frameThreads = (threads, screen) => asArray(threads).filter((thread) => isObject(thread) && thread.screen === screen &&
      normalize(thread.view) === MOBILE && isObject(thread.anchor)).map((thread) => ({
      id: thread.id, number: thread.number, status: thread.status,
      anchor: { kind: thread.anchor.kind, protoId: thread.anchor.protoId, x: thread.anchor.x, y: thread.anchor.y }
    }));

    return Object.freeze({ ofPayload: ofPayload, normalize: normalize, payloadFields: payloadFields, serverSupports: serverSupports,
      createDecision: createDecision, normalizeFilter: normalizeFilter, matchesFilter: matchesFilter, effective: effective,
      frameWidth: frameWidth, parseMessage: parseMessage, composeMessage: composeMessage, originAllowed: originAllowed,
      targetOrigin: targetOrigin, frameThreads: frameThreads, MOBILE_WIDTH: MOBILE_WIDTH });
  })();

  const Panel = (() => {
    const hasHandle = (context, thread) =>
      !(isObject(context) && typeof context.hasHandle === 'function') || !!context.hasHandle(thread);

    const hasNewEvent = (thread, context) => isStr(context.seenAt) && asArray(thread.events).some((event) =>
      isObject(event) && typeof event.at === 'string' && event.by !== context.me && event.at > context.seenAt);

    // Test wątku dla każdego filtra; nieznany filtr nie przepuszcza niczego.
    const FILTER_TESTS = Object.assign(emptyMap(), {
      all: () => true,
      open: (thread, ctx) => isOpenStatus(thread.status) && hasHandle(ctx, thread),
      lost: (thread, ctx) => isOpenStatus(thread.status) && !hasHandle(ctx, thread),
      agent: (thread) => !!thread.triage || asArray(thread.decisions).length > 0,
      new: hasNewEvent
    });

    const matches = (thread, filter, context) => {
      if (!isObject(thread)) return false;
      const test = FILTER_TESTS[filter];
      const ctx = isObject(context) ? context : {};
      // Filtr statusu i filtr widoku (`context.viewFilter`) łączy iloczyn.
      return test ? Views.matchesFilter(thread, ctx.viewFilter) && test(thread, ctx) : false;
    };

    // Wątki filtra pogrupowane po ekranach: [{screen, threads}], ekrany według `context.screenOrder` albo numeru w identyfikatorze.
    const grouped = (threads, filter, context) => {
      const ctx = isObject(context) ? context : {};
      const byScreen = new Map();
      asArray(threads).filter((thread) => matches(thread, filter, ctx)).forEach((thread) => {
        if (!byScreen.has(thread.screen)) byScreen.set(thread.screen, []);
        byScreen.get(thread.screen).push(thread);
      });
      const preferred = Array.isArray(ctx.screenOrder) ? ctx.screenOrder : null;
      const order = new Map();
      if (preferred) preferred.forEach((screen, at) => { if (!order.has(screen)) order.set(screen, at); });
      const rank = (screen) => (order.has(screen) ? order.get(screen) : preferred.length);
      const compareScreens = (a, b) => {
        if (preferred && (order.has(a) || order.has(b))) return rank(a) - rank(b);
        return compareNatural(a, b);
      };
      return Array.from(byScreen.keys()).sort(compareScreens).map((screen) => ({ screen: screen, threads: byScreen.get(screen) }));
    };

    const counts = (threads, context) => FILTERS.reduce((table, filter) =>
      Object.assign(table, { [filter]: asArray(threads).filter((thread) => matches(thread, filter, context)).length }), {});

    return Object.freeze({ matches: matches, grouped: grouped, counts: counts });
  })();

  // ── 10. People ────────────────────────────────────────────────────────────────────────────────────────────────

  const People = (() => {
    const wordsFromLogin = (login) => login.split('@')[0].split('.').filter((part) => part !== '').map(upperFirst);

    const personFromLogin = (login, name) => {
      const text = typeof login === 'string' ? login : '';
      const named = typeof name === 'string' ? splitWords(name) : [];
      const parts = named.length ? named : wordsFromLogin(text);
      return Shapes.person({
        login: text, firstName: parts.length ? parts[0] : text, surname: parts.slice(1).join(' '), fullName: parts.join(' ') || text
      });
    };

    const shortLabel = (person) => (person.surname ?
      person.firstName + ' ' + firstCodePoint(person.surname).toUpperCase() + '.' : person.firstName);

    const computeLabels = (logins, names) => {
      const overrides = isObject(names) ? names : {};
      const roster = emptyMap();
      const order = [];
      asArray(logins).filter(isStr).forEach((login) => {
        if (has(roster, login)) return;
        roster[login] = personFromLogin(login, has(overrides, login) ? overrides[login] : undefined);
        order.push(login);
      });
      const families = new Map();
      order.forEach((login) => {
        const first = roster[login].firstName;
        families.set(first, (families.get(first) || []).concat([login]));
      });
      const labels = emptyMap();
      families.forEach((members, first) => {
        const shorts = members.map((login) => shortLabel(roster[login]));
        members.forEach((login, at) => {
          const clash = shorts.filter((candidate) => candidate === shorts[at]).length > 1;
          const label = members.length === 1 ? first : (clash ? roster[login].fullName : shorts[at]);
          labels[login] = Shapes.label({ firstName: first, fullName: roster[login].fullName, label: label });
        });
      });
      return labels;
    };

    const fromOperations = (operations, names) => {
      const authors = uniqueBy(Journal.normalize(operations).map(authorOf).filter((by) => by !== null), (by) => by);
      return computeLabels(authors, names);
    };

    const initials = (person) => {
      const source = isObject(person) ? person : {};
      const letter = (value) => (typeof value === 'string' ? firstCodePoint(value).toUpperCase() : '');
      const pair = letter(source.firstName) + letter(source.surname);
      return pair || letter(source.login) || '?';
    };

    const avatarTone = (login) => {
      const text = typeof login === 'string' ? login : '';
      let hash = 2166136261;
      for (const unit of Array.from({ length: text.length }, (unused, at) => text.charCodeAt(at))) {
        hash = Math.imul(hash ^ unit, 16777619) >>> 0;
      }
      return (hash >>> 7) % 8;
    };

    return Object.freeze({
      personFromLogin: personFromLogin, computeLabels: computeLabels, fromOperations: fromOperations, initials: initials,
      avatarTone: avatarTone
    });
  })();

  // ── 11. Journal ───────────────────────────────────────────────────────────────────────────────────────────────

  const Journal = (() => {
    const OPERATION_RULES = [
      (value) => isStr(value.id),
      (value) => isStr(value.at),
      (value) => typeof value.type === 'string' && oneOf(OPERATION_TYPES, value.type),
      (value) => !oneOf(THREAD_TYPES, value.type) || isStr(value.threadId)
    ];
    const isOperation = (value) => isObject(value) && OPERATION_RULES.every((rule) => rule(value));

    // Kroki 1-2 foldu: odrzuć niepoprawne, przy powtórzonym `id` wygrywa pierwsza poprawna (kolejność tablicy).
    const normalize = (operations) => uniqueBy(asArray(operations).filter(isOperation), (operation) => operation.id);

    // (at, id) w jednostkach UTF-16, jak operator `<`.
    const compare = (a, b) => compareStrings(a.at, b.at) || compareStrings(a.id, b.id);
    const sort = (operations) => asArray(operations).slice().sort(compare);
    const merge = (a, b) => sort(normalize(asArray(a).concat(asArray(b))));

    const canonical = (operation) => {
      const source = isObject(operation) ? operation : {};
      const result = emptyMap();
      const fixed = OPERATION_KEYS.filter((key) => has(source, key));
      fixed.concat(Object.keys(source)).forEach((key) => {
        if (!has(result, key)) result[key] = source[key];
      });
      return result;
    };

    const serialize = (prototypeId, operations) => {
      const head = ['{"format":', JSON.stringify(FORMAT_NAME), ',"version":', FORMAT_VERSION, ',"prototypeId":',
        JSON.stringify(String(prototypeId)), ',"operations":['].join('');
      const lines = asArray(operations).filter(isObject).map((operation) => JSON.stringify(canonical(operation)));
      return head + '\n' + (lines.length ? lines.join(',\n') + '\n' : '') + ']}\n';
    };

    // Dokument obcego formatu, wersji albo prototypu jest ignorowany w całości (tryb tylko do odczytu).
    const readDocument = (doc, slug) => {
      const foreign = !isObject(doc) || doc.format !== FORMAT_NAME || doc.version !== FORMAT_VERSION || !Array.isArray(doc.operations);
      if (foreign) return [];
      const owner = doc.prototypeId;
      if (owner !== undefined && owner !== null && typeof owner !== 'string') return [];
      if (isStr(owner) && isStr(slug) && owner !== slug) return [];
      return normalize(doc.operations);
    };

    return Object.freeze({
      isOperation: isOperation, normalize: normalize, compare: compare, sort: sort, merge: merge, canonical: canonical,
      serialize: serialize, readDocument: readDocument
    });
  })();

  // ── 12. Fold ──────────────────────────────────────────────────────────────────────────────────────────────────

  const Fold = (() => {
    // Wynik reducera wątku: zastosowano / pominięto / wycofanie wpisu otwierającego ukrywa cały wątek.
    const APPLIED = true;
    const SKIPPED = false;
    const HIDE = 'hide';

    const buildMessage = (raw, by, at, opening) => {
      const source = isObject(raw) ? raw : {};
      return Shapes.message({
        id: strOr(source.id, null), text: strOr(source.text, ''), by: by, at: at, opening: opening,
        importedAuthor: strOr(source.importedAuthor, null)
      });
    };

    const messageById = (thread, messageId) => {
      if (!thread || !isStr(messageId)) return null;
      return thread.messages.find((message) => message.id === messageId) || null;
    };

    // Wiadomość, którą `by` może zmieniać (istnieje, nie wycofana, jego autorstwa).
    const ownMessage = (thread, payload, by) => {
      const message = messageById(thread, payload.messageId);
      return message && !message.retracted && by !== null && message.by === by ? message : null;
    };

    // Reducery wątku wg typu operacji: (wątek, operacja, payload, autor) -> APPLIED | SKIPPED | HIDE.
    const THREAD_REDUCERS = Object.assign(emptyMap(), {
      reply: (thread, operation, payload, by) => {
        if (!payload.message) return SKIPPED;
        const replyId = isObject(payload.message) ? strOr(payload.message.id, null) : null;
        if (thread.messages.some((item) => item.id === replyId)) return SKIPPED;
        thread.messages.push(buildMessage(payload.message, by, operation.at, false));
        return APPLIED;
      },
      edit: (thread, operation, payload, by) => {
        const message = ownMessage(thread, payload, by);
        if (!message || typeof payload.text !== 'string') return SKIPPED;
        Object.assign(message, { text: payload.text, edited: true, editedAt: operation.at });
        return APPLIED;
      },
      retract: (thread, operation, payload, by) => {
        const message = ownMessage(thread, payload, by);
        if (!message) return SKIPPED;
        message.retracted = true;
        const answered = thread.messages.some((item) => !item.opening && !item.retracted && item.by !== message.by);
        return message.opening && !answered ? HIDE : APPLIED;
      },
      vote: (thread, operation, payload, by) => {
        const message = messageById(thread, payload.messageId);
        const value = payload.value;
        if (!message || message.retracted || by === null || typeof value !== 'string' || !oneOf(VOTE_VALUES, value)) return SKIPPED;
        if (value === 'none') delete message.votes[by];
        else message.votes[by] = value;
        return APPLIED;
      },
      resolve: (thread, operation, payload) => {
        Object.assign(thread, { status: 'closed', statusReason: strOr(payload.reason, null) });
        return APPLIED;
      },
      reopen: (thread) => {
        Object.assign(thread, { status: 'open', statusReason: null });
        return APPLIED;
      },
      reanchor: (thread, operation, payload) => {
        if (typeof payload.screen !== 'string') return SKIPPED;
        Object.assign(thread, {
          screen: payload.screen, anchor: isObject(payload.anchor) ? payload.anchor : null, label: strOr(payload.label, '')
        });
        return APPLIED;
      },
      triage: (thread, operation, payload, by) => {
        const klass = payload.class;
        if (typeof klass !== 'string' || !oneOf(TRIAGE_CLASSES, klass)) return SKIPPED;
        thread.triage = Shapes.triage({ class: klass, note: strOr(payload.note, null), at: operation.at, by: by });
        if (klass === 'disputed') Object.assign(thread, { status: 'awaiting', statusReason: null });
        return APPLIED;
      },
      decision: (thread, operation, payload, by) => {
        const kind = payload.kind;
        const outcome = payload.outcome;
        if (typeof kind !== 'string' || !oneOf(DECISION_KINDS, kind) || typeof outcome !== 'string' || !has(DECISION_STATUS, outcome)) {
          return SKIPPED;
        }
        thread.decisions.push(Shapes.decision({
          id: operation.id, kind: kind, who: strOr(payload.who, null), outcome: outcome, text: strOr(payload.text, ''),
          iteration: isIntegral(payload.iteration) ? payload.iteration : null, at: operation.at, by: by
        }));
        Object.assign(thread, { status: DECISION_STATUS[outcome][0], statusReason: DECISION_STATUS[outcome][1] });
        return APPLIED;
      }
    });

    // Reducery operacji na poziomie prototypu: (stan foldu, operacja, payload).
    const PROTOTYPE_REDUCERS = Object.assign(emptyMap(), {
      accept: (state, operation, payload) => {
        const screens = payload.screens;
        if (!isObject(screens)) return;
        const by = authorOf(operation);
        const viaCli = payload.via === 'cli' && isStr(payload.who);
        Object.keys(screens).filter((screen) => isStr(screens[screen])).forEach((screen) => {
          state.acceptance[screen] = Shapes.acceptEntry({
            hash: screens[screen], by: by, who: viaCli ? payload.who : by, at: operation.at,
            iteration: isIntegral(payload.iteration) ? payload.iteration + 0 : null, scope: strOr(payload.scope, null),
            via: strOr(payload.via, null), opId: operation.id
          });
        });
      },
      unaccept: (state, operation, payload) => {
        asArray(payload.screens).filter(isStr).forEach((screen) => { delete state.acceptance[screen]; });
      },
      iteration: (state, operation, payload) => {
        if (isIntegral(payload.number) && payload.number >= 1) state.iteration = Math.max(state.iteration, payload.number);
      },
      restore: () => undefined
    });

    const openThread = (state, operation, payload, by) => {
      const threadId = operation.threadId;
      if (!payload.message || typeof payload.screen !== 'string') return;
      state.threads[threadId] = Shapes.thread({
        id: threadId, number: state.numbers[threadId], screen: payload.screen,
        anchor: isObject(payload.anchor) ? payload.anchor : null, label: strOr(payload.label, ''),
        hint: isObject(payload.hint) ? payload.hint : null, by: by, at: operation.at, view: Views.ofPayload(payload),
        messages: [buildMessage(payload.message, by, operation.at, true)], lastAt: operation.at,
        events: [Shapes.event({ type: 'create', by: by, at: operation.at })]
      });
      state.order.push(threadId);
    };

    const step = (state, operation) => {
      const payload = payloadOf(operation);
      const protoReducer = PROTOTYPE_REDUCERS[operation.type];
      if (protoReducer) {
        protoReducer(state, operation, payload);
        return;
      }
      const threadId = operation.threadId;
      if (state.deleted.has(threadId) || state.hidden.has(threadId)) return;
      const by = authorOf(operation);
      const thread = has(state.threads, threadId) ? state.threads[threadId] : null;
      if (operation.type === 'create') {
        if (!thread) openThread(state, operation, payload, by);
        return;
      }
      if (!thread) return;
      const reducer = THREAD_REDUCERS[operation.type];
      const verdict = reducer ? reducer(thread, operation, payload, by) : SKIPPED;
      if (verdict === HIDE) state.hidden.add(threadId);
      else if (verdict) {
        thread.events.push(Shapes.event({ type: operation.type, by: by, at: operation.at }));
        thread.lastAt = operation.at;
      }
    };

    // Numer wątku = kolejność pierwszego poprawnego `create` (także wątków później usuniętych).
    const numberThreads = (ordered) => {
      const numbers = emptyMap();
      let counted = 0;
      ordered.forEach((operation) => {
        if (operation.type !== 'create' || has(numbers, operation.threadId)) return;
        const payload = payloadOf(operation);
        if (payload.message && typeof payload.screen === 'string') {
          counted += 1;
          numbers[operation.threadId] = counted;
        }
      });
      return numbers;
    };

    const settle = (state) => {
      const removed = emptyMap();
      state.hidden.forEach((threadId) => { removed[threadId] = 'retracted'; });
      state.deleted.forEach((threadId) => { removed[threadId] = 'deleted'; });
      const visible = state.order.filter((threadId) => !state.hidden.has(threadId)).map((threadId) => state.threads[threadId]);
      const byId = emptyMap();
      visible.forEach((thread) => { byId[thread.id] = thread; });
      const acceptance = emptyMap();
      Object.keys(state.acceptance).sort().forEach((screen) => { acceptance[screen] = state.acceptance[screen]; });
      return Shapes.foldState({
        threads: visible, currentIteration: state.iteration + 1, removed: removed, acceptance: acceptance, byId: byId
      });
    };

    // Fold v2 (identyczny z `fold_operations` generatora). Stan zależy od (at, id), nie od kolejności w tablicy.
    const foldOperations = (operations) => {
      const ordered = Journal.sort(Journal.normalize(operations));
      const state = {
        deleted: new Set(ordered.filter((operation) => operation.type === 'delete').map((operation) => operation.threadId)),
        numbers: numberThreads(ordered), hidden: new Set(), threads: emptyMap(), order: [], acceptance: emptyMap(), iteration: 0
      };
      ordered.forEach((operation) => step(state, operation));
      return settle(state);
    };

    const foldDocument = (doc) => foldOperations(Array.isArray(doc) ? doc : (isObject(doc) ? doc.operations : []));

    const threadsOf = (state) => {
      if (Array.isArray(state)) return state;
      return isObject(state) && Array.isArray(state.threads) ? state.threads : [];
    };

    const openCountByScreen = (state) => {
      const counts = emptyMap();
      threadsOf(state).filter((thread) => isObject(thread) && isOpenStatus(thread.status)).forEach((thread) => {
        counts[thread.screen] = (counts[thread.screen] || 0) + 1;
      });
      return counts;
    };

    const voteSummary = (message) => {
      const votes = isObject(message) && isObject(message.votes) ? message.votes : {};
      const voters = (side) => Object.keys(votes).filter((login) => votes[login] === side).sort();
      return { up: voters('up'), down: voters('down') };
    };

    // Wątek albo sama kotwica: `element` (z niepustym `protoId`), `lost`, w pozostałych przypadkach `screen`.
    const anchorKind = (value) => {
      const wrapped = isObject(value) && !has(value, 'kind') && has(value, 'anchor');
      const anchor = wrapped ? value.anchor : value;
      if (!isObject(anchor)) return 'screen';
      if (anchor.kind === 'element' && isStr(anchor.protoId)) return 'element';
      return anchor.kind === 'lost' ? 'lost' : 'screen';
    };

    return Object.freeze({
      foldOperations: foldOperations, foldDocument: foldDocument, openCountByScreen: openCountByScreen,
      voteSummary: voteSummary, anchorKind: anchorKind
    });
  })();

  // ── 13. Interfejs w przeglądarce ──────────────────────────────────────────────────────────────────────────────
  // Stan strony, klient API (Remote), panel wątków, okna (logowanie, akceptacja, przywracanie, porównanie), pasek z przełącznikiem
  // ekranów, pinezki i kolejka offline. Moduły UI łączy szyna zdarzeń (Bus); nic z tej części nie trafia do eksportów.

  function readTheme(win) {
    try {
      const stored = win.localStorage.getItem(THEME_KEY);
      if (stored === 'light' || stored === 'dark') return stored;
    } catch (error) { /* brak dostępu do pamięci przeglądarki */ }
    try {
      return win.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    } catch (error) {
      return 'light';
    }
  }

  function applyTheme(win, doc) {
    const theme = readTheme(win);
    const root = doc.documentElement;
    if (!root) return theme;
    root.setAttribute('data-theme', theme);
    if (root.style) root.style.colorScheme = theme;
    return theme;
  }

  // Wczesny start (przed załadowaniem treści): blokada trybu ciemnego rozszerzeń i motyw prototypu.
  function bootEarly(win, doc) {
    Appearance.ensureMetas(doc);
    applyTheme(win, doc);
    const parents = [doc.documentElement, doc.head];
    Appearance.sweep(doc, parents);
    if (typeof win.MutationObserver === 'function') {
      const observer = new win.MutationObserver((records) => {
        asArray(records).forEach((record) => {
          Array.prototype.slice.call(record.addedNodes || []).forEach((node) => {
            if (Appearance.isLeftover(node) && node.parentNode && typeof node.parentNode.removeChild === 'function') {
              node.parentNode.removeChild(node);
            }
          });
        });
      });
      parents.forEach((parent) => {
        if (parent) observer.observe(parent, { childList: true });
      });
      if (typeof win.addEventListener === 'function') win.addEventListener('pagehide', () => observer.disconnect());
    }
  }

  // ── 13a. UI: podstawy (DOM, szyna zdarzeń, klawiatura) ───────────────────────────────────────────────────────────

  // Mała fabryka elementów: właściwości jako atrybuty (`text`, `class` mają skróty), dzieci jako węzły albo napisy.
  const Dom = (() => {
    const make = (tag, props, kids) => {
      const node = document.createElement(tag);
      Object.keys(props || {}).forEach((name) => {
        const value = props[name];
        if (value === null || value === undefined || value === false) return;
        if (name === 'text') node.textContent = value;
        else if (name === 'class') node.className = value;
        else node.setAttribute(name, value === true ? '' : String(value));
      });
      asArray(kids).forEach((kid) => node.appendChild(typeof kid === 'string' ? document.createTextNode(kid) : kid));
      return node;
    };
    const isTyping = (node) => !!node && (/^(INPUT|TEXTAREA|SELECT)$/.test(node.tagName) || node.isContentEditable === true);
    const reducedMotion = () => {
      try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (error) { return false; }
    };
    const neutralFocus = () => !document.activeElement || document.activeElement === document.body ||
      document.activeElement === document.documentElement;
    const openDialog = () => !!document.querySelector('dialog[open]');
    return Object.freeze({ make: make, isTyping: isTyping, reducedMotion: reducedMotion, neutralFocus: neutralFocus,
      openDialog: openDialog });
  })();

  // Grupa przycisków Desktop / Mobile (pasek, ekran, okna porównania i przywracania): `paint(widok)` ustawia `aria-pressed`.
  const ViewControls = (() => {
    const CHOICES = Object.freeze([['desktop', 'Desktop'], ['mobile', 'Mobile']]);
    const make = (attrs, onPick) => {
      const node = Dom.make('div', Object.assign({ class: 'segmented', role: 'group' }, attrs));
      const buttons = emptyMap();
      CHOICES.forEach((choice) => {
        const button = Dom.make('button', { type: 'button', 'aria-pressed': 'false', text: choice[1] });
        button.addEventListener('click', () => onPick(choice[0]));
        buttons[choice[0]] = button;
        node.appendChild(button);
      });
      const paint = (current) => CHOICES.forEach((choice) => buttons[choice[0]].setAttribute('aria-pressed', String(choice[0] === current)));
      return { node: node, buttons: buttons, paint: paint };
    };
    return Object.freeze({ make: make });
  })();

  // Szyna zdarzeń modułów interfejsu (np. zmiana trybu, zmiana bieżącego ekranu).
  const Bus = (() => {
    const listeners = emptyMap();
    return Object.freeze({
      on: (name, fn) => { (listeners[name] = listeners[name] || []).push(fn); },
      // Wyjątek jednego słuchacza nie przerywa pozostałych ani nadawcy (np. odpytywania).
      emit: (name, detail) => {
        asArray(listeners[name]).slice().forEach((fn) => {
          try { fn(detail); } catch (error) { console.error('skillsync-prototype: błąd obsługi zdarzenia ' + name, error); }
        });
      }
    });
  })();

  // Skróty jednoznakowe (C, G, [, ], strzałki ←/→ poza polami) można wyłączyć (WCAG 2.1.4); wybór jest zapamiętany w przeglądarce.
  const Shortcuts = (() => {
    const SINGLE = words('c C g G [ ] ArrowLeft ArrowRight');
    const enabled = () => {
      try { return window.localStorage.getItem(STORAGE.shortcuts) !== 'off'; } catch (error) { return true; }
    };
    const set = (on) => {
      try { window.localStorage.setItem(STORAGE.shortcuts, on ? 'on' : 'off'); } catch (error) { /* wybór tylko na tej karcie */ }
      Bus.emit('shortcuts', on);
    };
    const blocks = (event) => !event.ctrlKey && !event.metaKey && !event.altKey && oneOf(SINGLE, event.key) && !enabled();
    return Object.freeze({ enabled: enabled, set: set, blocks: blocks });
  })();

  // Klawiatura: moduły rejestrują obsługę (kolejność = pierwszeństwo); pierwsza zwracająca true kończy obsługę.
  const Keyboard = (() => {
    const handlers = [];
    const describe = (event) => ({
      key: event.key, ctrlKey: event.ctrlKey, metaKey: event.metaKey, altKey: event.altKey, shiftKey: event.shiftKey,
      isComposing: event.isComposing, typing: Dom.isTyping(event.target), dialog: Dom.openDialog(),
      neutralFocus: Dom.neutralFocus()
    });
    return Object.freeze({
      register: (fn) => { handlers.push(fn); },
      attach: () => {
        document.addEventListener('keydown', (event) => {
          if (event.defaultPrevented || Shortcuts.blocks(event)) return;
          const facts = describe(event);
          if (handlers.some((fn) => fn(event, facts) === true)) event.preventDefault();
        });
      }
    });
  })();

  // ── 13b. UI: stan strony (tryby, motyw, wysokość paska) ───────────────────────────────────────────────────────

  const PageState = (() => {
    const root = () => document.documentElement;
    const params = new URLSearchParams(typeof location !== 'undefined' ? location.search : '');
    const flags = { commentMode: false, hotspots: false, pinsHidden: false, presentation: false };
    const rootClass = { hotspots: 'proto-hotspots', pinsHidden: 'proto-pins-hidden', presentation: 'proto-presentation' };

    const setFlag = (name, on) => {
      flags[name] = !!on;
      if (has(rootClass, name)) root().classList.toggle(rootClass[name], flags[name]);
      Bus.emit('flag:' + name, flags[name]);
    };
    const setCommentMode = (on) => {
      flags.commentMode = !!on;
      root().classList.toggle('proto-comment-mode', flags.commentMode);
      Bus.emit('mode', flags.commentMode);
    };
    const nextTheme = () => (root().getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
    const setTheme = (theme) => {
      root().setAttribute('data-theme', theme);
      root().style.colorScheme = theme;
      try { window.localStorage.setItem(THEME_KEY, theme); } catch (error) { /* pamięć niedostępna: motyw tylko na tę kartę */ }
      Bus.emit('theme', theme);
    };
    // Wysokość paska (px, liczba całkowita) dla arkuszy: panel i lista ekranów zaczynają się pod nim.
    const syncToolbarHeight = (bar) => {
      root().style.setProperty('--proto-toolbar-height', bar.offsetHeight + 'px');
    };
    return Object.freeze({
      flags: flags,
      viewRole: params.get('embed') === 'view',
      embedded: Iterations.embedMode(typeof location !== 'undefined' ? location.href : '') !== '' || params.get('embed') === 'compare',
      frozen: () => root().hasAttribute('data-prototype-frozen'),
      setFlag: setFlag, setCommentMode: setCommentMode, nextTheme: nextTheme, setTheme: setTheme,
      syncToolbarHeight: syncToolbarHeight
    });
  })();

  // ── 13c. UI: pasek prototypu ──────────────────────────────────────────────────────────────────────────────────

  const ToolbarUi = (() => {
    const refs = {};

    const modeButton = (label, pressed) => Dom.make('button', { type: 'button', 'aria-pressed': String(pressed), text: label });

    const toggleButton = (label, flag) => {
      const button = Dom.make('button', { type: 'button', class: 'btn-outline', 'aria-pressed': 'false', text: label });
      button.addEventListener('click', () => PageState.setFlag(flag, !PageState.flags[flag]));
      Bus.on('flag:' + flag, (on) => button.setAttribute('aria-pressed', String(on)));
      return button;
    };

    const buildModeGroup = () => {
      const group = Dom.make('div', { class: 'segmented', role: 'group', 'aria-label': 'Tryb' });
      const clickable = modeButton('Klikalny', true);
      const comments = modeButton('Komentarze (C)', false);
      clickable.addEventListener('click', () => PageState.setCommentMode(false));
      comments.addEventListener('click', () => PageState.setCommentMode(true));
      Bus.on('mode', (on) => {
        clickable.setAttribute('aria-pressed', String(!on));
        comments.setAttribute('aria-pressed', String(on));
      });
      group.appendChild(clickable);
      group.appendChild(comments);
      refs.clickable = clickable;
      refs.comments = comments;
      return group;
    };

    const buildThemeButton = () => {
      const button = Dom.make('button', { type: 'button', class: 'btn-outline', text: 'Motyw', 'aria-label': 'Motyw: jasny lub ciemny' });
      button.addEventListener('click', () => PageState.setTheme(PageState.nextTheme()));
      return button;
    };

    // Menu „⋯” paska (okno węższe niż 600 px): `sources` to przyciski paska, których akcję pozycje tylko uruchamiają.
    const NARROW_QUERY = '(max-width: 599.98px)';
    const buildMore = (sources) => {
      const button = Dom.make('button', { type: 'button', class: 'btn-outline proto-more', 'aria-haspopup': 'menu',
        'aria-expanded': 'false', 'aria-controls': 'proto-more-menu', 'aria-label': 'Więcej', text: '\u22ef' });
      const menu = Dom.make('div', { id: 'proto-more-menu', class: 'proto-more-menu', role: 'menu', 'aria-label': 'Więcej', hidden: true });
      const items = sources.map((source) => {
        const toggle = source.hasAttribute('aria-pressed');
        const item = Dom.make('button', { type: 'button', role: toggle ? 'menuitemcheckbox' : 'menuitem', tabindex: '-1' });
        menu.appendChild(item);
        return { source: source, item: item, toggle: toggle };
      });
      const isOpen = () => !menu.hidden;
      const refresh = () => items.forEach((entry) => {
        entry.item.textContent = entry.source.textContent;
        if (entry.source.hasAttribute('aria-label')) entry.item.setAttribute('aria-label', entry.source.getAttribute('aria-label'));
        if (entry.toggle) entry.item.setAttribute('aria-checked', entry.source.getAttribute('aria-pressed') === 'true' ? 'true' : 'false');
      });
      const place = () => {
        const box = button.getBoundingClientRect();
        const width = menu.offsetWidth;
        const left = Math.max(4, Math.min(box.right - width, window.innerWidth - width - 4));
        const top = box.bottom + 4;
        menu.style.left = left + 'px';
        menu.style.top = top + 'px';
        menu.style.maxHeight = Math.max(48, window.innerHeight - top - 8) + 'px';
      };
      const close = (restoreFocus) => {
        if (!isOpen()) return;
        menu.hidden = true;
        button.setAttribute('aria-expanded', 'false');
        if (restoreFocus) button.focus();
      };
      const focusAt = (index) => {
        const total = items.length;
        items[((index % total) + total) % total].item.focus();
      };
      const open = () => {
        if (isOpen()) return;
        refresh();
        menu.hidden = false;
        button.setAttribute('aria-expanded', 'true');
        place();
        focusAt(0);
      };
      const activate = (entry) => {
        close(true);
        entry.source.click();
      };
      // aria-checked pozycji nadąża za przyciskami paska (akcja z menu, z przycisku i ze skrótu klawiszowego).
      if (typeof MutationObserver === 'function') {
        const watcher = new MutationObserver(refresh);
        sources.forEach((source) => watcher.observe(source, { attributes: true, attributeFilter: ['aria-pressed', 'aria-label'] }));
      }
      button.addEventListener('click', () => { if (isOpen()) close(true); else open(); });
      button.addEventListener('keydown', (event) => {
        if (event.key === 'Tab') { close(false); return; }
        if (event.key === 'Escape' && isOpen()) { event.preventDefault(); event.stopPropagation(); close(true); return; }
        if (event.key === 'Enter' || event.key === ' ' || event.key === 'ArrowDown') {
          event.preventDefault();
          open();
        }
        if (isOpen()) event.stopPropagation();
      });
      button.addEventListener('keyup', (event) => { if (event.key === ' ') event.preventDefault(); });
      menu.addEventListener('keyup', (event) => { if (event.key === ' ') event.preventDefault(); });
      menu.addEventListener('keydown', (event) => {
        const at = items.findIndex((entry) => entry.item === document.activeElement);
        if (event.key === 'Tab') { close(true); return; }
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(true); return; }
        const move = { ArrowDown: at + 1, ArrowUp: at < 0 ? items.length - 1 : at - 1, Home: 0, End: items.length - 1 }[event.key];
        if (move !== undefined) { event.preventDefault(); focusAt(move); return; }
        if ((event.key === 'Enter' || event.key === ' ') && at >= 0) { event.preventDefault(); activate(items[at]); return; }
        event.stopPropagation(); // pozostałe klawisze nie dochodzą do skrótów silnika
      });
      // Otwarte menu pochłania klawiaturę także poza sobą (Tab i Shift+Tab zamykają je i przepuszczają fokus).
      document.addEventListener('keydown', (event) => {
        if (!isOpen() || event.key === 'Tab' || menu.contains(event.target) || button.contains(event.target)) return;
        event.stopPropagation();
        if (event.key === 'Escape') { event.preventDefault(); close(true); }
      }, true);
      items.forEach((entry) => entry.item.addEventListener('click', () => activate(entry)));
      document.addEventListener('pointerdown', (event) => {
        if (isOpen() && !menu.contains(event.target) && !button.contains(event.target)) close(false);
      }, true);
      window.addEventListener('resize', () => { if (isOpen()) place(); });
      if (typeof window.matchMedia === 'function') {
        const narrow = window.matchMedia(NARROW_QUERY);
        const onChange = (event) => { if (!event.matches) close(false); };
        if (typeof narrow.addEventListener === 'function') narrow.addEventListener('change', onChange);
        else if (typeof narrow.addListener === 'function') narrow.addListener(onChange);
      }
      refs.more = button;
      refs.moreMenu = menu;
      return [button, menu];
    };

    // Kolejność narzędzi: tryb, wątki, stan zapisu, konto, akceptacja, iteracja (zamrożona), hotspoty, prezentacja, pinezki, motyw.
    const build = () => {
      let bar = document.querySelector('.doc-toolbar');
      if (!bar) {
        bar = Dom.make('div', { class: 'doc-toolbar' });
        document.body.insertBefore(bar, document.body.firstChild);
      }
      refs.bar = bar;
      const threads = Dom.make('button', { type: 'button', class: 'btn-outline anno-threads-btn', 'aria-expanded': 'false',
        text: 'Wątki' });
      refs.threads = threads;
      refs.save = Dom.make('span', { class: 'anno-save', role: 'status', hidden: true });
      refs.userSlot = Dom.make('span', { class: 'anno-user-slot' });
      refs.acceptSlot = Dom.make('span', { class: 'anno-accept-slot', hidden: true });
      refs.iterSlot = Dom.make('span', { class: 'anno-iteration-slot', hidden: true });
      refs.banners = bar.querySelector('.anno-banners') || Dom.make('div', { class: 'anno-banners' });
      // Cztery przyciski „do menu” (poniżej 600 px chowa je CSS; pozycje menu wywołują ich click, więc akcje są jedne).
      const overflow = [toggleButton('Pokaż hotspoty', 'hotspots'), toggleButton('Prezentacja', 'presentation'),
        toggleButton('Ukryj pinezki', 'pinsHidden'), buildThemeButton()];
      overflow.forEach((button) => button.setAttribute('data-proto-more-item', ''));
      const more = buildMore(overflow);
      const tools = Dom.make('div', { class: 'proto-toolbar' }, [
        buildModeGroup(), threads, refs.save, refs.userSlot, refs.acceptSlot, refs.iterSlot
      ].concat(overflow, more));
      refs.tools = tools;
      bar.appendChild(tools);
      bar.appendChild(refs.banners);
      const sync = () => PageState.syncToolbarHeight(bar);
      sync();
      if (typeof ResizeObserver === 'function') new ResizeObserver(sync).observe(bar);
      window.addEventListener('resize', sync);
      return bar;
    };

    return Object.freeze({ build: build, refs: refs });
  })();

  // ── 13d. UI: przełącznik ekranów ──────────────────────────────────────────────────────────────────────────────

  const ScreenNav = (() => {
    const state = { entries: [], links: emptyMap(), current: null, query: '', shown: [], active: -1, open: false, forced: null };
    const ui = {};
    let lastScrollAt = 0;
    let settleTimer = null;

    const screenOf = (id) => document.getElementById(id);
    const indexOf = (id) => state.entries.findIndex((entry) => entry.id === id);

    const readEntries = (nav) => {
      const anchors = Array.prototype.slice.call(nav.querySelectorAll(':scope > a'));
      const entries = Switcher.entriesFromLinks(anchors.map((anchor) => ({ href: anchor.getAttribute('href'), text: anchor.textContent })))
        .filter((entry) => !!screenOf(entry.id));
      anchors.forEach((anchor) => { state.links[String(anchor.getAttribute('href')).slice(1)] = anchor; });
      return entries;
    };

    const stepLabel = (verb, entry) => (entry ? verb + ' ekran: ' + entry.label : verb === 'Następny' ? 'Brak następnego ekranu' :
      'Brak poprzedniego ekranu');

    // Odświeża pasek, znaczniki bieżącego ekranu i zaznaczenie na liście.
    const paint = () => {
      const at = indexOf(state.current);
      const entry = state.entries[at];
      const prev = at > 0 ? state.entries[at - 1] : null;
      const next = at >= 0 ? Switcher.adjacent(state.entries, state.current, 1) : null;
      ui.num.textContent = entry ? entry.number + ' / ' + state.entries.length : '';
      ui.name.textContent = entry ? entry.name : '';
      ui.prev.setAttribute('aria-disabled', String(!prev));
      ui.prev.setAttribute('aria-label', stepLabel('Poprzedni', prev));
      ui.next.setAttribute('aria-disabled', String(!next));
      ui.next.setAttribute('aria-label', stepLabel('Następny', next));
      state.entries.forEach((item) => {
        const section = screenOf(item.id);
        if (section) {
          section.classList.toggle('is-current', item.id === state.current);
          if (item.id === state.current) section.setAttribute('aria-current', 'location');
          else section.removeAttribute('aria-current');
        }
        const link = state.links[item.id];
        if (!link) return;
        if (item.id === state.current) link.setAttribute('aria-current', 'location');
        else link.removeAttribute('aria-current');
      });
      Array.prototype.forEach.call(ui.list.children, (option) => {
        option.classList.toggle('is-current', option.getAttribute('data-screen') === state.current);
        option.setAttribute('aria-selected', String(option.getAttribute('data-screen') === state.current));
      });
    };

    const setCurrent = (id) => {
      if (id === state.current || indexOf(id) < 0) return;
      state.current = id;
      paint();
      Bus.emit('screen', id);
    };

    // ----- lista (okienko z wyszukiwaniem) -----

    const columns = () => {
      const options = Array.prototype.slice.call(ui.list.children);
      if (options.length < 2) return 1;
      const top = options[0].getBoundingClientRect().top;
      return Math.max(1, options.filter((option) => Math.abs(option.getBoundingClientRect().top - top) < 2).length);
    };

    const markActive = () => {
      const options = Array.prototype.slice.call(ui.list.children);
      options.forEach((option, at) => {
        option.classList.toggle('is-active', at === state.active);
      });
      const option = options[state.active];
      if (option) {
        ui.search.setAttribute('aria-activedescendant', option.id);
        if (state.open) option.scrollIntoView({ block: 'nearest' });
      } else {
        ui.search.removeAttribute('aria-activedescendant');
      }
    };

    const renderList = () => {
      state.shown = Switcher.filterEntries(state.entries, state.query);
      ui.list.textContent = '';
      state.shown.forEach((entry) => {
        const option = Dom.make('div', { class: 'proto-screens-option', role: 'option', id: 'proto-screen-opt-' + entry.id,
          'data-screen': entry.id, text: entry.number + ' ' + entry.name });
        ui.list.appendChild(option);
      });
      const trimmed = state.query.trim();
      if (ui.empty.parentNode) ui.empty.parentNode.removeChild(ui.empty);
      if (!state.shown.length) {
        ui.empty.textContent = 'Brak ekranu „' + trimmed + '”.';
        ui.pop.appendChild(ui.empty);
      }
      const currentAt = state.shown.findIndex((entry) => entry.id === state.current);
      state.active = state.shown.length ? (trimmed === '' && currentAt >= 0 ? currentAt : 0) : -1;
      paint();
      markActive();
    };

    const openList = () => {
      if (state.open) { ui.search.focus(); return; }
      state.open = true;
      state.query = '';
      ui.search.value = '';
      ui.pop.hidden = false;
      ui.main.setAttribute('aria-expanded', 'true');
      ui.search.setAttribute('aria-expanded', 'true');
      renderList();
      ui.search.focus();
    };

    const closeList = (returnFocus) => {
      if (!state.open) return;
      state.open = false;
      ui.pop.hidden = true;
      ui.main.setAttribute('aria-expanded', 'false');
      ui.search.setAttribute('aria-expanded', 'false');
      if (returnFocus) ui.main.focus({ preventScroll: true });
    };

    // ----- przejście do ekranu i śledzenie przewijania -----

    const settle = () => {
      settleTimer = null;
      if (!state.forced || state.forced.y !== null) return;
      if (Date.now() - lastScrollAt < 140) { settleTimer = setTimeout(settle, 160); return; }
      state.forced.y = window.scrollY;
    };

    const holdTarget = (id) => {
      state.forced = { id: id, y: null };
      if (settleTimer) clearTimeout(settleTimer);
      settleTimer = setTimeout(settle, 450);
    };

    const goTo = (id, native) => {
      const section = screenOf(id);
      if (!section || indexOf(id) < 0) return;
      setCurrent(id);
      holdTarget(id);
      if (native) return;
      section.scrollIntoView({ behavior: Dom.reducedMotion() ? 'instant' : 'smooth', block: 'start' });
      try { history.replaceState(null, '', '#' + id); } catch (error) { /* adres niezmienny */ }
    };

    const follow = () => {
      const bar = ToolbarUi.refs.bar;
      // Linia, na którą ląduje początek ekranu po przejściu (margines przewijania ekranu + dopełnienie dokumentu).
      const px = (value) => parseFloat(value) || 0;
      const first = screenOf(state.entries[0].id);
      const landing = px(getComputedStyle(document.documentElement).scrollPaddingTop) + px(getComputedStyle(first).scrollMarginTop);
      const threshold = Math.max(bar.getBoundingClientRect().bottom, landing) + 4;
      const rects = state.entries.map((entry) => ({ id: entry.id, bottom: screenOf(entry.id).getBoundingClientRect().bottom }));
      const atEnd = window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2 && window.scrollY > 0;
      setCurrent(atEnd && rects.length ? rects[rects.length - 1].id : Switcher.visibleId(rects, threshold));
    };

    let framePending = false;
    const onScroll = () => {
      lastScrollAt = Date.now();
      if (state.forced) {
        if (state.forced.y === null) {
          if (settleTimer) clearTimeout(settleTimer);
          settleTimer = setTimeout(settle, 160);
          return;
        }
        if (Math.abs(window.scrollY - state.forced.y) <= 8) return;
        state.forced = null;
      }
      if (framePending) return;
      framePending = true;
      setTimeout(() => { framePending = false; follow(); }, 60);
    };

    const fromHash = () => {
      const id = String(location.hash || '').slice(1);
      return indexOf(id) >= 0 ? id : null;
    };

    // ----- klawiatura i zdarzenia -----

    const choose = (id) => {
      closeList(true);
      goTo(id, false);
    };

    const onSearchKey = (event) => {
      const action = Switcher.searchKeyAction({ key: event.key, shiftKey: event.shiftKey, isComposing: event.isComposing || event.keyCode === 229,
        value: ui.search.value });
      if (!action) return;
      if (action === 'close') {
        event.preventDefault();
        closeList(true);
      } else if (action === 'choose') {
        event.preventDefault();
        const entry = state.shown[state.active];
        if (entry) choose(entry.id);
      } else if (action === 'move') {
        event.preventDefault();
        state.active = Switcher.listMove(state.active, event.key, state.shown.length, columns());
        markActive();
      } else {
        event.preventDefault();
        const target = Switcher.searchTabTarget(document, ui.main, ui.pop, action);
        closeList(false);
        if (target) target.focus();
      }
    };

    const step = (button, direction) => {
      if (button.getAttribute('aria-disabled') === 'true') return;
      const entry = Switcher.adjacent(state.entries, state.current, direction);
      if (entry) goTo(entry.id, false);
    };

    const bindEvents = () => {
      ui.main.addEventListener('click', () => (state.open ? closeList(false) : openList()));
      ToolbarUi.refs.threads.addEventListener('click', () => closeList(false));
      Bus.on('panel', () => closeList(false));
      ui.prev.addEventListener('click', () => step(ui.prev, -1));
      ui.next.addEventListener('click', () => step(ui.next, 1));
      ui.search.addEventListener('input', () => { state.query = ui.search.value; renderList(); });
      ui.search.addEventListener('keydown', onSearchKey);
      ui.list.addEventListener('mousedown', (event) => event.preventDefault());
      ui.list.addEventListener('click', (event) => {
        const option = event.target.closest ? event.target.closest('.proto-screens-option') : null;
        if (option) choose(option.getAttribute('data-screen'));
      });
      document.addEventListener('mousedown', (event) => {
        if (state.open && !ui.pop.contains(event.target) && !ui.main.contains(event.target)) closeList(false);
      }, true);
      window.addEventListener('scroll', onScroll, { passive: true });
      window.addEventListener('hashchange', () => { const id = fromHash(); if (id) goTo(id, true); });
      Keyboard.register((event, facts) => {
        if (event.key === 'Escape' && state.open && !facts.dialog) {
          closeList(true);
          return true;
        }
        const action = Switcher.shortcut(facts);
        if (action === 'open') { openList(); return true; }
        if (action === 'prev') { step(ui.prev, -1); return true; }
        if (action === 'next') { step(ui.next, 1); return true; }
        return false;
      });
    };

    const build = (nav) => {
      ui.prev = Dom.make('button', { type: 'button', 'data-screen-step': 'prev', text: '‹' });
      ui.next = Dom.make('button', { type: 'button', 'data-screen-step': 'next', text: '›' });
      ui.num = Dom.make('span', { class: 'proto-screens-num' });
      ui.name = Dom.make('span', { class: 'proto-screens-name' });
      ui.main = Dom.make('button', { type: 'button', class: 'proto-screens-main', 'aria-haspopup': 'listbox', 'aria-expanded': 'false',
        'aria-controls': 'proto-screens-list', 'aria-label': 'Lista ekranów' }, [ui.num, ui.name]);
      ui.search = Dom.make('input', { type: 'text', class: 'proto-screens-search', role: 'combobox', autocomplete: 'off',
        'aria-label': 'Szukaj ekranu', 'aria-expanded': 'false', 'aria-controls': 'proto-screens-list', 'aria-autocomplete': 'list',
        placeholder: 'Szukaj ekranu (numer lub nazwa)' });
      ui.list = Dom.make('div', { id: 'proto-screens-list', role: 'listbox', 'aria-label': 'Ekrany' });
      ui.empty = Dom.make('div', { class: 'proto-screens-empty', role: 'status' });
      ui.pop = Dom.make('div', { class: 'proto-screens-pop', hidden: true }, [ui.search, ui.list]);
      const bar = Dom.make('div', { class: 'proto-screens-bar' }, [ui.prev, ui.main, ui.next]);
      nav.classList.add('proto-screens');
      nav.appendChild(bar);
      nav.appendChild(ui.pop);
    };

    const start = () => {
      const nav = document.querySelector('nav.screen-nav');
      if (!nav || nav.getAttribute('data-proto-nav') === 'strip') return false;
      state.entries = readEntries(nav);
      if (!state.entries.length) return false;
      build(nav);
      bindEvents();
      state.current = null;
      const first = fromHash() || state.entries[0].id;
      setCurrent(first);
      if (fromHash()) holdTarget(first);
      else follow();
      return true;
    };

    return Object.freeze({ start: start, goTo: goTo, state: state });
  })();

  // ── 13e. UI: tryb klikalny (przejścia `data-goto`) i skróty paska ─────────────────────────────────────────────

  const Navigation = (() => {
    // Elementy z `data-goto` bez własnej obsługi klawiatury dostają tabindex, rolę przycisku oraz Enter i Spację.
    const nativeKinds = /^(A|BUTTON|INPUT|SELECT|TEXTAREA|SUMMARY)$/;
    const decorate = () => {
      Array.prototype.forEach.call(document.querySelectorAll('[data-goto]'), (node) => {
        if (nativeKinds.test(node.tagName)) return;
        if (!node.hasAttribute('tabindex')) node.setAttribute('tabindex', '0');
        if (!node.hasAttribute('role')) node.setAttribute('role', 'button');
      });
    };
    const start = () => {
      document.addEventListener('click', (event) => {
        if (PageState.flags.commentMode || !event.target.closest) return;
        const hotspot = event.target.closest('[data-goto]');
        if (!hotspot || event.defaultPrevented) return;
        event.preventDefault();
        ScreenNav.goTo(hotspot.getAttribute('data-goto'), false);
      });
      decorate();
      document.addEventListener('keydown', (event) => {
        if (PageState.flags.commentMode || event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
        if (event.key !== 'Enter' && event.key !== ' ') return;
        const node = event.target;
        if (!node || !node.hasAttribute || !node.hasAttribute('data-goto') || nativeKinds.test(node.tagName)) return;
        event.preventDefault();
        ScreenNav.goTo(node.getAttribute('data-goto'), false);
      });
      // Klawisz C przełącza tryb komentowania (poza polami tekstowymi i oknami).
      Keyboard.register((event, facts) => {
        if (facts.typing || facts.dialog || event.ctrlKey || event.metaKey || event.altKey) return false;
        if (event.key !== 'c' && event.key !== 'C') return false;
        PageState.setCommentMode(!PageState.flags.commentMode);
        return true;
      });
    };
    return Object.freeze({ start: start, decorate: decorate, nativeKinds: nativeKinds });
  })();

  // ── 13f. UI: komunikaty, okna dialogowe ───────────────────────────────────────────────────────────────────────

  // Powiadomienie: region aria-live istnieje w dokumencie przed pojawieniem się treści (czytniki ogłaszają zmianę tekstu).
  const Toast = (() => {
    let node = null;
    let timer = null;
    const ensure = () => {
      if (node) return node;
      node = Dom.make('div', { class: 'toast', role: 'status', 'aria-live': 'polite', 'aria-atomic': 'true' });
      document.body.appendChild(node);
      return node;
    };
    const show = (text) => {
      const region = ensure();
      if (timer) clearTimeout(timer);
      region.textContent = '';
      setTimeout(() => { region.textContent = text; }, 30);
      timer = setTimeout(() => { region.textContent = ''; }, 6000);
    };
    return Object.freeze({ show: show, init: ensure });
  })();

  // Okno modalne: tytuł (#anno-dialog-title), treść, przyciski. Obieg Tab według Acceptance.tabTarget, Esc = anulowanie.
  const DialogsUi = (() => {
    let current = null;

    const focusables = (dlg) => {
      const nodes = Array.prototype.slice.call(dlg.querySelectorAll('a[href], button, input, select, textarea, [tabindex]'));
      const usable = nodes.filter((node) => !node.disabled && node.tabIndex >= 0 && node.getClientRects().length > 0);
      const seenGroups = new Set();
      return usable.filter((node) => {
        if (node.type !== 'radio' || !node.name) return true;
        if (seenGroups.has(node.name)) return false;
        seenGroups.add(node.name);
        return true;
      }).map((node) => {
        if (node.type !== 'radio' || !node.name) return node;
        const group = Array.prototype.slice.call(dlg.querySelectorAll('input[type="radio"]')).filter((radio) => radio.name === node.name);
        return group.find((radio) => radio.checked) || node;
      });
    };

    const open = (spec) => {
      if (current) current.close();
      const dlg = Dom.make('dialog', { class: 'anno-dialog', 'aria-labelledby': 'anno-dialog-title' });
      const title = Dom.make('h2', { id: 'anno-dialog-title', text: spec.title });
      const body = Dom.make('div', { class: 'anno-dialog-body' }, asArray(spec.nodes));
      const footer = Dom.make('div', { class: 'anno-dialog-actions' });
      dlg.appendChild(title);
      dlg.appendChild(body);
      dlg.appendChild(footer);
      const origin = spec.returnFocus || document.activeElement;
      const api = {
        dialog: dlg,
        buttons: {},
        isOpen: () => current === api,
        close: (restore) => {
          if (current !== api) return;
          current = null;
          dlg.close();
          if (dlg.parentNode) dlg.parentNode.removeChild(dlg);
          if (restore === false) return;
          const back = typeof origin === 'function' ? origin() : origin;
          const target = back && back.isConnected && back !== document.body ? back : ToolbarUi.refs.threads;
          target.focus({ preventScroll: true });
        }
      };
      asArray(spec.actions).forEach((action, index) => {
        const button = Dom.make('button', { type: 'button', class: action.primary ? 'btn btn-primary' : 'btn btn-outline', text: action.label });
        button.addEventListener('click', () => {
          if (button.getAttribute('aria-disabled') === 'true') return;
          if (action.run) action.run(api);
          else api.close();
        });
        api.buttons[action.key || String(index)] = button;
        footer.appendChild(button);
      });
      dlg.addEventListener('cancel', (event) => {
        event.preventDefault();
        if (spec.cancelable === false) return;
        if (spec.onCancel) spec.onCancel(api);
        else api.close();
      });
      dlg.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
          // Własna obsługa Esc: przeglądarka zamyka okno modalne po drugim anulowanym Esc, a okno wymuszone ma zostać.
          event.preventDefault();
          if (spec.cancelable === false) return;
          if (spec.onCancel) spec.onCancel(api);
          else api.close();
          return;
        }
        if (event.key !== 'Tab') return;
        const target = Acceptance.tabTarget(focusables(dlg), document.activeElement, event.shiftKey);
        if (target) { event.preventDefault(); target.focus(); }
      });
      document.body.appendChild(dlg);
      current = api;
      dlg.showModal();
      const first = spec.initialFocus ? spec.initialFocus(dlg) : null;
      const start = first || footer.querySelector('button.btn-outline') || footer.querySelector('button');
      if (start) start.focus();
      return api;
    };

    return Object.freeze({ open: open, current: () => current, focusables: focusables });
  })();

  // ── 13g. UI: połączenie z serwerem (tryby, sesja, dziennik, odpytywanie, kolejka zapisu) ──────────────────────
  // Zapis typów kolejkowanych (Outbox: QUEUEABLE_TYPES) idzie przez trwałą kolejkę w localStorage (klucz per prototyp i login):
  // wpis trafia do kolejki, potem `flush` wysyła go po kolei. Wynik wysyłki klasyfikuje Outbox.classifyWrite.

  const Remote = (() => {
    const state = {
      mode: 'boot', user: null, csrf: null, server: null, ops: [], total: 0, prefix: '', people: emptyMap(), iteration: 1,
      version: null, screens: emptyMap(), acceptance: null, errors: 0, pending: 0, loaded: false,
      conn: 'ok',            // ok | offline | expired
      queueCount: 0, rejectedUnseen: 0, rejectedTotal: 0, retrying: false, blocked: null, pollProblem: null,
      iterations: null,      // lista iteracji z serwera ({number, path, exists}) albo null
      baseVersion: null,     // wersja prototypu przy pierwszym wczytaniu strony (to ją widział użytkownik)
      baseScreens: emptyMap(), // hashe ekranów z tego wczytania
      stale: false           // pliki prototypu zmieniły się po wczytaniu strony
    };
    let pollTimer = null;
    let polling = Promise.resolve();
    let flushing = false;
    let flushAgain = false;
    let retryTimer = null;
    let retryErrors = 0;

    const slug = () => document.documentElement.getAttribute('data-prototype-id') || '';
    const frozen = () => document.documentElement.hasAttribute('data-prototype-frozen');
    // Żądanie ponad REQUEST_TIMEOUT_MS jest przerywane (wyjątek = brak sieci dla wywołującego), więc wiszące połączenie nie blokuje kolejki ani odpytywania.
    const REQUEST_TIMEOUT_MS = 15000;
    const call = async (path, init) => {
      const controller = typeof AbortController === 'function' ? new AbortController() : null;
      const timer = controller ? setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS) : null;
      try {
        const response = await fetch(API_ROOT + path, Object.assign({ credentials: 'same-origin', cache: 'no-store' }, init,
          controller ? { signal: controller.signal } : {}));
        let json = null;
        try { json = await response.json(); } catch (error) { /* odpowiedź bez treści JSON albo przerwana */ }
        return { status: response.status, json: json };
      } finally {
        if (timer) clearTimeout(timer);
      }
    };
    const opsPath = () => '/prototypes/' + encodeURIComponent(slug()) + '/operations';

    const writable = () => state.mode === 'full' && !frozen();
    const login = () => (state.user ? state.user.login : null);

    // ----- trwałe listy (kolejka, odrzucone) -----

    const listKey = (prefix, owner) => prefix + slug() + ':' + (owner || 'anon');
    const readList = (prefix, owner) => {
      try {
        const raw = window.localStorage.getItem(listKey(prefix, owner));
        const value = raw ? JSON.parse(raw) : [];
        return Array.isArray(value) ? value : asArray(value && value.items);
      } catch (error) { return []; }
    };
    const writeList = (prefix, owner, list) => {
      try {
        if (list.length) window.localStorage.setItem(listKey(prefix, owner), JSON.stringify(list));
        else window.localStorage.removeItem(listKey(prefix, owner));
        return true;
      } catch (error) { return false; }
    };
    const queueOf = (owner) => Outbox.itemsAccepted(readList(QUEUE_PREFIX, owner));
    const rejectedOf = (owner) => readList(REJECTED_PREFIX, owner).filter((item) => isObject(item));
    const enqueue = (owner, item) => writeList(QUEUE_PREFIX, owner, Outbox.mergeItems(readList(QUEUE_PREFIX, owner), [item], []));
    const dequeue = (owner, cid) => writeList(QUEUE_PREFIX, owner, readList(QUEUE_PREFIX, owner).filter((item) => !isObject(item) || item.cid !== cid));
    const reject = (owner, item, status, code) => {
      const kept = rejectedOf(owner).filter((old) => old.cid !== item.cid);
      kept.push(Object.assign({}, item, { rejectedStatus: status, rejectedCode: code || '', seen: false, rejectedAt: Date.now() }));
      return writeList(REJECTED_PREFIX, owner, kept);
    };

    const recount = () => {
      const owner = login();
      state.queueCount = owner ? queueOf(owner).length : 0;
      state.rejectedUnseen = owner ? rejectedOf(owner).filter((item) => item.seen !== true).length : 0;
      state.rejectedTotal = owner ? rejectedOf(owner).length : 0;
    };

    // ----- stan na pasku -----

    const statusText = () => {
      if (state.mode === 'file') return 'Bez serwera';
      if (state.mode === 'readonly') return 'Tylko odczyt';
      if (state.mode === 'nodata') return 'Brak komentarzy dla tego prototypu';
      if (state.mode === 'anon') return 'Zaloguj się, żeby komentować';
      if (state.mode === 'offline') return 'Offline';
      if (state.mode !== 'full') return '';
      if (state.conn === 'expired') return 'Sesja wygasła';
      if (state.conn === 'offline') return state.queueCount > 0 ? 'Offline · ' + state.queueCount + ' w kolejce' : 'Offline';
      if (state.blocked) return state.blocked;
      if (state.rejectedUnseen > 0) return 'Błąd zapisu (' + state.rejectedUnseen + ')';
      if (state.pending > 0 || state.queueCount > 0) return 'Zapisywanie…';
      return 'Zapisano';
    };

    const publish = () => { recount(); Bus.emit('remote', state); };

    // ----- dziennik i odpytywanie -----

    const take = (json, replace) => {
      if (replace) state.ops = asArray(json.operations).slice();
      else state.ops = state.ops.concat(asArray(json.operations));
      state.total = isInt(json.total, 0) ? json.total : state.ops.length;
      state.prefix = strOr(json.prefix, '');
      if (isObject(json.people)) state.people = json.people;
      if (isObject(json.iteration) && isInt(json.iteration.current)) state.iteration = json.iteration.current;
      state.version = strOr(json.version, state.version);
      if (isObject(json.screens)) state.screens = json.screens;
      if (Array.isArray(json.iterations)) state.iterations = json.iterations;
      if (isStr(json.version)) {
        if (state.baseVersion === null) {
          state.baseVersion = json.version;
          state.baseScreens = isObject(json.screens) ? Object.assign(emptyMap(), json.screens) : emptyMap();
        } else if (json.version !== state.baseVersion && !frozen()) {
          state.stale = true;
        }
      }
      state.acceptance = isObject(json.acceptance) ? json.acceptance : state.acceptance;
      state.loaded = true;
      Bus.emit('data', state);
    };

    const fetchOps = async (full) => {
      const query = full ? '?after=0' : '?after=' + state.total + '&prefix=' + encodeURIComponent(state.prefix);
      const result = await call(opsPath() + query);
      if (result.status === 200 && isObject(result.json)) {
        take(result.json, full || result.json.reset === true);
        state.pollProblem = null;
      } else if (result.status === 401 && state.mode === 'full') {
        markExpired();
      } else if (result.status === 401) {
        // Odczyt wymaga logowania (serve --login-to-view): po wylogowaniu nie zostawiamy w pamięci cudzych wątków.
        state.ops = [];
        state.total = 0;
        state.prefix = '';
        state.loaded = false;
        state.pollProblem = 'error';
        Bus.emit('data', state);
      } else {
        state.pollProblem = result.status === 409 && isObject(result.json) ? strOr(result.json.error, 'error') : 'error';
      }
      return result;
    };

    const poll = () => {
      polling = polling.then(async () => {
        const wasOffline = state.conn === 'offline';
        try {
          const result = await fetchOps(false);
          state.errors = result.status === 200 ? 0 : state.errors + 1;
          if (state.conn === 'offline') state.conn = 'ok';
        } catch (error) {
          state.errors += 1;
          if (state.mode === 'full') state.conn = 'offline';
        }
        publish();
        if (wasOffline && state.conn === 'ok') flush();
      }).catch(() => { /* łańcuch odpytywania nie może zostać zatruty */ });
      return polling;
    };

    const schedule = () => {
      if (pollTimer) clearTimeout(pollTimer);
      pollTimer = setTimeout(async () => {
        if (state.mode === 'anon' || state.mode === 'full' || state.mode === 'password') await poll();
        else if (state.mode === 'offline') await connect();
        schedule();
      }, Outbox.pollDelay(!document.hidden, state.errors));
    };

    const readCommentsFile = async () => {
      const path = frozen() ? '../../comments.json' : 'comments.json';
      try {
        const response = await fetch(path, { cache: 'no-store' });
        if (response.status !== 200) { state.mode = 'nodata'; return; }
        const doc = await response.json();
        state.mode = 'readonly';
        state.ops = Journal.readDocument(doc, slug());
        state.total = state.ops.length;
        state.loaded = true;
        Bus.emit('data', state);
      } catch (error) {
        state.mode = 'nodata';
      }
    };

    const applySession = (json) => {
      const before = login();
      state.user = isObject(json.user) ? json.user : null;
      state.csrf = strOr(json.csrf, null);
      state.server = isObject(json.server) ? json.server : state.server;
      state.mode = !state.user ? 'anon' : (state.user.mustChange ? 'password' : 'full');
      state.conn = 'ok';
      state.blocked = null;
      if (login() !== before) retryErrors = 0;
      publish();
      if (state.mode === 'full') flush();
    };

    const refreshSession = async () => {
      let session = null;
      try { session = await call('/session'); } catch (error) { return { status: 0, json: null }; }
      if (session.status === 200 && isObject(session.json)) applySession(session.json);
      return session;
    };

    const markExpired = () => {
      if (state.conn === 'expired') return;
      state.conn = 'expired';
      publish();
      Bus.emit('expired', state);
    };

    // ----- wysyłka kolejki -----

    const send = (item) => call(opsPath(), {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Skillsync-CSRF': state.csrf || '' }, body: JSON.stringify(item)
    });
    const bodyOf = (item) => {
      const body = { cid: item.cid, type: item.type, payload: item.payload };
      if (isStr(item.threadId)) body.threadId = item.threadId;
      return body;
    };

    const scheduleRetry = (delay) => {
      if (retryTimer) clearTimeout(retryTimer);
      state.retrying = true;
      retryTimer = setTimeout(() => { retryTimer = null; state.retrying = false; flush(); }, delay);
    };

    const BLOCKED_TEXT = { 507: 'Serwer odmawia zapisu' };

    const flush = async () => {
      if (flushing) { flushAgain = true; return; }
      flushing = true;
      let sent = 0;
      let csrfRetried = false;
      try {
        for (;;) {
          const owner = login();
          if (!owner || state.mode !== 'full' || state.conn === 'expired') break;
          const item = queueOf(owner)[0];
          if (!item) break;
          let result = null;
          try { result = await send(bodyOf(item)); } catch (error) { result = { status: 0, json: null }; }
          if (login() !== owner) break;   // konto zmieniło się w trakcie: wynik nie dotyczy bieżącej kolejki
          const code = isObject(result.json) ? result.json.error : null;
          const verdict = Outbox.classifyWrite(result.status, code);
          if (verdict === 'done') {
            dequeue(owner, item.cid);
            sent += 1;
            retryErrors = 0;
            state.retrying = false;
            state.conn = 'ok';
            state.blocked = null;
          } else if (verdict === 'offline') {
            state.conn = 'offline';
            retryErrors += 1;
            scheduleRetry(Outbox.pollDelay(!document.hidden, retryErrors - 1));
            break;
          } else if (verdict === 'auth') {
            markExpired();
            break;
          } else if (verdict === 'csrf' && !csrfRetried) {
            csrfRetried = true;
            await refreshSession();
          } else if (verdict === 'password') {
            state.mode = 'password';
            if (isObject(state.user)) state.user.mustChange = true;
            break;
          } else if (verdict === 'retry') {
            retryErrors += 1;
            state.conn = 'ok';
            scheduleRetry(Outbox.pollDelay(!document.hidden, retryErrors - 1));
            break;
          } else if (verdict === 'blocked') {
            state.blocked = has(BLOCKED_TEXT, result.status) ? BLOCKED_TEXT[result.status] : 'Zapis wstrzymany: napraw plik komentarzy';
            break;
          } else {
            // Najpierw lista odrzuconych: gdy pamięć jest pełna, wpis zostaje w kolejce (nic nie ginie po cichu).
            if (!reject(owner, item, result.status, code)) {
              state.blocked = 'Zapis wstrzymany: pamięć przeglądarki jest pełna';
              break;
            }
            dequeue(owner, item.cid);
          }
          publish();
        }
      } finally {
        flushing = false;
      }
      publish();
      if (sent > 0) await poll();
      if (flushAgain) { flushAgain = false; flush(); }
    };

    // Zapis operacji. Zwraca { accepted, ok, queued, rejected }: `accepted` = wpis przyjęty (wysłany albo czeka w kolejce).
    const submit = async (type, threadId, payload) => {
      const item = { cid: Outbox.newClientId('c'), type: type, payload: payload };
      if (threadId) item.threadId = threadId;
      const owner = login();
      if (oneOf(QUEUEABLE_TYPES, type)) {
        if (!owner || !enqueue(owner, item)) {
          Toast.show('Nie można zapisać wpisu: pamięć przeglądarki jest niedostępna.');
          return { accepted: false, ok: false, queued: false, rejected: false };
        }
        publish();
        await flush();
        const queued = queueOf(owner).some((entry) => entry.cid === item.cid);
        const rejected = rejectedOf(owner).some((entry) => entry.cid === item.cid);
        return { accepted: !rejected, ok: !queued && !rejected, queued: queued, rejected: rejected };
      }
      // typy poza kolejką (usunięcie wątku, akceptacja ...): bez kolejki, od razu do serwera
      if (state.conn !== 'ok') {
        Toast.show(type === 'delete' ? 'Usunięcie wątku wymaga połączenia z serwerem.' : 'Ta akcja wymaga połączenia z serwerem i nie trafia do kolejki.');
        return { accepted: false, ok: false, queued: false, rejected: false };
      }
      state.pending += 1;
      publish();
      let result = null;
      try { result = await send(item); } catch (error) { result = { status: 0, json: null }; }
      const verdict = Outbox.classifyWrite(result.status, isObject(result.json) ? result.json.error : null);
      if (verdict === 'done') await poll();
      else if (verdict === 'auth') markExpired();
      else if (verdict === 'offline') { state.conn = 'offline'; }
      state.pending -= 1;
      publish();
      return { accepted: verdict === 'done', ok: verdict === 'done', queued: false, rejected: verdict !== 'done', status: result.status, json: result.json };
    };

    const postJson = (path, body) => call(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Skillsync-CSRF': state.csrf || '' }, body: JSON.stringify(body)
    });

    // Ostrzeżenie przeglądarki przy zamknięciu albo przeładowaniu karty, gdy kolejka zapisu nie jest pusta (K16, ryzyko „Utrata wpisów offline”).
    const guardUnload = () => {
      window.addEventListener('beforeunload', (event) => {
        const owner = login();
        if (!owner || queueOf(owner).length === 0) return;
        event.preventDefault();
        event.returnValue = '';
      });
    };

    // Pierwsze połączenie albo ponowna próba po starcie bez sieci: sesja, potem dziennik (tryb pełny / odczyt publiczny) albo plik komentarzy.
    const connect = async () => {
      let session = null;
      try { session = await call('/session'); } catch (error) {
        state.errors += 1;
        state.mode = 'offline';
        publish();
        return false;
      }
      state.errors = 0;
      if (session.status === 200 && isObject(session.json) && has(session.json, 'user')) {
        applySession(session.json);
        try { await fetchOps(true); } catch (error) { state.errors += 1; }
      } else {
        await readCommentsFile();
      }
      publish();
      return true;
    };

    let listening = false;
    const listen = () => {
      if (listening) return;
      listening = true;
      window.addEventListener('online', () => {
        state.errors = 0;
        if (state.mode === 'offline') connect().then(() => flush());
        else if (state.mode === 'anon' || state.mode === 'full') poll();
        flush();
      });
      // Utrata sieci: od razu sprawdź serwer, żeby blokady akcji bez kolejki (akceptacja, przywracanie) włączyły się bez czekania na odpytywanie.
      window.addEventListener('offline', () => { if (state.mode === 'anon' || state.mode === 'full') poll(); });
      window.addEventListener('storage', () => { publish(); flush(); });
      // Powrót karty na wierzch: odpytanie od razu (K15: wpis innej osoby w kilka sekund), bez czekania na 30-sekundowy odstęp karty w tle.
      document.addEventListener('visibilitychange', () => {
        if (document.hidden) return;
        if (state.mode === 'offline') connect();
        else if (state.mode === 'anon' || state.mode === 'full' || state.mode === 'password') poll();
        schedule();
      });
    };

    const start = async () => {
      guardUnload();
      listen();
      if (location.protocol === 'file:') {
        state.mode = 'file';
        publish();
        return;
      }
      await connect();
      schedule();
    };

    // ----- narzędzia dla okien i banerów -----

    const markStale = () => {
      if (state.stale) return;
      state.stale = true;
      publish();
    };
    // Serwer wymaga zmiany hasła (403 password_change_required przy zapisie poza kolejką): tryb zmiany hasła i okno.
    const requirePassword = () => {
      state.mode = 'password';
      if (isObject(state.user)) state.user.mustChange = true;
      publish();
    };

    const retryNow = () => { retryErrors = 0; state.conn = state.conn === 'offline' ? 'ok' : state.conn; if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; } state.retrying = false; poll(); flush(); };
    const markRejectedSeen = () => {
      const owner = login();
      if (!owner) return;
      writeList(REJECTED_PREFIX, owner, rejectedOf(owner).map((item) => Object.assign({}, item, { seen: true })));
      publish();
    };
    const dropRejected = (cid) => {
      const owner = login();
      if (!owner) return;
      writeList(REJECTED_PREFIX, owner, rejectedOf(owner).filter((item) => item.cid !== cid));
      publish();
    };

    return Object.freeze({
      state: state, start: start, submit: submit, send: send, poll: poll, flush: flush, writable: writable, login: login, statusText: statusText,
      applySession: applySession, refreshSession: refreshSession, call: call, postJson: postJson, publish: publish, slug: slug, frozen: frozen,
      queueOf: queueOf, rejectedOf: rejectedOf, enqueue: enqueue, retryNow: retryNow, markRejectedSeen: markRejectedSeen, dropRejected: dropRejected,
      markStale: markStale, expire: markExpired, requirePassword: requirePassword
    });
  })();

  // ── 13h. UI: model wątków (fold, etykiety osób, uchwyty, ostatnia wizyta) ─────────────────────────────────────

  const STATUS_LABELS = Object.freeze({
    open: 'Otwarty', awaiting: 'Czeka na decyzję', decided: 'Zdecydowany', closed: 'Zamknięty', deferred: 'Odłożony'
  });
  const ROLE_LABELS = Object.freeze({ commenter: 'komentujący', acceptor: 'akceptujący', developer: 'programista' });

  const Model = (() => {
    const model = { folded: Fold.foldOperations([]), labels: emptyMap(), seenAt: null };

    const seenKey = () => SEEN_PREFIX + Remote.slug() + ':' + (Remote.login() || 'anon');
    const readSeen = () => {
      try { return window.localStorage.getItem(seenKey()); } catch (error) { return null; }
    };
    const writeSeen = (value) => {
      try { window.localStorage.setItem(seenKey(), value); } catch (error) { /* bez pamięci: brak znacznika wizyty */ }
    };

    const refresh = () => {
      const ops = Remote.state.ops;
      model.folded = Fold.foldOperations(ops);
      model.labels = Object.keys(Remote.state.people).length ? Remote.state.people : People.fromOperations(ops, null);
      Bus.emit('model', model);
    };

    // Znacznik „ostatniej wizyty”: największe `at` widziane przy poprzednim wczytaniu (zapisujemy raz, po pierwszym komplecie danych).
    let seenTaken = false;
    Bus.on('data', () => {
      refresh();
      if (!seenTaken) markSeen.lastLogin = Remote.login();
      if (seenTaken || !Remote.state.loaded || Remote.state.mode === 'boot') return;
      seenTaken = true;
      model.seenAt = readSeen();
      markSeen();
    });
    // Zapis „widziałem do”: po wczytaniu, po zalogowaniu (klucz nowego konta) i przy opuszczaniu strony.
    const markSeen = () => {
      const latest = Remote.state.ops.reduce((best, op) => (isStr(op.at) && op.at > best ? op.at : best), '');
      if (latest) writeSeen(latest);
    };
    if (typeof window !== 'undefined') window.addEventListener('pagehide', () => { if (seenTaken) markSeen(); });
    Bus.on('remote', () => {
      const key = Remote.login();
      if (seenTaken && key !== markSeen.lastLogin) { markSeen.lastLogin = key; markSeen(); }
    });

    const labelOf = (login) => (isStr(login) && has(model.labels, login) ? model.labels[login].label : (login || 'Nieznany'));
    const fullNameOf = (login) => (isStr(login) && has(model.labels, login) ? model.labels[login].fullName : (login || 'Nieznany'));

    const elementOf = (thread) => {
      const protoId = isObject(thread.anchor) ? thread.anchor.protoId : null;
      if (!isStr(protoId)) return null;
      return document.querySelector(protoIdSelector(protoId));
    };
    const hasHandle = (thread) => {
      const kind = Fold.anchorKind(thread);
      if (kind === 'screen') return true;
      return kind === 'element' && !!elementOf(thread);
    };
    const context = () => ({
      me: Remote.login(), seenAt: model.seenAt, hasHandle: hasHandle,
      screenOrder: ScreenNav.state.entries.map((entry) => entry.id)
    });
    const threadById = (id) => (has(model.folded.byId, id) ? model.folded.byId[id] : null);
    const screenName = (id) => {
      const entry = ScreenNav.state.entries.find((item) => item.id === id);
      return entry ? entry.label : id;
    };
    const openCount = () => model.folded.threads.filter((thread) => isOpenStatus(thread.status)).length;
    const formatTime = (at) => {
      const date = new Date(at);
      return isNaN(date.getTime()) ? String(at) : date.toLocaleString('pl-PL', { dateStyle: 'short', timeStyle: 'short' });
    };
    return Object.freeze({
      data: model, labelOf: labelOf, fullNameOf: fullNameOf, elementOf: elementOf, hasHandle: hasHandle, context: context,
      threadById: threadById, screenName: screenName, openCount: openCount, formatTime: formatTime
    });
  })();

  // Wymaga trybu zapisu; w innych trybach pokazuje właściwy komunikat albo okno logowania. Zwraca true, gdy można pisać.
  const requireWrite = () => {
    const mode = Remote.state.mode;
    if (Remote.frozen()) {
      Toast.show('Zamrożona iteracja jest tylko do odczytu: komentuj w bieżącej wersji.');
      return false;
    }
    if (mode === 'full') return true;
    if (mode === 'anon') AuthUi.loginDialog('Zaloguj się, żeby komentować');
    else if (mode === 'password') Toast.show('Najpierw ustaw własne hasło.');
    else if (mode === 'file') Toast.show('Komentowanie wymaga serwera: uruchom „skillsync prototype serve”.');
    else if (mode === 'readonly' || mode === 'nodata') Toast.show('Ten podgląd jest tylko do odczytu.');
    else Toast.show('Brak połączenia z serwerem.');
    return false;
  };

  // ── 13i. UI: konto (chip, logowanie, hasło, menu), stan zapisu na pasku ──────────────────────────────────────

  const AuthUi = (() => {
    let menu = null;
    let rememberedPassword = null;   // tylko w pamięci: hasło właśnie użyte do logowania (potrzebne przy wymuszonej zmianie)
    let passwordDialogOpen = false;

    const chipNode = () => ToolbarUi.refs.userSlot.querySelector('.anno-user');
    const closeMenu = () => {
      if (menu && menu.parentNode) menu.parentNode.removeChild(menu);
      menu = null;
    };

    const logout = async () => {
      closeMenu();
      try { await Remote.postJson('/logout', {}); } catch (error) { /* wylogowanie lokalne i tak następuje */ }
      rememberedPassword = null;
      Remote.applySession({ user: null, csrf: null });
      await Remote.poll();
      Bus.emit('data', Remote.state);
    };

    const menuButton = (label, run) => {
      const button = Dom.make('button', { type: 'button', role: 'menuitem', text: label });
      button.addEventListener('click', () => { closeMenu(); run(); });
      return button;
    };

    const shortcutsLabel = () => 'Skróty jednoznakowe: ' + (Shortcuts.enabled() ? 'włączone' : 'wyłączone');

    // Menu konta z klawiaturą: strzałki i Home/End przesuwają fokus, Esc zamyka i oddaje fokus chipowi, Tab zamyka.
    const toggleMenu = (chip) => {
      if (menu) { closeMenu(); return; }
      const toggle = menuButton(shortcutsLabel(), () => Shortcuts.set(!Shortcuts.enabled()));
      toggle.setAttribute('aria-label', 'Przełącz skróty jednoznakowe (C, G, [, ], strzałki). ' + shortcutsLabel());
      menu = Dom.make('div', { class: 'anno-user-menu', role: 'menu' }, [
        menuButton('Odrzucone wpisy', () => RejectedUi.open(chip)), toggle, menuButton('Wyloguj', logout)
      ]);
      menu.addEventListener('keydown', (event) => {
        const items = Array.prototype.slice.call(menu.querySelectorAll('[role="menuitem"]'));
        const at = items.indexOf(document.activeElement);
        const move = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: items.length - 1 };
        if (has(move, event.key)) {
          event.preventDefault();
          items[(move[event.key] + items.length) % items.length].focus();
        } else if (event.key === 'Escape') {
          event.preventDefault();
          event.stopPropagation();
          closeMenu();
          chip.focus({ preventScroll: true });
        } else if (event.key === 'Tab') {
          closeMenu();
        }
      });
      chip.parentNode.appendChild(menu);
      const first = menu.querySelector('[role="menuitem"]');
      if (first) first.focus();
    };

    const onChip = (chip) => {
      if (Remote.state.mode === 'password') passwordDialog();
      else if (Remote.state.conn === 'expired') loginDialog('Zaloguj się ponownie', Remote.login());
      else toggleMenu(chip);
    };

    const renderSlot = () => {
      const slot = ToolbarUi.refs.userSlot;
      const user = Remote.state.user;
      slot.textContent = '';
      closeMenu();
      if (Remote.state.mode === 'anon') {
        const button = Dom.make('button', { type: 'button', class: 'btn-outline', 'data-anno-focus': 'user-login', text: 'Zaloguj' });
        button.addEventListener('click', () => loginDialog('Zaloguj się, żeby komentować'));
        slot.appendChild(button);
      } else if (user) {
        const must = Remote.state.mode === 'password';
        const first = strOr(user.firstName, strOr(user.name, user.login));
        const text = must ? 'Ustaw własne hasło' : first + ' · ' + (has(ROLE_LABELS, user.role) ? ROLE_LABELS[user.role] : user.role);
        const chip = Dom.make('button', { type: 'button', class: 'anno-user', 'data-anno-focus': 'user-chip', 'aria-haspopup': 'menu',
          'aria-label': must ? text : first + ', ' + (ROLE_LABELS[user.role] || user.role), text: text });
        chip.addEventListener('click', () => onChip(chip));
        slot.appendChild(chip);
      }
    };

    const loginDialog = (title, prefill) => {
      const existing = DialogsUi.current();
      if (existing && existing.dialog.querySelector('#anno-login-email')) return existing;
      const email = Dom.make('input', { id: 'anno-login-email', type: 'email', autocomplete: 'username', 'aria-label': 'E-mail', required: true });
      const password = Dom.make('input', { id: 'anno-login-password', type: 'password', autocomplete: 'current-password', 'aria-label': 'Hasło',
        required: true });
      const error = Dom.make('p', { role: 'alert', hidden: true });
      if (prefill) email.value = prefill;
      const expired = title === 'Zaloguj się ponownie';
      const fields = Dom.make('div', { class: 'form-group' }, (expired ? [Dom.make('p', { text: 'Sesja wygasła: zaloguj się ponownie, żeby dalej zapisywać zmiany.' })] : []).concat([
        Dom.make('label', { for: 'anno-login-email', text: 'E-mail' }), email,
        Dom.make('label', { for: 'anno-login-password', text: 'Hasło' }), password, error
      ]));
      let dialog = null;
      let busy = false;
      const submit = async () => {
        if (busy || !email.value.trim() || !password.value) return;
        busy = true;
        error.hidden = true;
        let result = null;
        try { result = await Remote.postJson('/login', { login: email.value.trim(), password: password.value }); } catch (failure) {
          result = { status: 0, json: null };
        }
        busy = false;
        if (result.status === 200 && isObject(result.json)) {
          rememberedPassword = password.value;
          dialog.close(false);
          Remote.applySession(result.json);
          await Remote.poll();
          Bus.emit('data', Remote.state);
          if (Remote.state.mode === 'full') {
            const target = chipNode() || ToolbarUi.refs.threads;
            target.focus({ preventScroll: true });
          }
          return;
        }
        error.textContent = result.status === 429 ? 'Za dużo prób: spróbuj za chwilę.' : 'Nieprawidłowy e-mail lub hasło.';
        error.hidden = false;
      };
      dialog = DialogsUi.open({
        title: title, nodes: [fields], returnFocus: () => chipNode() || ToolbarUi.refs.userSlot.querySelector('button') || ToolbarUi.refs.threads,
        actions: [{ label: 'Anuluj' }, { label: 'Zaloguj', primary: true, run: () => submit() }],
        initialFocus: () => (prefill ? password : email)
      });
      password.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); submit(); } });
      email.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); password.focus(); } });
      return dialog;
    };

    // Wymuszona zmiana hasła (mustChange): okna nie da się zamknąć Esc, wyjście tylko przez „Wyloguj”.
    const passwordDialog = () => {
      if (passwordDialogOpen) return;
      passwordDialogOpen = true;
      const known = rememberedPassword;
      const current = known === null ? Dom.make('input', { id: 'anno-pw-current', type: 'password', autocomplete: 'current-password', 'aria-label': 'Obecne hasło' }) : null;
      const next = Dom.make('input', { id: 'anno-pw-new', type: 'password', autocomplete: 'new-password', 'aria-label': 'Nowe hasło' });
      const again = Dom.make('input', { id: 'anno-pw-repeat', type: 'password', autocomplete: 'new-password', 'aria-label': 'Powtórz nowe hasło' });
      const error = Dom.make('p', { role: 'alert', hidden: true });
      const rows = [Dom.make('p', { text: 'Konto ma hasło tymczasowe. Ustaw własne (co najmniej 10 znaków), żeby komentować.' })];
      if (current) rows.push(Dom.make('label', { for: 'anno-pw-current', text: 'Obecne hasło' }), current);
      rows.push(Dom.make('label', { for: 'anno-pw-new', text: 'Nowe hasło' }), next, Dom.make('label', { for: 'anno-pw-repeat', text: 'Powtórz nowe hasło' }), again, error);
      let busy = false;
      let dialog = null;
      const submit = async () => {
        if (busy) return;
        const oldPassword = current ? current.value : known;
        if (!oldPassword || !next.value) return;
        if (next.value !== again.value) { error.textContent = 'Hasła nie są takie same.'; error.hidden = false; return; }
        busy = true;
        error.hidden = true;
        let result = null;
        try { result = await Remote.postJson('/password', { current: oldPassword, new: next.value }); } catch (failure) { result = { status: 0, json: null }; }
        busy = false;
        if (result.status === 204 || result.status === 200) {
          rememberedPassword = null;
          passwordDialogOpen = false;
          dialog.close(false);
          await Remote.refreshSession();
          await Remote.poll();
          Bus.emit('data', Remote.state);
          return;
        }
        const code = isObject(result.json) ? result.json.error : '';
        error.textContent = code === 'weak_password' ? 'Hasło musi mieć co najmniej 10 znaków i różnić się od obecnego.' :
          (code === 'invalid_current' ? 'Obecne hasło jest nieprawidłowe.' : 'Nie udało się zmienić hasła. Spróbuj ponownie.');
        error.hidden = false;
      };
      dialog = DialogsUi.open({
        title: 'Ustaw własne hasło', nodes: rows, cancelable: false, returnFocus: () => chipNode() || ToolbarUi.refs.threads,
        actions: [
          { label: 'Wyloguj', run: async (api) => { passwordDialogOpen = false; api.close(false); await logout(); } },
          { label: 'Zmień hasło', primary: true, run: () => submit() }
        ],
        initialFocus: () => current || next
      });
      [current, next, again].filter(Boolean).forEach((input) => input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') { event.preventDefault(); submit(); }
      }));
    };

    const start = () => {
      Bus.on('remote', () => {
        const save = ToolbarUi.refs.save;
        const text = Remote.statusText();
        if (save.textContent !== text) save.textContent = text;
        save.hidden = text === '';
        const role = Remote.state.rejectedUnseen > 0 || Remote.state.conn === 'expired' ? 'alert' : 'status';
        if (save.getAttribute('role') !== role) save.setAttribute('role', role);
        const signature = Remote.state.mode + ':' + (Remote.login() || '');
        if (start.last !== signature) { start.last = signature; renderSlot(); }
        if (Remote.state.mode === 'password') passwordDialog();
      });
      Bus.on('expired', () => loginDialog('Zaloguj się ponownie', Remote.login()));
      document.addEventListener('mousedown', (event) => {
        if (menu && !menu.contains(event.target) && !ToolbarUi.refs.userSlot.contains(event.target)) closeMenu();
      }, true);
    };

    return Object.freeze({ start: start, loginDialog: loginDialog, closeMenu: closeMenu });
  })();

  // ── 13m. UI: odrzucone wpisy, banery (kolejka, błędy, wpisy v1) ───────────────────────────────────────────

  const TYPE_LABELS = Object.freeze({
    create: 'nowy wątek', reply: 'odpowiedź', edit: 'edycja wpisu', retract: 'wycofanie wpisu', vote: 'głos',
    resolve: 'zamknięcie wątku', reopen: 'ponowne otwarcie wątku', reanchor: 'zmiana kotwicy'
  });

  const downloadJson = (name, value) => {
    const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' });
    const link = Dom.make('a', { href: URL.createObjectURL(blob), download: name });
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(() => URL.revokeObjectURL(link.href), 4000);
  };

  const itemText = (item) => {
    const payload = isObject(item.payload) ? item.payload : {};
    if (isObject(payload.message)) return strOr(payload.message.text, '');
    return strOr(payload.text, '');
  };

  const RejectedUi = (() => {
    const open = (origin) => {
      const items = Remote.rejectedOf(Remote.login());
      Remote.markRejectedSeen();
      const list = Dom.make('ul', { class: 'stack-2' });
      const fill = () => {
        list.textContent = '';
        const rows = Remote.rejectedOf(Remote.login());
        if (!rows.length) list.appendChild(Dom.make('li', { class: 'muted', text: 'Brak odrzuconych wpisów.' }));
        rows.forEach((item) => {
          const remove = Dom.make('button', { type: 'button', class: 'btn btn-outline btn-sm', text: 'Usuń z listy' });
          remove.addEventListener('click', () => { Remote.dropRejected(item.cid); fill(); });
          list.appendChild(Dom.make('li', {}, [
            Dom.make('p', { class: 'strong', text: (TYPE_LABELS[item.type] || item.type) + ' · serwer odpowiedział ' + item.rejectedStatus +
              (item.rejectedCode ? ' (' + item.rejectedCode + ')' : '') }),
            Dom.make('p', { style: 'white-space:pre-wrap', text: itemText(item) }), remove
          ]));
        });
      };
      fill();
      DialogsUi.open({
        title: 'Odrzucone wpisy', returnFocus: origin || undefined, nodes: [list],
        actions: [
          { label: 'Pobierz plik', run: () => downloadJson('odrzucone-wpisy.json', Remote.rejectedOf(Remote.login())) },
          { label: 'Zamknij', primary: true }
        ]
      });
      return items.length;
    };
    return Object.freeze({ open: open });
  })();

  const V1Ui = (() => {
    const key = () => V1_COMMENTS_PREFIX + Remote.slug();
    const state = { ops: [], downloaded: false, key: null };

    const read = () => {
      try {
        const raw = window.localStorage.getItem(key());
        if (!raw) return [];
        const doc = JSON.parse(raw);
        return asArray(isObject(doc) ? doc.operations : doc).filter(isObject);
      } catch (error) { return []; }
    };
    const forget = () => {
      try { window.localStorage.removeItem(key()); } catch (error) { /* bez pamięci nic do usunięcia */ }
      state.ops = [];
      Bus.emit('v1', state);
    };
    const refresh = () => { state.ops = read(); Bus.emit('v1', state); };

    // Wysyłka: operacje v1 -> pozycje kolejki (id powtarzalne, więc ponowna wysyłka niczego nie dubluje).
    const sendAs = () => {
      const owner = Remote.login();
      if (!owner) return;
      const serverCids = new Set(Remote.state.ops.map((op) => op.cid).filter(isStr));
      const knownIds = emptyMap();
      state.ops.forEach((op) => { if (isObject(op) && isStr(op.id) && serverCids.has(Outbox.v1ImportId('c', op.id))) knownIds[op.id] = true; });
      const items = Outbox.convertV1(state.ops, { knownIds: knownIds, knownThreads: {}, idFor: Outbox.v1ImportId });
      // Źródło (localStorage v1) zostaje, dopóki wszystkie wpisy nie trafią do kolejki; ponowna wysyłka jest bezpieczna (id powtarzalne).
      if (!items.every((item) => Remote.enqueue(owner, item))) {
        Toast.show('Nie można zapisać wpisów w kolejce: pamięć przeglądarki jest pełna. Stare wpisy zostały nietknięte.');
        return;
      }
      forget();
      Toast.show('Wysłano ' + items.length + ' ' + (items.length === 1 ? 'wpis' : 'wpisów') + ' ze starej wersji' +
        (items.length === 0 ? ' (wszystko już jest na serwerze).' : '.'));
      Remote.publish();
      Remote.flush();
    };

    const download = () => { downloadJson('wpisy-ze-starej-wersji.json', read()); state.downloaded = true; Bus.emit('v1', state); };

    const start = () => {
      refresh();
      window.addEventListener('storage', refresh);
    };
    return Object.freeze({ start: start, state: state, sendAs: sendAs, download: download, forget: forget });
  })();

  const BannersUi = (() => {
    let signature = '';

    const plural = (n) => (n === 1 ? 'wpis' : (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? 'wpisy' : 'wpisów'));

    const describe = () => {
      const s = Remote.state;
      const rows = [];
      const frozenInfo = IterUi.frozenInfo();
      if (frozenInfo) {
        rows.push({ key: 'frozen', role: 'status', text: 'Iteracja ' + frozenInfo.number + ' jest zamrożona · tylko do odczytu. Nowe komentarze, akceptacja i przywracanie są wyłączone.',
          buttons: [], links: [{ label: 'Wróć do bieżącej', href: frozenInfo.currentHref }] });
      } else if (s.stale && s.mode !== 'file') {
        rows.push({ key: 'stale', role: 'alert', text: 'Prototyp zmieniono, przeładuj.', buttons: [{ label: 'Przeładuj', run: () => location.reload() }] });
      }
      if (s.pollProblem === 'invalid_log' || s.pollProblem === 'needs_migration' || (s.pollProblem && s.mode === 'full' && !s.loaded)) {
        rows.push({ key: 'poll', role: 'alert', text: s.pollProblem === 'needs_migration' ?
          'Prototyp wymaga migracji do formatu v2: uruchom „skillsync prototype migrate”.' :
          'Nie udało się wczytać komentarzy: plik komentarzy jest niedostępny albo uszkodzony.',
        buttons: [{ label: 'Spróbuj ponownie', run: () => Remote.poll() }] });
      }
      if (s.mode === 'full' && s.queueCount > 0 && (s.conn !== 'ok' || s.retrying || s.blocked)) {
        const why = s.conn === 'offline' ? 'Brak połączenia z serwerem.' : (s.conn === 'expired' ? 'Sesja wygasła.' : (s.blocked ? s.blocked + '.' : 'Serwer chwilowo nie odpowiada.'));
        rows.push({ key: 'queue', role: 'status', text: why + ' W kolejce: ' + s.queueCount + '. Wpisy zostaną wysłane po powrocie połączenia.',
          buttons: [{ label: 'Ponów teraz', run: () => Remote.retryNow() }, { label: 'Pobierz kolejkę', run: () => downloadJson('kolejka.json', Remote.queueOf(Remote.login())) }] });
      }
      if (s.rejectedTotal > 0) {
        rows.push({ key: 'rejected', role: 'alert', text: 'Serwer odrzucił ' + s.rejectedTotal + ' ' + plural(s.rejectedTotal) + ' i nie zapisał ' +
          (s.rejectedTotal === 1 ? 'go' : 'ich') + '.', buttons: [{ label: 'Pokaż odrzucone', run: () => RejectedUi.open(null) }] });
      }
      const v1 = V1Ui.state;
      if (v1.ops.length > 0) {
        const buttons = [];
        if (s.mode === 'full' && s.loaded) {
          const user = s.user;
          buttons.push({ label: 'Wyślij jako ' + strOr(user.firstName, strOr(user.name, user.login)) + '…', run: () => V1Ui.sendAs() });
        }
        buttons.push({ label: 'Pobierz plik', run: () => V1Ui.download() });
        if (v1.downloaded) buttons.push({ label: 'Usuń z przeglądarki', run: () => V1Ui.forget() });
        rows.push({ key: 'v1', role: 'status', text: 'Wpisy ze starej wersji prototypu (' + v1.ops.length + ') są zapisane w tej przeglądarce.' +
          (s.mode === 'anon' ? ' Zaloguj się, żeby je wysłać.' : ''), buttons: buttons });
      }
      return rows;
    };

    const render = () => {
      const rows = describe();
      rows.forEach((row) => { if (!row.links) row.links = []; });
      const next = JSON.stringify(rows.map((row) => [row.key, row.role, row.text, row.buttons.map((b) => b.label), row.links.map((l) => l.label + l.href)]));
      if (next === signature) return;
      signature = next;
      const host = ToolbarUi.refs.banners;
      host.textContent = '';
      rows.forEach((row) => {
        const node = Dom.make('div', { role: row.role, 'data-banner': row.key }, [Dom.make('span', { text: row.text })]);
        row.links.forEach((spec) => node.appendChild(Dom.make('a', { href: spec.href, class: 'btn-outline btn-sm anno-banner-link', text: spec.label })));
        row.buttons.forEach((spec) => {
          const button = Dom.make('button', { type: 'button', class: 'btn-outline btn-sm', text: spec.label });
          button.addEventListener('click', spec.run);
          node.appendChild(button);
        });
        host.appendChild(node);
      });
    };

    const start = () => {
      Bus.on('remote', render);
      Bus.on('v1', render);
    };
    return Object.freeze({ start: start });
  })();

  // ── 13j. UI: pinezki, tryb komentowania i okienko komentarza ──────────────────────────────────────────────────

  // Położenie pinezki w ramce `frame` (px układu ramki, bez skali) dla kotwicy `anchor` i elementu `element` (null = cały ekran).
  const pinSpot = (frame, element, anchor) => {
    const base = frame.getBoundingClientRect();
    const target = (element || frame).getBoundingClientRect();
    if (target.width === 0 && target.height === 0) return { hidden: true };
    // Prostokąty z DOM są już przeskalowane (zwężenie panelu), więc dzielimy przez skalę.
    const scale = frame.offsetWidth > 0 ? base.width / frame.offsetWidth : 1;
    const point = isObject(anchor) ? anchor : {};
    const x = typeof point.x === 'number' ? Math.min(1, Math.max(0, point.x)) : 0.5;
    const y = typeof point.y === 'number' ? Math.min(1, Math.max(0, point.y)) : 0.5;
    // Pinezka leży w lewym górnym rogu od punktu kotwicy, żeby nie zasłaniać środka elementu (klik, dotyk).
    return {
      hidden: false,
      left: Math.max(0, Math.round((target.left - base.left + x * target.width) / scale - frame.clientLeft - 26)) + 'px',
      top: Math.max(0, Math.round((target.top - base.top + y * target.height) / scale - frame.clientTop - 26)) + 'px'
    };
  };

  const PinsUi = (() => {
    const pins = new Map();
    let observer = null;

    const frameOf = (thread) => {
      const element = Fold.anchorKind(thread) === 'element' ? Model.elementOf(thread) : null;
      if (element) return { frame: element.closest('.frame') || element.closest('.screen'), element: element };
      const section = document.getElementById(thread.screen);
      return { frame: section ? (section.querySelector('.frame') || section) : null, element: null };
    };

    // Układanie w dwóch przebiegach (odczyty, potem zapisy), żeby przy wielu pinezkach nie wymuszać układu strony po każdej z nich.
    const measure = (pin, thread) => {
      const found = frameOf(thread);
      if (!found.frame || (Fold.anchorKind(thread) === 'element' && !found.element)) return { hidden: true };
      return pinSpot(found.frame, found.element, thread.anchor);
    };

    const attach = (pin, thread) => {
      const found = frameOf(thread);
      if (found.frame && pin.parentNode !== found.frame) found.frame.appendChild(pin);
    };

    const layout = (entries) => {
      entries.forEach((entry) => attach(entry.pin, entry.thread));
      const plans = entries.map((entry) => measure(entry.pin, entry.thread));
      entries.forEach((entry, at) => {
        const plan = plans[at];
        entry.pin.hidden = plan.hidden;
        if (plan.hidden) return;
        entry.pin.style.left = plan.left;
        entry.pin.style.top = plan.top;
      });
    };

    const build = (thread) => {
      const pin = Dom.make('button', { type: 'button', class: 'anno-pin' });
      pin.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        PanelUi.showThread(pin.getAttribute('data-thread'), true);
      });
      pin.setAttribute('data-thread', thread.id);
      return pin;
    };

    let lastSignature = '';
    const render = () => {
      const threads = Model.data.folded.threads;
      // Bez zmiany danych (typowe odpytanie) nic nie przeliczamy ani nie dotykamy DOM.
      const signature = JSON.stringify(threads.map((thread) => [thread.id, thread.number, thread.status, thread.screen, thread.anchor, thread.view,
        ViewsUi.pinOnPage(thread), thread.messages.length ? [thread.messages[0].text, thread.messages[0].retracted] : null,
        Fold.anchorKind(thread) === 'element' ? !!Model.elementOf(thread) : true]));
      if (signature === lastSignature) return;
      lastSignature = signature;
      const alive = new Set(threads.map((thread) => thread.id));
      pins.forEach((pin, id) => {
        if (alive.has(id)) return;
        if (pin.parentNode) pin.parentNode.removeChild(pin);
        pins.delete(id);
      });
      const entries = [];
      threads.forEach((thread) => {
        // Pinezka jest na stronie tylko w widoku wątku (mobile rysuje iframe widoku); element bez uchwytu nie ma pinezki.
        if (!ViewsUi.pinOnPage(thread) || (Fold.anchorKind(thread) === 'element' && !Model.elementOf(thread))) {
          const old = pins.get(thread.id);
          if (old && old.parentNode) old.parentNode.removeChild(old);
          pins.delete(thread.id);
          return;
        }
        let pin = pins.get(thread.id);
        if (!pin) { pin = build(thread); pins.set(thread.id, pin); }
        pin.className = 'anno-pin anno-st-' + thread.status;
        pin.textContent = String(thread.number);
        const opening = thread.messages[0];
        const snippet = opening && !opening.retracted ? ': ' + truncateUtf16(opening.text, 80) : '';
        pin.setAttribute('aria-label', 'Wątek ' + thread.number + ': ' + (STATUS_LABELS[thread.status] || thread.status) + snippet);
        entries.push({ pin: pin, thread: thread });
      });
      layout(entries);
    };

    const reposition = () => {
      const entries = [];
      pins.forEach((pin, id) => {
        const thread = Model.threadById(id);
        if (thread) entries.push({ pin: pin, thread: thread });
      });
      layout(entries);
    };

    const start = () => {
      Bus.on('model', render);
      Bus.on('views', render);
      window.addEventListener('resize', reposition);
      window.addEventListener('load', reposition);
      if (typeof ResizeObserver === 'function') {
        observer = new ResizeObserver(reposition);
        Array.prototype.forEach.call(document.querySelectorAll('.frame'), (frame) => observer.observe(frame));
      }
      render();
    };

    return Object.freeze({ start: start, reposition: reposition, pinFor: (id) => pins.get(id) || null });
  })();

  const ComposerUi = (() => {
    let pop = null;
    let owner = null;

    const close = (refocus) => {
      if (!pop) return false;
      const element = owner;
      pop.parentNode.removeChild(pop);
      pop = null;
      owner = null;
      if (refocus && element && element.isConnected) element.focus({ preventScroll: true });
      return true;
    };

    const labelFor = (element) => {
      const named = element.getAttribute('aria-label') || element.getAttribute('title');
      if (named) return named.trim();
      const heading = /^H[1-6]$/.test(element.tagName) ? null : element.querySelector('h1, h2, h3, h4');
      const source = heading ? heading.textContent : element.textContent;
      const text = String(source || '').replace(SPACE_RUNS, ' ').replace(SPACE_EDGES, '');
      return truncateUtf16(text || element.getAttribute('value') || element.getAttribute('placeholder') || element.tagName.toLowerCase(), 60);
    };

    const place = (x, y) => {
      const width = pop.offsetWidth || 352;
      const maxLeft = document.documentElement.clientWidth - width - 8;
      pop.style.left = Math.max(8, Math.min(x, maxLeft)) + 'px';
      // Okienko mieści się w oknie także przy niskiej ramce: pionowo przycinamy do widocznej części strony.
      const height = pop.offsetHeight || 0;
      const lowest = window.scrollY + document.documentElement.clientHeight - height - 8;
      pop.style.top = Math.round(Math.max(window.scrollY + 8, Math.min(y, lowest))) + 'px';
    };

    // spec: { head, anchor, screen, label, hint, view? ("desktop" domyślnie), element (do zwrotu fokusu), x, y (położenie okienka na stronie) }
    const open = (spec) => {
      const decision = Views.createDecision(spec.view, Remote.state.server);
      if (!decision.allowed) { Toast.show(decision.reason); return; }   // mobile bez obsługi serwera: bez okienka, kolejki i zapisu
      close(false);
      const area = Dom.make('textarea', { 'aria-label': 'Komentarz', maxlength: MAX_TEXT, rows: 4, placeholder: 'Napisz komentarz…' });
      const send = Dom.make('button', { type: 'button', class: 'btn btn-primary btn-sm', text: 'Wyślij' });
      const cancel = Dom.make('button', { type: 'button', class: 'btn btn-outline btn-sm', text: 'Anuluj' });
      const hint = Dom.make('span', { class: 't-hint', text: 'Ctrl+Enter wysyła, Esc zamyka' });
      pop = Dom.make('div', { class: 'anno-composer-pop', role: 'group', 'aria-label': 'Nowy komentarz' }, [
        Dom.make('p', { class: 'anno-composer-head', text: spec.head }), area, Dom.make('div', { class: 'row between' }, [hint, Dom.make('div', { class: 'row' }, [cancel, send])])
      ]);
      owner = spec.element;
      document.body.appendChild(pop);
      place(spec.x, spec.y);
      area.focus();
      let busy = false;
      const submit = async () => {
        const text = area.value.trim();
        if (!text || busy) return;
        busy = true;
        send.disabled = true;
        const threadId = Outbox.newClientId('th');
        const payload = {
          message: { id: Outbox.newClientId('m'), text: text }, screen: spec.screen, anchor: spec.anchor, label: truncateUtf16(spec.label, MAX_LABEL)
        };
        if (spec.hint) payload.hint = { text: truncateUtf16(spec.hint, 120) };
        Object.assign(payload, Views.payloadFields(spec.view));   // widok ekranu (wywołujący podaje `spec.view`); desktop nie zapisuje pola
        const result = await Remote.submit('create', threadId, payload);
        busy = false;
        send.disabled = false;
        if (result.accepted) close(true);
      };
      send.addEventListener('click', submit);
      cancel.addEventListener('click', () => close(true));
      area.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); submit(); }
      });
    };

    const iteration = () => (isInt(Remote.state.iteration) ? Remote.state.iteration : Model.data.folded.currentIteration);

    // Opis klikniętego elementu (kotwica względna, etykieta, podpowiedź, prostokąt); używa go strona i iframe widoku.
    const describe = (element, point) => {
      const rect = element.getBoundingClientRect();
      const x = point && rect.width ? Math.min(1, Math.max(0, (point.x - rect.left) / rect.width)) : 0.5;
      const y = point && rect.height ? Math.min(1, Math.max(0, (point.y - rect.top) / rect.height)) : 0.5;
      return {
        protoId: element.getAttribute('data-proto-id'), label: labelFor(element), rect: rect,
        hint: String(element.textContent || '').replace(SPACE_RUNS, ' ').replace(SPACE_EDGES, ''),
        x: Math.round(x * 1000) / 1000, y: Math.round(y * 1000) / 1000
      };
    };

    const openForElement = (element, point) => {
      if (!requireWrite()) return;
      const section = element.closest('.screen');
      const info = describe(element, point);
      open({
        head: 'Element „' + info.label + '” · ' + info.protoId, element: element, screen: section ? section.id : (ScreenNav.state.current || ''),
        anchor: { kind: 'element', protoId: info.protoId, x: info.x, y: info.y, iteration: iteration() },
        label: info.label, hint: info.hint, view: ViewsUi.effective(section ? section.id : ''),
        x: info.rect.left + window.scrollX, y: info.rect.bottom + window.scrollY + 6
      });
    };

    const openForScreen = (section, button) => {
      if (!requireWrite()) return;
      const rect = button.getBoundingClientRect();
      const name = Model.screenName(section.id);
      open({
        head: 'Cały ekran · ' + name, element: button, screen: section.id,
        anchor: { kind: 'screen', x: 0.5, y: 0.05, iteration: iteration() }, label: 'Cały ekran ' + name, hint: null,
        view: ViewsUi.effective(section.id),
        x: rect.left + window.scrollX, y: rect.bottom + window.scrollY + 6
      });
    };

    return Object.freeze({ close: close, isOpen: () => !!pop, openForElement: openForElement, openForScreen: openForScreen,
      open: open, describe: describe, iteration: iteration });
  })();

  const CommentingUi = (() => {
    const inChrome = (node) => !!node.closest('.doc-toolbar, .anno-panel, .anno-composer-pop, .anno-banners, .anno-user-menu, dialog, .toast');
    const nativeInteractive = (node) => /^(A|BUTTON|INPUT|SELECT|TEXTAREA|SUMMARY)$/.test(node.tagName);
    const commentable = (node) => {
      const element = node && node.closest ? node.closest('[data-proto-id]') : null;
      return element && !inChrome(element) && element.closest('.screen') ? element : null;
    };

    // W trybie komentowania elementy bez własnej obsługi klawiatury dostają przystanek Tab (odwracane przy wyjściu z trybu).
    const syncTabStops = (on) => {
      Array.prototype.forEach.call(document.querySelectorAll('.screen [data-proto-id]'), (element) => {
        if (on && !nativeInteractive(element) && !element.hasAttribute('tabindex')) {
          element.setAttribute('tabindex', '0');
          element.setAttribute('data-anno-tab', '1');
        } else if (!on && element.getAttribute('data-anno-tab') === '1') {
          element.removeAttribute('tabindex');
          element.removeAttribute('data-anno-tab');
        }
      });
    };

    const addScreenTools = () => {
      Array.prototype.forEach.call(document.querySelectorAll('section.screen'), (section) => {
        const meta = section.querySelector('.screen-meta');
        if (!meta || meta.querySelector('.anno-screen-tools')) return;
        const tools = Dom.make('div', { class: 'anno-screen-tools' });
        const button = Dom.make('button', { type: 'button', class: 'anno-screen-comment', text: 'Komentarz do ekranu' });
        button.hidden = !PageState.flags.commentMode;
        button.addEventListener('click', () => ComposerUi.openForScreen(section, button));
        tools.appendChild(button);
        meta.appendChild(tools);
      });
    };

    const start = () => {
      addScreenTools();
      Bus.on('mode', (on) => {
        syncTabStops(on);
        Array.prototype.forEach.call(document.querySelectorAll('.anno-screen-comment'), (button) => { button.hidden = !on; });
        if (!on) ComposerUi.close(false);
      });
      document.addEventListener('click', (event) => {
        if (!PageState.flags.commentMode || !event.target.closest) return;
        const element = commentable(event.target);
        if (!element) return;
        event.preventDefault();
        event.stopPropagation();
        ComposerUi.openForElement(element, { x: event.clientX, y: event.clientY });
      }, true);
      document.addEventListener('keydown', (event) => {
        if (!PageState.flags.commentMode || (event.key !== 'Enter' && event.key !== ' ')) return;
        if (event.ctrlKey || event.metaKey || event.altKey) return;
        const element = commentable(event.target);
        if (!element || element !== event.target || nativeInteractive(element)) return;
        event.preventDefault();
        event.stopPropagation();
        ComposerUi.openForElement(element, null);
      }, true);
    };

    return Object.freeze({ start: start, commentable: commentable, syncTabStops: syncTabStops, nativeInteractive: nativeInteractive });
  })();

  // ── 13j2. UI: widok makiety (przełączniki, iframe widoku mobile) i instancja osadzona ────────────────────────────
  //
  // Strona (ViewsUi) trzyma widok każdego ekranu (przełącznik przy ekranie; domyślnie desktop), wstawia do `.frame` iframe widoku mobile i obsługuje wiadomości z niego.
  // Instancja osadzona (EmbedUi, `?embed=view#sN`) renderuje jeden ekran, rysuje pinezki z listy od strony i zgłasza kliknięcia;
  // dane i zapisy ma wyłącznie strona.

  const VIEW_PLACEHOLDER_HEIGHT = 667;   // wysokość iframe do pierwszego pomiaru treści (makieta ze 100vh zostaje przy niej)
  const VIEW_LOAD_LIMIT = 5000;          // ms od rozpoczęcia ładowania do uznania iframe za niegotowy

  const ViewsUi = (() => {
    const state = { local: emptyMap() };
    const hosts = new Map();       // id ekranu -> { sid, iframe, host, ready, timer, watcher, threads }
    let selected = null;           // wątek wskazany z panelu (podświetlenie pinezki w iframe)

    const sectionOf = (sid) => (typeof sid === 'string' && sid ? document.getElementById(sid) : null);
    const frameOf = (sid) => { const section = sectionOf(sid); return section ? section.querySelector('.frame') : null; };
    const locals = new Map();   // id ekranu -> grupa przycisków przy ekranie (tylko ekrany z ramką i nagłówkiem)
    const effective = (sid) => Views.effective(state.local[sid], locals.has(sid));
    // Pinezka wątku jest na stronie tylko wtedy, gdy wątek i ekran są w widoku desktop.
    const pinOnPage = (thread) => Views.normalize(thread.view) === 'desktop' && effective(thread.screen) === 'desktop';

    const frameUrl = (sid) => String(location.href).split('#')[0].split('?')[0] + '?embed=view#' + sid;
    const post = (entry, name, fields) => {
      const target = entry.iframe.contentWindow;
      if (target) target.postMessage(Views.composeMessage(name, fields), Views.targetOrigin(location.protocol, location.origin));
    };

    const pushState = (entry) => {
      if (!entry.ready) return;
      const flags = PageState.flags;
      post(entry, 'state', {
        theme: document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light',
        flags: { hotspots: flags.hotspots, pinsHidden: flags.pinsHidden, presentation: flags.presentation },
        commentMode: flags.commentMode, thread: selected
      });
    };
    const pushThreads = (entry) => {
      if (!entry.ready) return;
      const list = Views.frameThreads(Model.data.folded.threads, entry.sid);
      const key = JSON.stringify(list);
      if (key === entry.threads) return;
      entry.threads = key;
      post(entry, 'threads', { screen: entry.sid, threads: list });
    };
    const eachHost = (fn) => hosts.forEach(fn);

    // ----- ładowanie iframe i komunikat, gdy widok nie wczyta się w 5 s -----

    const stopTimer = (entry) => {
      if (entry.timer) { clearTimeout(entry.timer); entry.timer = null; }
      if (entry.watcher) { entry.watcher.disconnect(); entry.watcher = null; }
    };
    const arm = (entry) => {
      const begin = () => { entry.timer = setTimeout(() => fail(entry), VIEW_LOAD_LIMIT); };
      // Odliczanie startuje, gdy przeglądarka zaczyna ładować iframe (lazy: w pobliżu okna), nie od wstawienia do DOM.
      if (typeof IntersectionObserver !== 'function') { begin(); return; }
      entry.watcher = new IntersectionObserver((records) => {
        if (!records.some((record) => record.isIntersecting)) return;
        entry.watcher.disconnect();
        entry.watcher = null;
        begin();
      }, { rootMargin: '1000px 0px' });
      entry.watcher.observe(entry.iframe);
    };
    const fail = (entry) => {
      stopTimer(entry);
      if (entry.ready || hosts.get(entry.sid) !== entry) return;
      console.error('skillsync-prototype: nie udało się wczytać widoku mobile (ekran ' + entry.sid + ')');
      entry.iframe.remove();
      const retry = Dom.make('button', { type: 'button', class: 'btn btn-outline btn-sm', text: 'Spróbuj ponownie' });
      retry.addEventListener('click', () => { release(entry.sid); mount(entry.sid); });
      entry.host.appendChild(Dom.make('div', { class: 'anno-view-error', role: 'alert' }, [
        Dom.make('p', { text: 'Nie udało się wczytać widoku mobile' }), retry]));
    };

    const mount = (sid) => {
      const frame = frameOf(sid);
      if (!frame || hosts.has(sid)) return;
      const iframe = Dom.make('iframe', { src: frameUrl(sid), title: 'Widok mobile: ' + Model.screenName(sid), loading: 'lazy',
        'data-proto-view': 'mobile', 'data-screen': sid });
      iframe.style.height = VIEW_PLACEHOLDER_HEIGHT + 'px';
      const host = Dom.make('div', { class: 'anno-view-host' }, [iframe]);
      const entry = { sid: sid, iframe: iframe, host: host, ready: false, timer: null, watcher: null, threads: '' };
      hosts.set(sid, entry);
      frame.setAttribute('data-proto-view-on', 'mobile');
      frame.appendChild(host);
      arm(entry);
    };
    const release = (sid) => {
      const entry = hosts.get(sid);
      if (!entry) return;
      stopTimer(entry);
      entry.host.remove();
      hosts.delete(sid);
      const frame = frameOf(sid);
      if (frame) frame.removeAttribute('data-proto-view-on');
    };

    // ----- przełączniki -----

    const sync = () => {
      locals.forEach((group, sid) => {
        const view = effective(sid);
        group.paint(view);
        if (view === 'mobile') mount(sid); else release(sid);
      });
      Bus.emit('views');
    };
    const setLocal = (sid, view) => {
      if (!locals.has(sid)) return;
      state.local[sid] = Views.normalize(view);
      sync();
    };

    // Klik w wątek: ekran wątku przechodzi na widok wątku, a ekran jest przewijany na początek.
    const reveal = (thread, scroll) => {
      if (!thread || !locals.has(thread.screen)) return;
      const want = Views.normalize(thread.view);
      const changed = effective(thread.screen) !== want;
      if (changed) setLocal(thread.screen, want);
      selected = thread.id;
      eachHost(pushState);
      if (!changed && !scroll) return;
      ScreenNav.goTo(thread.screen, true);
      sectionOf(thread.screen).scrollIntoView({ behavior: 'instant', block: 'start' });
    };

    // ----- wiadomości z iframe widoku -----

    const pick = (entry, message) => {
      // Zgłoszenie ma sens tylko w trybie Komentarze i dla elementu z ekranu tego iframe.
      const section = sectionOf(entry.sid);
      const known = section && section.querySelector(protoIdSelector(message.anchor.protoId));
      if (!PageState.flags.commentMode || !known) return;
      if (!requireWrite()) return;
      const box = entry.iframe.getBoundingClientRect();
      const scale = entry.iframe.offsetWidth > 0 ? box.width / entry.iframe.offsetWidth : 1;
      const rect = message.rect;
      // Zwrot fokusu po zamknięciu okienka: iframe i polecenie dla instancji osadzonej (element w iframe).
      const proxy = { isConnected: true, focus: () => { entry.iframe.focus({ preventScroll: true }); post(entry, 'focus-return', { protoId: message.anchor.protoId }); } };
      ComposerUi.open({
        head: 'Element „' + message.label + '” · ' + message.anchor.protoId, element: proxy, screen: entry.sid,
        anchor: { kind: 'element', protoId: message.anchor.protoId, x: Math.round(message.anchor.x * 1000) / 1000,
          y: Math.round(message.anchor.y * 1000) / 1000, iteration: ComposerUi.iteration() },
        label: message.label, hint: message.hint, view: 'mobile',
        x: box.left + window.scrollX + rect.left * scale, y: box.top + window.scrollY + rect.bottom * scale + 6
      });
    };
    const HANDLERS = Object.assign(emptyMap(), {
      ready: (entry) => { entry.ready = true; entry.threads = ''; stopTimer(entry); pushState(entry); pushThreads(entry); },
      height: (entry, message) => { entry.iframe.style.height = Math.ceil(message.height) + 'px'; },
      pick: pick,
      'pin-open': (entry, message) => {
        const thread = Model.threadById(message.threadId);
        if (thread && thread.screen === entry.sid) PanelUi.showThread(thread.id, true);
      },
      navigate: (entry, message) => { ScreenNav.goTo(message.hash.slice(1), false); },
      // Esc w iframe działa jak Esc na stronie (okienko, panel, tryb komentowania).
      escape: () => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })); }
    });
    const onMessage = (event) => {
      let entry = null;
      hosts.forEach((candidate) => { if (candidate.iframe.contentWindow && candidate.iframe.contentWindow === event.source) entry = candidate; });
      if (!entry || !Views.originAllowed(location.protocol, event.origin, location.origin)) return;
      const message = Views.parseMessage(event.data, 'toPage');
      if (message) HANDLERS[message.type](entry, message);
    };

    const start = () => {
      Array.prototype.forEach.call(document.querySelectorAll('section.screen[id]'), (section) => {
        const meta = section.querySelector('.screen-meta');
        if (!meta || !section.querySelector('.frame')) return;   // ekran bez ramki nie ma przełącznika (widok zawsze desktop)
        const group = ViewControls.make({ 'aria-label': 'Widok ekranu ' + Model.screenName(section.id), 'data-proto-view-switch': '' },
          (view) => setLocal(section.id, view));
        group.paint('desktop');
        meta.appendChild(group.node);
        locals.set(section.id, group);
      });
      window.addEventListener('message', onMessage);
      ['theme', 'flag:hotspots', 'flag:pinsHidden', 'flag:presentation', 'mode'].forEach((name) => Bus.on(name, () => eachHost(pushState)));
      Bus.on('model', () => eachHost(pushThreads));
    };

    return Object.freeze({ start: start, state: state, setLocal: setLocal, effective: effective,
      pinOnPage: pinOnPage, reveal: reveal });
  })();

  const EmbedUi = (() => {
    const state = { sid: '', section: null, pins: new Map(), threads: [], selected: null, commentMode: false, height: -1, viewport: -1 };
    const parentWindow = () => (window.parent && window.parent !== window ? window.parent : null);
    const send = (name, fields) => {
      const target = parentWindow();
      if (target) target.postMessage(Views.composeMessage(name, fields), Views.targetOrigin(location.protocol, location.origin));
    };

    const chooseSection = () => {
      const screens = Array.prototype.slice.call(document.querySelectorAll('section.screen[id]'));
      const wanted = String(location.hash || '').slice(1);
      const target = screens.find((section) => section.id === wanted) || screens[0] || null;
      screens.forEach((section) => section.classList.toggle('is-embed-target', section === target));
      state.section = target;
      state.sid = target ? target.id : '';
      isolateFrame(target);
    };

    // Dokument iframe pokazuje wyłącznie `.frame` wybranego ekranu: rodzeństwo każdego przodka ramki (pasek, spis ekranów,
    // nagłówek dokumentu i ekranu, notatki, inne ekrany, stopki autora) dostaje `data-embed-hide` (CSS: display: none).
    const KEEP_TAGS = /^(SCRIPT|STYLE|LINK|META|TEMPLATE|NOSCRIPT)$/;
    const isolateFrame = (section) => {
      Array.prototype.forEach.call(document.querySelectorAll('[data-embed-hide]'), (node) => node.removeAttribute('data-embed-hide'));
      const frame = section ? section.querySelector('.frame') : null;
      let node = frame;
      while (node && node !== document.documentElement) {
        const parent = node.parentNode;
        if (parent && parent.children) {
          Array.prototype.forEach.call(parent.children, (sibling) => {
            if (sibling !== node && !KEEP_TAGS.test(sibling.tagName) && !sibling.hasAttribute('data-proto-embed-keep')) sibling.setAttribute('data-embed-hide', '');
          });
        }
        node = parent;
      }
    };

    // Wysokość treści ekranu (nie okna): sekcja jest kontekstem formatowania bloku, więc marginesy dzieci się wliczają.
    // Treść zależna od wysokości okna (np. `min-height: 100vh`) rosłaby po każdej zmianie wysokości iframe: wzrost, który
    // przyszedł razem ze zmianą wysokości okna iframe (po naszym własnym zgłoszeniu), nie jest zgłaszany ponownie.
    const reportHeight = () => {
      if (!state.section) return;
      // Dokument ramki nie przewija się (iframe ma wysokość treści): przewinięcie z fragmentu adresu albo fokusu przesunęłoby treść poza iframe.
      const scroller = document.scrollingElement;
      if (scroller && (scroller.scrollTop !== 0 || scroller.scrollLeft !== 0)) window.scrollTo(0, 0);
      const height = Math.ceil(state.section.getBoundingClientRect().height);
      const viewport = window.innerHeight;
      const feedback = state.height >= 0 && viewport !== state.viewport && height > state.height;
      state.viewport = viewport;
      if (feedback || (state.height >= 0 && Math.abs(height - state.height) < 1)) return;
      state.height = height;
      send('height', { height: height });
    };

    // ----- pinezki z listy wątków od strony -----

    const frameFor = (element) => (element ? (element.closest('.frame') || element.closest('.screen')) : (state.section ? state.section.querySelector('.frame') : null));
    const elementFor = (anchor) => (anchor.kind === 'element' ? document.querySelector(protoIdSelector(anchor.protoId)) : null);
    const layoutPins = () => {
      state.threads.forEach((thread) => {
        const pin = state.pins.get(thread.id);
        if (!pin) return;
        const element = elementFor(thread.anchor);
        const frame = frameFor(element);
        if (!frame || (thread.anchor.kind === 'element' && !element)) { pin.hidden = true; return; }
        if (pin.parentNode !== frame) frame.appendChild(pin);
        const spot = pinSpot(frame, element, thread.anchor);
        pin.hidden = spot.hidden;
        if (spot.hidden) return;
        pin.style.left = spot.left;
        pin.style.top = spot.top;
      });
    };
    const renderPins = () => {
      const alive = new Set(state.threads.map((thread) => thread.id));
      state.pins.forEach((pin, id) => { if (!alive.has(id)) { pin.remove(); state.pins.delete(id); } });
      state.threads.forEach((thread) => {
        let pin = state.pins.get(thread.id);
        if (!pin) {
          pin = Dom.make('button', { type: 'button', class: 'anno-pin', 'data-thread': thread.id });
          pin.addEventListener('click', (event) => { event.preventDefault(); event.stopPropagation(); send('pin-open', { threadId: thread.id }); });
          state.pins.set(thread.id, pin);
        }
        pin.className = 'anno-pin anno-st-' + thread.status;
        pin.textContent = String(thread.number);
        pin.setAttribute('aria-label', 'Wątek ' + thread.number + ': ' + (STATUS_LABELS[thread.status] || thread.status));
        if (state.selected === thread.id) pin.setAttribute('data-anno-target', '1'); else pin.removeAttribute('data-anno-target');
      });
      layoutPins();
    };

    // ----- stan od strony -----

    const applyState = (message) => {
      const root = document.documentElement;
      root.setAttribute('data-theme', message.theme);
      root.style.colorScheme = message.theme;
      root.classList.toggle('proto-hotspots', message.flags.hotspots);
      root.classList.toggle('proto-pins-hidden', message.flags.pinsHidden);
      root.classList.toggle('proto-presentation', message.flags.presentation);
      root.classList.toggle('proto-comment-mode', message.commentMode);
      if (message.commentMode !== state.commentMode) CommentingUi.syncTabStops(message.commentMode);
      state.commentMode = message.commentMode;
      if (message.thread !== state.selected) { state.selected = message.thread; renderPins(); }
    };
    const returnFocus = (protoId) => {
      const element = protoId ? document.querySelector(protoIdSelector(protoId)) : null;
      if (!element) return;
      if (!CommentingUi.nativeInteractive(element) && !element.hasAttribute('tabindex')) element.setAttribute('tabindex', '-1');
      element.focus({ preventScroll: true });
    };
    const onMessage = (event) => {
      if (event.source !== parentWindow() || !Views.originAllowed(location.protocol, event.origin, location.origin)) return;
      const message = Views.parseMessage(event.data, 'toFrame');
      if (!message) return;
      if (message.type === 'state') applyState(message);
      else if (message.type === 'threads') { if (message.screen === state.sid) { state.threads = message.threads; renderPins(); } }
      else returnFocus(message.protoId);
    };

    // ----- zdarzenia w ramce -----

    const pickFrom = (element, point) => {
      const info = ComposerUi.describe(element, point);
      const rect = info.rect;
      send('pick', { anchor: { kind: 'element', protoId: info.protoId, x: info.x, y: info.y }, label: truncateUtf16(info.label, MAX_LABEL),
        hint: truncateUtf16(info.hint, 120), rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom } });
    };
    // Cel kliknięcia: `data-goto` albo link `#…`. Ekran instancji osadzonej jest ustalony od startu, więc każdy taki link
    // jest przechwytywany; tylko `#sN` jest przekazywany stronie.
    const hashOf = (node) => {
      const goto = node.closest ? node.closest('[data-goto]') : null;
      if (goto) return '#' + goto.getAttribute('data-goto');
      const link = node.closest ? node.closest('a[href^="#"]') : null;
      return link ? link.getAttribute('href') : null;
    };
    const wire = () => {
      document.addEventListener('click', (event) => {
        if (!event.target.closest) return;
        if (state.commentMode) {
          const element = CommentingUi.commentable(event.target);
          if (!element) return;
          event.preventDefault();
          event.stopPropagation();
          pickFrom(element, { x: event.clientX, y: event.clientY });
          return;
        }
        const hash = hashOf(event.target);
        if (hash === null || event.defaultPrevented) return;
        event.preventDefault();
        if (/^#s\d+$/.test(hash)) send('navigate', { hash: hash });
      }, true);
      document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') { send('escape', {}); return; }
        if ((event.key !== 'Enter' && event.key !== ' ') || event.ctrlKey || event.metaKey || event.altKey) return;
        const node = event.target;
        if (state.commentMode) {
          const element = CommentingUi.commentable(node);
          if (!element || element !== node || CommentingUi.nativeInteractive(element)) return;
          event.preventDefault();
          event.stopPropagation();
          pickFrom(element, null);
          return;
        }
        if (!node || !node.hasAttribute || !node.hasAttribute('data-goto') || Navigation.nativeKinds.test(node.tagName)) return;
        event.preventDefault();
        send('navigate', { hash: '#' + node.getAttribute('data-goto') });
      }, true);
    };

    const start = () => {
      chooseSection();
      Navigation.decorate();
      window.addEventListener('message', onMessage);
      wire();
      if (typeof ResizeObserver === 'function') {
        const watcher = new ResizeObserver(() => { reportHeight(); layoutPins(); });
        watcher.observe(document.body);
        if (state.section) watcher.observe(state.section);
      }
      window.addEventListener('load', () => { reportHeight(); layoutPins(); });
      send('ready', { screen: state.sid });
      reportHeight();
    };

    return Object.freeze({ start: start });
  })();

  // ── 13k. UI: panel wątków (lista, filtry, wyszukiwanie, widok wątku, odpowiedzi, głosy, edycja, szkice) ─────

  const PanelUi = (() => {
    const FILTER_LABELS = Object.freeze({
      open: 'Otwarte', new: 'Nowe od ostatniej wizyty', agent: 'Decyzje agenta', lost: 'Bez uchwytu', all: 'Wszystkie iteracje'
    });
    const view = { open: false, name: 'list', threadId: null, filter: 'open', query: '', viewFilter: 'all' };
    const VIEW_FILTERS = Object.freeze([['all', 'Wszystkie'], ['desktop', 'Desktop'], ['mobile', 'Mobile']]);
    const VIEW_LABELS = Object.freeze({ desktop: 'Desktop', mobile: 'Mobile' });
    const listContext = () => Object.assign({}, Model.context(), { viewFilter: view.viewFilter });
    const drafts = emptyMap();
    const ui = {};
    let signature = '';
    let editing = null;

    const draftOf = (id) => (has(drafts, id) ? drafts[id] : '');
    // Niezapisana edycja własnego wpisu to też szkic: pytanie przed opuszczeniem widoku.
    const dirtyEdit = () => !!editing;   // otwarta edycja (także bez zmian) traktowana jak szkic: pytanie przed opuszczeniem widoku
    const anyDraft = () => dirtyEdit() || Object.keys(drafts).some((id) => drafts[id].trim() !== '');
    const replyField = () => ui.panel.querySelector('textarea[aria-label="Odpowiedź"]');

    // ----- lista -----

    const visibleThreads = () => {
      const ctx = listContext();
      const needle = normalizeQuery(view.query);
      return Panel.grouped(Model.data.folded.threads, view.filter, ctx).map((group) => ({
        screen: group.screen,
        threads: group.threads.filter((thread) => needle === '' || searchText(thread).indexOf(needle) >= 0)
      })).filter((group) => group.threads.length > 0);
    };
    const normalizeQuery = (text) => Switcher.normalizeSearch(text);
    const searchText = (thread) => Switcher.normalizeSearch([thread.label, '#' + thread.number, thread.screen].concat(
      thread.messages.filter((message) => !message.retracted).map((message) => message.text + ' ' + Model.labelOf(message.by))).join(' '));
    const orderedIds = () => visibleThreads().reduce((ids, group) => ids.concat(group.threads.map((thread) => thread.id)), []);

    const cardFor = (thread) => {
      const opening = thread.messages.find((message) => message.opening) || thread.messages[0];
      const lost = !Model.hasHandle(thread);
      const open = Dom.make('button', { type: 'button', class: 'anno-card-open' }, [
        Dom.make('span', { class: 'strong', text: '#' + thread.number + ' ' + (thread.label || 'Wątek') }),
        Dom.make('span', { class: 'muted', text: STATUS_LABELS[thread.status] + (lost ? ' · bez uchwytu' : '') }),
        Dom.make('span', { class: 'anno-view-badge', 'data-proto-view-badge': Views.normalize(thread.view), text: VIEW_LABELS[Views.normalize(thread.view)] })
      ]);
      open.addEventListener('click', (event) => { event.stopPropagation(); showThread(thread.id, true, false, true); });
      const text = opening && !opening.retracted ? opening.text : 'Wpis wycofany przez autora.';
      const card = Dom.make('article', { class: 'anno-card anno-st-' + thread.status, 'data-thread': thread.id }, [
        open, Dom.make('p', { class: 't-body', text: truncateUtf16(text, 160) }),
        Dom.make('span', { class: 'muted', text: Model.labelOf(thread.by) + ' · ' + thread.messages.filter((m) => !m.retracted).length + ' wpisów' })
      ]);
      card.addEventListener('click', () => { showThread(thread.id, true, false, true); });   // cała karta otwiera wątek i ustawia widok ekranu
      return card;
    };

    const renderList = () => {
      const counts = Panel.counts(Model.data.folded.threads, listContext());
      ui.views.textContent = '';
      VIEW_FILTERS.forEach((entry) => {
        const button = Dom.make('button', { type: 'button', class: 'btn-outline', 'aria-pressed': String(view.viewFilter === entry[0]), text: entry[1] });
        button.addEventListener('click', () => { view.viewFilter = entry[0]; signature = ''; render(); });
        ui.views.appendChild(button);
      });
      ui.filters.textContent = '';
      FILTERS.forEach((filter) => {
        const button = Dom.make('button', { type: 'button', class: 'btn-outline', 'aria-pressed': String(view.filter === filter),
          text: FILTER_LABELS[filter] + ' ' + counts[filter] });
        button.addEventListener('click', () => { view.filter = filter; signature = ''; render(); });
        ui.filters.appendChild(button);
      });
      ui.cards.textContent = '';
      const groups = visibleThreads();
      if (!groups.length) ui.cards.appendChild(Dom.make('p', { class: 'muted anno-empty', text: 'Brak wątków dla tych filtrów.' }));
      groups.forEach((group) => {
        ui.cards.appendChild(Dom.make('h3', { class: 't-label', text: Model.screenName(group.screen) }));
        group.threads.forEach((thread) => ui.cards.appendChild(cardFor(thread)));
      });
      const mode = Remote.state.mode;
      ui.hint.textContent = '';
      ui.hint.hidden = mode !== 'file';
      if (mode === 'file') {
        ui.hint.appendChild(document.createTextNode('Komentarze wymagają serwera. Uruchom w katalogu projektu: '));
        ui.hint.appendChild(Dom.make('code', { text: 'skillsync prototype serve' }));
      }
    };

    // ----- widok wątku -----

    const voteButton = (message, side, summary) => {
      const mine = Remote.login();
      const names = summary[side];
      const pressed = mine !== null && names.indexOf(mine) >= 0;
      const emoji = side === 'up' ? '👍' : '👎';
      const button = Dom.make('button', { type: 'button', class: 'anno-vote', 'aria-pressed': String(pressed),
        'aria-label': (side === 'up' ? 'Głos za' : 'Głos przeciw') + ' (' + names.length + ')', text: names.length + ' ' + emoji });
      button.addEventListener('click', async () => {
        if (!requireWrite()) return;
        await Remote.submit('vote', ui.thread.id, { messageId: message.id, value: pressed ? 'none' : side });
      });
      return button;
    };

    const messageNode = (thread, message) => {
      const mine = Remote.login() !== null && message.by === Remote.login();
      const head = Dom.make('div', { class: 'row between' }, [
        Dom.make('span', { class: 'strong', text: Model.labelOf(message.by) }),
        Dom.make('span', { class: 'muted', text: Model.formatTime(message.at) + (message.edited ? ' · edytowano' : '') })
      ]);
      const node = Dom.make('div', { class: 'anno-msg', 'data-message': message.id }, [head]);
      if (message.retracted) {
        node.appendChild(Dom.make('p', { class: 'muted', text: 'Wpis wycofany przez autora.' }));
        return node;
      }
      node.appendChild(Dom.make('p', { class: 't-body', style: 'white-space:pre-wrap', text: message.text }));
      const summary = Fold.voteSummary(message);
      const row = Dom.make('div', { class: 'row' }, [voteButton(message, 'up', summary), voteButton(message, 'down', summary)]);
      const voters = [];
      if (summary.up.length) voters.push('👍 ' + summary.up.map(Model.labelOf).join(', '));
      if (summary.down.length) voters.push('👎 ' + summary.down.map(Model.labelOf).join(', '));
      row.appendChild(Dom.make('span', { class: 'anno-voters', text: voters.join(' · ') }));
      node.appendChild(row);
      if (mine && Remote.writable()) {
        const edit = Dom.make('button', { type: 'button', class: 'btn-ghost', text: 'Edytuj' });
        const retract = Dom.make('button', { type: 'button', class: 'btn-ghost', text: 'Wycofaj' });
        edit.addEventListener('click', () => startEdit(thread, message, node));
        retract.addEventListener('click', () => confirmRetract(message, retract));
        node.appendChild(Dom.make('div', { class: 'row' }, [edit, retract]));
      }
      return node;
    };

    const startEdit = (thread, message, node) => {
      const area = Dom.make('textarea', { 'aria-label': 'Edytuj wpis', maxlength: MAX_TEXT, rows: 3 });
      area.value = message.text;
      editing = { id: message.id, area: area, original: message.text };
      const save = Dom.make('button', { type: 'button', class: 'btn btn-primary btn-sm', text: 'Zapisz' });
      const cancel = Dom.make('button', { type: 'button', class: 'btn btn-outline btn-sm', text: 'Anuluj' });
      const finish = () => { editing = null; signature = ''; render(); };
      const commit = async () => {
        const text = area.value.trim();
        if (!text) return;
        if (text === message.text) { finish(); return; }
        const result = await Remote.submit('edit', thread.id, { messageId: message.id, text: text });
        if (result.accepted) finish();
      };
      save.addEventListener('click', commit);
      cancel.addEventListener('click', finish);
      area.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); commit(); }
        else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish(); }
      });
      node.textContent = '';
      node.appendChild(area);
      node.appendChild(Dom.make('div', { class: 'row' }, [cancel, save]));
      area.focus();
      area.select();
    };

    const confirmRetract = (message, origin) => {
      DialogsUi.open({
        title: 'Wycofać wpis?', returnFocus: origin,
        nodes: [Dom.make('p', { text: 'Treść wpisu zostanie ukryta dla wszystkich. Tego nie można cofnąć.' })],
        actions: [{ label: 'Anuluj' }, { label: 'Wycofaj', primary: true, run: async (api) => {
          api.close(false);
          await Remote.submit('retract', ui.thread.id, { messageId: message.id });
        } }]
      });
    };

    const confirmDelete = (thread, origin) => {
      DialogsUi.open({
        title: 'Usunąć wątek #' + thread.number + '?', returnFocus: origin,
        nodes: [Dom.make('p', { text: 'Wątek zniknie dla wszystkich. Numeracja pozostałych wątków się nie zmienia.' })],
        actions: [{ label: 'Anuluj' }, { label: 'Usuń wątek', primary: true, run: async (api) => {
          api.close(false);
          const result = await Remote.submit('delete', thread.id, {});
          if (result.ok) showList(true);
        } }]
      });
    };

    const threadSignature = (thread) => JSON.stringify([thread.view, thread.status, thread.statusReason, thread.triage, thread.decisions.length, Remote.login(),
      Remote.state.mode, Model.hasHandle(thread), IterUi.linkKey(thread), thread.messages.map((m) => [m.id, m.text, m.edited, m.retracted, m.votes])]);

    const decisionNodes = (thread) => {
      const nodes = [];
      if (thread.triage) nodes.push(Dom.make('p', { class: 'muted', text: 'Ocena agenta: ' + thread.triage.class + (thread.triage.note ? ' · ' + thread.triage.note : '') }));
      thread.decisions.forEach((decision) => nodes.push(Dom.make('p', { class: 'muted',
        text: 'Decyzja (' + (decision.kind === 'developer' ? 'programista' : 'agent') + '): ' + decision.outcome + (decision.text ? ' · ' + decision.text : '') })));
      return nodes;
    };

    const manageButtons = (thread) => {
      const user = Remote.state.user;
      const rows = [];
      if (!user || !Remote.writable()) return rows;
      const can = user.role === 'developer' || thread.by === user.login;
      if (can) {
        const closed = thread.status === 'closed' || thread.status === 'deferred';
        const toggle = Dom.make('button', { type: 'button', class: 'btn-outline', text: closed ? 'Otwórz ponownie' : 'Zamknij wątek' });
        toggle.addEventListener('click', () => Remote.submit(closed ? 'reopen' : 'resolve', thread.id, closed ? {} : { reason: 'author' }));
        rows.push(toggle);
      }
      if (user.role === 'developer') {
        const del = Dom.make('button', { type: 'button', class: 'btn-outline', text: 'Usuń wątek' });
        del.addEventListener('click', () => confirmDelete(thread, del));
        rows.push(del);
      }
      return rows;
    };

    const renderThread = () => {
      const thread = Model.threadById(view.threadId);
      if (!thread) { showList(false, true); return; }
      const next = threadSignature(thread);
      if (ui.thread && ui.thread.id === thread.id && ui.thread.signature === next) return;
      if (editing) return;
      ui.thread = { id: thread.id, signature: next };
      ui.title.textContent = 'Wątek #' + thread.number;
      ui.messages.textContent = '';
      const lost = !Model.hasHandle(thread);
      const iterLink = lost ? IterUi.linkNode(thread) : null;
      ui.messages.appendChild(Dom.make('div', { class: 'anno-thread anno-st-' + thread.status }, [
        Dom.make('p', { class: 'strong', text: (thread.label || 'Wątek') + ' · ' + STATUS_LABELS[thread.status] }),
        Dom.make('p', { class: 'muted', text: Model.screenName(thread.screen) + (lost ? ' · bez uchwytu' : '') })
      ].concat(iterLink ? [iterLink] : []).concat(decisionNodes(thread)).concat(thread.messages.map((message) => messageNode(thread, message)))
        .concat([Dom.make('div', { class: 'row' }, manageButtons(thread))])));
      renderReplyForm(thread);
    };

    const renderReplyForm = (thread) => {
      const mode = Remote.state.mode;
      if (ui.reply && ui.reply.thread === thread.id && ui.reply.mode === mode) return;
      ui.reply = { thread: thread.id, mode: mode };
      ui.compose.textContent = '';
      if (mode !== 'full' || Remote.frozen()) {
        const note = Dom.make('p', { class: 'muted', text: Remote.frozen() ? 'Zamrożona iteracja jest tylko do odczytu.' :
          (mode === 'anon' ? 'Zaloguj się, żeby odpowiedzieć.' : 'Odpowiedzi wymagają trybu z serwerem i zalogowania.') });
        ui.compose.appendChild(note);
        return;
      }
      const area = Dom.make('textarea', { 'aria-label': 'Odpowiedź', maxlength: MAX_TEXT, rows: 3, placeholder: 'Odpowiedz…' });
      area.value = draftOf(thread.id);
      const send = Dom.make('button', { type: 'button', class: 'btn btn-primary btn-sm', text: 'Wyślij' });
      let busy = false;
      const submit = async () => {
        const text = area.value.trim();
        if (!text || busy) return;
        busy = true;
        const result = await Remote.submit('reply', thread.id, { message: { id: Outbox.newClientId('m'), text: text } });
        busy = false;
        if (result.accepted) { delete drafts[thread.id]; area.value = ''; }
      };
      area.addEventListener('input', () => { drafts[thread.id] = area.value; });
      area.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); submit(); }
      });
      send.addEventListener('click', submit);
      ui.compose.appendChild(area);
      ui.compose.appendChild(Dom.make('div', { class: 'row' }, [send]));
    };

    // ----- ogólne -----

    const render = () => {
      if (!view.open) return;
      ui.list.hidden = view.name !== 'list';
      ui.threadBox.hidden = view.name !== 'thread';
      ui.back.hidden = view.name !== 'thread';
      if (view.name === 'list') {
        ui.title.textContent = 'Wątki';
        const next = JSON.stringify([view.filter, view.viewFilter, view.query, Remote.state.mode, Model.context().seenAt,
          Model.data.folded.threads.map((t) => [t.id, t.view, t.status, t.lastAt, t.messages.length, Model.hasHandle(t)])]);
        if (next !== signature) { signature = next; renderList(); }
      } else {
        renderThread();
      }
    };

    const updateButton = () => {
      const count = Model.openCount();
      ui.button.textContent = 'Wątki (' + count + ')';
      ui.button.setAttribute('aria-expanded', String(view.open));
      ScaleUi.sync(false);
    };

    const showList = (focusTitle, forced) => {
      if (!forced && dirtyEdit()) { askDiscard(() => showList(focusTitle, true)); return; }
      editing = null;
      view.name = 'list';
      view.threadId = null;
      ui.thread = null;
      ui.reply = null;
      ui.messages.textContent = '';
      ui.compose.textContent = '';
      signature = '';
      view.open = true;
      ui.panel.classList.add('open');
      render();
      updateButton();
      if (focusTitle) ui.title.focus({ preventScroll: true });
    };

    const showThread = (id, focus, forced, scroll) => {
      const thread = Model.threadById(id);
      if (!thread) return;
      if (editing && view.name === 'thread' && view.threadId === id) { editing.area.focus(); return; }
      if (!forced && dirtyEdit()) { askDiscard(() => showThread(id, focus, true, scroll)); return; }
      ViewsUi.reveal(thread, !!scroll);   // widok ekranu zmienia się dopiero po decyzji o szkicu
      editing = null;
      Bus.emit('panel', true);
      view.name = 'thread';
      view.threadId = id;
      view.open = true;
      ui.thread = null;
      ui.reply = null;
      ui.panel.classList.add('open');
      render();
      updateButton();
      if (focus) ui.title.focus({ preventScroll: true });
    };

    const open = () => {
      if (view.open) return;
      Bus.emit('panel', true);
      view.open = true;
      ui.panel.classList.add('open');
      render();
      updateButton();
    };

    const hide = (restoreFocus) => {
      view.open = false;
      ui.panel.classList.remove('open');
      updateButton();
      if (restoreFocus) ui.button.focus({ preventScroll: true });
    };

    // Pytanie o szkic przed opuszczeniem widoku; `proceed` wykonuje akcję, która wywołała pytanie.
    const askDiscard = (proceed) => {
      const field = editing && dirtyEdit() ? editing.area : (view.name === 'thread' ? replyField() : null);
      DialogsUi.open({
        title: 'Porzucić szkic?', returnFocus: () => field || ui.button,
        nodes: [Dom.make('p', { text: 'Masz niezapisany szkic odpowiedzi lub edycji. Zamknięcie go usunie.' })],
        actions: [
          { label: 'Anuluj', run: (api) => { api.close(); if (field && field.isConnected) field.focus(); } },
          { label: 'Porzuć szkic', primary: true, run: (api) => {
            api.close(false);
            Object.keys(drafts).forEach((id) => { delete drafts[id]; });
            editing = null;
            ui.thread = null;
            ui.reply = null;
            proceed();
          } }
        ]
      });
    };

    const closePanel = (confirm) => {
      if (confirm && anyDraft()) { askDiscard(() => hide(true)); return; }
      hide(true);
    };

    const leaveThread = (confirm) => {
      if (confirm && anyDraft()) { askDiscard(() => showList(true)); return; }
      showList(true);
    };

    const toggle = () => (view.open ? closePanel(true) : open());

    // Esc w panelu: wynik Acceptance.panelEscape (blur / lista / zamknięcie, z pytaniem o szkic).
    const escape = () => {
      const field = replyField();
      const active = document.activeElement;
      const decision = Acceptance.panelEscape({
        inField: !!field && active === field, fieldDraft: !!field && field.value.trim() !== '', threadView: view.name === 'thread',
        anyDraft: anyDraft()
      });
      if (decision.action === 'blur') { field.blur(); ui.title.focus({ preventScroll: true }); }
      else if (decision.action === 'list') leaveThread(decision.confirm);
      else closePanel(decision.confirm);
    };

    const step = (direction) => {
      const ids = orderedIds();
      if (!ids.length) return;
      const at = ids.indexOf(view.threadId);
      let target = null;
      if (view.name !== 'thread' || at < 0) target = direction > 0 ? ids[0] : ids[ids.length - 1];
      else target = ids[at + direction] || null;
      if (target) showThread(target, true);
    };

    const build = () => {
      ui.title = Dom.make('h2', { class: 'anno-panel-title', tabindex: '-1', text: 'Wątki' });
      ui.back = Dom.make('button', { type: 'button', class: 'btn-ghost', text: '← Wszystkie wątki', hidden: true });
      ui.close = Dom.make('button', { type: 'button', class: 'btn-ghost', 'aria-label': 'Zamknij panel', title: 'Zamknij panel', text: '×' });
      const head = Dom.make('div', { class: 'anno-panel-head' }, [ui.title, Dom.make('div', { class: 'row' }, [ui.back, ui.close])]);
      ui.hint = Dom.make('p', { class: 'muted', hidden: true });
      ui.search = Dom.make('input', { type: 'search', class: 'input', 'aria-label': 'Szukaj w wątkach', placeholder: 'Szukaj w wątkach' });
      ui.filters = Dom.make('div', { class: 'anno-filters', role: 'group', 'aria-label': 'Filtry wątków' });
      ui.views = Dom.make('div', { class: 'anno-view-filter', role: 'group', 'aria-label': 'Widok wątków' });
      ui.cards = Dom.make('div', { class: 'anno-cards' });
      ui.list = Dom.make('div', { class: 'anno-list' }, [ui.hint, Dom.make('div', { class: 'anno-search' }, [ui.search]), ui.views, ui.filters, ui.cards]);
      ui.messages = Dom.make('div', { class: 'anno-thread-messages' });
      ui.compose = Dom.make('div', { class: 'anno-compose' });
      ui.threadBox = Dom.make('div', { class: 'anno-thread-view', hidden: true }, [ui.messages, ui.compose]);
      ui.panel = Dom.make('aside', { class: 'anno-panel', role: 'complementary', 'aria-label': 'Wątki' }, [head, ui.list, ui.threadBox]);
      document.body.appendChild(ui.panel);
      ui.button = ToolbarUi.refs.threads;
      ui.button.addEventListener('click', toggle);
      ui.back.addEventListener('click', () => showList(true));
      ui.close.addEventListener('click', () => closePanel(true));
      ui.search.addEventListener('input', () => { view.query = ui.search.value; signature = ''; render(); });
    };

    const start = () => {
      build();
      Bus.on('model', () => { updateButton(); render(); });
      Bus.on('remote', () => { if (view.open) render(); });
      Bus.on('iter', () => { if (view.open) render(); });
      Keyboard.register((event, facts) => {
        if (facts.dialog) return false;
        if ((event.key === '[' || event.key === ']') && view.open && !facts.typing && !event.ctrlKey && !event.metaKey && !event.altKey) {
          step(event.key === ']' ? 1 : -1);
          return true;
        }
        if (event.key !== 'Escape') return false;
        if (ComposerUi.isOpen()) { ComposerUi.close(true); return true; }
        const inPanel = ui.panel.contains(document.activeElement);
        if (view.open && (inPanel || !PageState.flags.commentMode)) { escape(); return true; }
        if (PageState.flags.commentMode) { PageState.setCommentMode(false); return true; }
        return false;
      });
      updateButton();
    };

    return Object.freeze({ start: start, showThread: showThread, open: open, toggle: toggle, state: view });
  })();

  // ── 13l. UI: zwężenie i skalowanie makiet przy otwartym panelu ───────────────────────────────────────────────
  // Okno >= 1024 px: panel zabiera szerokość, a treść (`main.doc`) kurczy się proporcjonalnie (transform od lewego górnego rogu,
  // układ wewnątrz bez zmian). Przewinięcie jest korygowane w każdej klatce, więc oglądany ekran nie skacze. Poniżej 1024 px panel
  // jest nakładką bez przeliczania.

  const ScaleUi = (() => {
    const MIN_WIDTH = 1024;
    const DURATION = 180;
    let scale = 1;
    let shiftNow = 0;
    let frame = null;
    let pinned = null;   // ekran, który był bieżący w chwili otwarcia panelu: to jego pozycja ma wrócić po zamknięciu

    const target = () => document.querySelector('main.doc') || document.querySelector('main');
    const wide = () => {
      try { return window.matchMedia('(min-width: ' + MIN_WIDTH + 'px)').matches; } catch (error) { return window.innerWidth >= MIN_WIDTH; }
    };
    const wanted = () => {
      const panel = document.querySelector('.anno-panel.open');
      if (!panel || !wide()) return 1;
      const total = document.documentElement.clientWidth;
      return Math.min(1, Math.max(0.4, (total - panel.getBoundingClientRect().width) / total));
    };

    // Skala + korekta przewinięcia. Gdy ekran wymagałby przewinięcia powyżej początku strony, resztę przejmuje przesunięcie treści w dół.
    const paint = (el, value, anchor) => {
      scale = value;
      const need = anchor ? anchor.docTop + anchor.rel * value - anchor.viewTop : 0;
      const shift = need < 0 ? -need : 0;
      shiftNow = shift;
      el.style.transformOrigin = value === 1 && shift === 0 ? '' : '0 0';
      el.style.transform = value === 1 && shift === 0 ? '' : (shift ? 'translateY(' + shift + 'px) ' : '') + 'scale(' + value + ')';
      if (anchor) window.scrollTo({ top: Math.max(0, need), behavior: 'instant' });
    };

    // Punkt odniesienia: bieżący ekran (jego górna krawędź w oknie ma się nie zmieniać).
    const anchorFor = (el) => {
      // Podczas pomniejszenia przewijanie przesuwa „bieżący” ekran (scroll-spy), więc punktem odniesienia jest ekran z chwili otwarcia.
      const id = pinned || ScreenNav.state.current;
      const section = id ? document.getElementById(id) : null;
      if (!section) return null;
      const box = el.getBoundingClientRect();
      const top = section.getBoundingClientRect().top;
      return { viewTop: top, rel: (top - box.top) / scale, docTop: box.top - shiftNow + window.scrollY };
    };

    const sync = (immediate) => {
      const el = target();
      if (!el) return;
      const next = wanted();
      if (frame) { cancelAnimationFrame(frame); frame = null; }
      if (Math.abs(next - scale) < 0.0005) return;
      if (scale >= 0.9995 && next < 1) pinned = ScreenNav.state.current;
      const anchor = anchorFor(el);
      if (next >= 1) pinned = null;
      const from = scale;
      if (immediate || document.hidden || Dom.reducedMotion() || typeof requestAnimationFrame !== 'function') { paint(el, next, anchor); return; }
      const started = performance.now();
      const tick = (now) => {
        const t = Math.max(0, Math.min(1, (now - started) / DURATION));   // znacznik klatki bywa sprzed `started`
        const eased = 1 - Math.pow(1 - t, 3);
        paint(el, from + (next - from) * eased, anchor);
        frame = t < 1 ? requestAnimationFrame(tick) : null;
      };
      frame = requestAnimationFrame(tick);
      // Karta w tle nie dostaje klatek: dociągnięcie stanu końcowego.
      setTimeout(() => { if (frame && Math.abs(scale - next) > 0.0005) { cancelAnimationFrame(frame); frame = null; paint(el, next, anchor); } }, DURATION + 120);
    };

    const start = () => {
      window.addEventListener('resize', () => sync(true));
    };
    return Object.freeze({ start: start, sync: sync });
  })();

  // ── 13n. UI: wspólne narzędzia ekranu (stan sieci, przyciski w nagłówku ekranu) ────────────────────────────────

  const NetState = Object.freeze({
    // Konto zalogowane (także z wymuszoną zmianą hasła): akcje według roli dostępne przez API.
    apiUser: () => oneOf(['full', 'password'], Remote.state.mode),
    // Tryby z danymi z serwera (odczyt publiczny albo pełny).
    dataMode: () => oneOf(['anon', 'full', 'password'], Remote.state.mode),
    offline: () => {
      const s = Remote.state;
      const serverOk = s.loaded && s.errors === 0 && s.conn === 'ok';
      return Acceptance.offline(s.conn === 'offline' || s.mode === 'offline', typeof navigator !== 'undefined' ? navigator.onLine : true, serverOk);
    }
  });

  const screenTitle = (id) => {
    const entry = ScreenNav.state.entries.find((item) => item.id === id);
    return entry ? entry.name : id;
  };

  // Przyciski w `.anno-screen-tools` nagłówka ekranu: tworzone i usuwane według dostępności, aktualizowane w miejscu (fokus się nie gubi).
  const ScreenTools = (() => {
    const of = (section) => {
      let tools = section.querySelector('.anno-screen-tools');
      if (tools) return tools;
      const meta = section.querySelector('.screen-meta');
      if (!meta) return null;
      tools = Dom.make('div', { class: 'anno-screen-tools' });
      meta.appendChild(tools);
      return tools;
    };

    // Powód blokady: ukryty element opisujący przycisk (`aria-describedby`) i `title`.
    const reasonOf = (tools, button, text) => {
      const id = button.id + '-why';
      let note = tools.querySelector('#' + id);
      if (!text) {
        button.removeAttribute('aria-describedby');
        button.removeAttribute('title');
        if (note) note.remove();
        return;
      }
      if (!note) {
        note = Dom.make('span', { id: id, hidden: true });
        tools.appendChild(note);
      }
      if (note.textContent !== text) note.textContent = text;
      button.setAttribute('aria-describedby', id);
      button.title = text;
    };

    // spec: { cls, id, label, availability: {visible, enabled, reason}, run(button) }
    const sync = (tools, spec) => {
      let button = tools.querySelector('.' + spec.cls);
      if (!spec.availability.visible) {
        if (button) button.remove();
        const note = tools.querySelector('#' + spec.id + '-why');
        if (note) note.remove();
        return null;
      }
      if (!button) {
        button = Dom.make('button', { type: 'button', class: spec.cls, id: spec.id });
        button.addEventListener('click', () => {
          if (button.getAttribute('aria-disabled') === 'true') return;
          spec.run(button);
        });
        tools.appendChild(button);
      }
      if (button.textContent !== spec.label) button.textContent = spec.label;
      const blocked = !spec.availability.enabled;
      button.setAttribute('aria-disabled', String(blocked));
      reasonOf(tools, button, blocked ? spec.availability.reason : '');
      return button;
    };

    return Object.freeze({ of: of, sync: sync, reasonOf: reasonOf });
  })();

  // ── 13o. UI: iteracje (zamrożona strona, porównanie, przywracanie, „Pokaż w iteracji N”, tryb osadzony) ─────

  const IterUi = (() => {
    const probes = new Map();       // numer iteracji -> true | false | 'running' (sprawdzenie katalogu, gdy serwer nie podał listy)
    const handles = emptyMap();     // numer iteracji -> { ekran: { protoId: true } }
    const loading = new Set();
    let version = 0;                // rośnie po każdym nowym wyniku: podpis widoków
    let targetDone = false;
    let compareView = null;         // otwarte okno porównania: { refresh }
    let restoreView = null;         // otwarte okno przywracania: { refresh }
    let queued = false;

    const frozenNumber = () => {
      const number = Number(document.documentElement.getAttribute('data-prototype-iteration'));
      return isInt(number) ? number : null;
    };
    const rootPrefix = () => (PageState.frozen() ? '../../' : '');
    const indexOf = (number) => rootPrefix() + Iterations.pathOf(number) + 'index.html';

    const frozenInfo = () => {
      if (!PageState.frozen()) return null;
      const number = frozenNumber();
      const hash = String(location.hash || '');
      return { number: number === null ? '' : number, currentHref: '../../index.html' + (/^#s\d+$/.test(hash) ? hash : '') };
    };

    // ----- lista iteracji (serwer albo dziennik; brak informacji o katalogu -> sprawdzenie GET) -----

    const probe = (number) => {
      probes.set(number, 'running');
      fetch(indexOf(number), { cache: 'no-store' })
        .then((response) => { probes.set(number, response.status === 200); version += 1; Bus.emit('iter'); })
        .catch(() => { probes.delete(number); });
    };

    const rowsNow = () => Iterations.list(Remote.state.iterations, Remote.state.ops).map((row) => {
      if (typeof row.exists === 'boolean') return row;
      const result = probes.get(row.number);
      if (typeof result === 'boolean') return Object.assign({}, row, { exists: result });
      if (Iterations.shouldProbe(row, undefined, result === 'running')) probe(row.number);
      return row;
    });
    const existing = () => rowsNow().filter((row) => row.exists === true);

    // ----- „Pokaż w iteracji N” -----

    const loadHandles = (number) => {
      if (has(handles, String(number)) || loading.has(number)) return;
      loading.add(number);
      fetch(indexOf(number), { cache: 'no-store' })
        .then((response) => (response.status === 200 ? response.text() : null))
        .then((text) => {
          const screens = emptyMap();
          if (text !== null) {
            const doc = new DOMParser().parseFromString(text, 'text/html');
            Array.prototype.forEach.call(doc.querySelectorAll('section.screen[id]'), (section) => {
              const ids = emptyMap();
              Array.prototype.forEach.call(section.querySelectorAll('[data-proto-id]'), (node) => { ids[node.getAttribute('data-proto-id')] = true; });
              screens[section.getAttribute('id')] = ids;
            });
          }
          handles[String(number)] = screens;
        })
        .catch(() => { handles[String(number)] = emptyMap(); })
        .then(() => { loading.delete(number); version += 1; Bus.emit('iter'); });
    };

    const linkFor = (thread) => {
      const found = Iterations.forThread(rowsNow(), handles, thread, frozenNumber());
      if (found.number !== null) return found.number;
      if (found.need !== null) loadHandles(found.need);
      return null;
    };
    const linkKey = (thread) => String(linkFor(thread)) + ':' + version;
    const linkNode = (thread) => {
      const number = linkFor(thread);
      if (number === null) return null;
      return Dom.make('p', {}, [Dom.make('a', { class: 'anno-iter-link', href: indexOf(number) + '#thread=' + thread.id,
        text: 'Pokaż w iteracji ' + number })]);
    };

    // Zamrożona strona otwarta z `#thread=<id>`: pinezka wątku jest podświetlona i widoczna.
    const highlightTarget = () => {
      if (targetDone) return;
      const id = Iterations.threadIdFromHash(location.hash);
      if (!id) { targetDone = true; return; }
      const pin = PinsUi.pinFor(id);
      if (!pin || pin.hidden) return;
      targetDone = true;
      pin.setAttribute('data-anno-target', '1');
      pin.scrollIntoView({ block: 'center' });
    };

    // ----- stany dostępności -----

    const restoreContext = (extra) => Object.assign({
      mode: NetState.apiUser() ? 'api' : Remote.state.mode, frozen: PageState.frozen(), embed: PageState.embedded, user: Remote.state.user,
      sourceWrites: !!(isObject(Remote.state.server) && Remote.state.server.sourceWrites), iterations: existing().length,
      stale: Remote.state.stale, version: Remote.state.baseVersion, offline: NetState.offline(), expired: Remote.state.conn === 'expired'
    }, extra);
    const restoreGate = () => Iterations.restoreAvailability(restoreContext({}));

    // ----- okno porównania -----

    const openCompare = (sid, origin) => {
      const rows = rowsNow();
      const usable = rows.filter((row) => row.exists === true);
      if (!usable.length) return;
      let selected = usable[0].number;
      const label = Dom.make('label', { for: 'anno-compare-from', text: 'Porównaj z iteracją' });
      const select = Dom.make('select', { id: 'anno-compare-from', 'aria-label': 'Iteracja do porównania' });
      rows.forEach((row) => {
        const option = Dom.make('option', { value: String(row.number), text: 'Iteracja ' + row.number + (row.exists === false ? ' (brak katalogu)' : '') });
        if (row.exists !== true) option.disabled = true;
        select.appendChild(option);
      });
      select.value = String(selected);
      const pick = Dom.make('div', { class: 'anno-compare-pick' }, [label, select]);
      const restore = Dom.make('button', { type: 'button', class: 'btn btn-outline btn-sm', id: 'anno-compare-restore', text: 'Przywróć…' });
      const restoreWhy = Dom.make('span', { id: 'anno-compare-restore-why', hidden: true });
      const compareBox = Dom.make('div', { class: 'anno-compare' });
      const views = ViewControls.make({ 'aria-label': 'Widok' }, (view) => {
        views.paint(view);
        compareBox.classList.toggle('anno-view-mobile', view === 'mobile');   // tylko szerokość podglądu: min(375, dostępna), dla każdej iteracji
      });
      views.paint('desktop');
      const head = Dom.make('div', { class: 'row anno-compare-head' }, [pick, views.node, restore, restoreWhy]);
      const frameUrl = (number) => (number === null ? 'index.html' : Iterations.pathOf(number) + 'index.html') + '?embed=compare#' + sid;
      const left = Dom.make('iframe', { src: frameUrl(selected), title: 'Iteracja ' + selected });
      const right = Dom.make('iframe', { src: frameUrl(null), title: 'Bieżąca wersja' });
      compareBox.appendChild(left);
      compareBox.appendChild(right);
      const caption = Dom.make('p', { class: 't-hint anno-compare-caption' });
      const paintCaption = () => {
        caption.textContent = 'Po lewej: iteracja ' + selected + ' · po prawej: bieżąca wersja (iteracja ' + Remote.state.iteration + ').';
      };
      const refresh = () => {
        const gate = restoreGate();
        restore.hidden = !gate.visible;
        restore.setAttribute('aria-disabled', String(gate.visible && !gate.enabled));
        const reason = gate.visible && !gate.enabled ? gate.reason : '';
        restoreWhy.textContent = reason;
        if (reason) restore.setAttribute('aria-describedby', restoreWhy.id);
        else restore.removeAttribute('aria-describedby');
      };
      paintCaption();
      refresh();
      select.addEventListener('change', () => {
        selected = Number(select.value);
        left.setAttribute('src', frameUrl(selected));
        left.setAttribute('title', 'Iteracja ' + selected);
        paintCaption();
      });
      const api = DialogsUi.open({
        title: 'Porównanie ekranu ' + sid + ' · ' + screenTitle(sid), returnFocus: origin,
        nodes: [head, caption, compareBox],
        actions: [{ key: 'close', label: 'Zamknij', primary: true }],
        initialFocus: () => select
      });
      restore.addEventListener('click', () => {
        if (restore.getAttribute('aria-disabled') === 'true') return;
        api.close(false);
        openRestore(sid, origin, selected);
      });
      compareView = { refresh: () => { if (!api.isOpen()) { compareView = null; return; } refresh(); } };
    };

    // ----- okno przywracania -----

    const REFUSALS = {
      source_writes_disabled: 'Serwer ma wyłączony zapis plików prototypu (--no-source-writes): przywracanie nie jest dostępne.',
      forbidden: 'Serwer odmówił przywrócenia: brak uprawnień.',
      iteration_not_found: 'Serwer odmówił przywrócenia: iteracji nie ma na dysku.',
      screen_missing: 'Serwer odmówił przywrócenia: ekranu nie ma w bieżącej wersji albo w wybranej iteracji.',
      ambiguous_section: 'Serwer odmówił przywrócenia: sekcja ekranu jest niejednoznaczna (zduplikowane id albo niezamknięta).',
      ids_check_failed: 'Serwer odmówił przywrócenia: wynik nie przeszedłby kontroli uchwytów (data-proto-id).',
      incomplete_restore: 'Serwer odmówił przywrócenia: poprzednie przywracanie jest niedokończone (uruchom „skillsync prototype restore --resume”).'
    };

    const openRestore = (sid, origin, preferred) => {
      const rows = rowsNow();
      const usable = rows.filter((row) => row.exists === true);
      if (!usable.length) return;
      const slug = Remote.slug();
      const ask = {
        from: isInt(preferred) ? preferred : usable[0].number, previewState: 'idle', preview: null, token: 0, busy: false, message: '',
        cid: Outbox.newClientId('c')
      };
      let api = null;

      const fromLabel = Dom.make('label', { for: 'anno-restore-from', text: 'Przywróć z iteracji' });
      const select = Dom.make('select', { id: 'anno-restore-from', 'aria-label': 'Przywróć z iteracji' });
      rows.forEach((row) => {
        const option = Dom.make('option', { value: String(row.number), text: 'Iteracja ' + row.number + (row.exists === false ? ' (brak katalogu)' : '') });
        if (row.exists !== true) option.disabled = true;
        select.appendChild(option);
      });
      select.value = String(ask.from);
      const whyLabel = Dom.make('label', { for: 'anno-restore-why', text: 'Powód przywrócenia (wymagany)' });
      const why = Dom.make('textarea', { id: 'anno-restore-why', rows: 3, maxlength: MAX_RESTORE_REASON, 'aria-describedby': 'anno-restore-hint' });
      const hint = Dom.make('p', { id: 'anno-restore-hint' });
      const effects = Dom.make('ul', { class: 'anno-effects', hidden: true });
      const retry = Dom.make('button', { type: 'button', class: 'btn btn-outline btn-sm', text: 'Ponów podgląd', hidden: true });
      const alertBox = Dom.make('div', { role: 'alert', hidden: true });
      const lead = Dom.make('p', { text: 'Ekran ' + sid + ' (' + screenTitle(sid) + ') wróci do wyglądu z wybranej iteracji. CSS i <head> zostają bieżące.' });
      // Podgląd ekranu z wybranej iteracji; przełącznik Desktop / Mobile zmienia tylko szerokość podglądu.
      const previewUrl = () => Iterations.pathOf(ask.from) + 'index.html?embed=compare#' + sid;
      const preview = Dom.make('iframe', { src: previewUrl(), title: 'Podgląd iteracji ' + ask.from });
      const previewBox = Dom.make('div', { class: 'anno-restore-preview' }, [preview]);
      const views = ViewControls.make({ 'aria-label': 'Widok' }, (view) => {
        views.paint(view);
        previewBox.classList.toggle('anno-view-mobile', view === 'mobile');
      });
      views.paint('desktop');

      const context = () => ({
        busy: ask.busy, stale: Remote.state.stale, unchanged: ask.previewState === 'ok' && isObject(ask.preview) && ask.preview.unchanged === true,
        from: ask.from, previewFailed: ask.previewState === 'failed', previewOk: ask.previewState === 'ok', reason: why.value
      });
      const gateState = () => {
        const state = Iterations.restoreSubmitState(context());
        if (state.enabled && (NetState.offline() || Remote.state.conn === 'expired')) return Shapes.submit(false, TEXTS.noQueueRestore);
        return state;
      };

      let submitButton = null;
      const refresh = () => {
        if (!api || !api.isOpen()) { restoreView = null; return; }
        const ctx = context();
        const state = gateState();
        hint.textContent = state.enabled ? '' : state.reason;
        submitButton.setAttribute('aria-disabled', String(!state.enabled));
        submitButton.disabled = ctx.unchanged;
        retry.hidden = !ctx.previewFailed;
        effects.textContent = '';
        effects.hidden = !ctx.previewOk;
        if (ctx.previewOk) {
          const e = Iterations.effects(ask.preview, Remote.state.iteration);
          const lines = [];
          if (e.frozen !== null) lines.push('Bieżąca wersja (iteracja ' + e.frozen + ') zostanie zamrożona; nowa iteracja: ' + e.created + '.');
          if (e.back !== null) lines.push('Wątki, które wrócą na miejsce (pinezki): ' + e.back + '.');
          if (e.lost !== null) lines.push('Wątki, które trafią do „Bez uchwytu”: ' + e.lost + '.');
          if (e.acceptanceLost !== null) lines.push(e.acceptanceLost ? 'Akceptacja tego ekranu straci aktualność.' : 'Akceptacja tego ekranu nie zmieni się.');
          lines.push('CSS i <head> zostają bieżące.');
          lines.forEach((line) => effects.appendChild(Dom.make('li', { text: line })));
        }
        alertBox.hidden = ask.message === '';
        if (alertBox.textContent !== ask.message) alertBox.textContent = ask.message;
      };

      const loadPreview = async () => {
        ask.token += 1;
        const token = ask.token;
        ask.previewState = 'loading';
        ask.preview = null;
        ask.message = '';
        refresh();
        let result = null;
        try {
          result = await Remote.call('/prototypes/' + encodeURIComponent(slug) + '/restore-preview?screen=' + encodeURIComponent(sid) + '&from=' + ask.from);
        } catch (error) { result = { status: 0, json: null }; }
        if (token !== ask.token || !api.isOpen()) return;
        if (result.status === 200 && isObject(result.json)) {
          ask.previewState = 'ok';
          ask.preview = result.json;
        } else {
          ask.previewState = 'failed';
          if (result.status === 401) Remote.expire();
          const code = isObject(result.json) ? result.json.error : null;
          if (result.status === 403 && code === 'source_writes_disabled') ask.message = REFUSALS.source_writes_disabled;
        }
        refresh();
      };

      const finish = async (result) => {
        const code = isObject(result.json) ? result.json.error : null;
        const verdict = Iterations.classifyResult(result.status, code, result.json);
        if (verdict === 'done') {
          api.close(false);
          Toast.show('Przywrócono ekran ' + sid + ' z iteracji ' + ask.from + '. Przeładowuję stronę…');
          setTimeout(() => location.reload(), 500);
          return;
        }
        if (verdict === 'auth') { api.close(false); Remote.expire(); return; }
        if (verdict === 'password') { api.close(false); Remote.requirePassword(); return; }
        if (verdict === 'unchanged') {
          ask.previewState = 'ok';
          ask.preview = Object.assign({}, isObject(ask.preview) ? ask.preview : {}, { unchanged: true });
          ask.message = Iterations.unchangedText(ask.from);
        } else if (verdict === 'stale') {
          Remote.markStale();
          ask.message = 'Prototyp zmieniono od wczytania strony: przeładuj stronę i spróbuj ponownie. Nic nie zapisano.';
        } else if (verdict === 'csrf') {
          await Remote.refreshSession();
          ask.message = 'Nie udało się potwierdzić sesji: odśwież połączenie i ponów akcję.';
        } else if (verdict === 'busy') {
          ask.message = 'Serwer jest zajęty: poczekaj chwilę i ponów akcję.';
        } else if (verdict === 'offline') {
          ask.message = TEXTS.noQueueRestore;
        } else {
          if (code === 'source_writes_disabled' && isObject(Remote.state.server)) {
            Remote.state.server.sourceWrites = false;
            Remote.publish();
          }
          ask.message = has(REFUSALS, code) ? REFUSALS[code] : 'Nie udało się przywrócić ekranu (' + (code || result.status) + '). Nic nie zapisano.';
        }
        if (api.isOpen()) refresh();
        else Toast.show(ask.message);
      };

      const submit = async () => {
        if (ask.busy || !gateState().enabled) return;
        ask.busy = true;
        ask.message = '';
        refresh();
        let result = null;
        try {
          result = await Remote.postJson('/prototypes/' + encodeURIComponent(slug) + '/restore', {
            cid: ask.cid, screen: sid, from: ask.from, reason: why.value.trim(), version: Remote.state.baseVersion
          });
        } catch (error) { result = { status: 0, json: null }; }
        ask.busy = false;
        await finish(result);
      };

      select.addEventListener('change', () => {
        const number = Number(select.value);
        if (!isInt(number)) return;
        ask.from = number;
        // `location.replace`: zmiana podglądu nie dokłada wpisów do historii przeglądarki.
        try { preview.contentWindow.location.replace(previewUrl()); } catch (error) { preview.setAttribute('src', previewUrl()); }
        preview.setAttribute('title', 'Podgląd iteracji ' + number);
        loadPreview();
      });
      why.addEventListener('input', refresh);
      why.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); submit(); }
      });
      retry.addEventListener('click', loadPreview);

      api = DialogsUi.open({
        title: 'Przywróć ekran ' + sid, returnFocus: origin,
        nodes: [lead, fromLabel, select, views.node, previewBox, whyLabel, why, hint, effects, retry, alertBox],
        actions: [{ key: 'cancel', label: 'Anuluj' }, { key: 'submit', label: 'Przywróć ekran', primary: true, run: submit }],
        initialFocus: () => select
      });
      submitButton = api.buttons.submit;
      submitButton.setAttribute('aria-describedby', 'anno-restore-hint');
      restoreView = { refresh: refresh };
      refresh();
      loadPreview();
    };

    // ----- przyciski w nagłówkach ekranów -----

    const sync = () => {
      queued = false;
      const s = Remote.state;
      const list = existing();
      const canCompare = !PageState.frozen() && list.length > 0 && s.loaded && oneOf(['anon', 'full', 'password', 'readonly'], s.mode);
      const gate = restoreGate();
      Array.prototype.forEach.call(document.querySelectorAll('section.screen[id]'), (section) => {
        const tools = ScreenTools.of(section);
        if (!tools) return;
        const sid = section.id;
        ScreenTools.sync(tools, {
          cls: 'anno-compare-open', id: 'anno-compare-open-' + sid, label: 'Porównaj z iteracją ' + (list[0] ? list[0].number : ''),
          availability: Shapes.availability(canCompare, true, ''), run: (button) => openCompare(sid, button)
        });
        ScreenTools.sync(tools, {
          cls: 'anno-restore-open', id: 'anno-restore-open-' + sid, label: 'Przywróć…', availability: gate,
          run: (button) => openRestore(sid, button)
        });
      });
      if (compareView) compareView.refresh();
      if (restoreView) restoreView.refresh();
      highlightTarget();
    };
    const schedule = () => {
      if (queued) return;
      queued = true;
      setTimeout(sync, 0);
    };

    // Zamrożona strona: znacznik iteracji na pasku (baner dokłada BannersUi).
    const paintFrozen = () => {
      const info = frozenInfo();
      if (!info) return;
      const slot = ToolbarUi.refs.iterSlot;
      slot.textContent = '';
      slot.appendChild(Dom.make('span', { class: 'anno-iteration', text: 'Iteracja ' + info.number + ' · zamrożona' }));
      slot.hidden = false;
    };

    const start = () => {
      paintFrozen();
      ['remote', 'model', 'data', 'iter'].forEach((name) => Bus.on(name, schedule));
      window.addEventListener('hashchange', () => { targetDone = false; schedule(); });
      schedule();
    };

    // Strona osadzona w porównaniu (`?embed=compare`): bez paska, panelu i odpytywania; widoczny tylko wskazany ekran.
    const startEmbedded = () => {
      const apply = () => {
        const screens = Array.prototype.slice.call(document.querySelectorAll('section.screen[id]'));
        const wanted = String(location.hash || '').slice(1);
        const target = screens.find((section) => section.id === wanted) || screens[0];
        screens.forEach((section) => section.classList.toggle('is-embed-target', section === target));
      };
      apply();
      window.addEventListener('hashchange', apply);
    };

    return Object.freeze({
      start: start, startEmbedded: startEmbedded, frozenInfo: frozenInfo, linkKey: linkKey, linkNode: linkNode
    });
  })();

  // ── 13p. UI: akceptacja (chip w pasku, przycisk „Akceptuj…” przy ekranie, okno akceptacji ekranu i projektu) ────

  const AcceptUi = (() => {
    const nodes = { chip: null, kind: '', count: null, label: null };
    let active = null;     // otwarte okno akceptacji: { refresh }
    let queued = false;

    const entryOf = (sid) => {
      const all = Remote.state.acceptance;
      return isObject(all) && has(all, sid) && isObject(all[sid]) ? all[sid] : null;
    };

    // Ekrany do akceptacji: z serwera (bez przykładowych i niejednoznacznych), a bez serwera z dokumentu.
    const idsNow = () => {
      if (NetState.dataMode() && Remote.state.loaded) return Object.keys(Remote.state.screens).sort(compareNatural);
      return Array.prototype.map.call(document.querySelectorAll('section.screen[id]:not([data-prototype-example])'), (el) => el.id);
    };

    const stateFor = (sid, extra) => {
      const s = Remote.state;
      const entry = entryOf(sid);
      return Object.assign({
        mode: NetState.apiUser() ? 'api' : s.mode, frozen: PageState.frozen(), embed: PageState.embedded, example: false, user: s.user,
        mustChange: !!(s.user && s.user.mustChange), expired: s.conn === 'expired', stale: s.stale, offline: NetState.offline(),
        loading: !s.loaded, problem: s.pollProblem, version: s.baseVersion, hash: has(s.baseScreens, sid) ? s.baseScreens[sid] : null,
        accepted: !!entry && entry.valid === true
      }, extra);
    };

    const two = (n) => (n < 10 ? '0' : '') + n;
    const formatTime = (at) => {
      const date = new Date(at);
      if (isNaN(date.getTime())) return String(at);
      return two(date.getDate()) + '.' + two(date.getMonth() + 1) + '.' + date.getFullYear() + ' ' + two(date.getHours()) + ':' + two(date.getMinutes());
    };

    // ----- chip w pasku -----

    const buildChip = (interactive) => {
      const label = Dom.make('span', { class: 'anno-accept-label' });
      const count = Dom.make('span', { class: 'anno-accept-count' });
      const chip = interactive ?
        Dom.make('button', { type: 'button', class: 'anno-accept', id: 'anno-accept-chip', 'aria-haspopup': 'dialog' }, [label, count]) :
        Dom.make('span', { class: 'anno-accept', role: 'note' }, [label, count]);
      if (interactive) {
        chip.addEventListener('click', () => {
          if (chip.getAttribute('aria-disabled') === 'true') return;
          open(Acceptance.targetScreen(ScreenNav.state.current, idsNow()), chip);
        });
      }
      nodes.chip = chip;
      nodes.kind = interactive ? 'button' : 'note';
      nodes.count = count;
      nodes.label = label;
      const slot = ToolbarUi.refs.acceptSlot;
      slot.textContent = '';
      slot.appendChild(chip);
    };

    const renderChip = () => {
      const slot = ToolbarUi.refs.acceptSlot;
      const s = Remote.state;
      const shown = oneOf(['anon', 'full', 'password', 'readonly'], s.mode) && s.loaded && !PageState.frozen();
      const ids = shown ? idsNow() : [];
      const summary = shown ? Acceptance.summary(ids, s.mode === 'readonly' ? null : s.acceptance, Model.data.folded.acceptance) : null;
      if (!summary) {
        slot.textContent = '';
        slot.hidden = true;
        nodes.chip = null;
        nodes.kind = '';
        return;
      }
      const target = Acceptance.targetScreen(ScreenNav.state.current, ids);
      const gate = Acceptance.availability(stateFor(target));
      const kind = gate.visible ? 'button' : 'note';
      if (!nodes.chip || nodes.kind !== kind || !slot.contains(nodes.chip)) buildChip(gate.visible);
      slot.hidden = false;
      const chip = nodes.chip;
      if (nodes.label.textContent !== 'Akceptacja') nodes.label.textContent = 'Akceptacja';
      const count = summary.accepted + '/' + ids.length;
      if (nodes.count.textContent !== count) nodes.count.textContent = count;
      chip.setAttribute('aria-label', summary.title);
      chip.setAttribute('data-state', summary.state);
      if (gate.visible) {
        const blocked = !gate.enabled;
        chip.setAttribute('aria-disabled', String(blocked));
        ScreenTools.reasonOf(slot, chip, blocked ? gate.reason : '');
      } else {
        chip.title = summary.title;
      }
    };

    // ----- okno akceptacji -----

    const open = (sid, origin) => {
      if (!sid) return;
      const ask = {
        scope: 'screen', busy: false, cid: Outbox.newClientId('c'), message: '', partial: null,
        project: { state: 'idle', data: null, summary: null }
      };
      let api = null;
      const screenName = screenTitle(sid);

      const info = Dom.make('p');
      const acceptedLine = Dom.make('p', { hidden: true });
      const screenRadio = Dom.make('input', { type: 'radio', name: 'anno-accept-scope', id: 'anno-accept-screen', value: 'screen', checked: true });
      const projectRadio = Dom.make('input', { type: 'radio', name: 'anno-accept-scope', id: 'anno-accept-project', value: 'project' });
      const projectText = Dom.make('span');
      const group = Dom.make('div', { role: 'radiogroup', 'aria-label': 'Zakres akceptacji' }, [
        Dom.make('label', {}, [screenRadio, ' Ten ekran (' + sid + ' ' + screenName + ')']),
        Dom.make('label', {}, [projectRadio, ' ', projectText])
      ]);
      const projectBox = Dom.make('div', { hidden: true });
      const reasonNote = Dom.make('p', { id: 'anno-accept-reason' });
      const alertBox = Dom.make('div', { role: 'alert', hidden: true });

      const context = () => stateFor(sid, {
        scope: ask.scope, busy: ask.busy, projectState: ask.project.state, targets: ask.project.summary ? ask.project.summary.withScreens : 0
      });

      let submitButton = null;
      let undoButton = null;

      const paintProject = () => {
        projectBox.textContent = '';
        const project = ask.project;
        projectBox.hidden = ask.scope !== 'project';
        projectText.textContent = project.summary ? project.summary.optionText : 'Wszystkie prototypy w obecnej wersji';
        if (ask.scope !== 'project') return;
        if (project.state === 'loading' || project.state === 'idle') {
          projectBox.appendChild(Dom.make('p', { text: 'Pobieram listę prototypów…' }));
        } else if (project.state === 'failed') {
          const again = Dom.make('button', { type: 'button', class: 'btn btn-outline btn-sm', text: 'Ponów pobranie listy' });
          again.addEventListener('click', loadProject);
          projectBox.appendChild(again);
        } else {
          const summary = project.summary;
          const list = Dom.make('ul', { class: 'stack-2' });
          summary.items.forEach((item) => {
            let line = '';
            if (item.format !== 2) line = 'pominięty: wymaga migracji do formatu v2';
            else if (!item.target) line = 'pominięty: brak ekranów do akceptacji';
            else line = 'iteracja ' + (item.iteration === null ? '?' : item.iteration) + ' · ekranów: ' + item.screens +
              ' · otwarte wątki: ' + (item.open === null ? '?' : item.open);
            const name = item.title && item.title !== item.slug ? item.slug + ' (' + item.title + ')' : item.slug;
            list.appendChild(Dom.make('li', {}, [Dom.make('span', { class: 'strong', text: name }), ' · ' + line]));
          });
          projectBox.appendChild(list);
          projectBox.appendChild(Dom.make('p', { text: 'Otwarte wątki w projekcie: ' + summary.open + ' · czekają na decyzję: ' + summary.awaiting + '.' }));
        }
      };

      const refresh = () => {
        if (!api || !api.isOpen()) { active = null; return; }
        const entry = entryOf(sid);
        const threads = Model.data.folded.threads;
        const open = Model.openCount();
        const onScreen = Fold.openCountByScreen(Model.data.folded)[sid] || 0;
        const waiting = threads.filter((thread) => thread.status === 'awaiting').length;
        info.textContent = ask.scope === 'project' ?
          'Zatwierdzasz wszystkie ekrany wszystkich prototypów v2 w projekcie, w ich obecnej wersji.' :
          'Iteracja ' + Remote.state.iteration + ' · otwarte wątki: ' + open + ' (na tym ekranie: ' + onScreen + ') · czekają na decyzję: ' + waiting + '.';
        if (entry && ask.scope === 'screen') {
          const who = Model.fullNameOf(entry.who);
          acceptedLine.textContent = Acceptance.acceptedLine(entry, who, formatTime(entry.at)) +
            (entry.valid === true ? '' : ' · akceptacja nieaktualna: zmieniono pliki ekranu po akceptacji.');
          acceptedLine.hidden = false;
        } else {
          acceptedLine.hidden = true;
        }
        paintProject();
        const state = Acceptance.submitState(context());
        reasonNote.textContent = state.reason;
        reasonNote.hidden = state.reason === '';
        submitButton.setAttribute('aria-disabled', String(!state.enabled));
        if (state.reason) submitButton.setAttribute('aria-describedby', reasonNote.id);
        else submitButton.removeAttribute('aria-describedby');
        const canUndo = ask.scope === 'screen' && Acceptance.canUnaccept(Remote.state.user, entry);
        undoButton.hidden = !canUndo;
        undoButton.setAttribute('aria-disabled', String(ask.busy || NetState.offline() || Remote.state.conn === 'expired'));
        alertBox.textContent = '';
        alertBox.hidden = ask.message === '';
        if (ask.message !== '') {
          alertBox.appendChild(Dom.make('span', { text: ask.message }));
          if (ask.partial) {
            const again = Dom.make('button', { type: 'button', class: 'btn btn-outline btn-sm', text: 'Ponów' });
            again.addEventListener('click', submit);
            alertBox.appendChild(document.createTextNode(' '));
            alertBox.appendChild(again);
          }
        }
      };

      async function loadProject() {
        ask.project = { state: 'loading', data: null, summary: null };
        refresh();
        let result = null;
        try { result = await Remote.call('/project'); } catch (error) { result = { status: 0, json: null }; }
        if (!api.isOpen()) return;
        if (result.status === 200 && isObject(result.json)) {
          ask.project = { state: 'ready', data: result.json,
            summary: Acceptance.projectSummary(result.json, Remote.slug(), Remote.state.baseVersion) };
        } else {
          if (result.status === 401) Remote.expire();
          ask.project = { state: 'failed', data: null, summary: null };
        }
        refresh();
      }

      const refusal = (action, code) => {
        if (action === 'forbidden') return 'Serwer odmówił akceptacji: brak uprawnień.';
        if (action !== 'invalid') return null;
        if (code === 'unknown_screen') return 'Serwer odmówił akceptacji: ekranu nie ma w bieżącej wersji albo jest przykładowy.';
        return 'Serwer odmówił akceptacji: nieprawidłowe żądanie' + (code ? ' (' + code + ')' : '') + '.';
      };

      // Wynik zapisu: okno jeszcze otwarte (komunikat w oknie) albo już zamknięte Esc-em (komunikat w powiadomieniu).
      const settle = async (result, kind) => {
        const code = isObject(result.json) ? result.json.error : null;
        const data = isObject(result.json) ? result.json : {};
        const action = Acceptance.classifyResult(result.status, code, data);
        const out = Acceptance.closedOutcome(action, kind, data, screenName, Remote.slug(), code ? String(code) : 'HTTP ' + result.status);
        if (oneOf(['done', 'not_accepted', 'auth', 'password'], action) && api.isOpen()) api.close(action === 'done' || action === 'not_accepted');
        if (out.poll) await Remote.poll();
        if (out.markStale) Remote.markStale();
        if (out.auth) Remote.expire();
        if (out.password) Remote.requirePassword();
        if (out.renew) await Remote.refreshSession();
        let message = refusal(action, code) || out.message;
        ask.partial = null;
        if (action === 'partial') {
          const accepted = Acceptance.slugList(data.accepted);
          const pending = Acceptance.slugList(data.pending);
          ask.partial = { accepted: accepted, pending: pending };
          message = 'Zapisano część akceptacji.' + (accepted.length ? ' Zaakceptowano: ' + accepted.join(', ') + '.' : '') +
            (pending.length ? ' Czeka: ' + pending.join(', ') + '.' : '');
        }
        const inWindow = api.isOpen() && !oneOf(['done', 'not_accepted'], action);
        if (inWindow) {
          ask.message = message;
          refresh();
        } else {
          ask.partial = null;
          Toast.show(message);
        }
      };

      async function submit() {
        if (ask.busy) return;
        const state = Acceptance.submitState(context());
        if (!state.enabled) return;
        const project = ask.scope === 'project';
        ask.busy = true;
        ask.message = '';
        refresh();
        let result = null;
        try {
          result = project ? await Remote.postJson('/project/accept', { cid: ask.cid, versions: ask.project.summary.versions }) :
            await Remote.send(Acceptance.screenBody(ask.cid, sid, Remote.state.baseScreens[sid], Remote.state.baseVersion));
        } catch (error) { result = { status: 0, json: null }; }
        ask.busy = false;
        await settle(result, project ? 'project' : 'screen');
      }

      const undo = async () => {
        if (ask.busy) return;
        ask.busy = true;
        ask.message = '';
        refresh();
        let result = null;
        try { result = await Remote.send(Acceptance.unacceptBody(Outbox.newClientId('c'), sid)); } catch (error) { result = { status: 0, json: null }; }
        ask.busy = false;
        await settle(result, 'undo');
      };

      [screenRadio, projectRadio].forEach((radio) => radio.addEventListener('change', () => {
        if (!radio.checked) return;
        ask.scope = radio.value;
        ask.message = '';
        ask.partial = null;
        if (ask.scope === 'project' && ask.project.state === 'idle') loadProject();
        else refresh();
      }));

      api = DialogsUi.open({
        title: 'Akceptacja prototypu', returnFocus: origin,
        nodes: [info, acceptedLine, group, projectBox, reasonNote, alertBox],
        actions: [
          { key: 'cancel', label: 'Anuluj' },
          { key: 'undo', label: 'Cofnij akceptację', run: undo },
          { key: 'submit', label: 'Akceptuję', primary: true, run: submit }
        ]
      });
      submitButton = api.buttons.submit;
      undoButton = api.buttons.undo;
      active = { refresh: refresh };
      refresh();
    };

    // ----- przyciski „Akceptuj…” przy ekranach -----

    const sync = () => {
      queued = false;
      renderChip();
      const ids = idsNow();
      Array.prototype.forEach.call(document.querySelectorAll('section.screen[id]'), (section) => {
        const tools = ScreenTools.of(section);
        if (!tools) return;
        const sid = section.id;
        const gate = ids.indexOf(sid) >= 0 ? Acceptance.availability(stateFor(sid)) : Shapes.availability(false, false, '');
        ScreenTools.sync(tools, {
          cls: 'anno-accept-open', id: 'anno-accept-open-' + sid, label: 'Akceptuj…', availability: gate, run: (button) => open(sid, button)
        });
      });
      if (active) active.refresh();
    };
    const schedule = () => {
      if (queued) return;
      queued = true;
      setTimeout(sync, 0);
    };

    const start = () => {
      ['remote', 'model', 'data', 'screen', 'iter'].forEach((name) => Bus.on(name, schedule));
      schedule();
    };

    return Object.freeze({ start: start });
  })();

  function startUi(win, doc) {
    if (doc.documentElement) doc.documentElement.setAttribute('data-prototype-engine', String(ENGINE_VERSION));
    if (!doc.body || doc.documentElement.getAttribute('data-prototype-engine-ui') === 'on') return;
    doc.documentElement.setAttribute('data-prototype-engine-ui', 'on');
    if (PageState.embedded) {
      doc.documentElement.classList.add('proto-embed');
      IterUi.startEmbedded();
      return;
    }
    if (PageState.viewRole) {
      doc.documentElement.classList.add('proto-embed-view');
      EmbedUi.start();
      return;
    }
    Toast.init();
    ToolbarUi.build();
    ScreenNav.start();
    Navigation.start();
    AuthUi.start();
    BannersUi.start();
    V1Ui.start();
    ViewsUi.start();
    PinsUi.start();
    CommentingUi.start();
    PanelUi.start();
    ScaleUi.start();
    IterUi.start();
    AcceptUi.start();
    Keyboard.attach();
    Remote.start();
  }

  // ── 14. Eksporty i start ──────────────────────────────────────────────────────────────────────────────────────

  // Mapa nazwa -> implementacja (dokładnie 106 nazw kontraktu); funkcje pomocnicze UI tu nie trafiają.
  const EXPORT_MAP = {
    // stałe
    ENGINE_VERSION, FORMAT_NAME, FORMAT_VERSION, API_ROOT, QUEUE_PREFIX, REJECTED_PREFIX, SEEN_PREFIX, V1_COMMENTS_PREFIX,
    OPERATION_TYPES, THREAD_TYPES, PROTOTYPE_TYPES, QUEUEABLE_TYPES, STATUSES, OPEN_STATUSES, FILTERS, MAX_TEXT,
    THEME_LOCK_METAS, RESTORE_ROLES, MAX_RESTORE_REASON, ACCEPT_ROLES,
    // dziennik i fold
    isOperation: Journal.isOperation,
    normalizeOperations: Journal.normalize,
    sortOperations: Journal.sort,
    mergeOperations: Journal.merge,
    canonicalOperation: Journal.canonical,
    serializeCommentsDocument: Journal.serialize,
    normalizeCommentsDocument: Journal.readDocument,
    foldDocument: Fold.foldDocument,
    foldOperations: Fold.foldOperations,
    isOpenStatus: isOpenStatus,
    openCountByScreen: Fold.openCountByScreen,
    voteSummary: Fold.voteSummary,
    anchorKind: Fold.anchorKind,
    // osoby i panel
    personFromLogin: People.personFromLogin,
    computeLabels: People.computeLabels,
    peopleFromOperations: People.fromOperations,
    initialsFor: People.initials,
    avatarTone: People.avatarTone,
    threadViewOf: Views.ofPayload,
    createViewFields: Views.payloadFields,
    serverSupportsViews: Views.serverSupports,
    createViewDecision: Views.createDecision,
    normalizeViewFilter: Views.normalizeFilter,
    threadMatchesView: Views.matchesFilter,
    effectiveView: Views.effective,
    viewFrameWidth: Views.frameWidth,
    parseViewMessage: Views.parseMessage,
    viewMessageOriginAllowed: Views.originAllowed,
    viewTargetOrigin: Views.targetOrigin,
    viewFrameThreads: Views.frameThreads,
    threadMatches: Panel.matches,
    filterThreads: Panel.grouped,
    filterCounts: Panel.counts,
    // kolejka i import
    classifyWriteResult: Outbox.classifyWrite,
    mergeQueueItems: Outbox.mergeItems,
    queueItemAccepted: Outbox.itemAccepted,
    queueItemsAccepted: Outbox.itemsAccepted,
    pollDelay: Outbox.pollDelay,
    newClientId: Outbox.newClientId,
    convertV1Operations: Outbox.convertV1,
    v1ImportId: Outbox.v1ImportId,
    // motyw
    ensureThemeLockMetas: Appearance.ensureMetas,
    isDarkReaderLeftover: Appearance.isLeftover,
    hasDarkReaderLock: Appearance.hasLock,
    removeDarkReaderLeftovers: Appearance.sweep,
    // przełącznik ekranów
    screenEntriesFromLinks: Switcher.entriesFromLinks,
    normalizeSearch: Switcher.normalizeSearch,
    filterScreens: Switcher.filterEntries,
    adjacentScreen: Switcher.adjacent,
    visibleScreenId: Switcher.visibleId,
    screenListMove: Switcher.listMove,
    screenShortcut: Switcher.shortcut,
    screenSearchKeyAction: Switcher.searchKeyAction,
    focusableAfter: Switcher.focusableAfter,
    screenSearchTabTarget: Switcher.searchTabTarget,
    screenEntriesWithCounts: Switcher.withCounts,
    toolbarFitsOneRow: Switcher.toolbarFitsOneRow,
    // iteracje
    shouldProbeIteration: Iterations.shouldProbe,
    iterationPath: Iterations.pathOf,
    iterationList: Iterations.list,
    iterationForThread: Iterations.forThread,
    embedModeFrom: Iterations.embedMode,
    threadIdFromHash: Iterations.threadIdFromHash,
    restoreAvailability: Iterations.restoreAvailability,
    restoreSubmitState: Iterations.restoreSubmitState,
    restoreUnchangedText: Iterations.unchangedText,
    classifyRestoreResult: Iterations.classifyResult,
    restoreEffects: Iterations.effects,
    // akceptacja
    acceptanceSummary: Acceptance.summary,
    acceptAvailability: Acceptance.availability,
    acceptSubmitState: Acceptance.submitState,
    canUnaccept: Acceptance.canUnaccept,
    acceptOffline: Acceptance.offline,
    acceptTargetScreen: Acceptance.targetScreen,
    acceptLoadReason: Acceptance.loadReason,
    dialogEscapeAllowed: Acceptance.escapeAllowed,
    panelEscapeAction: Acceptance.panelEscape,
    dialogTabTarget: Acceptance.tabTarget,
    acceptDoneMessage: Acceptance.doneMessage,
    acceptClosedOutcome: Acceptance.closedOutcome,
    projectSummary: Acceptance.projectSummary,
    acceptScreenBody: Acceptance.screenBody,
    unacceptBody: Acceptance.unacceptBody,
    classifyAcceptResult: Acceptance.classifyResult,
    slugList: Acceptance.slugList,
    acceptedLine: Acceptance.acceptedLine
  };

  if (typeof module !== 'undefined' && module.exports) Object.assign(module.exports, EXPORT_MAP);

  if (typeof window !== 'undefined' && typeof document !== 'undefined') {
    bootEarly(window, document);
    const start = () => startUi(window, document);
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
  }
}());
