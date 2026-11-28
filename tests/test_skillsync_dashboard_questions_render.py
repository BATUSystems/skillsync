"""CORE-QTAB (K1-K9): zakładka „Pytania” renderera dashboardu ma dwie grupy: do realizacji (pytania blokujące i założenia ze speca) i do eksperta domenowego.

Testy na cieniu DOM z `tests/test_skillsync_dashboard_renderer.py` (node >= 18); oczekiwania zapisane literalnie według specu, nie wyliczone kodem renderera.
`PROBE_TAIL` i `probe_cases` używa też `tests/test_skillsync_dashboard_renderer_compat.py`, żeby pokryć scenariusze wyłączone ze zrzutu 1.12.0.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.test_skillsync_dashboard_renderer import NODE_MAJOR, fixture_files, ms  # noqa: E402
from tests.test_skillsync_dashboard_renderer_i18n import HEAD, clone, run_node  # noqa: E402

SKIP = unittest.skipUnless(NODE_MAJOR >= 18, "set SKILLSYNC_TEST_NODE (or have node on PATH), node >= 18")
DEFAULT_TIP = "Pytania do realizacji i do eksperta"

PROBE_TAIL = r"""
(async () => {
  const clone = (x) => JSON.parse(JSON.stringify(x));
  const deps0 = { prototypeUrl: (port, slug) => `http://127.0.0.1:${port}/${slug}/`, prototypeOrigin: (port) => `http://127.0.0.1:${port}` };
  const attr = (n, k) => (n ? n.getAttribute(k) : null);
  const probe = (m) => {
    const root = m.root;
    const arr = (sel, scope) => (scope || root).querySelectorAll(sel).__asArray();
    const tab = root.querySelector('#tab-questions'), panel = root.querySelector('#view-questions');
    const row = (n) => { const t = n.querySelector('.q-text');
      return { id: n.getAttribute('data-question') || n.getAttribute('data-assumption'), cls: n.getAttribute('class'), text: text(t), title: attr(t, 'title'), head: text(n.querySelector('.q-head')),
        chips: arr('.chip', n).map((c) => ({ cls: c.getAttribute('class'), text: text(c) })), accepted: n.querySelector('.q-accepted') ? text(n.querySelector('.q-accepted')) : null }; };
    const groups = arr('#view-questions .q-group').map((g) => { const h = g.querySelector('.q-group-title');
      return { group: g.getAttribute('data-group'), heading: h ? h.localName : null, title: h && h.childNodes[0] ? h.childNodes[0].textContent : null, titleText: h ? text(h) : null, count: h && h.querySelector('.count') ? text(h.querySelector('.count')) : null,
        questions: arr('.q-realize', g).map(row), assumptions: arr('.q-assume', g).map(row),
        order: arr('li', g).map((li) => { const c = (li.getAttribute('class') || '').split(/\s+/); return c.includes('q-realize') ? 'q' : c.includes('q-assume') ? 'a' : c.includes('q-more') ? 'more' : 'other'; }),
        more: arr('.q-more', g).map((n) => text(n)), empties: arr('.empty', g).map((n) => text(n)),
        protos: arr('.q-proto', g).map((n) => ({ slug: n.getAttribute('data-proto'), hrefs: arr('a', n).map((a) => a.getAttribute('href')) })),
        items: arr('#qItems .q-item', g).map((n) => ({ id: n.getAttribute('data-question'), hrefs: arr('a', n).map((a) => a.getAttribute('href')) })),
        subtitles: arr('.q-title', g).map((n) => ({ tag: n.localName, text: text(n) })) }; });
    const tags = []; walk(root, (n) => { if (n instanceof E) tags.push(n.localName); });
    const tabProtos = arr('#protoList .q-proto').map((n) => ({ slug: n.getAttribute('data-proto'), hrefs: arr('a', n).map((a) => a.getAttribute('href')) }));
    return { tabProtos, hidden: tab.hidden, panelHidden: panel.hidden, tip: attr(tab, 'data-tip'), count: text(root.querySelector('#qCount')), view: m.board.state().view, groups,
      groupsAll: arr('.q-group').length, groupsInActs: arr('#acts .q-group').length, imgs: tags.filter((t) => t === 'img').length, bolds: tags.filter((t) => t === 'b').length };
  };
  const out = { runs: {}, violations };
  for (const sc of input.cases) {
    try {
      const deps = Object.assign({}, deps0, sc.deps || {});
      if (deps.hasApproveToken !== undefined) { const has = deps.hasApproveToken; deps.hasApproveToken = () => has; }
      const m = mount(sc.json, { now: sc.now, deps });
      const res = probe(m);
      if (sc.then) { m.root.querySelector('#tab-questions').click(); res.viewOpened = m.board.state().view; m.board.update(sc.then); res.after = probe(m); }
      m.board.destroy();
      out.runs[sc.name] = res;
    } catch (e) { out.runs[sc.name] = { error: String(e && e.stack || e) }; }
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) })); });
"""


def probe_cases(cases: list[dict]) -> dict:
    """Mounts every case (`name`, `json`, `now`, optional `deps`, `then`) and returns the structure of the questions tab for each."""
    out = run_node(HEAD + PROBE_TAIL, {"cases": cases})
    if out["violations"]:
        raise AssertionError(out["violations"])
    return out["runs"]


EXPERT_REF = re.compile(r"\((?:ekspert|expert):\s*qn-[0-9a-f]+\)", re.I)   # the form depends on the language of the spec


def expected_tab(data: dict) -> dict:
    """What the tab must show for a status, written from the spec (K1-K8), for the compat scenarios that the golden snapshot of 1.12.0 cannot cover."""
    def items(field):
        group = data.get(field)
        return [x for x in (group or {}).get("items", []) if isinstance(x, dict) and x.get("id") and isinstance(x.get("question"), str)]
    blocking, assumptions = items("blockingQuestions"), items("assumptions")
    waits = set()
    for w in data.get("waiting") or []:
        if w.get("source") == "checkpoint" and isinstance(w.get("what"), str):
            waits.update(re.findall(r"(?<![\w-])(Q\d+)(?![\w-])", w["what"]))
    cur = (data.get("expertQuestions") or {}).get("current")
    expert_open = cur["open"] if isinstance(cur, dict) else 0
    realize_open = sum(1 for q in blocking if not EXPERT_REF.search(q["question"]))
    total = realize_open + expert_open
    return {"blocking": blocking, "assumptions": assumptions, "waits": waits, "realize_open": realize_open, "expert_open": expert_open, "total": total,
            "count": str(total) if total else "",
            "tip": f"{realize_open} do realizacji, {expert_open} do eksperta" if total else DEFAULT_TIP}


def assert_tab_matches_data(case: unittest.TestCase, data: dict, got: dict) -> None:
    """The contract of K1, K3, K6, K7, K8 applied to the page that was drawn from `data`."""
    exp = expected_tab(data)
    case.assertNotIn("error", got, got.get("error"))
    case.assertFalse(got["hidden"], "the tab shows when a group has content")
    case.assertEqual([g["group"] for g in got["groups"]], ["realize", "expert"])
    case.assertEqual((got["groupsInActs"], got["groupsAll"]), (0, 2))
    realize, expert = got["groups"]
    case.assertEqual((realize["title"], expert["title"]), ("Pytania do realizacji", "Pytania do eksperta domenowego"))
    case.assertEqual(realize["count"], str(exp["realize_open"]) if exp["realize_open"] else None)
    case.assertEqual(expert["count"], str(exp["expert_open"]) if exp["expert_open"] else None)
    case.assertEqual(got["count"], exp["count"])
    case.assertEqual(got["tip"], exp["tip"])
    # assertions changed on purpose (CORE-QTAB): one limit of 50 rows for the whole realize group (questions first, assumptions only in the room left)
    shown_q = exp["blocking"][:50]
    shown_a = exp["assumptions"][:max(0, 50 - len(shown_q))]
    case.assertEqual([q["id"] for q in realize["questions"]], [q["id"] for q in shown_q])
    case.assertEqual([a["id"] for a in realize["assumptions"]], [a["id"] for a in shown_a])
    hidden_rows = len(exp["blocking"]) + len(exp["assumptions"]) - len(shown_q) - len(shown_a)
    case.assertEqual(realize["more"], [f"+{hidden_rows} więcej"] if hidden_rows else [])
    case.assertEqual(realize["order"].count("more"), 1 if hidden_rows else 0)
    for shown, src in zip(realize["questions"] + realize["assumptions"], shown_q + shown_a):
        full = src["question"]
        case.assertEqual((shown["text"], shown["title"]), ((full[:160] + "…") if len(full) > 160 else full, full), shown["id"])
    for g, n in ((realize, exp["realize_open"]), (expert, exp["expert_open"])):
        case.assertEqual(g["titleText"], g["title"] + (f" {n}" if n else ""), "a space between the name and the counter")
    for shown, src in zip(realize["questions"], exp["blocking"]):
        chips = [c["text"] for c in shown["chips"]]
        if EXPERT_REF.search(src["question"]):
            case.assertEqual(chips, ["u eksperta"], shown["id"])
        elif src["id"] in exp["waits"]:
            case.assertEqual(chips, ["sesja czeka"], shown["id"])
        else:
            case.assertEqual(chips, [], shown["id"])
    for shown, src in zip(realize["assumptions"], exp["assumptions"]):
        case.assertEqual([c["text"] for c in shown["chips"]], ["założenie"], shown["id"])
        accepted = (src.get("accepted") or "").strip()
        case.assertEqual(shown["accepted"], ("przyjęte: " + accepted) if accepted else None, shown["id"])
    kinds = [o for o in realize["order"] if o != "more"]
    case.assertEqual(kinds, sorted(kinds, key=lambda x: {"q": 0, "a": 1}[x]), "questions come before assumptions")


def base_status() -> tuple[dict, int]:
    data = clone(fixture_files()["questions"])
    return data, ms(data["generatedAt"])


@SKIP
class QuestionsTabGroups(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base, now = base_status()
        cls.base = base
        proto = {"slug": "run-alpha", "iteration": 1, "screens": 2, "accepted": 1, "stale": 0, "threads": {"open": 1}}
        expert_item = lambda i, state="open": {"id": f"qn-{i}", "runSlug": "run-alpha", "text": f"pytanie {i}", "blocking": False, "addressees": [], "state": state,  # noqa: E731
                                                "at": "2026-01-12T11:00:00Z", "url": "/_experts/run-alpha"}
        cls.proto, cls.expert_item = proto, expert_item
        cur = {"open": 2, "blocking": 1, "items": [expert_item("a"), expert_item("b")]}
        cases: list[dict] = []

        def case(name, realize=None, assumptions=None, waiting=None, current=cur, mut=None, then=None):
            d = clone(base)
            d["blockingQuestions"] = {"count": len(realize or []), "items": realize or []}
            d["assumptions"] = {"count": len(assumptions or []), "items": assumptions or []}
            d["waiting"] = waiting if waiting is not None else []
            d["expertQuestions"]["current"] = clone(current)
            d["expertQuestions"]["open"] = (current or {}).get("open", 0) if isinstance(current, dict) else 0
            if mut:
                mut(d)
            cases.append({"name": name, "json": d, "now": now, "then": then})
            return d

        Q = lambda i, text="pytanie": {"id": f"Q{i}", "question": f"{text} {i}"}  # noqa: E731
        A = lambda i, accepted="tak": {"id": f"Q{i}", "question": f"założenie {i}", "accepted": accepted}  # noqa: E731
        check = lambda what: {"what": what, "source": "checkpoint", "since": "10:00", "action": "a", "link": None}  # noqa: E731
        cls.QA = (Q, A)
        long_text = "x" * 200

        case("both", [Q(3), {"id": "Q4", "question": "do eksperta (ekspert: qn-ab12cd)"}, Q(10), Q(1)], [A(7, "ISO"), A(8, ""), {"id": "Q9", "question": "bez wartości"}],
             waiting=[check("odpowiedź Q3"), check("odpowiedź Q4")])
        case("hostile", [{"id": "Q1", "question": "<img src=x onerror=alert(1)>"}, {"id": "Q2", "question": long_text}], [{"id": "Q3", "question": "<b>z</b>", "accepted": "<b>tak</b>"}])
        case("assumptions-only", None, [A(1, "ISO"), A(2, "")], current=None)
        case("realize-only", [Q(2), Q(5)], None, current=None)
        case("both-empty", None, None, current=None)
        case("both-empty-but-fields-count", None, None, current={"open": 0, "blocking": 0, "items": []},
             mut=lambda d: (d["blockingQuestions"].update(count=2), d["assumptions"].update(count=3)))
        case("expert-only", None, None, waiting=[])
        case("count-without-items", None, None, current={"open": 0, "blocking": 0, "items": []}, mut=lambda d: d["blockingQuestions"].update(count=2))
        case("only-handed-to-expert", [{"id": "Q1", "question": "oddane (ekspert: qn-0a1b)"}], None, current={"open": 0, "blocking": 0, "items": []},
             waiting=[check("odpowiedź Q1")])
        case("not-an-expert-ref", [{"id": "Q1", "question": "nie ta forma (ekspert: qn-xyz)"}, {"id": "Q2", "question": "ekspert: qn-ab12"}], None, current=None)
        # K7
        case("wait-q10", [Q(1), Q(10)], None, waiting=[check("czeka: odpowiedź Q10")])
        case("wait-q1", [Q(1), Q(10)], None, waiting=[check("odpowiedź Q1")])
        case("wait-many", [Q(3), Q(4), Q(5)], None, waiting=[check("odpowiedź Q3, Q4")])
        case("wait-no-what", [Q(3)], None, waiting=[{"source": "checkpoint", "since": None, "action": "a", "link": None}, {"what": 3, "source": "checkpoint"}])
        case("wait-other-source", [Q(3)], None, waiting=[{"what": "odpowiedź Q3", "source": "expert"}, {"what": "odpowiedź Q3", "source": "spec"}, {"what": "Q3"}])
        case("wait-none", [Q(3)], None, waiting=[])
        case("wait-missing", [Q(3)], None, mut=lambda d: d.pop("waiting"))
        # K6
        case("counts", [Q(1), Q(2), Q(3)], [A(4)], current={"open": 4, "blocking": 1, "items": []})
        case("counts-wrong-open", [Q(1)], None, current={"open": "3", "blocking": 0, "items": []})
        # K9
        for field in ("blockingQuestions", "assumptions"):
            for label, value in (("missing", "<del>"), ("string", "x"), ("number", 5), ("list", []), ("null", None), ("items-string", {"items": "x"}), ("items-object", {"items": {"Q1": "x"}}),
                                 ("items-null", {"items": None})):
                def mutate(d, field=field, value=value):
                    if value == "<del>":
                        d.pop(field, None)
                    else:
                        d[field] = value
                case(f"bad-{field}-{label}", [Q(1)] if field == "assumptions" else None, [A(1)] if field == "blockingQuestions" else None, mut=mutate)
        junk = [None, 7, "x", [], {"question": "bez id"}, {"id": "Q1"}, {"id": "", "question": "pusty id"}, {"id": None, "question": "id null"}, {"id": "Q2", "question": 5},
                {"id": 5, "question": "id liczbowe"}, {"id": "Q6", "question": "dobre"}]
        case("junk-items", junk, [dict(x) if isinstance(x, dict) else x for x in junk])
        case("fifty", [Q(i) for i in range(1, 51)], [A(i) for i in range(1, 51)], current=None)
        case("fifty-one", [Q(i) for i in range(1, 52)], [A(i) for i in range(1, 52)], current=None)
        case("sixty", [Q(i) for i in range(1, 61)] + [{"id": "Q99", "question": "oddane (ekspert: qn-ff00)"}], [A(i) for i in range(1, 56)], current=None)
        case("exactly-fifty-rows", [Q(i) for i in range(1, 31)], [A(i) for i in range(31, 51)], current=None)
        case("shared-limit", [Q(i) for i in range(1, 31)], [A(i) for i in range(31, 61)], current=None)
        case("only-assumptions-over", None, [A(i) for i in range(1, 56)], current=None)
        case("expert-ref-en", [{"id": "Q1", "question": "oddane (expert: qn-abc123)"}, {"id": "Q2", "question": "oddane (EXPERT:qn-ABC123)"}, Q(3)], None, current=None,
             waiting=[check("odpowiedź Q1, Q3")])

        def fallback(d):
            d["expertQuestions"].pop("current", None)
            d["prototype"] = {"port": 5130, "slug": "run-alpha", "up": True, "writing": None}
            d["prototypes"] = [proto, dict(proto, slug="run-beta")]
            d["expertQuestions"]["items"] = [dict(expert_item("a"), runSlug="run-alpha"), dict(expert_item("z"), runSlug="run-beta")]
            d["expertQuestions"]["runs"] = [{"slug": "run-alpha", "open": 2, "blocking": 1, "url": "/_experts/run-alpha"}, {"slug": "run-beta", "open": 5, "blocking": 0, "url": "/_experts/run-beta"}]
        case("no-current-key", [Q(1)], None, mut=fallback)
        case("no-current-key-no-spec", None, None, mut=fallback)
        # K5: the open tab falls back to the progress view when both groups empty out
        full = clone(cases[0]["json"])
        empty = clone(full)
        empty["blockingQuestions"] = {"count": 0, "items": []}
        empty["assumptions"] = {"count": 0, "items": []}
        empty["expertQuestions"]["current"] = None
        cases.append({"name": "then-empty", "json": full, "now": now, "then": empty})
        realize_left = clone(full)
        realize_left["expertQuestions"]["current"] = None
        cases.append({"name": "then-realize-only", "json": full, "now": now, "then": realize_left})
        cls.out = probe_cases(cases)

    def run_of(self, name):
        got = self.out[name]
        self.assertNotIn("error", got, got.get("error"))
        return got

    def realize(self, name):
        return self.run_of(name)["groups"][0]

    # --- K1
    def test_k1_two_groups_in_order_with_headings_and_counts_only_in_the_questions_panel(self):
        got = self.run_of("both")
        self.assertEqual([(g["group"], g["heading"], g["title"]) for g in got["groups"]],
                         [("realize", "h2", "Pytania do realizacji"), ("expert", "h2", "Pytania do eksperta domenowego")])
        self.assertEqual([g["count"] for g in got["groups"]], ["3", "2"])
        self.assertEqual([g["titleText"] for g in got["groups"]], ["Pytania do realizacji 3", "Pytania do eksperta domenowego 2"], "the counter is separated by a space")
        self.assertEqual((got["groupsInActs"], got["groupsAll"]), (0, 2), "`.q-group` exists only inside #view-questions")
        self.assertFalse(got["hidden"])

    # --- K2
    def test_k2_question_text_is_text_and_the_full_text_is_in_the_title(self):
        r = self.realize("hostile")
        got = self.run_of("hostile")
        self.assertEqual((got["imgs"], got["bolds"]), (0, 0), "a question must not create elements")
        q1, q2 = r["questions"]
        self.assertEqual(q1["text"], "<img src=x onerror=alert(1)>")
        self.assertEqual(q1["title"], "<img src=x onerror=alert(1)>")
        self.assertEqual(q2["id"], "Q2")
        self.assertEqual(q2["text"], "x" * 160 + "…")
        self.assertEqual(q2["title"], "x" * 200)
        self.assertEqual(r["assumptions"][0]["accepted"], "przyjęte: <b>tak</b>")
        self.assertEqual(r["assumptions"][0]["text"], "<b>z</b>")

    def test_k2_a_row_has_the_id_and_the_text(self):
        q3 = self.realize("both")["questions"][0]
        self.assertEqual((q3["id"], q3["text"]), ("Q3", "pytanie 3"))
        self.assertTrue(q3["head"].startswith("Q3"))
        self.assertIn("q-realize", q3["cls"].split())

    # --- K3
    def test_k3_assumptions_are_a_separate_state_below_the_questions_and_outside_all_counters(self):
        both = self.realize("both")
        self.assertEqual(both["order"], ["q", "q", "q", "q", "a", "a", "a"])
        a1, a2, a3 = both["assumptions"]
        self.assertEqual((a1["id"], a1["text"], [c["text"] for c in a1["chips"]], a1["accepted"]), ("Q7", "założenie 7", ["założenie"], "przyjęte: ISO"))
        self.assertEqual(([c["text"] for c in a2["chips"]], a2["accepted"]), (["założenie"], None), "an empty value: the chip and the text only")
        self.assertEqual(([c["text"] for c in a3["chips"]], a3["accepted"], a3["text"]), (["założenie"], None, "bez wartości"), "a missing value too")
        only = self.run_of("assumptions-only")
        self.assertFalse(only["hidden"], "assumptions alone show the tab (K5)")
        self.assertEqual((only["count"], only["tip"]), ("", DEFAULT_TIP))
        self.assertEqual((only["groups"][0]["count"], only["groups"][1]["count"]), (None, None))
        self.assertEqual([a["id"] for a in only["groups"][0]["assumptions"]], ["Q1", "Q2"])
        self.assertEqual(only["groups"][0]["questions"], [])
        self.assertEqual(only["groups"][0]["empties"], [], "a group with assumptions is not empty")

    # --- K4
    def test_k4_expert_group_keeps_its_selectors_links_and_subtitles(self):
        expert = self.run_of("both")["groups"][1]
        # assertions changed on purpose (CORE-2000-UNIFIED K6): no prototype among the questions; the tab of the prototypes lists those of the status with their links
        self.assertEqual(expert["protos"], [])
        rows = self.run_of("both")["tabProtos"]
        self.assertEqual([p["slug"] for p in rows], [p["slug"] for p in self.base["prototypes"]])
        self.assertEqual(rows[0]["hrefs"], [f"http://127.0.0.1:5130/{rows[0]['slug']}/"])
        self.assertEqual([i["id"] for i in expert["items"]], ["qn-a", "qn-b"])
        self.assertEqual(expert["items"][0]["hrefs"], ["http://127.0.0.1:5130/_experts/run-alpha"])
        self.assertEqual([(s["tag"], s["text"]) for s in expert["subtitles"]], [("h3", "Pytania do eksperta")])
        self.assertEqual(expert["questions"], [], "the expert rows are not rows of the realize group")

    def test_k4_expert_items_stay_limited_to_fifty(self):
        base, now = base_status()
        d = clone(base)
        d["expertQuestions"]["current"]["items"] = [self.expert_item(f"n{i}") for i in range(60)]
        got = probe_cases([{"name": "x", "json": d, "now": now}])["x"]
        self.assertEqual(len(got["groups"][1]["items"]), 50)

    # --- K5
    def test_k5_realize_alone_shows_the_tab_and_the_expert_group_says_there_is_nothing(self):
        got = self.run_of("realize-only")
        self.assertFalse(got["hidden"])
        self.assertEqual(got["count"], "2")
        self.assertEqual(got["tip"], "2 do realizacji, 0 do eksperta")
        self.assertEqual(got["groups"][1]["empties"], ["Brak pytań"])
        self.assertEqual(got["groups"][1]["title"], "Pytania do eksperta domenowego", "the heading stays above the message")

    def test_k5_expert_alone_shows_the_tab_and_the_realize_group_says_there_is_nothing(self):
        got = self.run_of("expert-only")
        self.assertFalse(got["hidden"])
        realize = got["groups"][0]
        self.assertEqual((realize["title"], realize["count"], realize["empties"]), ("Pytania do realizacji", None, ["Brak pytań do realizacji"]))
        self.assertEqual((realize["questions"], realize["assumptions"]), ([], []))
        self.assertEqual((got["count"], got["tip"]), ("2", "0 do realizacji, 2 do eksperta"))

    def test_k5_both_groups_empty_hide_the_tab_and_the_open_view_returns_to_progress(self):
        for name in ("both-empty", "both-empty-but-fields-count"):
            got = self.run_of(name)
            self.assertTrue(got["hidden"] and got["panelHidden"], name)
            self.assertEqual((got["count"], got["view"]), ("", "progress"), name)
        gone = self.run_of("then-empty")
        self.assertEqual(gone["viewOpened"], "questions")
        self.assertEqual(gone["after"]["view"], "progress")
        self.assertTrue(gone["after"]["hidden"] and gone["after"]["panelHidden"])
        self.assertEqual(gone["after"]["count"], "")
        left = self.run_of("then-realize-only")
        self.assertEqual((left["viewOpened"], left["after"]["view"], left["after"]["hidden"]), ("questions", "questions", False), "the realize group keeps the tab and the view")
        self.assertEqual(left["after"]["count"], "3")

    # --- K6
    def test_k6_counters_come_from_the_items_and_the_expert_open_count(self):
        got = self.run_of("both")
        self.assertEqual(got["count"], "5", "3 spec questions (Q4 is with the expert) + 2 open expert questions")
        self.assertEqual(got["tip"], "3 do realizacji, 2 do eksperta")
        c = self.run_of("counts")
        self.assertEqual((c["count"], c["tip"], [g["count"] for g in c["groups"]]), ("7", "3 do realizacji, 4 do eksperta", ["3", "4"]), "assumptions are in no counter")

    def test_k6_a_count_without_items_gives_zero(self):
        got = self.run_of("count-without-items")
        self.assertEqual((got["count"], got["tip"]), ("", DEFAULT_TIP))
        self.assertEqual(got["groups"][0]["count"], None)
        # assertions changed on purpose (CORE-2000-UNIFIED K6): a prototype no longer keeps the tab of the questions
        self.assertTrue(got["hidden"] and got["panelHidden"])
        self.assertTrue(got["tabProtos"], "the prototypes stay in their own tab")

    def test_k6_a_non_numeric_open_gives_no_expert_count(self):
        got = self.run_of("counts-wrong-open")
        self.assertEqual((got["count"], got["groups"][1]["count"], got["groups"][0]["count"]), ("1", None, "1"))

    # --- K7
    def chips(self, name):
        return {q["id"]: [c["text"] for c in q["chips"]] for q in self.realize(name)["questions"]}

    def test_k7_session_waits_marks_the_named_question_only(self):
        self.assertEqual(self.chips("both")["Q3"], ["sesja czeka"])
        self.assertEqual(self.chips("wait-q10"), {"Q1": [], "Q10": ["sesja czeka"]}, "Q1 is not Q10")
        self.assertEqual(self.chips("wait-q1"), {"Q1": ["sesja czeka"], "Q10": []})
        self.assertEqual(self.chips("wait-many"), {"Q3": ["sesja czeka"], "Q4": ["sesja czeka"], "Q5": []})

    def test_k7_no_mark_without_what_other_source_or_entry(self):
        for name in ("wait-no-what", "wait-other-source", "wait-none", "wait-missing"):
            self.assertEqual(self.chips(name), {"Q3": []}, name)

    # --- K8
    def test_k8_a_question_handed_to_the_expert_is_marked_and_not_counted(self):
        both = self.realize("both")
        q4 = next(q for q in both["questions"] if q["id"] == "Q4")
        self.assertEqual([c["text"] for c in q4["chips"]], ["u eksperta"], "also when a waiting entry names Q4: no `sesja czeka` on top")
        self.assertIn("is-with-expert", q4["cls"])
        self.assertEqual(both["count"], "3")
        only = self.run_of("only-handed-to-expert")
        self.assertFalse(only["hidden"], "the row stays")
        self.assertEqual((only["count"], only["tip"], only["groups"][0]["count"]), ("", DEFAULT_TIP, None))
        self.assertEqual([q["id"] for q in only["groups"][0]["questions"]], ["Q1"])
        self.assertEqual(only["groups"][0]["empties"], [])

    def test_k8_the_english_reference_form_is_handed_to_the_expert_too(self):
        got = self.run_of("expert-ref-en")
        self.assertEqual(self.chips("expert-ref-en"), {"Q1": ["u eksperta"], "Q2": ["u eksperta"], "Q3": ["sesja czeka"]}, "`(expert: qn-…)` is not a question to implement")
        self.assertEqual((got["count"], got["groups"][0]["count"], got["tip"]), ("1", "1", "1 do realizacji, 0 do eksperta"))

    def test_k8_only_the_expert_reference_form_counts(self):
        got = self.run_of("not-an-expert-ref")
        self.assertEqual(self.chips("not-an-expert-ref"), {"Q1": [], "Q2": []})
        self.assertEqual((got["count"], got["groups"][0]["count"]), ("2", "2"))

    # --- K9
    def test_k9_missing_or_wrong_typed_fields_render_without_an_error(self):
        for name, got in self.out.items():
            if name.startswith("bad-"):
                with self.subTest(case=name):
                    self.assertNotIn("error", got, got.get("error"))
                    field = name.split("-")[1]
                    shown = got["groups"][0]["questions"] if field == "assumptions" else got["groups"][0]["assumptions"]
                    self.assertEqual(len(shown), 1, "the other list is still drawn")
                    broken = got["groups"][0]["assumptions"] if field == "assumptions" else got["groups"][0]["questions"]
                    self.assertEqual(broken, [], "the broken list is skipped")

    def test_k9_junk_items_are_skipped_and_a_numeric_id_is_drawn(self):
        r = self.realize("junk-items")
        self.assertEqual([q["id"] for q in r["questions"]], ["5", "Q6"])
        self.assertEqual([a["id"] for a in r["assumptions"]], ["5", "Q6"])
        self.assertEqual(r["questions"][0]["text"], "id liczbowe")
        self.assertEqual(r["count"], "2")

    def test_k9_more_than_fifty_rows_end_with_one_note(self):
        """assertions changed on purpose (CORE-QTAB): one limit of 50 rows for the whole realize group, one note `+N więcej` at the end of the last list; the counter counts all."""
        exact = self.realize("exactly-fifty-rows")
        self.assertEqual((len(exact["questions"]), len(exact["assumptions"]), exact["more"]), (30, 20, []), "exactly 50 rows: no note")
        shared = self.realize("shared-limit")
        self.assertEqual((len(shared["questions"]), len(shared["assumptions"]), shared["more"]), (30, 20, ["+10 więcej"]), "assumptions only get the room left")
        self.assertEqual(shared["order"], ["q"] * 30 + ["a"] * 20 + ["more"], "the note closes the last list")
        self.assertEqual(shared["count"], "30", "assumptions are in no counter")
        fifty = self.realize("fifty")
        self.assertEqual((len(fifty["questions"]), len(fifty["assumptions"]), fifty["more"]), (50, 0, ["+50 więcej"]), "50 questions fill the limit: the 50 assumptions are in the note")
        self.assertEqual(fifty["order"], ["q"] * 50 + ["more"])
        one = self.realize("fifty-one")
        self.assertEqual((len(one["questions"]), len(one["assumptions"]), one["more"]), (50, 0, ["+52 więcej"]))
        self.assertEqual(one["count"], "51", "the group counter counts all")
        six = self.run_of("sixty")
        r = six["groups"][0]
        self.assertEqual((len(r["questions"]), len(r["assumptions"]), r["more"]), (50, 0, ["+66 więcej"]))
        self.assertEqual(r["count"], "60", "61 questions, one of them with the expert")
        self.assertEqual(six["count"], "60")
        only = self.realize("only-assumptions-over")
        self.assertEqual((len(only["questions"]), len(only["assumptions"]), only["more"], only["count"]), (0, 50, ["+5 więcej"], None))
        self.assertEqual(only["order"], ["a"] * 50 + ["more"])

    def test_k9_without_the_current_key_the_expert_group_keeps_the_local_approximation(self):
        got = self.run_of("no-current-key")
        self.assertEqual(got["groups"][1]["protos"], [])
        self.assertEqual([p["slug"] for p in got["tabProtos"]], ["run-alpha", "run-beta"])
        self.assertEqual([i["id"] for i in got["groups"][1]["items"]], ["qn-a"])
        self.assertEqual((got["count"], got["tip"]), ("3", "1 do realizacji, 2 do eksperta"))
        no_spec = self.run_of("no-current-key-no-spec")
        self.assertEqual((no_spec["hidden"], no_spec["count"]), (False, "2"))
        self.assertEqual(no_spec["groups"][0]["empties"], ["Brak pytań do realizacji"])


@SKIP
class QuestionsTabOnTheFixtures(unittest.TestCase):
    """The contract fixtures of the server: `questions` (2 blocking, 3 assumptions, `GO plan` waiting) and `plan-in-progress` (1 blocking, `odpowiedź Q3` waiting)."""

    @classmethod
    def setUpClass(cls):
        fx = fixture_files()
        cls.fx = fx
        cls.out = probe_cases([{"name": n, "json": clone(fx[n]), "now": ms(fx[n]["generatedAt"])} for n in ("questions", "plan-in-progress", "spec-act", "empty")])

    def test_questions_fixture(self):
        got = self.out["questions"]
        self.assertFalse(got["hidden"])
        realize, expert = got["groups"]
        self.assertEqual([(q["id"], q["text"], [c["text"] for c in q["chips"]]) for q in realize["questions"]],
                         [("Q4", "czy wysyłać podsumowanie tygodniowe?", []), ("Q6", "czy dopuszczamy wyciszenie?", [])])
        self.assertEqual([(a["id"], a["accepted"]) for a in realize["assumptions"]], [("Q1", "przyjęte: w aplikacji"), ("Q2", "przyjęte: raz dziennie"), ("Q5", "przyjęte: UTC")])
        self.assertEqual((realize["count"], expert["count"], got["count"], got["tip"]), ("2", "2", "4", "2 do realizacji, 2 do eksperta"))
        # assertions changed on purpose (CORE-2000-UNIFIED K6): the prototypes are rows of their own tab, all of the project
        self.assertEqual(expert["protos"], [])
        self.assertEqual([p["slug"] for p in got["tabProtos"]], ["run-alpha", "run-beta", "run-delta", "run-gamma"])
        self.assertEqual([i["id"] for i in expert["items"]], ["qn-b00001", "qn-a00001"])

    def test_plan_in_progress_fixture_marks_the_question_the_session_waits_for(self):
        got = self.out["plan-in-progress"]
        self.assertFalse(got["hidden"], "assertions changed on purpose (CORE-QTAB): a spec question alone shows the tab")
        realize, expert = got["groups"]
        q3, = realize["questions"]
        self.assertEqual((q3["id"], [c["text"] for c in q3["chips"]]), ("Q3", ["sesja czeka"]))
        self.assertEqual([a["id"] for a in realize["assumptions"]], ["Q1", "Q2"])
        self.assertEqual((got["count"], got["tip"], realize["count"], expert["count"]), ("1", "1 do realizacji, 0 do eksperta", "1", None))
        self.assertEqual((expert["empties"], expert["protos"], got["tabProtos"]), (["Brak pytań"], [], []))

    def test_spec_act_fixture_assumption_alone(self):
        got = self.out["spec-act"]
        self.assertFalse(got["hidden"])
        self.assertEqual((got["count"], got["tip"]), ("", DEFAULT_TIP))
        self.assertEqual([(a["id"], a["accepted"]) for a in got["groups"][0]["assumptions"]], [("Q1", "przyjęte: polski")])

    def test_empty_fixture_hides_the_tab(self):
        got = self.out["empty"]
        self.assertTrue(got["hidden"] and got["panelHidden"])
        self.assertEqual(got["count"], "")


if __name__ == "__main__":
    unittest.main()
