"""CORE-1180-MULTIWT M-6: the History tab of the page (finished plans of all tabs) and the sentence about the scope of a consent.

Two parts, both run in node on stubs of the browser (no network, no server):

- `ArchiveViewRenderer`: `DashboardRenderer.createArchiveView` of `dashboard-render.js` (a pure view): the loading, error, empty and ready states, the groups by tab,
  the chips (status, "in N tabs", abandoned), the preview, the button of the action only when the page may change the status (a hint otherwise), the question before
  the change (focus on "Cancel", Escape), the notice, the keys of the list, data only as text, PL and EN texts (EN has no Polish character, every error code has a text).
- `ArchivePageAdapter`: `app.js` against fake renderers: `/api/history` (no `tab` parameter, no stream of its own), the preview of the OWNER of a row, the change of a
  status (the header token of the owner tab, the body, every code of a refusal), late answers dropped, the poll only while the tab is shown and visible, the plan menu
  without finished plans (several tabs only), the address `?tab=history`, the sentence about the scope of a consent (several tabs only).
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from tests.test_skillsync_dashboard_renderer_i18n import HEAD, POLISH, run_node, ui_catalog  # noqa: E402
from tests.test_skillsync_dashboard_tabs_page import (  # noqa: E402
    BARE, CSRF, HEX, SKIP, TAB_A, TAB_B, TAB_C, last_model, run_page, session_route, status_body, tabs_body, urls,
)

KEY_OF_CODE = {"login_required": "loginRequired", "session_expired": "sessionExpired", "forbidden_role": "forbiddenRole", "consent_refused": "consentRefused",
               "password_change_required": "passwordChange", "consent_context": "consentContext", "audit_unavailable": "auditUnavailable", "approvals_disabled": "approvalsOff"}


def err_key(code: str) -> str:
    return "ui.history.err." + KEY_OF_CODE.get(code, code)


ERROR_CODES = [*KEY_OF_CODE, "approvalsOff", "badRequest", "planGone", "shaMismatch", "statusNotAllowed", "tabChanged", "locked", "tabGone", "auditRefused",
               "serverError", "notAbandoned"]


# ---------------------------------------------------------------------------------------------------------------------------------- the view
def item(key, title, status="done", **extra):
    row = {"key": key, "title": title, "path": f"docs/plans/{key}.md", "status": status, "copies": 1, "copyNames": ["Skillsync"], "ownerName": "Skillsync", "abandoned": False,
           "action": "archive" if status == "done" else "unarchive" if status == "archived" else None, "canAct": status != "approved", "why": None, "mtime": "2026-11-20T10:00:00+00:00",
           "selected": False}
    row.update(extra)
    return row


def view_model(**extra):
    model = {
        "lang": "pl", "state": "ready", "error": "", "stale": "", "refreshing": False, "count": 5, "truncated": False, "more": 0, "preview": None, "confirm": None, "notice": None,
        "groups": [
            {"id": TAB_A, "name": "Skillsync", "branch": "main", "main": True, "items": [
                item("main|a", "CORE-1161 Biegi podumyslow", copies=2, copyNames=["Skillsync", "ENROLL-6431"], selected=True),
                item("main|b", "CORE-1150 Instalacja", "archived")]},
            {"id": TAB_B, "name": "ENROLL-6431", "branch": "feature/enroll", "main": False, "items": [
                item("b|c", "ENROLL-6390 Eksperyment", "approved", abandoned=True, ownerName="ENROLL-6431"),
                item("b|d", "ENROLL-6402 Import", ownerName="ENROLL-6431", canAct=False, why="role")]},
            {"id": TAB_C, "name": "ENROLL-6409", "branch": "", "main": False, "items": []}],
    }
    model.update(extra)
    return model


def preview_of(it, state="ready", **extra):
    return {"state": state, "item": it, "html": "<p>Plan</p>", "error": "", "truncated": False, **extra}


VIEW_TAIL = r"""
(async () => {
  const out = { runs: {}, violations };
  const used = new Set();
  const cat = input.catalogs[input.lang];
  const t = (key, params) => {
    used.add(key);
    const v = cat[key];
    return (typeof v === 'string' ? v : key).replace(/\{(\w+)\}/g, (w, n) => (params && Object.prototype.hasOwnProperty.call(params, n) ? String(params[n]) : w));
  };
  const doc = makeDoc();
  const mk = () => {
    const root = doc.createElement('div'); doc.root = root;
    const calls = { select: [], action: [], confirm: 0, cancel: 0, more: 0, reload: 0, dismiss: 0, mount: [] };
    const view = R.createArchiveView(root, { t, mountHtml: (el, html) => { calls.mount.push(html); }, onSelect: (k) => calls.select.push(k), onAction: (k) => calls.action.push(k),
      onConfirm: () => { calls.confirm++; }, onCancel: () => { calls.cancel++; }, onMore: () => { calls.more++; }, onRetryPreview: () => { calls.retryPreview = (calls.retryPreview || 0) + 1; }, onReload: () => { calls.reload++; }, onDismiss: () => { calls.dismiss++; } });
    return { root, view, calls };
  };
  const q = (m, sel) => m.root.querySelector(sel), qa = (m, sel) => m.root.querySelectorAll(sel).__asArray();
  const describe = (m) => ({
    state: qa(m, '.wh-list .wh-state').map(text),
    groups: qa(m, '.wh-group').map((g) => ({ name: text(g.querySelector('.wh-gtitle')), sub: text(g.querySelector('.wh-sub')), label: g.getAttribute('aria-labelledby'),
      rows: g.querySelectorAll('.wh-row').__asArray().map((r) => ({ text: text(r), current: r.getAttribute('aria-current'), cls: r.getAttribute('class'), focusKey: r.getAttribute('data-ah-focus'),
        chips: r.querySelectorAll('.wh-chip').__asArray().map((c) => ({ text: text(c), title: c.getAttribute('title'), cls: c.getAttribute('class') })) })) })),
    foot: text(q(m, '.wh-foot')), more: q(m, '[data-ah-focus="more"]') ? text(q(m, '[data-ah-focus="more"]')) : null,
    listLabel: q(m, '.wh-list').getAttribute('aria-label'), prevLabel: q(m, '.wh-prev').getAttribute('aria-label'),
    heading: text(q(m, '.wh-title')), refresh: { text: text(q(m, '[data-ah-focus="refresh"]')), disabled: q(m, '[data-ah-focus="refresh"]').disabled },
    prev: { head: text(q(m, '.wh-htitle')), meta: text(q(m, '.wh-hmeta')), act: q(m, '[data-ah-focus="act"]') ? text(q(m, '[data-ah-focus="act"]')) : null, hint: text(q(m, '.wh-hint')),
      empty: q(m, '.wh-prev .wh-state') ? text(q(m, '.wh-prev .wh-state')) : null, doc: q(m, '.wh-doc') ? text(q(m, '.wh-doc')) : null },
    confirm: q(m, '.wh-confirm') ? { role: q(m, '.wh-confirm').getAttribute('role'), title: text(q(m, '.wh-ctitle')), body: text(q(m, '#wh-cb')), code: qa(m, '.wh-confirm code').map(text),
      cancel: text(q(m, '[data-ah-focus="cancel"]')), ok: text(q(m, '[data-ah-focus="confirm"]')), busy: q(m, '[data-ah-focus="confirm"]').getAttribute('aria-busy'),
      disabled: [q(m, '[data-ah-focus="cancel"]').disabled, q(m, '[data-ah-focus="confirm"]').disabled], labelled: q(m, '.wh-confirm').getAttribute('aria-labelledby'), described: q(m, '.wh-confirm').getAttribute('aria-describedby') } : null,
    notice: q(m, '.wh-alert') ? { cls: q(m, '.wh-alert').getAttribute('class'), role: q(m, '.wh-alert').getAttribute('role'), text: text(q(m, '.wh-alert')), code: qa(m, '.wh-alert code').map(text) } : null,
  });
  const model = input.model;
  const focused = () => (doc.focused ? doc.focused.getAttribute('data-ah-focus') : null);

  {
    const m = mk();
    m.view.update(Object.assign({}, model, { state: 'loading', groups: [], count: 0 })); out.runs.loading = describe(m);
    m.view.update(Object.assign({}, model, { state: 'error', error: 'HTTP 500', groups: [], count: 0 })); out.runs.error = describe(m);
    q(m, '[data-ah-focus="retry"]').click(); out.runs.retryCalls = m.calls.reload;
    m.view.update(Object.assign({}, model, { groups: [], count: 0 })); out.runs.empty = describe(m);
    m.view.update(Object.assign({}, model, { stale: 'HTTP 502' })); out.runs.stale = describe(m);
    m.view.update(Object.assign({}, model, { truncated: true, more: 40, count: 90 })); out.runs.cut = describe(m);
    q(m, '[data-ah-focus="more"]').click(); out.runs.moreCalls = m.calls.more;
    m.view.update(Object.assign({}, model, { refreshing: true })); out.runs.refreshing = describe(m);
    m.view.update(model); out.runs.full = describe(m); out.runs.fullText = text(m.root);
    q(m, '[data-ah-focus="refresh"]').click(); out.runs.refreshCalls = m.calls.reload;
    qa(m, '.wh-row')[2].click(); out.runs.selectCalls = m.calls.select.slice();
    m.view.destroy();
  }
  if (input.lang === 'pl') {
    // a hostile title, path and name are text
    {
      const m = mk();
      const bad = '<img src=x onerror=1>';
      m.view.update(Object.assign({}, model, { groups: [{ id: 'main', name: bad, branch: '</script>', main: false, items: [Object.assign(item('x|y', bad), { path: '"><script>', ownerName: bad })] }],
        notice: { kind: 'error', title: ['ui.history.failedTitle', { title: '`' + bad + '`' }], bodyText: '<b>x</b>' } }));
      const tags = []; walk(m.root, (n) => { if (n instanceof E && ['img', 'script', 'b'].includes(n.localName)) tags.push(n.localName); });
      out.runs.hostile = { tags, text: text(m.root) };
      m.view.destroy();
    }
    // the keys of the list
    {
      const m = mk(); m.view.update(model);
      const rows = qa(m, '.wh-row');
      const press = (name, from, mods) => { const ev = new Ev('keydown', Object.assign({ key: name, target: from }, mods || {})); doc.focused = null; q(m, '.wh-list').dispatchEvent(ev); return { prevented: ev.defaultPrevented, focus: doc.focused ? doc.focused.getAttribute('data-ah-focus') : null }; };
      out.runs.keys = { down: press('ArrowDown', rows[0]), up: press('ArrowUp', rows[1]), top: press('ArrowUp', rows[0]), end: press('End', rows[0]), home: press('Home', rows[3]),
        bottom: press('ArrowDown', rows[3]), ctrl: press('ArrowDown', rows[0], { ctrlKey: true }), other: press('a', rows[0]) };
      out.runs.rowKeys = rows.map((r) => r.getAttribute('data-ah-focus'));
      m.view.destroy();
    }
    // the preview and the button of the action
    {
      const m = mk();
      const act = (it, extra) => { m.view.update(Object.assign({}, model, { preview: preview_of(it, extra) })); return describe(m).prev; };
      function preview_of(it, extra) { return Object.assign({ state: 'ready', item: it, html: '<p>Plan</p>', error: '', truncated: false }, extra || {}); }
      m.view.update(model);
      out.runs.prevNone = describe(m).prev;
      out.runs.prevDone = act(item('a|a', 'T'));
      q(m, '[data-ah-focus="act"]').click(); out.runs.actionCalls = m.calls.action.slice(); out.runs.mounted = m.calls.mount.slice();
      out.runs.prevArchived = act(item('a|b', 'T', 'archived', { action: 'unarchive' }));
      out.runs.prevToken = act(item('a|c', 'T', 'done', { canAct: false, why: 'login' }));
      out.runs.prevViewer = act(item('a|d', 'T', 'done', { canAct: false, why: 'viewer' }));
      out.runs.prevAbandoned = act(item('a|e', 'T', 'approved', { action: null, canAct: false, abandoned: true }));
      out.runs.prevApproved = act(item('a|f', 'T', 'approved', { action: null, canAct: false }));
      out.runs.prevIdle = act(item('a|j', 'T', 'approved', { action: 'abandon', abandoned: true, canAct: true }));
      const seen = m.calls.action.length; q(m, '[data-ah-focus="act"]').click(); out.runs.idleCalls = m.calls.action.slice(seen);
      out.runs.prevIdleToken = act(item('a|k', 'T', 'approved', { action: 'abandon', abandoned: true, canAct: false, why: 'role' }));
      out.runs.prevLoading = act(item('a|g', 'T'), { state: 'loading', html: '' });
      out.runs.prevError = act(item('a|h', 'T'), { state: 'error', error: 'HTTP 404', html: '' });
      out.runs.prevCut = act(item('a|i', 'T'), { truncated: true });
      act(item('a|g', 'T'), { state: 'error', error: 'HTTP 404', html: '' });
      out.runs.retryButton = text(q(m, '[data-ah-focus="retryPreview"]')); q(m, '[data-ah-focus="retryPreview"]').click(); out.runs.retryPreviewCalls = m.calls.retryPreview;
      // links of the Markdown preview: never follow, an anchor scrolls to its id inside the plan
      {
        const l = mk(); l.view.update(model);
        l.view.update(Object.assign({}, model, { preview: { state: 'ready', item: item('a|l', 'T'), html: '', error: '', truncated: false } }));
        const box = l.root.querySelector('.wh-doc');
        const target = doc.createElement('h2'); target.setAttribute('id', 'sec-1'); let scrolled = 0; target.scrollIntoView = () => { scrolled++; }; box.appendChild(target);
        const mkLink = (cls, attr) => { const a = doc.createElement('a'); a.setAttribute('class', cls); a.setAttribute('href', '#'); for (const [k, v] of Object.entries(attr)) a.setAttribute(k, v); box.appendChild(a); return a; };
        const doclink = mkLink('md-doc', { 'data-doc': 'docs/x.md' }), anchor = mkLink('md-anchor', { 'data-anchor': 'sec-1' }), other = mkLink('plain', {});
        const fire = (a) => { const ev = new Ev('click', { target: a }); a.dispatchEvent(ev); return ev.defaultPrevented; };
        out.runs.links = { doc: fire(doclink), anchor: fire(anchor), scrolled, other: fire(other), selected: l.calls.select.length };
        l.view.destroy();
      }
      out.runs.prevButtons = qa(m, '.wh-hhead button').length;
      m.view.destroy();
    }
    // the question before the change
    {
      const m = mk();
      const it = item('a|a', 'Plan `x`', 'done', { copyNames: ['Skillsync', 'ENROLL-6431'], copies: 2 });
      const ask = (extra) => ({ action: 'archive', title: 'Plan `x`', tab: 'Skillsync', sha: 'abcdef0123456789abcdef', busy: false, copyNames: ['ENROLL-6431'], ...(extra || {}) });
      m.view.update(Object.assign({}, model, { preview: { state: 'ready', item: it, html: '', error: '', truncated: false } }));
      doc.focused = null;
      m.view.update(Object.assign({}, model, { preview: { state: 'ready', item: it, html: '', error: '', truncated: false }, confirm: ask() }));
      out.runs.confirm = describe(m).confirm; out.runs.confirmFocus = focused();
      const esc = new Ev('keydown', { key: 'Escape', target: q(m, '[data-ah-focus="cancel"]') }); q(m, '.wh-confirm').dispatchEvent(esc);
      out.runs.escape = { cancel: m.calls.cancel, prevented: esc.defaultPrevented };
      q(m, '[data-ah-focus="cancel"]').click(); q(m, '[data-ah-focus="confirm"]').click();
      out.runs.buttons = { cancel: m.calls.cancel, confirm: m.calls.confirm };
      // a redraw with the same question does not steal the focus again
      doc.focused = null; m.view.update(Object.assign({}, model, { refreshing: true, preview: { state: 'ready', item: it, html: '', error: '', truncated: false }, confirm: ask() }));
      out.runs.noRefocus = focused();
      // busy: nothing to click, Escape does nothing
      m.view.update(Object.assign({}, model, { preview: { state: 'ready', item: it, html: '', error: '', truncated: false }, confirm: ask({ busy: true }) }));
      out.runs.busy = describe(m).confirm;
      const before = m.calls.cancel; q(m, '.wh-confirm').dispatchEvent(new Ev('keydown', { key: 'Escape' })); out.runs.escapeBusy = m.calls.cancel - before;
      // the question is gone: the focus goes back to the button that opened it
      m.root.matches = (sel) => sel === ':focus-within';
      q(m, '[data-ah-focus="confirm"]').dispatchEvent(new Ev('focusin', { target: q(m, '[data-ah-focus="confirm"]') }));
      m.view.update(Object.assign({}, model, { preview: { state: 'ready', item: it, html: '', error: '', truncated: false } }));
      out.runs.afterQuestion = focused();
      // the write ended (busy -> closed): the button that was disabled had the focus, then lost it; the question closes and the focus goes back to the action button
      {
        m.view.update(Object.assign({}, model, { preview: { state: 'ready', item: it, html: '', error: '', truncated: false }, confirm: ask({ busy: true }) }));
        m.root.matches = (sel) => sel === ':focus-within';
        const okBtn = q(m, '[data-ah-focus="confirm"]');
        okBtn.dispatchEvent(new Ev('focusin', { target: okBtn })); m.root.dispatchEvent(new Ev('focusout', {}));
        doc.focused = null;
        m.view.update(Object.assign({}, model, { preview: { state: 'ready', item: it, html: '', error: '', truncated: false } }));
        out.runs.afterBusy = focused();
        // not the case when the focus was elsewhere
        m.view.update(Object.assign({}, model, { preview: { state: 'ready', item: it, html: '', error: '', truncated: false }, confirm: ask() }));
        const row = qa(m, '.wh-row')[1]; row.dispatchEvent(new Ev('focusin', { target: row })); m.root.dispatchEvent(new Ev('focusout', {}));
        doc.focused = null;
        m.view.update(Object.assign({}, model, { preview: { state: 'ready', item: it, html: '', error: '', truncated: false } }));
        out.runs.afterElsewhere = focused();
      }
      // the unarchive wording
      m.view.update(Object.assign({}, model, { preview: { state: 'ready', item: it, html: '', error: '', truncated: false }, confirm: ask({ action: 'unarchive', copyNames: [] }) }));
      out.runs.confirmUnarchive = describe(m).confirm;
      // abandon, and the restore of an abandoned plan (back to `approved`)
      m.view.update(Object.assign({}, model, { preview: { state: 'ready', item: it, html: '', error: '', truncated: false }, confirm: ask({ action: 'abandon', restoreTo: null, copyNames: [] }) }));
      out.runs.confirmAbandon = describe(m).confirm;
      m.view.update(Object.assign({}, model, { preview: { state: 'ready', item: it, html: '', error: '', truncated: false }, confirm: ask({ action: 'unarchive', restoreTo: 'approved', copyNames: [] }) }));
      out.runs.confirmRestoreApproved = describe(m).confirm;
      m.view.destroy();
    }
    // a row that has the focus keeps it when the list is drawn again
    {
      const m = mk(); m.view.update(model);
      m.root.matches = (sel) => sel === ':focus-within';
      const row = qa(m, '.wh-row')[1];
      row.dispatchEvent(new Ev('focusin', { target: row }));
      doc.focused = null;
      const next = JSON.parse(JSON.stringify(model)); next.groups[0].items[0].selected = false; next.groups[0].items[1].selected = true;
      m.view.update(next);
      out.runs.refocusedRow = focused();
      m.root.dispatchEvent(new Ev('focusout', {}));
      doc.focused = null; m.view.update(model);
      out.runs.notRefocused = focused();
      const list = q(m, '.wh-list'); m.view.update(JSON.parse(JSON.stringify(model)));
      out.runs.sameList = list === q(m, '.wh-list') && list.childNodes.length > 0;
      m.view.destroy();
    }
    // notices, one per code: each text is drawn in the language of the run
    {
      const m = mk();
      out.runs.notices = {};
      m.view.update(Object.assign({}, model, { preview: null, confirm: null, notice: { kind: 'warn', title: ['ui.history.doneTitle', { title: 'a`b', to: 'q' }], body: ['ui.history.auditFailedBody', { tab: 'x`y`z' }] } }));
      out.runs.ticks = { text: describe(m).notice.text, code: describe(m).notice.code };
      m.view.update(Object.assign({}, model, { notice: { kind: 'error', title: ['ui.history.networkTitle', { title: 'Plan' }], body: ['ui.history.err.network', {}] } })); out.runs.networkTitle = describe(m).notice.text;
      for (const code of input.codes.concat(['network', 'other'])) {
        m.view.update(Object.assign({}, model, { notice: { kind: 'error', title: ['ui.history.failedTitle', { title: 'Plan' }], body: [input.keys[code] || 'ui.history.err.' + code, { tab: 'ENROLL-6431', status: 418 }] } }));
        out.runs.notices[code] = describe(m).notice.text;
      }
      for (const [key, params] of [['ui.history.doneTitle', { title: 'Plan', to: 'x' }], ['ui.history.auditFailedTitle', { title: 'Plan' }], ['ui.history.auditFailedBody', { tab: 'T' }], ['ui.history.err.planGone', {}]]) {
        m.view.update(Object.assign({}, model, { notice: { kind: 'warn', title: [key, params], body: null } }));
      }
      m.view.update(Object.assign({}, model, { notice: { kind: 'warn', title: ['ui.history.doneTitle', { title: 'P', to: 'q' }], body: null } })); out.runs.noticeWarn = describe(m).notice;
      q(m, '.wh-alert [data-ah-focus="dismiss"]').click(); out.runs.dismissCalls = m.calls.dismiss;
      // a known code: the text of the catalog wins over the text of the server; an unknown code: the text of the server
      m.view.update(Object.assign({}, model, { notice: { kind: 'error', title: ['ui.history.failedTitle', { title: 'P' }], bodyText: 'SERVER TEXT', body: ['ui.history.err.locked', {}] } })); out.runs.noticeKnown = describe(m).notice.text;
      m.view.update(Object.assign({}, model, { notice: { kind: 'error', title: ['ui.history.failedTitle', { title: 'P' }], bodyText: 'SERVER TEXT', body: null } })); out.runs.noticeUnknown = describe(m).notice.text;
      m.view.update(Object.assign({}, model, { notice: null })); out.runs.noNotice = describe(m).notice;
      m.view.destroy();
    }
    try { R.createArchiveView(null, {}); out.runs.noRoot = 'no error'; } catch (e) { out.runs.noRoot = e.constructor.name; }
  }
  if (input.lang === 'en') {
    // the English run draws the texts the adapter hands over as keys too
    const m = mk();
    m.view.update(Object.assign({}, model, { preview: { state: 'ready', item: item('a|a', 'T'), html: '', error: '', truncated: true }, confirm: { action: 'archive', title: 'T', tab: 'A', sha: 'abc', busy: false, copyNames: ['B'] } }));
    m.view.update(Object.assign({}, model, { preview: { state: 'ready', item: item('a|a', 'T', 'archived', { action: 'unarchive' }), html: '', error: '', truncated: false }, confirm: { action: 'unarchive', title: 'T', tab: 'A', sha: 'abc', busy: true, copyNames: [] } }));
    for (const [key, params] of [['ui.history.doneTitle', { title: 'P', to: 'q' }], ['ui.history.auditFailedTitle', { title: 'P' }], ['ui.history.auditFailedBody', { tab: 'T' }], ['ui.history.networkTitle', { title: 'P' }]]) {
      m.view.update(Object.assign({}, model, { notice: { kind: 'warn', title: [key, params], body: null } }));
    }
    m.view.update(Object.assign({}, model, { preview: { state: 'error', item: item('a|a', 'T'), html: '', error: 'x', truncated: false }, notice: null }));
    m.view.update(Object.assign({}, model, { state: 'error', error: 'x', groups: [], count: 0 })); m.view.update(Object.assign({}, model, { state: 'loading', groups: [], count: 0 }));
    m.view.update(Object.assign({}, model, { groups: [], count: 0 })); m.view.update(Object.assign({}, model, { stale: 'x', truncated: true, more: 3 }));
    for (const why of ['login', 'role', 'password', 'viewer']) m.view.update(Object.assign({}, model, { preview: { state: 'ready', item: item('a|a', 'T', 'done', { canAct: false, why }), html: '', error: '', truncated: false } }));
    m.view.update(Object.assign({}, model, { preview: { state: 'ready', item: item('a|a', 'T', 'approved', { action: null, canAct: false, abandoned: true }), html: '', error: '', truncated: false } }));
    m.view.update(Object.assign({}, model, { preview: { state: 'ready', item: item('a|a', 'T', 'approved', { action: null, canAct: false }), html: '', error: '', truncated: false } }));
    for (const confirm of [{ action: 'abandon', restoreTo: null }, { action: 'unarchive', restoreTo: 'approved' }]) {
      m.view.update(Object.assign({}, model, { preview: { state: 'ready', item: item('a|a', 'T', 'approved', { action: 'abandon', abandoned: true, canAct: true }), html: '', error: '', truncated: false },
        confirm: Object.assign({ title: 'T', tab: 'A', sha: 'abc', busy: false, copyNames: [] }, confirm) }));
    }
    m.view.update(Object.assign({}, model, { preview: null }));
    for (const state of ['loading', 'error']) m.view.update(Object.assign({}, model, { preview: { state, item: item('a|a', 'T'), html: '', error: 'x', truncated: false } }));
    out.runs.usedKeys = [...used].sort();
    m.view.destroy();
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) })); });
"""


def view_run(lang: str) -> dict:
    model = json.loads(json.dumps(view_model()))
    model["lang"] = lang
    helper = "const item = (key, title, status, extra) => Object.assign({ key, title, path: 'docs/plans/' + key + '.md', status: status || 'done', copies: 1, copyNames: ['Skillsync'], ownerName: 'Skillsync', abandoned: false, action: (status || 'done') === 'done' ? 'archive' : (status === 'archived' ? 'unarchive' : null), canAct: status !== 'approved', why: null, mtime: '2026-11-20T10:00:00+00:00', selected: false }, extra || {});\n"
    source = HEAD + helper + VIEW_TAIL
    return run_node(source, {"lang": lang, "model": model, "codes": ERROR_CODES, "keys": {code: err_key(code) for code in ERROR_CODES}, "catalogs": {"pl": ui_catalog("pl"), "en": ui_catalog("en")}})


@SKIP
class ArchiveViewRenderer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pl, cls.en = view_run("pl")["runs"], view_run("en")["runs"]

    def test_states_loading_error_empty(self):
        self.assertEqual(self.pl["loading"]["state"], ["Wczytuję historię…"])
        self.assertEqual(self.pl["error"]["state"], ["Nie udało się wczytać historii: HTTP 500"])
        self.assertEqual(self.pl["retryCalls"], 1)
        self.assertEqual(self.pl["empty"]["state"], ["Brak zakończonych planów."])
        self.assertEqual(self.pl["stale"]["state"][0], "Nie udało się odświeżyć listy: HTTP 502", "the old list stays, the failure of the refresh is said")
        self.assertEqual(len(self.pl["stale"]["groups"]), 3)

    def test_groups_rows_and_chips(self):
        full = self.pl["full"]
        self.assertEqual([(g["name"], g["sub"]) for g in full["groups"]], [("Skillsync", "katalog główny"), ("ENROLL-6431", "feature/enroll"), ("ENROLL-6409", None)])
        self.assertEqual([len(g["rows"]) for g in full["groups"]], [2, 2, 0], "a tab without a finished plan is still a group")
        self.assertEqual(full["state"], ["brak zakończonych planów"], "the only state line of the ready list is the empty group")
        first = full["groups"][0]["rows"][0]
        self.assertEqual(first["current"], "true")
        self.assertIn("is-sel", first["cls"])
        self.assertEqual([c["text"] for c in first["chips"]], ["zakończony", "w 2 zakładkach"])
        self.assertIn("ENROLL-6431", first["chips"][1]["title"], "the chip names the tabs that hold the same file")
        self.assertIn("tylko kopii w zakładce Skillsync", first["chips"][1]["title"])
        second = full["groups"][0]["rows"][1]
        self.assertIsNone(second["current"])
        self.assertEqual([c["text"] for c in second["chips"]], ["zarchiwizowany"])
        abandoned = full["groups"][1]["rows"][0]
        self.assertEqual([c["text"] for c in abandoned["chips"]], ["zatwierdzony", "porzucony"])
        # kontrakt B-4a: porzucony = bez postępu i bez przebiegu przez ponad 14 dni, z przejściem do „archived”
        for fragment in ("żaden krok nie jest odhaczony", "nie ma lokalnego przebiegu", "ponad 14 dni", "Można go porzucić"):
            self.assertIn(fragment, abandoned["chips"][1]["title"])
        self.assertEqual(full["listLabel"], "Zakończone plany, według zakładek")
        self.assertTrue(all(g["label"] for g in full["groups"]))

    def test_the_rows_select_and_the_footer_pages(self):
        self.assertEqual(self.pl["selectCalls"], ["b|c"])
        self.assertEqual(self.pl["full"]["foot"], "Pokazano 4 z 5.")
        self.assertIsNone(self.pl["full"]["more"])
        self.assertEqual(self.pl["cut"]["more"], "Pokaż więcej (40)")
        self.assertEqual(self.pl["moreCalls"], 1)
        self.assertIn("ucięta limitami serwera", " ".join(self.pl["cut"]["state"]))
        self.assertEqual(self.pl["refreshCalls"], 2, "the retry button and the refresh button both ask for a new load")
        self.assertTrue(self.pl["refreshing"]["refresh"]["disabled"])
        self.assertFalse(self.pl["full"]["refresh"]["disabled"])

    def test_the_keys_of_the_list_move_between_the_rows(self):
        keys, ids = self.pl["keys"], self.pl["rowKeys"]
        self.assertEqual(ids, ["row:main|a", "row:main|b", "row:b|c", "row:b|d"])
        self.assertEqual(keys["down"], {"prevented": True, "focus": ids[1]})
        self.assertEqual(keys["up"], {"prevented": True, "focus": ids[0]})
        self.assertEqual(keys["top"]["focus"], ids[0], "no wrap above the first row")
        self.assertEqual(keys["end"]["focus"], ids[3])
        self.assertEqual(keys["home"]["focus"], ids[0])
        self.assertEqual(keys["bottom"]["focus"], ids[3])
        self.assertEqual(keys["ctrl"], {"prevented": False, "focus": None})
        self.assertEqual(keys["other"], {"prevented": False, "focus": None})

    def test_the_preview_and_who_may_change_the_status(self):
        pl = self.pl
        self.assertEqual(pl["prevNone"]["empty"], "Wybierz plan z listy, aby zobaczyć podgląd.")
        self.assertEqual(pl["prevDone"]["act"], "Archiwizuj")
        self.assertEqual(pl["actionCalls"], ["a|a"])
        self.assertEqual(pl["mounted"], ["<p>Plan</p>"], "the HTML of the server goes through `mountHtml` only")
        self.assertEqual(pl["prevDone"]["meta"], "Skillsync · zakończony · tylko do odczytu")
        self.assertEqual(pl["prevArchived"]["act"], "Przywróć")
        for name, hint in (("prevToken", "Zmiana statusu wymaga zalogowania na konto z rolą approver."),
                           ("prevViewer", "Zmiana statusu jest możliwa tylko na stronie zgód; ta strona jest tylko do odczytu."),
                           ("prevAbandoned", ui_catalog("pl")["ui.history.abandonedTip"]),
                           ("prevIdleToken", "Zmiana statusu wymaga roli approver."),
                           ("prevApproved", "Dla tego planu nie ma zmiany statusu.")):
            with self.subTest(name):
                self.assertIsNone(pl[name]["act"], "no button without the right to use it")
                self.assertEqual(pl[name]["hint"], hint)
        self.assertEqual(pl["prevLoading"]["doc"], "Wczytuję plan…")
        self.assertEqual(pl["prevError"]["doc"], "Nie udało się wczytać planu: HTTP 404Spróbuj ponownie")
        self.assertIn("Podgląd jest ucięty", pl["prevCut"]["meta"])

    def test_an_abandoned_plan_has_the_abandon_button(self):
        self.assertEqual(self.pl["prevIdle"]["act"], "Porzuć")
        self.assertEqual(self.pl["idleCalls"], ["a|j"])
        self.assertIsNone(self.pl["prevIdleToken"]["act"])

    def test_the_questions_of_abandon_and_of_the_restore_to_approved(self):
        abandon = self.pl["confirmAbandon"]
        self.assertEqual((abandon["title"], abandon["ok"], abandon["cancel"]), ("Porzucić plan?", "Porzuć", "Anuluj"))
        body = abandon["body"].replace("`", "")
        for fragment in ("z approved na archived", "Porzucony", "sha pliku abcdef012345"):
            self.assertIn(fragment, body)
        restore = self.pl["confirmRestoreApproved"]
        self.assertEqual((restore["title"], restore["ok"]), ("Przywrócić plan?", "Przywróć"))
        body = restore["body"].replace("`", "")
        self.assertIn("z archived na approved", body)
        self.assertNotIn("na done", body)
        self.assertIn("straci linię", body)

    def test_the_question_before_the_change(self):
        pl = self.pl
        confirm = pl["confirm"]
        self.assertEqual(confirm["role"], "alertdialog")
        self.assertEqual((confirm["labelled"], confirm["described"]), ("wh-ct", "wh-cb"))
        self.assertEqual(confirm["title"], "Zarchiwizować plan?")
        self.assertIn("zmieni status z done na archived", confirm["body"])
        self.assertIn("sha pliku abcdef012345", confirm["body"], "the short sha of the file")
        self.assertIn("Kopie tego planu w innych zakładkach (ENROLL-6431) nie zmienią się.", confirm["body"])
        self.assertIn("zabrudzi git status", confirm["body"])
        self.assertEqual(confirm["code"], ["done", "archived", "abcdef012345", "git status"])
        self.assertEqual((confirm["cancel"], confirm["ok"]), ("Anuluj", "Archiwizuj"))
        self.assertEqual(pl["confirmFocus"], "cancel", "the safe button has the focus")
        self.assertEqual(pl["escape"], {"cancel": 1, "prevented": True})
        self.assertEqual(pl["buttons"], {"cancel": 2, "confirm": 1})
        self.assertNotEqual(pl["noRefocus"], "cancel", "a redraw of the same question does not take the focus again")
        self.assertEqual(pl["busy"]["ok"], "Zapisuję…")
        self.assertEqual(pl["busy"]["disabled"], [True, True])
        self.assertEqual(pl["busy"]["busy"], "true")
        self.assertEqual(pl["escapeBusy"], 0, "no way out while the request is in flight")
        self.assertEqual(pl["afterQuestion"], "act", "the focus returns to the button that opened the question")
        self.assertEqual(pl["confirmUnarchive"]["title"], "Przywrócić plan?")
        self.assertIn("z archived na done", pl["confirmUnarchive"]["body"].replace("`", ""))
        self.assertNotIn("Kopie tego planu", pl["confirmUnarchive"]["body"])

    def test_the_notice_and_its_close_button(self):
        self.assertEqual(self.pl["noticeWarn"]["role"], "alert", "a warning is read at once")
        self.assertEqual(self.pl["dismissCalls"], 1)
        self.assertIsNone(self.pl["noNotice"])

    def test_a_failed_preview_can_be_retried_and_its_links_do_not_jump(self):
        self.assertEqual(self.pl["retryButton"], "Spróbuj ponownie")
        self.assertEqual(self.pl["retryPreviewCalls"], 1)
        links = self.pl["links"]
        self.assertTrue(links["doc"] and links["anchor"], "the links of the preview never follow `#`")
        self.assertEqual(links["scrolled"], 1, "an anchor scrolls to its heading inside the plan")
        self.assertFalse(links["other"], "other links are left alone")
        self.assertEqual(links["selected"], 0)

    def test_the_focus_returns_to_the_action_button_when_the_question_closes_after_the_write(self):
        self.assertEqual(self.pl["afterBusy"], "act")
        self.assertNotEqual(self.pl["afterElsewhere"], "act", "a focus the user moved to another control is not taken back")

    def test_backticks_in_names_do_not_open_code_fragments_and_the_network_has_its_own_title(self):
        self.assertEqual(self.pl["ticks"]["code"], [".skillsync/audit", "done"], "the only code fragments are those of the text, not a piece of a name")
        self.assertIn("zakładki xyz", self.pl["ticks"]["text"])
        self.assertIn("Plan ab:", self.pl["ticks"]["text"])
        self.assertEqual(self.pl["networkTitle"], "Nie potwierdzono zmiany statusu planu Plan.Brak połączenia z serwerem. Nie wiadomo, czy zmiana została zapisana; odśwież listę.Zamknij")

    def test_a_row_that_has_the_focus_keeps_it_and_the_same_model_draws_nothing(self):
        self.assertEqual(self.pl["refocusedRow"], "row:main|b")
        self.assertIsNone(self.pl["notRefocused"])
        self.assertTrue(self.pl["sameList"])

    def test_the_text_of_the_catalog_wins_over_the_server_text_only_for_a_known_code(self):
        self.assertIn("zmieniany przez inny zapis", self.pl["noticeKnown"])
        self.assertNotIn("SERVER TEXT", self.pl["noticeKnown"])
        self.assertIn("SERVER TEXT", self.pl["noticeUnknown"])

    def test_data_stays_text(self):
        self.assertEqual(self.pl["hostile"]["tags"], [])
        self.assertIn("<img src=x onerror=1>", self.pl["hostile"]["text"])
        self.assertEqual(self.pl["noRoot"], "TypeError")

    def test_every_refusal_code_has_a_text_in_both_languages(self):
        for code in ERROR_CODES + ["network", "other"]:
            with self.subTest(code):
                self.assertTrue(self.pl["notices"][code].startswith("Nie zmieniono statusu planu Plan."), "the title of a refusal")
                self.assertNotIn("ui.history", self.pl["notices"][code])
                self.assertNotEqual(ui_catalog("en")[err_key(code)].strip(), "")
                self.assertIsNone(POLISH.search(ui_catalog("en")[err_key(code)]))
        self.assertIn("418", self.pl["notices"]["other"])

    def test_english_run_has_no_polish_character_and_draws_every_history_key(self):
        en = self.en
        self.assertIsNone(POLISH.search(json.dumps(en["full"], ensure_ascii=False)), "the English page")
        catalog = ui_catalog("en")
        keys = {key for key in catalog if key.startswith("ui.history.")}
        drawn = set(en["usedKeys"]) | {err_key(code) for code in ERROR_CODES + ["network", "other"]} | {"ui.history.failedTitle"}
        self.assertEqual(sorted(keys - drawn), [], "keys of the History tab that no run draws")
        for key in keys | {"ui.wt.consentScope"}:
            self.assertIsNone(POLISH.search(catalog[key]), key)
            self.assertNotEqual(catalog[key].strip(), "", key)

    def test_both_catalogs_have_the_same_keys_and_placeholders(self):
        pl, en = ui_catalog("pl"), ui_catalog("en")
        keys = {key for key in pl if key.startswith("ui.history.")} | {"ui.wt.consentScope"}
        self.assertEqual(keys, {key for key in en if key.startswith("ui.history.")} | {"ui.wt.consentScope"})
        for key in keys:
            self.assertEqual(sorted(re.findall(r"\{\w+\}", pl[key])), sorted(re.findall(r"\{\w+\}", en[key])), key)
        for code in ERROR_CODES + ["network", "other"]:
            self.assertIn(err_key(code), pl)


# ------------------------------------------------------------------------------------------------------------------------------ the page adapter
def row(tab, path, title, status="done", sha="a" * 40, **extra):
    base = {"tab": tab, "tabName": {TAB_A: "Skillsync", TAB_B: "ENROLL-6431", TAB_C: "ENROLL-6409"}[tab], "path": path, "title": title, "status": status, "sha": sha, "copies": 1,
            "copyTabs": [tab], "abandoned": False, "action": "archive" if status == "done" else "unarchive" if status == "archived" else None, "mtime": "2026-11-20T10:00:00+00:00"}
    base.update(extra)
    return base


def history_body(*items, here=True, **extra):
    return {"status": 200, "body": {"schema": 1, "here": here, "truncated": False, "count": len(items),
                                    "tabs": [{"id": TAB_A, "name": "Skillsync", "branch": "main", "main": True}, {"id": TAB_B, "name": "ENROLL-6431", "branch": "feature/enroll", "main": False}],
                                    "items": list(items), **extra}}


A1 = row(TAB_A, "docs/plans/a1.md", "A1 plan", copies=2, copyTabs=[TAB_A, TAB_B])
A2 = row(TAB_A, "docs/plans/a2.md", "A2 old", "archived", sha="b" * 40)
B1 = row(TAB_B, "docs/plans/b1.md", "B1 plan", sha="c" * 40)
B2 = row(TAB_B, "docs/plans/b2.md", "B2 stuck", "approved", sha="d" * 40, abandoned=True, action=None)
TWO = {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}


def routes(**extra):
    return {**TWO, "/_api/v1/session": session_route(), "/api/history": history_body(A1, A2, B1, B2), "/api/doc": {"status": 200, "body": {"html": "<p>x</p>", "truncated": False}}, **extra}


def last_arch(out):
    return out["log"]["archive"][-1]


def flat(model):
    return {it["key"]: it for group in model["groups"] for it in group["items"]}


OPEN = [{"op": "select", "id": "history"}]


@SKIP
class ArchivePageAdapter(unittest.TestCase):
    # ----------------------------------------------------------------------------------------------------------------------- opening the tab
    def test_the_history_tab_replaces_the_page_of_the_tab_and_opens_no_stream(self):
        out = run_page(routes=routes(), steps=OPEN)
        self.assertEqual(out["log"]["pushed"], ["/?tab=history"])
        self.assertEqual(out["log"]["panels"], {"app": True, "history": False}, "one root at a time; the renderer of the tab stays alive behind")
        self.assertEqual(urls(out, "/api/history"), ["/api/history"], "no `tab`: the list is of the whole project")
        self.assertEqual(out["log"]["streams"], ["/events?tab=" + TAB_A], "no stream of its own")
        self.assertEqual(out["log"]["creates"], 1, "the renderer of the tab is not rebuilt")
        model = last_model(out)
        self.assertEqual(model["archive"], {"count": 4, "active": True})
        self.assertTrue(all(not tab["active"] for tab in model["tabs"]))

    def test_the_counter_is_the_count_of_the_list_and_stays_when_the_list_is_opened(self):
        tabs = tabs_body(TAB_A, TAB_B)
        for row_, count in zip(tabs["body"]["tabs"], (3, 3)):
            row_["history"] = count
        out = run_page(routes=routes(**{"/api/tabs": tabs}), steps=OPEN)
        counts = [m["archive"]["count"] for m in out["log"]["models"]]
        self.assertEqual(counts[-1], 4, "the count of the list (4), not the sum of the hints (6)")
        self.assertTrue(set(counts) <= {3, 4}, "the largest hint (3) until the list answers, never the sum")

    def test_without_the_hint_the_counter_is_the_count_of_the_list_as_before(self):
        out = run_page(routes=routes(), steps=OPEN)
        counts = [m["archive"]["count"] for m in out["log"]["models"]]
        self.assertIsNone(counts[0])
        self.assertEqual(counts[-1], 4)

    def test_the_address_with_history_opens_it_on_a_server_with_tabs_and_is_ignored_on_one_tab(self):
        out = run_page(routes=routes(), search="?tab=history")
        self.assertEqual(out["log"]["panels"], {"app": True, "history": False})
        self.assertEqual(urls(out, "/api/status"), ["/api/status?tab=" + TAB_A], "the page of the default tab keeps working behind it")
        self.assertEqual(len(urls(out, "/api/history")), 1)
        self.assertTrue(last_model(out)["archive"]["active"])
        one = run_page(routes={"/api/tabs": tabs_body(TAB_A), "/api/status": status_body(), "/api/history": history_body(A1)}, search="?tab=history")
        self.assertEqual(urls(one, "/api/history"), [], "a server with one tab has no History tab")
        self.assertEqual(one["log"]["panels"], {"app": False, "history": True})
        self.assertEqual(last_model(one), {"tabs": []})

    def test_going_back_to_the_tab_the_page_was_on_keeps_its_renderer_and_view(self):
        out = run_page(routes=routes(), storage={f"dashboard:view:{TAB_A}": "tests"}, steps=OPEN + [{"op": "select", "id": TAB_A}])
        self.assertEqual(out["log"]["pushed"], ["/?tab=history", "/?tab=" + TAB_A + "#tests"])
        self.assertEqual(out["log"]["creates"], 1)
        self.assertEqual(out["log"]["panels"], {"app": False, "history": True})
        self.assertFalse(last_model(out)["archive"]["active"])
        self.assertEqual(len(out["log"]["streams"]), 1)
        out = run_page(routes=routes(), steps=OPEN + [{"op": "select", "id": TAB_B}])
        self.assertEqual(out["log"]["creates"], 2, "another tab: a new renderer, as before")
        self.assertEqual(out["log"]["streams"], ["/events?tab=" + TAB_A, "/events?tab=" + TAB_B])
        self.assertEqual(out["log"]["panels"], {"app": False, "history": True})

    def test_back_and_forward_move_between_the_history_and_the_tabs(self):
        out = run_page(routes=routes(), steps=OPEN + [{"op": "popstate", "url": "/?tab=" + TAB_A}, {"op": "popstate", "url": "/?tab=history"}])
        self.assertEqual(out["log"]["pushed"], ["/?tab=history"], "going through the history of the browser pushes nothing")
        self.assertTrue(last_model(out)["archive"]["active"])
        self.assertEqual(out["log"]["panels"], {"app": True, "history": False})

    def test_the_title_names_the_history(self):
        out = run_page(routes=routes(), steps=[{"op": "title", "value": "Status"}] + OPEN)
        self.assertEqual(out["title"], "Status · ui.wt.history")

    # ----------------------------------------------------------------------------------------------------------------------- the list
    def test_the_rows_are_grouped_by_the_tabs_and_say_who_may_change_them(self):
        out = run_page(routes=routes(), steps=OPEN)
        model = last_arch(out)
        self.assertEqual([(g["id"], g["name"], g["main"], [it["title"] for it in g["items"]]) for g in model["groups"]],
                         [(TAB_A, "Skillsync", True, ["A1 plan", "A2 old"]), (TAB_B, "ENROLL-6431", False, ["B1 plan", "B2 stuck"])])
        rows = flat(model)
        a1, a2, b1, b2 = (rows[f"{tab}|{path}"] for tab, path in ((TAB_A, A1["path"]), (TAB_A, A2["path"]), (TAB_B, B1["path"]), (TAB_B, B2["path"])))
        self.assertEqual((a1["canAct"], a1["why"], a1["action"]), (True, None, "archive"))
        self.assertEqual((a2["canAct"], a2["action"]), (True, "unarchive"))
        self.assertEqual((b1["canAct"], b1["why"]), (True, None), "the session is one for every tab")
        self.assertEqual((b2["canAct"], b2["why"], b2["abandoned"], b2["action"]), (False, None, True, None))
        self.assertEqual((a1["copies"], a1["copyNames"], a1["ownerName"]), (2, ["Skillsync", "ENROLL-6431"], "Skillsync"))
        self.assertEqual((model["state"], model["count"], model["truncated"], model["more"]), ("ready", 4, False, 0))
        viewer = run_page(routes=routes(**{"/api/history": history_body(A1, here=False)}), steps=OPEN)
        self.assertEqual((flat(last_arch(viewer))[f"{TAB_A}|{A1['path']}"]["canAct"], flat(last_arch(viewer))[f"{TAB_A}|{A1['path']}"]["why"]), (False, "viewer"),
                         "the read socket: no button even for an approver")

    def test_loading_error_and_a_late_error_over_an_old_list(self):
        out = run_page(routes=routes(**{"/api/history": {"status": 500, "body": {"error": "boom"}}}), steps=OPEN)
        model = last_arch(out)
        self.assertEqual((model["state"], model["error"], model["groups"]), ("error", "boom", []))
        out = run_page(routes=routes(), steps=OPEN + [{"op": "route", "path": "/api/history", "value": {"status": 500, "body": {"error": "later"}}}, {"op": "arch", "name": "onReload"}])
        model = last_arch(out)
        self.assertEqual((model["state"], model["stale"]), ("ready", "later"))
        self.assertEqual(len(model["groups"]), 2, "the old list stays on screen")
        out = run_page(routes=routes(**{"/api/history": {"status": 200, "body": {"items": "x"}}}), steps=OPEN)
        self.assertEqual(last_arch(out)["state"], "error", "another shape is an error, not an empty list")

    def test_a_row_of_a_wrong_shape_is_dropped_not_drawn(self):
        bad = [{"tab": "../x", "path": "p", "status": "done", "sha": "s"}, {"tab": TAB_A, "path": "", "status": "done", "sha": "s"}, {"tab": TAB_A, "path": "p", "status": "wild", "sha": "s"}, "x", None]
        out = run_page(routes=routes(**{"/api/history": history_body(*bad, A1)}), steps=OPEN)
        self.assertEqual([it["title"] for g in last_arch(out)["groups"] for it in g["items"]], ["A1 plan"])

    def test_more_rows_and_the_server_limit(self):
        many = [row(TAB_A, f"docs/plans/p{n:03d}.md", f"P{n}", sha=f"{n:040d}") for n in range(120)]
        out = run_page(routes=routes(**{"/api/history": history_body(*many, truncated=True, count=900)}), steps=OPEN)
        model = last_arch(out)
        self.assertEqual((len(model["groups"][0]["items"]), model["more"], model["count"], model["truncated"]), (100, 20, 900, True))
        out = run_page(routes=routes(**{"/api/history": history_body(*many)}), steps=OPEN + [{"op": "arch", "name": "onMore"}])
        self.assertEqual((len(last_arch(out)["groups"][0]["items"]), last_arch(out)["more"]), (120, 0))

    # ----------------------------------------------------------------------------------------------------------------------- the preview
    def test_the_preview_is_read_from_the_tab_that_owns_the_row(self):
        out = run_page(routes=routes(), steps=OPEN + [{"op": "arch", "name": "onSelect", "args": [f"{TAB_B}|{B1['path']}"]}])
        self.assertEqual(urls(out, "/api/doc"), ["/api/doc?path=docs%2Fplans%2Fb1.md&tab=" + TAB_B])
        model = last_arch(out)
        self.assertEqual((model["preview"]["state"], model["preview"]["html"], model["preview"]["item"]["title"]), ("ready", "<p>x</p>", "B1 plan"))
        self.assertTrue(flat(model)[f"{TAB_B}|{B1['path']}"]["selected"])

    def test_a_late_preview_of_another_plan_is_dropped(self):
        a_key, b_key = f"{TAB_A}|{A1['path']}", f"{TAB_B}|{B1['path']}"
        out = run_page(routes=routes(**{"/api/doc": {"hold": "doc"}}),
                       steps=OPEN + [{"op": "arch", "name": "onSelect", "args": [a_key]}, {"op": "arch", "name": "onSelect", "args": [b_key]},
                                     {"op": "release", "name": "doc", "body": {"html": "<p>A</p>"}}, {"op": "release", "name": "doc", "body": {"html": "<p>B</p>"}}])
        preview = last_arch(out)["preview"]
        self.assertEqual((preview["item"]["title"], preview["html"]), ("B1 plan", "<p>B</p>"), "the answer for A, which came first, does not replace B")

    def test_a_failed_preview_says_so(self):
        out = run_page(routes=routes(**{"/api/doc": {"status": 404, "body": {"error": "gone"}}}), steps=OPEN + [{"op": "arch", "name": "onSelect", "args": [f"{TAB_A}|{A1['path']}"]}])
        self.assertEqual((last_arch(out)["preview"]["state"], last_arch(out)["preview"]["error"]), ("error", "gone"))

    def test_the_preview_html_goes_through_the_one_mount_of_the_page(self):
        out = run_page(routes=routes(), steps=OPEN + [{"op": "mount", "html": "<p>x</p>"}])
        self.assertEqual(out["results"], ["<p>x</p>"])

    # ----------------------------------------------------------------------------------------------------------------------- the change of a status
    def ask(self, key=None):
        key = key or f"{TAB_A}|{A1['path']}"
        return OPEN + [{"op": "arch", "name": "onSelect", "args": [key]}, {"op": "arch", "name": "onAction", "args": [key]}]

    def post(self, out):
        return next(call for call in out["log"]["fetch"] if call["url"] == "/api/plan-status")

    def test_archiving_asks_first_and_writes_nothing_until_the_confirmation(self):
        out = run_page(routes=routes(), steps=self.ask())
        self.assertFalse([call for call in out["log"]["fetch"] if call["url"] == "/api/plan-status"])
        confirm = last_arch(out)["confirm"]
        self.assertEqual((confirm["action"], confirm["title"], confirm["tab"], confirm["sha"], confirm["busy"]), ("archive", "A1 plan", "Skillsync", "a" * 40, False))
        self.assertEqual(confirm["copyNames"], ["ENROLL-6431"], "the other copies are named: they do not change")
        out = run_page(routes=routes(), steps=self.ask() + [{"op": "arch", "name": "onCancel"}])
        self.assertIsNone(last_arch(out)["confirm"])
        self.assertFalse([call for call in out["log"]["fetch"] if call["url"] == "/api/plan-status"])

    def test_the_confirmation_posts_with_the_session_of_the_page_and_the_tab_of_the_owner(self):
        ok = {"status": 200, "body": {"ok": True, "tab": TAB_A, "path": A1["path"], "from": "done", "to": "archived", "sha": "a" * 40, "auditFailed": False}}
        out = run_page(routes=routes(**{"/api/plan-status": ok}),
                       steps=self.ask() + [{"op": "arch", "name": "onConfirm"}])
        call = self.post(out)
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], "/api/plan-status", "nothing secret and no tab in the address")
        self.assertEqual(call["body"], {"tab": TAB_A, "path": A1["path"], "sha": "a" * 40, "action": "archive"})
        self.assertEqual(call["headers"], {"Content-Type": "application/json", "X-Status-Board": "1", "X-Skillsync-CSRF": CSRF})
        model = last_arch(out)
        self.assertIsNone(model["confirm"])
        self.assertEqual(model["notice"]["kind"], "info")
        self.assertEqual(model["notice"]["title"], ["ui.history.doneTitle", {"title": "A1 plan", "to": "ui.history.status.archived"}])
        self.assertEqual(len(urls(out, "/api/history")), 2, "the list is read again after the change")
        self.assertGreaterEqual(len(urls(out, "/api/status")), 2, "and the tab on the page")

    def test_restoring_sends_unarchive(self):
        key = f"{TAB_A}|{A2['path']}"
        out = run_page(routes=routes(**{"/api/plan-status": {"status": 200, "body": {"ok": True}}}), steps=self.ask(key) + [{"op": "arch", "name": "onConfirm"}])
        self.assertEqual(self.post(out)["body"]["action"], "unarchive")
        self.assertEqual(last_arch(out)["notice"]["title"][1]["to"], "ui.history.status.done")

    IDLE = row(TAB_A, "docs/plans/a3.md", "A3 idle", "approved", sha="e" * 40, abandoned=True, action="abandon", restoreTo=None)
    GIVEN_UP = row(TAB_A, "docs/plans/a4.md", "A4 gone", "archived", sha="f" * 40, restoreTo="approved")

    def history_change(self, item, answer):
        out = run_page(routes=routes(**{"/api/history": history_body(item), "/api/plan-status": answer}),
                       steps=self.ask(f"{TAB_A}|{item['path']}"))
        self.assertFalse([call for call in out["log"]["fetch"] if call["url"] == "/api/plan-status"], "nothing before the confirmation")
        confirm = last_arch(out)["confirm"]
        done = run_page(routes=routes(**{"/api/history": history_body(item), "/api/plan-status": answer}),
                        steps=self.ask(f"{TAB_A}|{item['path']}") + [{"op": "arch", "name": "onConfirm"}])
        return confirm, self.post(done)["body"], last_arch(done)["notice"]

    def test_abandoning_asks_then_posts_the_action_abandon(self):
        for label, answer in (("server says", {"status": 200, "body": {"ok": True, "from": "approved", "to": "archived"}}),
                              ("no target in the answer", {"status": 200, "body": {"ok": True}})):
            with self.subTest(label):
                confirm, body, notice = self.history_change(self.IDLE, answer)
                self.assertEqual((confirm["action"], confirm["restoreTo"]), ("abandon", None))
                self.assertEqual(body, {"tab": TAB_A, "path": self.IDLE["path"], "sha": "e" * 40, "action": "abandon"})
                self.assertEqual(notice["title"], ["ui.history.doneTitle", {"title": "A3 idle", "to": "ui.history.status.archived"}])

    def test_restoring_an_abandoned_plan_names_approved(self):
        confirm, body, notice = self.history_change(self.GIVEN_UP, {"status": 200, "body": {"ok": True, "from": "archived", "to": "approved"}})
        self.assertEqual((confirm["action"], confirm["restoreTo"]), ("unarchive", "approved"))
        self.assertEqual(body["action"], "unarchive")
        self.assertEqual(notice["title"][1]["to"], "ui.history.status.approved")

    def test_the_view_gets_the_action_and_the_target_and_nothing_unknown(self):
        odd = row(TAB_A, "docs/plans/a5.md", "A5", "approved", sha="1" * 40, abandoned=True, action="delete", restoreTo="deleted")
        out = run_page(routes=routes(**{"/api/history": history_body(self.IDLE, self.GIVEN_UP, odd)}), steps=OPEN)
        rows = flat(last_arch(out))
        self.assertEqual([(rows[f"{TAB_A}|{it['path']}"]["action"], rows[f"{TAB_A}|{it['path']}"]["restoreTo"]) for it in (self.IDLE, self.GIVEN_UP, odd)],
                         [("abandon", None), ("unarchive", "approved"), (None, None)])

    def test_a_plan_that_is_no_longer_abandoned_is_a_message_and_the_list_is_read_again(self):
        confirm, body, notice = self.history_change(self.IDLE, {"status": 409, "body": {"error": "server says", "code": "notAbandoned"}})
        self.assertEqual((notice["kind"], notice["body"][0]), ("error", "ui.history.err.notAbandoned"))

    def test_without_the_right_there_is_no_action_and_no_request(self):
        for name, session, why in (("anonymous", session_route(user=False), "login"), ("commenter", session_route(("commenter",)), "role"),
                                   ("must change", session_route(must_change=True), "password"), ("no session routes", {"status": 404, "body": {}}, "login")):
            with self.subTest(name):
                out = run_page(routes=routes(**{"/_api/v1/session": session}), steps=self.ask() + [{"op": "arch", "name": "onConfirm"}])
                self.assertIsNone(last_arch(out)["confirm"])
                row_ = flat(last_arch(out))[f"{TAB_A}|{A1['path']}"]
                self.assertEqual((row_["canAct"], row_["why"]), (False, why))
                self.assertFalse([call for call in out["log"]["fetch"] if call["url"] == "/api/plan-status"])
        out = run_page(routes=routes(), steps=self.ask(f"{TAB_B}|{B2['path']}"))   # abandoned: no action at all
        self.assertIsNone(last_arch(out)["confirm"])

    def test_a_confirmation_after_the_right_was_lost_says_why_and_sends_nothing(self):
        for name, session, key in (("signed out elsewhere", session_route(user=False), "sessionExpired"), ("role withdrawn", session_route(("commenter",)), "forbiddenRole"),
                                   ("password to change", session_route(must_change=True), "passwordChange")):
            with self.subTest(name):
                out = run_page(routes=routes(), steps=self.ask() + [{"op": "route", "path": "/_api/v1/session", "value": session}, {"op": "tick", "ms": 30000},
                                                                    {"op": "arch", "name": "onConfirm"}])
                model = last_arch(out)
                self.assertIsNone(model["confirm"])
                self.assertEqual((model["notice"]["kind"], model["notice"]["title"][0], model["notice"]["body"][0]),
                                 ("error", "ui.history.failedTitle", "ui.history.err." + key))
                self.assertFalse([call for call in out["log"]["fetch"] if call["url"] == "/api/plan-status"])

    def test_the_login_button_of_the_view_goes_to_the_login_page(self):
        out = run_page(routes=routes(**{"/_api/v1/session": session_route(user=False)}), steps=OPEN + [{"op": "arch", "name": "onLogin"}])
        self.assertEqual(out["log"]["assigned"], ["/_login?next=%2F%3Ftab%3Dhistory"])

    def test_every_refusal_is_a_message_and_the_page_acts_on_the_code(self):
        for code, http in (("login_required", 401), ("session_expired", 401), ("forbidden_role", 403), ("consent_refused", 403), ("password_change_required", 403),
                           ("consent_context", 403), ("audit_unavailable", 503), ("approvals_disabled", 403), ("approvalsOff", 403), ("badRequest", 400), ("planGone", 404),
                           ("shaMismatch", 409), ("statusNotAllowed", 409), ("tabChanged", 409), ("locked", 409), ("tabGone", 410), ("auditRefused", 500), ("serverError", 500)):
            with self.subTest(code):
                out = run_page(routes=routes(**{"/api/plan-status": {"status": http, "body": {"error": f"server says {code}", "code": code}}}),
                               steps=self.ask() + [{"op": "arch", "name": "onConfirm"}])
                model = last_arch(out)
                self.assertIsNone(model["confirm"])
                notice = model["notice"]
                self.assertEqual((notice["kind"], notice["title"][0], notice["bodyText"]), ("error", "ui.history.failedTitle", f"server says {code}"))
                self.assertEqual(notice["body"][0], err_key(code))
                self.assertEqual(len(urls(out, "/api/history")), 2, "the list is read again, whatever the answer")
                asked = len(urls(out, "/_api/v1/session"))
                self.assertEqual(asked, 2 if code in ("login_required", "session_expired", "forbidden_role", "consent_refused", "password_change_required") else 1)
        out = run_page(routes=routes(**{"/api/plan-status": {"status": 418, "body": {}}}), steps=self.ask() + [{"op": "arch", "name": "onConfirm"}])
        self.assertEqual(last_arch(out)["notice"]["body"], ["ui.history.err.other", {"status": 418}], "an unknown code is still a message")

    def test_the_text_of_the_catalog_wins_for_a_known_code_and_the_server_text_is_for_an_unknown_one(self):
        out = run_page(routes=routes(**{"/api/plan-status": {"status": 409, "body": {"error": "odd refusal", "code": "newCode"}}}), steps=self.ask() + [{"op": "arch", "name": "onConfirm"}])
        notice = last_arch(out)["notice"]
        self.assertEqual((notice["bodyText"], notice["body"]), ("odd refusal", None), "an unknown code: the text of the server")

    def test_the_tab_that_is_gone_is_marked_gone(self):
        out = run_page(routes=routes(**{"/api/plan-status": {"status": 410, "body": {"error": "gone", "code": "tabGone"}}}), steps=self.ask(f"{TAB_A}|{A1['path']}") + [{"op": "arch", "name": "onConfirm"}])
        self.assertEqual(last_arch(out)["notice"]["body"][0], "ui.history.err.tabGone")
        self.assertGreaterEqual(len(urls(out, "/api/tabs")), 2, "the list of the tabs is read again")

    def test_a_status_that_changed_but_has_no_audit_is_a_warning(self):
        out = run_page(routes=routes(**{"/api/plan-status": {"status": 200, "body": {"ok": True, "auditFailed": True}}}), steps=self.ask() + [{"op": "arch", "name": "onConfirm"}])
        notice = last_arch(out)["notice"]
        self.assertEqual((notice["kind"], notice["title"][0], notice["body"][0]), ("warn", "ui.history.auditFailedTitle", "ui.history.auditFailedBody"))

    def test_a_success_with_an_unreadable_body_is_still_a_success(self):
        out = run_page(routes=routes(**{"/api/plan-status": {"status": 200, "body": {}, "badJson": True}}), steps=self.ask() + [{"op": "arch", "name": "onConfirm"}])
        self.assertEqual(last_arch(out)["notice"]["kind"], "info", "a 200 with an unreadable body is still a success: the list is read again")

    def test_a_row_that_changed_under_the_open_question_closes_it(self):
        changed = dict(A1, sha="e" * 40)
        out = run_page(routes=routes(), steps=self.ask() + [{"op": "route", "path": "/api/history", "value": history_body(changed, A2, B1, B2)}, {"op": "arch", "name": "onReload"}])
        model = last_arch(out)
        self.assertIsNone(model["confirm"])
        self.assertEqual(model["notice"]["title"][0], "ui.history.err.shaMismatch")
        gone = run_page(routes=routes(), steps=self.ask() + [{"op": "route", "path": "/api/history", "value": history_body(A2, B1, B2)}, {"op": "arch", "name": "onReload"}])
        self.assertIsNone(last_arch(gone)["preview"])
        self.assertEqual(last_arch(gone)["notice"]["title"][0], "ui.history.err.planGone")

    def test_a_lost_connection_during_the_change_is_said(self):
        out = run_page(routes=routes(**{"/api/plan-status": {"throw": True}}), steps=self.ask() + [{"op": "arch", "name": "onConfirm"}])
        notice = last_arch(out)["notice"]
        self.assertEqual((notice["kind"], notice["title"][0], notice["body"]), ("error", "ui.history.networkTitle", ["ui.history.err.network", {}]), "its own title: it is not known whether it was written")
        self.assertEqual(len(urls(out, "/api/history")), 2, "the list is read again: the change may have been written")

    def test_the_tab_of_the_owner_of_the_row_is_sent_whatever_tab_is_on_the_page(self):
        ok = {"status": 200, "body": {"ok": True}}
        for search, key, tab in (("?tab=" + TAB_B, f"{TAB_A}|{A1['path']}", TAB_A), ("", f"{TAB_B}|{B1['path']}", TAB_B)):
            out = run_page(routes=routes(**{"/api/plan-status": ok}), search=search, steps=self.ask(key) + [{"op": "arch", "name": "onConfirm"}])
            self.assertEqual((self.post(out)["body"]["tab"], self.post(out)["headers"]["X-Skillsync-CSRF"]), (tab, CSRF))

    def test_another_row_picked_while_a_write_is_in_flight_changes_nothing_and_a_second_post_is_refused(self):
        other = f"{TAB_B}|{B1['path']}"
        out = run_page(routes=routes(**{"/api/plan-status": {"hold": "post"}}),
                       steps=self.ask() + [{"op": "arch", "name": "onConfirm"}, {"op": "arch", "name": "onSelect", "args": [other]}, {"op": "arch", "name": "onAction", "args": [other]},
                                           {"op": "arch", "name": "onAction", "args": [f"{TAB_A}|{A1['path']}"]}, {"op": "arch", "name": "onConfirm"}])
        model = last_arch(out)
        self.assertTrue(model["confirm"]["busy"], "the question of the row in flight stays")
        self.assertEqual(model["preview"]["item"]["title"], "A1 plan", "the page stays on the row being written")
        self.assertEqual(len(urls(out, "/api/doc")), 1)
        self.assertEqual(len([c for c in out["log"]["fetch"] if c["url"] == "/api/plan-status"]), 1)
        done = run_page(routes=routes(**{"/api/plan-status": {"hold": "post"}}),
                        steps=self.ask() + [{"op": "arch", "name": "onConfirm"}, {"op": "release", "name": "post", "body": {"ok": True}, "status": 200}])
        self.assertIsNone(last_arch(done)["confirm"])
        self.assertEqual(last_arch(done)["notice"]["kind"], "info")
        # a question opened for another row after the first one finished is not closed by the late answer
        late = run_page(routes=routes(**{"/api/plan-status": {"hold": "post"}}),
                        steps=self.ask() + [{"op": "arch", "name": "onConfirm"}, {"op": "release", "name": "post", "body": {"ok": True}, "status": 200},
                                            {"op": "arch", "name": "onSelect", "args": [other]}, {"op": "arch", "name": "onAction", "args": [other]}])
        self.assertEqual(last_arch(late)["confirm"]["title"], "B1 plan")

    def test_a_failed_preview_is_tried_again(self):
        out = run_page(routes=routes(**{"/api/doc": {"status": 500, "body": {"error": "boom"}}}), steps=OPEN + [{"op": "arch", "name": "onSelect", "args": [f"{TAB_A}|{A1['path']}"]},
                       {"op": "route", "path": "/api/doc", "value": {"status": 200, "body": {"html": "<p>ok</p>"}}}, {"op": "arch", "name": "onRetryPreview"}])
        self.assertEqual((len(urls(out, "/api/doc")), last_arch(out)["preview"]["state"]), (2, "ready"))
        again = run_page(routes=routes(**{"/api/doc": {"status": 500, "body": {"error": "boom"}}}), steps=OPEN + [{"op": "arch", "name": "onSelect", "args": [f"{TAB_A}|{A1['path']}"]},
                         {"op": "arch", "name": "onSelect", "args": [f"{TAB_A}|{A1['path']}"]}])
        self.assertEqual(len(urls(again, "/api/doc")), 2, "the same row clicked again retries a failed preview")
        ok = run_page(routes=routes(), steps=OPEN + [{"op": "arch", "name": "onSelect", "args": [f"{TAB_A}|{A1['path']}"]}, {"op": "arch", "name": "onSelect", "args": [f"{TAB_A}|{A1['path']}"]}])
        self.assertEqual(len(urls(ok, "/api/doc")), 1, "a loaded preview is not read again")

    def test_a_null_body_is_not_a_crash(self):
        null = {"status": 500, "body": None}
        out = run_page(routes=routes(**{"/api/plan-status": null}), steps=self.ask() + [{"op": "arch", "name": "onConfirm"}])
        self.assertEqual(last_arch(out)["notice"]["body"], ["ui.history.err.other", {"status": 500}])
        out = run_page(routes=routes(**{"/api/history": {"status": 200, "body": None}}), steps=OPEN)
        self.assertEqual(last_arch(out)["state"], "error")
        out = run_page(routes=routes(**{"/api/doc": {"status": 200, "body": None}}), steps=OPEN + [{"op": "arch", "name": "onSelect", "args": [f"{TAB_A}|{A1['path']}"]}])
        self.assertIn(last_arch(out)["preview"]["state"], ("ready", "error"))

    def test_the_keys_of_the_hidden_page_of_a_tab_do_nothing_behind_the_history(self):
        out = run_page(routes=routes(), steps=[{"op": "key", "key": "p"}])
        self.assertEqual(len([c for c in out["log"]["board"] if c[0] == "handleKey"]), 1, "on the page of a tab the renderer gets the key")
        out = run_page(routes=routes(), steps=OPEN + [{"op": "key", "key": "p"}, {"op": "key", "key": "Escape"}])
        self.assertEqual(len([c for c in out["log"]["board"] if c[0] == "handleKey"]), 0, "behind the History the keys of the tab are not for the hidden renderer")
        out = run_page(routes=routes(), steps=OPEN + [{"op": "key", "key": "t"}])
        self.assertEqual(out["log"]["themes"], ["dark"], "the theme key stays")
        out = run_page(routes=routes(), steps=OPEN + [{"op": "key", "key": "t", "ctrlKey": True}, {"op": "key", "key": "t", "inInput": True}])
        self.assertEqual(out["log"]["themes"], [])

    def test_a_double_confirmation_posts_once(self):
        out = run_page(routes=routes(**{"/api/plan-status": {"hold": "post"}}), steps=self.ask() + [{"op": "arch", "name": "onConfirm"}, {"op": "arch", "name": "onConfirm"}])
        self.assertEqual(len([call for call in out["log"]["fetch"] if call["url"] == "/api/plan-status"]), 1)
        self.assertTrue(last_arch(out)["confirm"]["busy"])

    # ----------------------------------------------------------------------------------------------------------------------- late answers
    def test_a_late_list_does_not_replace_a_newer_one(self):
        out = run_page(routes=routes(**{"/api/history": {"hold": "list"}}),
                       steps=OPEN + [{"op": "arch", "name": "onReload"}, {"op": "release", "name": "list", "body": {"count": 1}},   # the first held read is the background one of the counter
                                     {"op": "release", "name": "list", "body": history_body(B1)["body"]}, {"op": "release", "name": "list", "body": history_body(A1)["body"]}])
        self.assertEqual([it["title"] for g in last_arch(out)["groups"] for it in g["items"]], ["A1 plan"], "the first answer is dropped: the second request was the newer one")

    # ----------------------------------------------------------------------------------------------------------------------- following the server
    def test_the_list_follows_the_poll_only_while_it_is_shown_and_visible(self):
        out = run_page(routes=routes(), steps=[{"op": "tick", "ms": 15000}])
        self.assertEqual(urls(out, "/api/history"), [], "a tab of the page: no read of the history")
        out = run_page(routes=routes(), steps=OPEN + [{"op": "tick", "ms": 15000}])
        self.assertEqual(len(urls(out, "/api/history")), 2)
        out = run_page(routes=routes(), steps=OPEN + [{"op": "visibility", "value": "hidden"}, {"op": "tick", "ms": 15000}])
        self.assertEqual(len(urls(out, "/api/history")), 1, "a hidden page does not poll")
        out = run_page(routes=routes(), steps=OPEN + [{"op": "select", "id": TAB_A}, {"op": "tick", "ms": 15000}])
        self.assertEqual(len(urls(out, "/api/history")), 1, "after leaving the History tab the poll stops")

    def test_the_change_of_the_watched_tab_refreshes_the_list(self):
        out = run_page(routes=routes(), steps=OPEN + [{"op": "stream", "name": "change"}])
        self.assertEqual(len(urls(out, "/api/history")), 2)
        out = run_page(routes=routes(), steps=[{"op": "stream", "name": "change"}])
        self.assertEqual(urls(out, "/api/history"), [], "on the page of a tab `change` refreshes the status only")

    def test_a_burst_of_changes_is_one_more_read(self):
        out = run_page(routes=routes(**{"/api/history": {"hold": "list"}}), steps=OPEN + [{"op": "stream", "name": "change"}, {"op": "stream", "name": "change"}, {"op": "stream", "name": "change"},
                                                                                       {"op": "release", "name": "list", "body": history_body(A1)["body"]}, {"op": "release", "name": "list", "body": history_body(A1)["body"]}])
        self.assertEqual(len(urls(out, "/api/history")), 2)

    # ----------------------------------------------------------------------------------------------------------------------- the plan menu
    def test_the_finished_plans_leave_the_plan_menu_of_a_server_with_several_tabs(self):
        plans = [{"path": "docs/plans/live.md", "status": "in_progress"}, {"path": "docs/plans/old.md", "status": "done"}, {"path": "docs/plans/gone.md", "status": "archived"},
                 {"path": "docs/plans/next.md", "status": "approved"}, {"path": "docs/plans/shown.md", "status": "done"}]
        status = {"status": 200, "body": {"plans": plans, "plan": {"path": "docs/plans/shown.md"}, "lang": "pl"}}
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status})
        self.assertEqual(out["log"]["plans"][-1], ["docs/plans/live.md", "docs/plans/next.md", "docs/plans/shown.md"], "the plan the page shows stays even when it is a finished one")
        one = run_page(routes={"/api/tabs": tabs_body(TAB_A), "/api/status": status})
        self.assertEqual(one["log"]["plans"][-1], [p["path"] for p in plans], "a server with one tab lists them as today")
        none = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": {"status": 200, "body": {"lang": "pl"}}})
        self.assertEqual(none["log"]["plans"][-1], None)

    def test_a_manually_picked_finished_plan_is_still_a_valid_choice(self):
        status = {"status": 200, "body": {"plans": [{"path": "docs/plans/old.md", "status": "done"}, {"path": "docs/plans/live.md", "status": "in_progress"}], "plan": {"path": "docs/plans/old.md"}, "lang": "pl"}}
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status}, storage={"dashboard:plan": "docs/plans/old.md"})
        self.assertEqual(out["storage"]["dashboard:plan"], "docs/plans/old.md", "validated against the whole list, before the filter")

    # ----------------------------------------------------------------------------------------------------------------------- the scope of a consent
    def waiting_status(self, **extra):
        body = {"plans": [], "lang": "pl", "waiting": [{"what": "CP", "approve": "cp"}],
                "approvals": {"enabled": True, "here": True, "actions": [{"kind": "cp", "state": "waiting", "sha": "4f2a9c1d0b7e55aa", "word": "CP"}]}}
        body.update(extra)
        return {"status": 200, "body": body}

    def test_a_waiting_consent_says_it_applies_to_this_worktree_and_sha_only(self):
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": self.waiting_status(), "/_api/v1/session": session_route()}, search="?tab=" + TAB_B)
        alerts = [a for a in last_model(out)["alerts"] if a["title"][0] == "ui.wt.consentScope"]
        self.assertEqual(alerts, [{"kind": "info", "title": ["ui.wt.consentScope", {"name": "ENROLL-6431", "sha": "4f2a9c1d"}], "body": None, "dismiss": None}])

    def test_two_waiting_consents_name_both_shas_and_only_an_active_one_counts(self):
        actions = [{"kind": "cp", "state": "waiting", "sha": "4f2a9c1d0b7e55aa"}, {"kind": "ready", "state": "waiting", "sha": "99887766aabbccdd"}, {"kind": "go", "state": "running", "sha": "1111111122222222"}]
        status = self.waiting_status(waiting=[{"what": "CP", "approve": "cp"}, {"what": "R", "approve": "ready"}, {"what": "G", "approve": "go"}],
                                     approvals={"enabled": True, "here": True, "actions": actions})
        out = run_page(routes={"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status, "/_api/v1/session": session_route()}, search="?tab=" + TAB_B)
        alert = next(a for a in last_model(out)["alerts"] if a["title"][0] == "ui.wt.consentScope")
        self.assertEqual(alert["title"][1]["sha"], "4f2a9c1d, 99887766", "the shas of the buttons that can be pressed now; a running one is not named")

    def test_no_sentence_without_a_waiting_consent_on_one_tab_or_in_the_history(self):
        given = self.waiting_status(approvals={"enabled": True, "here": True, "actions": [{"kind": "cp", "state": "after", "sha": "4f2a9c1d0b7e55aa", "word": "CP"}]})
        for name, scenario in (("given", {"routes": {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": given}}),
                               ("nothing waits", {"routes": {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": status_body()}}),
                               ("no sha", {"routes": {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": self.waiting_status(approvals={"enabled": True, "actions": []})}}),
                               ("no role", {"routes": {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": self.waiting_status(), "/_api/v1/session": session_route(("commenter",))}, "search": "?tab=" + TAB_B}),
                               ("anonymous", {"routes": {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": self.waiting_status(), "/_api/v1/session": session_route(user=False)}}),
                               ("read socket", {"routes": {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": self.waiting_status(approvals={"enabled": True, "here": False, "actions": [{"kind": "cp", "state": "waiting", "sha": "4f2a9c1d0b7e55aa"}]})}}),
                               ("running", {"routes": {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": self.waiting_status(approvals={"enabled": True, "here": True, "actions": [{"kind": "cp", "state": "running", "sha": "4f2a9c1d0b7e55aa"}]})}}),
                               ("one tab", {"routes": {"/api/tabs": tabs_body(TAB_A), "/api/status": self.waiting_status()}}),
                               ("history", {"routes": {"/api/tabs": tabs_body(TAB_A, TAB_B), "/api/status": self.waiting_status(), "/api/history": history_body(A1)}, "steps": OPEN})):
            with self.subTest(name):
                out = run_page(**scenario)
                self.assertEqual([a for a in last_model(out).get("alerts", []) if a["title"][0] == "ui.wt.consentScope"], [])

    # ----------------------------------------------------------------------------------------------------------------------- secrets and a page without the view
    def test_no_secret_is_in_an_address_a_stream_or_the_model_of_the_view(self):
        ok = {"status": 200, "body": {"ok": True}}
        out = run_page(routes=routes(**{"/api/plan-status": ok}), steps=self.ask() + [{"op": "arch", "name": "onConfirm"}])   # run_page scans everything it logged
        self.assertTrue(out["log"]["archive"])
        self.assertNotIn(CSRF, json.dumps(out["log"]["archive"]))

    def test_a_page_whose_renderer_has_no_history_view_works_as_before(self):
        out = run_page(routes=routes(), noArchive=True, steps=OPEN)
        self.assertEqual(out["log"]["archive"], [])
        self.assertEqual(urls(out, "/api/history"), [], "no view, no read")
        self.assertEqual(out["log"]["pushed"], [])


if __name__ == "__main__":
    unittest.main()
