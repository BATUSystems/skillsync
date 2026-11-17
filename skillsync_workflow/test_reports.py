"""Parsers of test run reports (JUnit XML and VSTest TRX) into one flat result shape.

Hardened for untrusted input: DTDs and entities are rejected before parsing, the input size and the number of tests are capped,
and every parser failure surfaces as a short `ValueError` (never the file content)."""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .common import decode_text

MAX_BYTES = 32 * 1024 * 1024
MAX_TESTS = 100_000
MAX_MSG = 500
_FORBIDDEN = re.compile(r'<!\s*(doctype|entity)', re.I)
_ILLEGAL = re.compile('[\x01-\x08\x0b\x0c\x0e-\x1f￾￿]')


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


def _load(source):
    """Root element of an XML document given as a path, bytes or str; raises ValueError on anything unsafe or malformed."""
    if isinstance(source, Path):
        try:
            if source.stat().st_size > MAX_BYTES:
                raise ValueError('report too large')
            data = source.read_bytes()
        except OSError:
            raise ValueError('report not readable') from None
    elif isinstance(source, str):
        data = source.encode('utf-8', errors='replace')
    elif isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    else:
        raise ValueError('unsupported report source')
    if len(data) > MAX_BYTES:
        raise ValueError('report too large')
    if not data.strip():
        raise ValueError('empty report')
    text = decode_text(data).lstrip('﻿')
    # Checked on the raw bytes too: the decoded text drops NULs, the raw form catches UTF-16 without a BOM.
    if _FORBIDDEN.search(text) or _FORBIDDEN.search(data.replace(b'\x00', b'').decode('latin-1')):
        raise ValueError('DOCTYPE/ENTITY declarations are not allowed')
    text = _ILLEGAL.sub('', text)
    try:
        return ET.fromstring(text)
    except (ET.ParseError, ValueError, RecursionError):
        raise ValueError('invalid XML report') from None


def _short(*parts):
    msg = '\n'.join(p.strip() for p in parts if p and p.strip())
    if not msg:
        return None
    return msg if len(msg) <= MAX_MSG else msg[:MAX_MSG] + '...'


def _result(name, group, state, ms, msg):
    return {'id': f'{group}::{name}' if group else name, 'name': name, 'group': group, 'state': state, 'ms': ms, 'msg': msg}


def _finish(results, truncated, with_info):
    return (results, {'truncated': truncated}) if with_info else results


def parse_junit(source, with_info=False):
    """Results of a JUnit XML report (any `testsuite` nesting); `with_info=True` returns `(results, {'truncated': bool})`."""
    root = _load(source)
    results, truncated = [], False
    for case in root.iter():
        if _local(case.tag) != 'testcase':
            continue
        if len(results) >= MAX_TESTS:
            truncated = True
            break
        state, msg = 'passed', None
        for child in case:
            kind = _local(child.tag)
            if kind in ('failure', 'error'):
                state, msg = 'failed', _short(child.get('message'), child.text)
                break
            if kind == 'skipped' and state == 'passed':
                state, msg = 'skipped', _short(child.get('message'))
        if state != 'failed' and case.get('failure') is not None:
            state, msg = 'failed', _short(case.get('failure'))
        try:
            ms = round(float(case.get('time')) * 1000)
        except (TypeError, ValueError, OverflowError):
            ms = None
        results.append(_result(case.get('name') or '', case.get('classname') or None, state, ms, msg))
    return _finish(results, truncated, with_info)


def _duration_ms(value):
    m = re.fullmatch(r'(?:(\d+)\.)?(\d+):(\d+):(\d+(?:\.\d+)?)', (value or '').strip())
    if not m:
        return None
    days, h, mi, s = m.groups()
    return round(((int(days or 0) * 24 + int(h)) * 3600 + int(mi) * 60 + float(s)) * 1000)


def parse_trx(source, with_info=False):
    """Results of a VSTest TRX report; `with_info=True` returns `(results, {'truncated': bool})`."""
    root = _load(source)
    classes = {}
    for unit in root.iter():
        if _local(unit.tag) == 'UnitTest' and unit.get('id'):
            for child in unit:
                if _local(child.tag) == 'TestMethod':
                    classes[unit.get('id')] = child.get('className') or None
    results, truncated = [], False
    for res in root.iter():
        if _local(res.tag) != 'UnitTestResult':
            continue
        if len(results) >= MAX_TESTS:
            truncated = True
            break
        outcome = (res.get('outcome') or '').strip().lower()
        state = 'passed' if outcome == 'passed' else 'skipped' if outcome in ('notexecuted', 'skipped') else 'failed'
        msg = None
        for el in res.iter():
            if _local(el.tag) == 'Message':
                msg = _short(el.text)
                break
        results.append(_result(res.get('testName') or '', classes.get(res.get('testId')), state, _duration_ms(res.get('duration')), msg))
    return _finish(results, truncated, with_info)


def parse_any(path, with_info=False):
    """Parses a report file with the parser matching its root element (`TestRun` = TRX, `testsuite(s)` = JUnit)."""
    path = Path(path)
    kind = _local(_load(path).tag)
    if kind == 'TestRun':
        return parse_trx(path, with_info)
    if kind in ('testsuites', 'testsuite'):
        return parse_junit(path, with_info)
    raise ValueError('unrecognized report format')
