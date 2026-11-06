#!/usr/bin/env python3
"""Environment of the fixed prototype E2E suite: project, accounts, test pages, headless Chrome.

Used by `tests/test_skillsync_prototype_e2e.py` and runnable by hand:

    python3 env.py prepare  <workdir> <library> <chrome-port> <port1> <port2>   # writes <workdir>/env.json
    python3 env.py chrome   <workdir>/env.json <chrome-binary>                  # starts Chrome, writes chrome.pid
    python3 env.py stop     <workdir>/env.json                                  # stops Chrome and servers by PID

Passwords of the test accounts are generated here, written to `secrets.json` (mode 0600) in the
private working directory and never printed. Servers and Chrome are stopped by PID, never by name.
"""
import json
import os
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
        handle.write('layers:\n  - {id: core, kind: core, path: "%s"}\n' % core)
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
    child = subprocess.Popen([binary, '--headless=new', '--remote-debugging-port=%d' % port, '--remote-debugging-address=127.0.0.1',
                              '--user-data-dir=%s' % profile, '--no-first-run', '--no-default-browser-check', '--disable-extensions',
                              '--disable-gpu', '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
                              '--disable-backgrounding-occluded-windows', '--window-size=1440,900', 'about:blank'],
                             stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True, env=child_env)
    log.close()   # the child has its own copy of the descriptor
    CHILDREN[child.pid] = child
    (workdir / 'chrome.pid').write_text(str(child.pid))
    if not wait_port(port):
        stop_pid(child.pid)
        raise RuntimeError('Chrome did not open the debugging port %d' % port)
    return child.pid


CHILDREN = {}   # Popen objects of the processes started here (waited for after the stop, no zombie or ResourceWarning)


def stop_pid(pid, grace=6, group=False):
    """SIGTERM, then SIGKILL after `grace` seconds; `group=True` signals the whole process group (Chrome starts in its own session).
    A child started by this module is waited for, so no zombie remains."""
    send = (lambda sig: os.killpg(pid, sig)) if group else (lambda sig: os.kill(pid, sig))
    child = CHILDREN.pop(pid, None)
    try:
        send(signal.SIGTERM)
    except ProcessLookupError:
        if child:
            child.wait()
        return
    deadline = time.time() + grace
    while time.time() < deadline:
        if child:
            if child.poll() is not None:
                return
        else:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
        time.sleep(0.2)
    try:
        send(signal.SIGKILL)
    except ProcessLookupError:
        pass
    if child:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


# PID file -> text that must occur in /proc/<pid>/cmdline (a stale file whose PID was reused is not killed)
EXPECTED_CMDLINE = (('chrome', '--remote-debugging-port'), ('static-', 'http.server'), ('serve-', 'serve'))


def is_ours(pid_file, pid):
    try:
        cmdline = Path('/proc/%d/cmdline' % pid).read_bytes().replace(b'\0', b' ').decode('utf-8', 'replace')
    except OSError:
        return False
    for prefix, needle in EXPECTED_CMDLINE:
        if pid_file.name.startswith(prefix):
            return needle in cmdline
    return False


def stop_all(env):
    workdir = Path(env['workdir'])
    for pid_file in sorted(set(workdir.glob('*.pid')) | set(workdir.glob('run-*/*.pid'))):
        if not pid_file.exists():
            continue
        try:
            pid = int(pid_file.read_text().strip())
            if is_ours(pid_file, pid):
                stop_pid(pid, group=pid_file.name.startswith('chrome'))
        except (ValueError, OSError):
            pass
        pid_file.unlink(missing_ok=True)
    link = workdir / 'chrome-tmp.link'
    if link.exists():
        Path(link.read_text().strip()).unlink(missing_ok=True)
        link.unlink()


if __name__ == '__main__':
    command = sys.argv[1]
    if command == 'prepare':
        prepare(*sys.argv[2:7])
        print('env: %s/env.json' % sys.argv[2])
    elif command == 'chrome':
        print('chrome pid', start_chrome(json.loads(Path(sys.argv[2]).read_text()), sys.argv[3]))
    elif command == 'stop':
        stop_all(json.loads(Path(sys.argv[2]).read_text()))
    else:
        sys.exit('unknown command')
