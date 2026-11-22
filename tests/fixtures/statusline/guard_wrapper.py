"""Test wrapper for the status line script: runs it with processes and sockets blocked and file opens counted.

Usage: python guard_wrapper.py <script>   (stdin, stdout and the exit code are the script's own)
Environment: SL_ROOT = the project fixture directory (only opens under it are recorded),
             SL_LOG  = JSON file written at exit: {"opened": [paths], "blocked": [names]}.

A blocked call is recorded in the log AND raises `Blocked` (a BaseException, so `except Exception` in the script does not hide it);
the test asserts on the log, so even a script that swallows everything cannot pass by catching the error.
"""
import builtins
import io
import json
import os
import runpy
import socket
import subprocess
import sys

ROOT = os.path.realpath(os.environ['SL_ROOT'])
LOG = os.environ['SL_LOG']
EVENTS = {'opened': [], 'blocked': []}


class Blocked(BaseException):
    pass


def _block(name):
    def refuse(*args, **kwargs):
        EVENTS['blocked'].append(name)
        raise Blocked(name)
    return refuse


def _counting(real):
    def counted(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            path = os.path.abspath(os.fsdecode(file))
            if path == ROOT or path.startswith(ROOT + os.sep):
                EVENTS['opened'].append(path)
        return real(file, *args, **kwargs)
    return counted


for _name in ('Popen', 'run', 'call', 'check_call', 'check_output', 'getoutput', 'getstatusoutput'):
    setattr(subprocess, _name, _block('subprocess.' + _name))
for _name in ('system', 'popen', 'fork', 'forkpty', 'posix_spawn', 'posix_spawnp', 'startfile',
              'execv', 'execve', 'execvp', 'execvpe', 'execl', 'execle', 'execlp', 'execlpe',
              'spawnv', 'spawnve', 'spawnvp', 'spawnvpe', 'spawnl', 'spawnle', 'spawnlp', 'spawnlpe'):
    if hasattr(os, _name):
        setattr(os, _name, _block('os.' + _name))
for _name in ('socket', 'create_connection', 'create_server', 'getaddrinfo', 'gethostbyname', 'socketpair', 'fromfd'):
    if hasattr(socket, _name):
        setattr(socket, _name, _block('socket.' + _name))
builtins.open = _counting(builtins.open)
io.open = _counting(io.open)
os.open = _counting(os.open)

try:
    runpy.run_path(sys.argv[1], run_name='__main__')
finally:
    sys.stdout.flush()
    with open(LOG, 'w', encoding='utf-8') as handle:
        json.dump(EVENTS, handle)
