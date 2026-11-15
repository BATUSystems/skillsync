"""The viewer's view of the status contract (`approvals.viewer`): numbers, roles and states, never logins or question texts.

Decision Q2 of STATUS-160-A ("Widz: liczby, role, stan"). Pure functions without a server: the handler of `GET /api/status` applies
`apply_viewer_view` to a COPY of the shared answer (the status is cached and shared between clients, so it is never filtered in place).
`dashboard show --json` (the operator's local CLI) does not filter. The fields that carry people or question contents:
`waiting[].who.login` (also inside the raw checkpoint texts `run.checkpoints[].text` and `timing.windows[].what`), `expertQuestions.runs[].addressees`, `expertQuestions.items[]` (`text`, `addressees`) and `prototypes[]` threads.
"""
import copy
import re

# The raw text of a checkpoint keeps `(kto: <role> <login>)`: the viewer sees the role only.
WHO_LOGIN_RE = re.compile(r'\(\s*kto\s*:\s*(?P<role>[^\s()]+)(?:\s+[^()]*(?:\([^()]*\)[^()]*)*)?\)', re.IGNORECASE)


def _scrub(text):
    return WHO_LOGIN_RE.sub(lambda match: f"(kto: {match.group('role')})", text) if isinstance(text, str) else text


def apply_viewer_view(data, viewer):
    """`data` unchanged for a host that may give consents; for a viewer a deep copy without logins and question contents."""
    if not viewer:
        return data
    view = copy.deepcopy(data)
    for item in view.get('waiting') or []:
        who = item.get('who') if isinstance(item, dict) else None
        if isinstance(who, dict):
            who['login'] = None
    for item in (view.get('run') or {}).get('checkpoints') or []:
        if isinstance(item, dict) and 'text' in item:
            item['text'] = _scrub(item['text'])
    for window in (view.get('timing') or {}).get('windows') or []:
        if isinstance(window, dict) and 'what' in window:
            window['what'] = _scrub(window['what'])
    questions = view.get('expertQuestions')
    if isinstance(questions, dict):
        for run in questions.get('runs') or []:
            if isinstance(run, dict) and 'addressees' in run:
                run['addressees'] = []
        for item in questions.get('items') or []:
            if isinstance(item, dict):
                item.pop('text', None)
                if 'addressees' in item:
                    item['addressees'] = []
    for proto in view.get('prototypes') or []:
        if isinstance(proto, dict) and isinstance(proto.get('threads'), dict):
            proto['threads'] = {key: value for key, value in proto['threads'].items() if isinstance(value, int) and not isinstance(value, bool)}
    return view
