#!/usr/bin/env python3
"""`skillsync prototype accept` through a pseudo-terminal (the password is read from /dev/tty).

Usage: python3 -I accept_cli.py <env.json> <account-key> <accept args...>
The password comes from the 0600 secrets file of the environment and is never printed; the output is
scrubbed from it. Prints the CLI output and a final line `[pty] prompt_sent=<bool> exit=<code>`.
"""
import json
import os
import pty
import select
import signal
import sys
import time

env_path, key, *args = sys.argv[1:]
env = json.load(open(env_path, encoding='utf-8'))
password = json.load(open(env['secrets'], encoding='utf-8'))[key]
cmd = [sys.executable, '-I', env['cli'], 'prototype', 'accept', *args]
pid, fd = pty.fork()
if pid == 0:
    os.environ['SKILLSYNC_CONFIG'] = env['config']
    os.execv(sys.executable, cmd)
out = b''
sent = False
while True:
    ready, _, _ = select.select([fd], [], [], 20)
    if not ready:
        break
    try:
        chunk = os.read(fd, 4096)
    except OSError:
        break
    if not chunk:
        break
    out += chunk
    if not sent and b':' in out and (b'assw' in out or b'Has' in out):
        os.write(fd, password.encode() + b'\n')
        sent = True
deadline = time.time() + 30
while True:
    done, status = os.waitpid(pid, os.WNOHANG)
    if done:
        break
    if time.time() > deadline:   # the CLI did not exit: stop it instead of waiting forever
        os.kill(pid, signal.SIGKILL)
        _, status = os.waitpid(pid, 0)
        break
    time.sleep(0.1)
text = out.decode('utf-8', 'replace').replace(password, '<redacted>')
sys.stdout.write(text)
sys.stdout.write('\n[pty] prompt_sent=%s exit=%d\n' % (sent, os.waitstatus_to_exitcode(status)))
