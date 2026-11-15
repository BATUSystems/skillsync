#!/usr/bin/env python3
"""Environment of the fixed prototype E2E suite: project, accounts, test pages, headless Chrome.

Used by `tests/test_skillsync_prototype_e2e.py` and runnable by hand:

    python3 env.py prepare  <workdir> <library> <chrome-port> <port1> <port2>   # writes <workdir>/env.json
    python3 env.py chrome   <workdir>/env.json <chrome-binary>                  # starts Chrome, writes chrome.pid
    python3 env.py stop     <workdir>/env.json                                  # stops Chrome and servers (PID file + descendants)

Passwords of the test accounts are generated here, written to `secrets.json` (mode 0600) in the
private working directory and never printed. Servers and Chrome are stopped by the identity (pid, start time, uid) of their PID file and
their listed descendants, never by name.
"""
import json
import os
import re
import secrets as _secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CLI = REPO / 'skillsync'
FIXTURE_V1 = REPO / 'tests' / 'fixtures' / 'prototype' / 'v1'

ACCOUNTS = [  # key, e-mail, role, name, password (None = default password + mustChange)
    ('anna', 'anna@example.com', 'commenter', 'Anna Komentuje', True),
    ('bartek', 'bartek@example.com', 'commenter', 'Bartek Komentuje', True),
    ('celina', 'celina@example.com', 'acceptor', 'Celina Akcept', True),
    ('dawid', 'dawid@example.com', 'developer', 'Dawid Programista', True),
    ('ewa', 'ewa@example.com', 'commenter', 'Ewa Komentuje', False),
    ('marta', 'marta@example.com', 'acceptor', 'Marta Akcept', False),
]


def run(argv, input_text=None, env=None, cwd=None, timeout=120):
    result = subprocess.run(argv, input=input_text, capture_output=True, text=True, env=env, cwd=cwd, timeout=timeout)
    return result


def cli(project, *args, env=None, input_text=None, check=True):
    argv = [sys.executable, str(CLI), 'prototype', *args]
    result = run(argv, input_text=input_text, env=env)
    if check and result.returncode != 0:
        # never echo stdin; stdout/stderr of the CLI do not carry passwords
        raise RuntimeError('skillsync prototype %s failed (%s): %s' % (args[0], result.returncode, (result.stdout + result.stderr)[-400:]))
    return result


def section(sid, title, body):
    return ('<section class="screen" id="%s">\n  <div class="screen-meta"><h2>%s</h2><p>Ekran testowy %s.</p></div>\n'
            '  <div class="frame"><div class="frame-bar"><span class="frame-url">/%s</span></div>\n'
            '  <div class="app"><main class="page">\n%s\n  </main></div></div>\n</section>\n' % (sid, title, sid, sid, body))


def section_shell(sid, title, heading, content):
    """Screen with a full application shell (sidebar with navigation, topbar with crumbs, page head, content), per the dictionary pattern."""
    nav = ''.join('<a class="nav-item%s" href="#" data-proto-id="%s-nav%d">%s</a>' % (' active' if i == 0 else '', sid, i, name)
                  for i, name in enumerate(('Tydzień', 'Sale', 'Wykładowcy', 'Raporty')))
    return ('<section class="screen" id="%s">\n  <div class="screen-meta"><h2>%s</h2><p>Ekran z powłoką aplikacji (%s).</p></div>\n'
            '  <div class="frame"><div class="frame-bar"><span class="frame-url">/%s</span></div>\n'
            '  <div class="app app-narrow"><aside class="sidebar"><div class="sidebar-app"><span class="app-mark">S</span> Skillsync</div>'
            '<div class="sidebar-search"><input class="input" type="search" aria-label="Szukaj" placeholder="Szukaj"></div>'
            '<nav class="sidebar-nav" aria-label="Główna"><div class="nav-group">Planowanie</div><div class="nav-items">%s</div></nav>'
            '<div class="sidebar-foot">Semestr zimowy</div></aside>\n'
            '  <div class="main"><header class="topbar"><div class="topbar-left"><nav class="crumbs" aria-label="Okruszki"><a href="#">Start</a> / <a href="#">Zasoby</a> / <span class="here">Sale</span></nav></div>'
            '<div class="topbar-right"><span class="avatar">AK</span></div></header>\n'
            '  <main class="page"><div class="page-head"><h1 class="t-page-title" data-proto-id="%s-h1">%s</h1><div class="page-actions"><button class="btn btn-primary" type="button" data-proto-id="%s-dodaj">Dodaj</button></div></div>\n%s\n'
            '  </main><footer class="app-foot">Skillsync</footer></div></div></div>\n</section>\n' % (sid, title, sid, sid, nav, sid, heading, sid, content))


def screens_shell():
    rows = ''.join('<tr><td>Sala %d</td><td>%d miejsc</td><td>Budynek %s</td><td><span class="badge badge-success">Wolna</span></td></tr>' % (n, 20 + n * 5, 'AB'[n % 2]) for n in range(1, 7))
    table = '<div class="scroll-x"><table class="table" data-proto-id="s1-tabela"><thead><tr><th>Sala</th><th>Pojemność</th><th>Budynek</th><th>Stan</th></tr></thead><tbody>%s</tbody></table></div>' % rows
    form = ('<form class="form-layout" data-proto-id="s2-form"><div class="field"><label>Nazwa sali <input class="input" type="text" value="Sala 12" data-proto-id="s2-nazwa"></label></div>'
            '<div class="field"><label>Pojemność <input class="input" type="number" value="45"></label></div><div class="form-foot"><button class="btn btn-primary" type="button">Zapisz</button></div></form>')
    return [('s1', 'Sale', section_shell('s1', '1. Sale', 'Sale wykładowe w semestrze zimowym', table)),
            ('s2', 'Edycja sali', section_shell('s2', '2. Edycja sali', 'Edycja sali wykładowej', form))]


def screens_goto():
    """Screens with `data-goto` on non-native elements (`tr`, `div`): keyboard reachability and activation."""
    s1 = section('s1', '1. Lista', '''    <table class="table"><tbody>
      <tr data-goto="s2" data-proto-id="goto-wiersz"><td>Wiersz prowadzący do ekranu 2</td><td>Szczegóły</td></tr></tbody></table>
    <div class="card" data-goto="s3" data-proto-id="goto-karta"><h3>Karta prowadząca do ekranu 3</h3></div>''')
    s2 = section('s2', '2. Szczegóły', '    <h3 data-proto-id="goto-s2-tytul">Szczegóły</h3><button class="btn" type="button" data-goto="s1" data-proto-id="goto-s2-wroc">Wróć</button>')
    s3 = section('s3', '3. Karta', '    <h3 data-proto-id="goto-s3-tytul">Karta</h3><button class="btn" type="button" data-goto="s1" data-proto-id="goto-s3-wroc">Wróć</button>')
    return [('s1', 'Lista', s1), ('s2', 'Szczegóły', s2), ('s3', 'Karta', s3)]


def screens_demo():
    s1 = section('s1', '1. Podsumowanie', '''    <button class="btn btn-outline" type="button" data-proto-id="s1-nowy">Nowy</button>
    <div class="card" data-proto-id="s1-karta"><h3 data-proto-id="s1-karta-tytul">Karta podsumowania</h3>
      <p>Treść karty z liczbami.</p>
      <button class="btn" type="button" data-proto-id="s1-usuwany">Do usunięcia</button>
      <button class="btn btn-primary" type="button" data-goto="s2" data-proto-id="s1-dalej">Dalej</button></div>''')
    s2 = section('s2', '2. Formularz', '''    <form data-proto-id="s2-form"><label>Nazwa <input type="text" data-proto-id="s2-nazwa" value="Pierwsza"></label>
      <label>Typ <select data-proto-id="s2-typ"><option>A</option><option>B</option></select></label>
      <button class="btn btn-primary" type="button" data-proto-id="s2-zapisz">Zapisz</button></form>''')
    return [('s1', 'Podsumowanie', s1), ('s2', 'Formularz', s2)]


def screens_simple(prefix):
    out = []
    for n, name in ((1, 'Lista'), (2, 'Formularz')):
        sid = 's%d' % n
        body = ('    <h3 data-proto-id="%s-%s-tytul">%s %s</h3><p>Treść %s.</p>\n'
                '    <button class="btn" type="button" data-proto-id="%s-%s-przycisk">Akcja %s</button>' % (prefix, sid, name, prefix, sid, prefix, sid, sid))
        out.append((sid, name, section(sid, '%d. %s' % (n, name), body)))
    return out


def screens_plan(count=28):
    out = []
    for n in range(1, count + 1):
        sid = 's%d' % n
        body = ('    <div style="min-height:70vh"><h3 data-proto-id="%s-tytul">Widok %d</h3>\n'
                '    <button class="btn" type="button" data-proto-id="%s-przycisk">Akcja %d</button></div>' % (sid, n, sid, n))
        out.append((sid, 'Plan tydzień %d' % n if n in (4, 17) else 'Widok %d' % n, section(sid, '%d. %s' % (n, 'Plan tydzień %d' % n if n in (4, 17) else 'Widok %d' % n), body)))
    return out


def write_screens(index, screens, example_last=None):
    text = index.read_text(encoding='utf-8')
    start = text.index('<section class="screen"')
    end = text.index('</section>', text.rindex('<section class="screen"')) + len('</section>\n')
    body = ''.join(s[2] for s in screens)
    if example_last:
        body += example_last
    text = text[:start] + body + text[end:]
    nav_start = text.index('<nav class="screen-nav"')
    nav_end = text.index('</nav>', nav_start) + len('</nav>')
    links = ''.join('<a href="#%s">%s %s</a>\n    ' % (sid, sid[1:], name) for sid, name, _ in screens)
    text = text[:nav_start] + '<nav class="screen-nav" aria-label="Ekrany">\n    ' + links + '</nav>' + text[nav_end:]
    index.write_text(text, encoding='utf-8')


def v1_package(project, slug, screens=7, elements=0):
    target = project / 'docs' / 'prototypes' / slug
    shutil.copytree(FIXTURE_V1, target)
    meta = json.loads((target / 'prototype.json').read_text())
    meta['slug'] = slug
    (target / 'prototype.json').write_text(json.dumps(meta))
    index = target / 'index.html'
    text = index.read_text(encoding='utf-8').replace('data-prototype-id="stary"', 'data-prototype-id="%s"' % slug)
    if elements:
        head = text[:text.index('<section class="screen"')]
        tail = text[text.index('<script src="comments.js">'):]
        sections = ''
        for n in range(1, screens + 1):
            buttons = ''.join('<button type="button" class="btn">Akcja %d.%d</button>\n' % (n, k) for k in range(elements))
            sections += '<section class="screen" id="s%d"><h2>Ekran %d</h2>\n<div class="frame">%s</div></section>\n' % (n, n, buttons)
        text = head + sections + '\n' + tail
    index.write_text(text, encoding='utf-8')
    comments = target / 'comments.js'
    comments.write_text(comments.read_text(encoding='utf-8').replace('"stary"', '"%s"' % slug), encoding='utf-8')
    return target


def prepare(workdir, library, chrome_port, port1, port2):
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    os.chmod(workdir, 0o700)
    try:   # Windows: the mode means nothing there; an inheriting ACL makes secrets.json and config.yaml private (a no-op elsewhere)
        sys.path.insert(0, str(REPO))
        from skillsync_workflow import winacl
        winacl.restrict_to_owner(str(workdir))
    except ImportError:
        pass
    base = workdir / 'base'
    if base.exists():
        shutil.rmtree(base)
    (base / '.github').mkdir(parents=True)
    (base / 'docs').mkdir()
    (base / 'docs' / 'req.md').write_text('# Wymagania\n\nW-1 Podsumowanie.\n', encoding='utf-8')
    (base / '.github' / '.skillsync.yaml').write_text('workflow: {}\n', encoding='utf-8')
    subprocess.run(['git', 'init', '-q', str(base)], check=True)
    (base / '.gitignore').write_text('.skillsync/prototype/\n', encoding='utf-8')
    secrets = {key: _secrets.token_urlsafe(18) for key, *_ in ACCOUNTS}
    secrets['default'] = _secrets.token_urlsafe(18)
    sec_path = workdir / 'secrets.json'
    fd = os.open(sec_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(secrets, handle)
    config = workdir / 'config.yaml'
    fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write('prototype:\n  default_password: "%s"\n' % secrets['default'])
        # Layer `core` = a copy of the test library with a `layer.yaml` (a real core layer, read-only by default).
        sys.path.insert(0, str(REPO))
        from skillsync_workflow import __version__
        core = workdir / 'core'
        if core.exists():
            shutil.rmtree(core)
        shutil.copytree(library, core, ignore=shutil.ignore_patterns('.git', 'node_modules', '__pycache__'))
        (core / 'layer.yaml').write_text('schema: 1\nid: core\nkind: core\nversion: %s\ncore_min: %s\n'
                                         % (__version__, __version__), encoding='utf-8')
        handle.write('layers:\n  - {id: core, kind: core, path: %s}\n' % json.dumps(str(core)))   # JSON string = YAML double-quoted: backslashes of Windows paths stay valid
        handle.write('layer_rules:\n  - {match: ["**"], use: [core]}\n')
    environ = dict(os.environ, SKILLSYNC_CONFIG=str(config))
    for slug, screens, extra in (('demo', screens_demo(), None), ('alfa', screens_simple('alfa'), None),
                                 ('beta', screens_simple('beta'), None), ('plan', screens_plan(28), None), ('shell', screens_shell(), None), ('goto', screens_goto(), None)):
        cli(base, 'init', str(base), slug, '--requirements', 'docs/req.md', '--json', env=environ)
        index = base / 'docs' / 'prototypes' / slug / 'index.html'
        example = None
        if slug == 'alfa':
            example = section('s3', '3. Przykład', '    <p>Ekran przykładowy.</p>').replace('<section class="screen" id="s3"', '<section class="screen" id="s3" data-prototype-example')
        write_screens(index, screens, example)
        if slug == 'alfa':
            text = index.read_text(encoding='utf-8')
            index.write_text(text.replace('</nav>', '<a href="#s3">3 Przykład</a></nav>', 1), encoding='utf-8')
    v1_package(base, 'delta')
    v1_package(base, 'migr', screens=28, elements=3)
    for key, login, role, name, with_password in ACCOUNTS:
        args = ['user', 'add', str(base), login, '--role', role, '--name', name, '--json']
        if with_password:
            args.append('--password-stdin')
            cli(base, *args, env=environ, input_text=secrets[key] + '\n')
        else:
            cli(base, *args, env=environ)
    env = {'workdir': str(workdir), 'base': str(base), 'cli': str(CLI), 'library': str(library), 'config': str(config),
           'secrets': str(sec_path), 'chromePort': int(chrome_port), 'ports': [int(port1), int(port2)],
           'accounts': {key: login for key, login, *_ in ACCOUNTS}, 'names': {key: name for key, _l, _r, name, _p in ACCOUNTS},
           'results': str(workdir / 'results'), 'shots': str(workdir / 'shots')}
    (workdir / 'results').mkdir(exist_ok=True)
    (workdir / 'shots').mkdir(exist_ok=True)
    (workdir / 'env.json').write_text(json.dumps(env, indent=1), encoding='utf-8')
    return env


def wait_port(port, seconds=20):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            urllib.request.urlopen('http://127.0.0.1:%d/json/version' % port, timeout=1).read()
            return True
        except Exception:
            time.sleep(0.3)
    return False


def _pid_record(pid, members=None):
    """The text of a PID file: JSON with the identity (pid, start time, uid) and the process group of the live process and, when given,
    the identities of its descendants (`members`); the bare number when `ps` fails."""
    try:
        row = process_table().get(pid)
    except RuntimeError:
        row = None
    if not row:
        return str(pid)
    record = dict(identity_of(row), pgid=row['pgid'])
    if members:
        record['members'] = [identity for member, identity in sorted(members.items()) if member != pid]
    return json.dumps(record)


def record_members(env):
    """Writes the helpers that live now into `chrome.pid`: a helper that left the process group cannot be found by anything else once the
    leader has died (the tree is gone, the group no longer names it). Called after the start; may be called again."""
    pid_file = Path(env['workdir']) / 'chrome.pid'
    record = read_pid_file(pid_file)
    if record is None or record.get('start') is None:
        return False
    try:
        table = process_table()
    except RuntimeError:
        return False
    if not is_ours(pid_file, record, table.get(record['pid'])):
        return False
    temporary = pid_file.with_name('chrome.pid.tmp')
    temporary.write_text(_pid_record(record['pid'], collect_members(record['pid'], table)))
    os.replace(temporary, pid_file)
    return True


def start_chrome(env, binary):
    port = env['chromePort']
    workdir = Path(env['workdir'])
    profile = workdir / 'chrome-profile'
    log = open(workdir / 'chrome.log', 'ab')
    # Chrome puts a unix socket under TMPDIR (path limit ~100 chars): a short symlink to a directory inside the working directory.
    real_tmp = workdir / 'chrome-tmp'
    real_tmp.mkdir(exist_ok=True)
    short = Path.home() / '.cache' / ('skse2e-%d' % port)
    short.parent.mkdir(exist_ok=True)
    if short.is_symlink() or short.exists():
        short.unlink()
    short.symlink_to(real_tmp)
    (workdir / 'chrome-tmp.link').write_text(str(short))
    child_env = dict(os.environ, TMPDIR=str(short))
    # GitHub runners (Ubuntu 24.04) restrict unprivileged user namespaces, so the Chrome sandbox cannot start there.
    # The flag is added only in CI (CI=true), never on a developer machine.
    sandbox = ['--no-sandbox'] if os.environ.get('CI') == 'true' else []
    # Light colour scheme forced: headless Chrome follows the desktop preference (e.g. GNOME `prefer-dark`), which turns the test pages
    # dark and fails the light-background assertions (DR-start, DR-późny) on such a machine; CI is light by default (DEBT-142 D7).
    child = subprocess.Popen([binary, '--headless=new', *sandbox, '--remote-debugging-port=%d' % port, '--remote-debugging-address=127.0.0.1',
                              '--user-data-dir=%s' % profile, '--no-first-run', '--no-default-browser-check', '--disable-extensions',
                              '--disable-gpu', '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
                              '--disable-backgrounding-occluded-windows', '--blink-settings=preferredColorScheme=1', '--window-size=1440,900',
                              'about:blank'],
                             stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True, env=child_env)
    log.close()   # the child has its own copy of the descriptor
    CHILDREN[child.pid] = child
    (workdir / 'chrome.pid').write_text(_pid_record(child.pid))
    if not wait_port(port):
        stop_pid(child.pid)
        raise RuntimeError('Chrome did not open the debugging port %d' % port)
    record_members(env)
    return child.pid


CHILDREN = {}   # Popen objects of the processes started here (waited for after the stop, no zombie or ResourceWarning)

# --- Process ownership without /proc (Linux, macOS) -----------------------------------------------------------------------------------
# A process is "ours" when its identity (pid, start time, uid) matches the one recorded when it was started. The table of processes comes
# from `ps` (the same flags work on Linux and macOS); `/proc` is only the fallback where `ps` is missing. Nothing is ever chosen by name:
# what is signalled is the process of a PID file and its descendants, listed BEFORE the first signal (after the leader dies its
# children are re-parented and the tree is gone), each of them only while its identity still matches.
START_TOLERANCE = 2   # seconds: `ps` start times have a one second resolution
GRACE = 6             # seconds between SIGTERM and SIGKILL
KILL_WAIT = 5         # seconds to wait for the death after SIGKILL
_PS_ROW = re.compile(r'^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(.+?)\s*$')


def _parse_start(text):
    try:
        return int(time.mktime(time.strptime(' '.join(text.split()), '%a %b %d %H:%M:%S %Y')))
    except ValueError:
        return None


def _table_from_ps():
    """The complete table or OSError: a non-zero exit status or a line that cannot be read means the table may be partial, and a partial
    table would make a living process look gone. One retry (a process that exits while `ps` runs can print a half line)."""
    for attempt in (1, 2):
        result = subprocess.run(['ps', '-A', '-o', 'pid=,ppid=,pgid=,uid=,stat=,lstart='], capture_output=True, text=True, timeout=15,
                                env=dict(os.environ, LC_ALL='C', LANG='C'), check=False)
        table, unreadable = {}, 0
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            match = _PS_ROW.match(line)
            start = _parse_start(match.group(6)) if match else None
            if match and start is not None:
                pid = int(match.group(1))
                table[pid] = {'pid': pid, 'ppid': int(match.group(2)), 'pgid': int(match.group(3)), 'uid': int(match.group(4)),
                              'state': match.group(5), 'start': start}
            else:
                unreadable += 1
        if result.returncode == 0 and not unreadable and table:
            return table
    raise OSError('ps gave an incomplete table (exit status %s, %d unreadable lines)' % (result.returncode, unreadable))


def _table_from_proc():
    """Linux fallback when `ps` is not installed; empty elsewhere."""
    table = {}
    root = Path('/proc')
    try:
        boot = next(int(line.split()[1]) for line in (root / 'stat').read_text().splitlines() if line.startswith('btime '))
        ticks = os.sysconf('SC_CLK_TCK')
        entries = [item for item in root.iterdir() if item.name.isdigit()]
    except (OSError, StopIteration, ValueError):
        return table
    for entry in entries:
        try:
            raw = (entry / 'stat').read_text(encoding='utf-8', errors='replace')
            fields = raw[raw.rfind(')') + 2:].split()
            pid = int(entry.name)
            table[pid] = {'pid': pid, 'ppid': int(fields[1]), 'pgid': int(fields[2]), 'uid': entry.stat().st_uid, 'state': fields[0],
                          'start': boot + int(fields[19]) // ticks}
        except (OSError, ValueError, IndexError):
            continue
    return table


def process_table():
    """`{pid: {pid, ppid, pgid, uid, state, start}}`; RuntimeError when no source can list processes (nothing is then decided)."""
    table = {}
    try:
        table = _table_from_ps()
    except (OSError, subprocess.SubprocessError):
        pass
    if not table:
        table = _table_from_proc()
    if not table:
        raise RuntimeError('cannot list processes (neither `ps` nor /proc worked)')
    return table


def same_identity(row, record):
    """`row` (live) is the process of `record` ({pid, start, uid}): same start time (within the resolution of `ps`) and the same owner."""
    return (row is not None and not row['state'].startswith('Z') and row['uid'] == record['uid']
            and abs(row['start'] - record['start']) <= START_TOLERANCE)


def identity_of(row):
    return {'pid': row['pid'], 'start': row['start'], 'uid': row['uid']}


def read_pid_file(pid_file):
    """`{pid, start, uid, pgid, members}` from the JSON a record of this module wrote, or `{pid, mtime}` for a plain number (a file written by a
    server script right after the spawn); None for anything else."""
    try:
        text = pid_file.read_text().strip()
        if text.startswith('{'):
            data = json.loads(text)
            record = {'pid': int(data['pid']), 'start': int(data['start']), 'uid': int(data['uid'])}
            pgid = int(data['pgid']) if data.get('pgid') is not None else None
            record['pgid'] = pgid if pgid is not None and pgid > 1 else None
            record['members'] = [{'pid': int(item['pid']), 'start': int(item['start']), 'uid': int(item['uid'])}
                                 for item in data.get('members') or [] if int(item['pid']) > 1]
        else:
            record = {'pid': int(text), 'start': None, 'uid': None, 'mtime': pid_file.stat().st_mtime}
        return record if record['pid'] > 1 else None   # 0, 1 and negative numbers name no process of ours (kill(0) would be this group)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


# PID file -> text that must occur in the command line (only for a plain-number file, whose identity has no start time)
EXPECTED_CMDLINE = (('chrome', '--remote-debugging-port'), ('static-', 'http.server'), ('serve-', 'serve'))


def _command_of(pid):
    try:
        return Path('/proc/%d/cmdline' % pid).read_bytes().replace(b'\0', b' ').decode('utf-8', 'replace')
    except OSError:
        pass
    try:
        result = subprocess.run(['ps', '-o', 'command=', '-p', str(pid)], capture_output=True, text=True, timeout=10, check=False,
                                env=dict(os.environ, LC_ALL='C'))
        return result.stdout if result.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        return ''


def is_ours(pid_file, record, row):
    """The live `row` is the process this PID file stands for. A JSON record: identity (pid, start, uid). A plain number: the process
    must be ours, must have started before the file was written (a reused PID starts later) and must have the expected command line."""
    if row is None or row['state'].startswith('Z') or row['uid'] != os.getuid():
        return False
    if record.get('start') is not None:
        return same_identity(row, record)
    if row['start'] > record['mtime'] + START_TOLERANCE:
        return False
    command = _command_of(record['pid'])
    return any(pid_file.name.startswith(prefix) and needle in command for prefix, needle in EXPECTED_CMDLINE)


def _signal(pid, sig):
    if pid <= 1:
        return
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _signal_group(pgid, sig):
    if pgid <= 1:
        return
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _protected():
    """This process and its ancestors: never signalled, whatever the tree says."""
    pids, pid = set(), os.getpid()
    try:
        table = process_table()
    except RuntimeError:
        return {pid, os.getppid()}
    while pid in table and pid not in pids and pid > 1:
        pids.add(pid)
        pid = table[pid]['ppid']
    return pids | {os.getpid(), os.getppid()}


def own_group_leader(row):
    """The group of `row` is its own (the process started in a new session), is not the group of this process and the process is not an
    ancestor of this one (a shell with job control leads its own group too): only then is the group signalled."""
    return (row['pid'] > 1 and row['pgid'] == row['pid'] and row['pid'] != os.getpgrp() and row['pid'] not in _protected())


def _eligible(pid, row, protected):
    return pid > 1 and pid not in protected and row['uid'] == os.getuid() and not row['state'].startswith('Z')


def _below(pids, table):
    """`pids` and everything below them (by parent), as a set of pids."""
    children = {}
    for row in table.values():
        children.setdefault(row['ppid'], []).append(row['pid'])
    seen, queue = set(pids), list(pids)
    while queue:
        for child in children.get(queue.pop(), []):
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return seen


def collect_members(root_pid, table, known=None, root=None):
    """`{pid: identity}` of `root_pid`, of its descendants and (for a group leader of its own group) of its group, added to `known`. With
    `root` (the recorded identity) the tree is taken only when the process of that number still IS the recorded one. Never this process or
    its ancestors, never another user's process."""
    members = dict(known or {})
    row = table.get(root_pid)
    if row is None or (root is not None and not same_identity(row, root)):
        return members
    seen = _below([root_pid], table)
    if own_group_leader(row):
        seen |= {item['pid'] for item in table.values() if item['pgid'] == root_pid}
    protected = _protected()
    for pid in seen:
        if _eligible(pid, table[pid], protected):
            members.setdefault(pid, identity_of(table[pid]))
    return members


def _alive(members, table):
    return {pid: identity for pid, identity in members.items() if same_identity(table.get(pid), identity)}


def _send(members, sig):
    """`sig` to every member whose identity still matches (the table is read just before)."""
    try:
        table = process_table()
    except RuntimeError:
        return
    for pid in _alive(members, table):
        _signal(pid, sig)


def _wait_gone(root_pid, root, members, seconds):
    """Waits until no member lives; what the recorded root spawned meanwhile is added (only while the root is still the recorded process).
    Returns the living members."""
    deadline = time.time() + seconds
    while True:
        child = CHILDREN.get(root_pid)
        if child is not None:
            child.poll()   # reaps our own child
        try:
            table = process_table()
        except RuntimeError:
            return members
        members = _alive(members, table)
        if root_pid is not None:
            members = _alive(collect_members(root_pid, table, members, root), table)
        if not members or time.time() >= deadline:
            return members
        time.sleep(0.2)


def orphans_of(record, table):
    """What lives of a recorded run when its leader is gone: ONLY the helpers the record lists, each by its own identity (pid, start time,
    uid; never this process or its ancestors, never another user's process). No group is swept: a group number can be reused, and real
    helpers end by themselves when their browser dies."""
    found = {}
    protected = _protected()
    for item in record.get('members') or []:
        row = table.get(item['pid'])
        if item['pid'] > 1 and item['pid'] not in protected and item['uid'] == os.getuid() and same_identity(row, item):
            found[item['pid']] = identity_of(row)
    return found


def stop_tree(root_pid, grace=None, seed=None, root=None):
    """SIGTERM, after `grace` seconds SIGKILL, to the process `root_pid` (None: its leader is gone) and everything below it (listed first),
    plus the `seed` helpers (identities from the record) and what lives below them. `root` is the recorded identity of `root_pid`: the
    tree and the group are taken only while the process of that number still matches it (checked on a fresh table before each use).
    Returns the members that still live afterwards (empty = confirmed dead). A process whose identity changed meanwhile is not signalled."""
    grace = GRACE if grace is None else grace
    try:
        table = process_table()
    except RuntimeError:
        return {root_pid: None}
    if root_pid is not None:
        row = table.get(root_pid)
        if row is None or (root is not None and not same_identity(row, root)):
            root_pid, root = None, None
        elif root is None:
            root = identity_of(row)
    protected = _protected()
    alive_seed = _alive(seed or {}, table)
    members = {pid: identity_of(table[pid]) for pid in _below(list(alive_seed), table) if _eligible(pid, table[pid], protected)}
    if root_pid is not None:
        members = collect_members(root_pid, table, members, root)
    group = root_pid is not None
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, KILL_WAIT)):
        if not members:
            break
        if group:
            try:
                fresh = process_table().get(root_pid)
            except RuntimeError:
                fresh = None
            if fresh is not None and same_identity(fresh, root) and own_group_leader(fresh):
                _signal_group(root_pid, sig)
        _send(members, sig)
        members = _wait_gone(root_pid, root, members, wait)
        group = False   # the group is signalled with the first signal only; later rounds go member by member
    child = CHILDREN.pop(root_pid, None) if root_pid is not None else None
    if child is not None and not members:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    return members


def stop_pid(pid, grace=None):
    """Stops the process `pid` started by this module (and its descendants); returns the survivors."""
    return stop_tree(pid, grace)


def stop_all(env):
    """Stops every process of a PID file in the working directory. A file is removed only when its process is confirmed gone (or never
    was ours: gone, a reused PID, junk); a process that survives keeps its file, is reported on stderr and is returned."""
    workdir = Path(env['workdir'])
    survivors = {}
    for pid_file in sorted(set(workdir.glob('*.pid')) | set(workdir.glob('run-*/*.pid'))):
        if not pid_file.exists():
            continue
        record = read_pid_file(pid_file)
        if record is None:
            pid_file.unlink(missing_ok=True)
            continue
        try:
            table = process_table()
            row = table.get(record['pid'])
        except RuntimeError as error:
            print('env: %s: %s; %s kept' % (pid_file.name, error, pid_file.name), file=sys.stderr)
            survivors[record['pid']] = None
            continue
        if record['pid'] in _protected():   # a record that names this process or one of its ancestors is junk, whatever it says
            print('env: %s: pid %d is this process or one of its ancestors; not signalled, file removed' % (pid_file.name, record['pid']),
                  file=sys.stderr)
            pid_file.unlink(missing_ok=True)
            continue
        leader = is_ours(pid_file, record, row)
        seed = orphans_of(record, table) if record.get('start') is not None else {}
        if not leader and not seed:
            if row is not None and not row['state'].startswith('Z'):   # the number lives, but it is not the process of the record
                print('env: %s: pid %d is alive but is not the recorded process (other start time or user); not signalled, file removed'
                      % (pid_file.name, record['pid']), file=sys.stderr)
            pid_file.unlink(missing_ok=True)   # nothing of ours lives behind it: never signalled
            continue
        if not leader:
            print('env: %s: the leader (pid %d) is gone, stopping %d helper(s) of the record' % (pid_file.name, record['pid'], len(seed)),
                  file=sys.stderr)
        left = stop_tree(record['pid'] if leader else None, seed=seed,
                         root={'pid': record['pid'], 'start': record['start'], 'uid': record['uid']} if leader and record.get('start') is not None else None)
        if left:
            print('env: %s: still alive after SIGKILL: %s; %s kept' % (pid_file.name, ', '.join(str(pid) for pid in sorted(left)), pid_file.name),
                  file=sys.stderr)
            survivors.update(left)
            continue
        pid_file.unlink(missing_ok=True)
    link = workdir / 'chrome-tmp.link'
    if link.exists():
        Path(link.read_text().strip()).unlink(missing_ok=True)
        link.unlink()
    return survivors


if __name__ == '__main__':
    command = sys.argv[1]
    if command == 'prepare':
        prepare(*sys.argv[2:7])
        print('env: %s/env.json' % sys.argv[2])
    elif command == 'chrome':
        print('chrome pid', start_chrome(json.loads(Path(sys.argv[2]).read_text()), sys.argv[3]))
    elif command == 'stop':
        sys.exit(1 if stop_all(json.loads(Path(sys.argv[2]).read_text())) else 0)
    else:
        sys.exit('unknown command')
