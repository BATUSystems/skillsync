"""CORE-2000-REGEX: the secret scans and the URL mask stay linear on a very long word (their input comes from the network), and answer exactly as the
patterns they replace.

Two parts: a time limit on hostile inputs of 100 000 characters, and a differential check on a corpus (fixed cases plus a seeded random one) against
the former patterns, copied here as the reference."""
import random
import re
import time
import unittest

from skillsync_workflow import common, feedback, layers, prototype, secret_words
from skillsync_workflow.common import redact

SIZE = 100_000
# one second at SIZE is the goal on a workstation (measured: under 0.2 s); the limit leaves room for a slow runner. The shapes with a prefix run at
# twice the size, so that a quadratic search shows up even with a small constant.
LIMIT_S = 3.0
PREFIXED = (("-----BEGIN ", "PRIVATE " "KEY"), ("-----BEGIN ", "A "), ("https://", "a:"), ("http://", ":"), ("ssh://", "a:"), ("a://", "a:@"),
            ("://", "a"), ("token", "a."), ("secret_key", "-a"), ("_pat", "."), ("pwd", "-pwd"), ("eyJ", "-eyJ"), ("eyJ", "."), ("https://h/?", "a?"),
            ("Authorization: ", "a "), ("bearer ", " "), ("password=", chr(92)), ('token="', chr(92) * 2))

HOSTILE = ("a", "a.", "a-", "a+", "_pat", "-eyJ", "a:", "x-pwd", "-sk-", " ", "bearer ", "-xoxb-", ".ghp_", "-npm_", "-----BEGIN ", "a2",
           "authorization ", "https://a:", "sig=", "token", "secret.", "_pat.", "a://", "?a", "pwd", "-token-", ".eyJabcdefghij.", "eyJ-",
           "password ", "_pat ", "x=pat_pat=", "secret_", "key-")

# the former patterns (v2.0.0 before CORE-2000-REGEX)
OLD_USERINFO = re.compile(r'(?i)\b([a-z][a-z0-9+.-]*://)[^/\s]*@')
OLD_URL_PASSWORD = re.compile(r'(?i:[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@)')
OLD_JWT = re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}')
WORDS = r'token|secret|password|passwd|credential'
KEYS = r'api[-_]?key|access[-_]?key|private[-_]?key|secret[-_]?key'
OLD_FEEDBACK_NAME = re.compile(rf'(?i)(?<![a-z0-9])[\w.-]*(?:{WORDS}|{KEYS})[\w.-]*["\']?\s*[:=]\s*["\']?[^\s,;"\'&<>]{{8,}}')
OLD_FEEDBACK_PAT = re.compile(r'(?i)(?<![a-z0-9])[\w.-]*_pat(?!h|tern|ch)[\w.-]*(["\']?\s*[:=]\s*)(?=["\']?[^\s,;"\'&<>]{8})')
OLD_PAIR = re.compile(rf'(?i:\b[\w-]{{0,40}}(?:{WORDS}|{KEYS}|private|apikey|passw(?:or)?d|accountkey|sharedaccesskey)'
                      r'[\w-]{0,40}["\']?\s*[=:]\s*["\']?\S{6,})')
OLD_PWD = re.compile(r'(?i)\b([\w-]{0,40}pwd[\w-]{0,40})(["\']?\s*[=:]\s*)(?=["\']?\S{6})')
OLD_PRIVATE_KEY = re.compile(r'-----BEGIN [A-Z ]*PRIVATE ' r'KEY[A-Z ]*-----')
OLD_URL_CREDENTIALS = re.compile(r'(https?://)[^/@\s]+:[^/@\s]+@')
KEY_NAMES = ("token", "secret", "password", "passwd", "credential", "apikey", "api_key", "api-key", "accesskey", "access_key", "privatekey",
             "private-key", "secretkey", "secret_key", "secret-key", "private", "passwrd", "accountkey", "sharedaccesskey", "pwd")

PIECES = ("a", "B", "1", "_", "-", ".", "+", ":", "/", "//", "://", "@", " ", "  ", "\n", "=", "\"", "'", "?", "&", ",", ";", "é", "ſ",
          "http", "https", "ssh", "token", "secret", "password", "passwd", "key", "api_key", "access-key", "private", "apikey", "pwd", "PWD",
          "_pat", "_PAT", "_path", "_patch", "pat", "eyJ", "abcdefghij", "ABCDE", "zzzzz", "$(", "/ho" "me/x", "OLD_", "re.compile(", ")", "[", "]",
          "x" * 12, "q" * 38, "w" * 41,
          "-----BEGIN ", "-----", "PRIVATE " "KEY", "RSA ", "secret_key", "secret-key", "_key", "-key")


def threshold_cases():
    """Every word of the pair scans with 38-42 characters of the name before it and 35-45 after it, around the limit of 40."""
    for word in KEY_NAMES:
        for after in range(35, 46):
            yield word + "x" * after + "=abcdef"
            yield word + "_" + "x" * after + ": 'abcdef"
        for before in range(38, 43):
            yield "x" * before + word + "=abcdef"
            yield "x" * before + "-" + word + "=abcdef"
            yield "x" * before + word + "y" * 40 + "=abcdef"


def corpus():
    yield from threshold_cases()
    yield from ("https://u:p@h/x", "x https://user@host?q=1", "1https://u@h", "_a://u@h", "+a://u@h", "a://@@//b@c", "see git+ssh://k@h and a://x:y@z",
                "eyJ" + "a" * 10 + "." + "b" * 10 + "." + "c" * 5, "x-eyJ" + "a" * 10 + "." + "b" * 10 + "." + "c" * 5,
                "x_eyJ" + "a" * 10 + "." + "b" * 10 + "." + "c" * 5, "eyJ" + "a" * 9 + "." + "b" * 10 + "." + "c" * 5,
                "client_secret=abcdefgh", "a.b-token.c = 'abcdefgh'", "\"token\": \"abcdefgh\"", "token=short", "my_pat_ro:\n  abcdefgh",
                "url_pat = re.compile(zz)", "x_path=abcdefgh", "self.mypwd = f(x)", "OLD_PWD=/srv/app", "a" * 40 + "token=abcdef",
                "a" * 41 + "token=abcdef", "-----BEGIN RSA PRIVATE " "KEY-----", "-----BEGIN PRIVATE " "KEY", "-----BEGIN PRIVATE " "KEY BLOCK-----",
                "-----BEGIN A-----BEGIN PRIVATE " "KEY-----", "-----BEGIN  PRIVATE " "KEY ----", "http://a:b@h", "https://:a@h", "https://a:@h",
                "https://a::@h", "https://::a@h", "DJANGO_SECRET_KEY_PRODUCTION_EU_WEST_PRIMARY_V2_BACKUP=abc123",
                "token" + "a" * 40 + "=abcdef", "token" + "a" * 41 + "=abcdef", "a" * 30 + "-" + "a" * 20 + "pwd=abcdef")
    source = random.Random(20261127)
    for _number in range(60_000):
        yield "".join(source.choice(PIECES) for _piece in range(source.randint(1, 14)))


class SameAnswers(unittest.TestCase):
    def test_every_replaced_pattern_answers_as_before(self):
        harmless = secret_words.harmless_assignment
        checks = {   # name: (former answer, new answer); each compared on its own, so that one cannot cover for another
            "userinfo": (lambda t: OLD_USERINFO.sub(r"\1***@", t), secret_words.mask_url_userinfo),
            "url-password": (lambda t: bool(OLD_URL_PASSWORD.search(t)), secret_words.has_url_password),
            "jwt": (lambda t: bool(OLD_JWT.search(t)), secret_words.has_jwt),
            "private-key": (lambda t: bool(OLD_PRIVATE_KEY.search(t)), secret_words.has_private_key),
            "url-credentials": (lambda t: OLD_URL_CREDENTIALS.sub(r"\1[REDACTED]@", t), lambda t: common._URL_CREDENTIALS.sub(r"\1[REDACTED]@", t)),
            "feedback-name": (lambda t: bool(OLD_FEEDBACK_NAME.search(t)), feedback._has_secret_name),
            "feedback-pat": (lambda t: any(not harmless("", f.group(1), t, f.end()) for f in OLD_FEEDBACK_PAT.finditer(t)), feedback._has_pat_name),
            "prototype-pair": (lambda t: bool(OLD_PAIR.search(t)), prototype._has_pair),
            "prototype-pwd": (lambda t: any(not harmless(f.group(1), f.group(2), t, f.end()) for f in OLD_PWD.finditer(t)), prototype._has_pwd_pair),
        }
        changed = dict.fromkeys(checks, 0)
        for text in corpus():
            for name, (former, new) in checks.items():
                expected = former(text)
                if new(text) != expected:
                    self.fail(f"{name}: {text!r}: former {expected!r}")
                changed[name] += bool(expected) and expected != text
        for name, count in changed.items():   # the corpus reaches the positive answer of every check
            with self.subTest(positive=name):
                self.assertGreaterEqual(count, 2)


class LinearTime(unittest.TestCase):
    def assert_fast(self, call):
        for unit in HOSTILE:
            text = (unit * (SIZE // len(unit) + 1))[:SIZE]
            for tail in ("", "=abcdefghij", "@"):
                with self.subTest(unit=unit, tail=tail):
                    started = time.perf_counter()
                    call(text + tail)
                    self.assertLess(time.perf_counter() - started, LIMIT_S)
        for prefix, unit in PREFIXED:
            text = prefix + unit * (2 * SIZE // len(unit))
            with self.subTest(prefix=prefix, unit=unit):
                started = time.perf_counter()
                call(text)
                self.assertLess(time.perf_counter() - started, LIMIT_S)

    def test_the_prototype_scan(self):
        self.assert_fast(prototype._has_secret)

    def test_the_feedback_gate(self):
        self.assert_fast(lambda text: feedback.secret_findings([("body", text)]))

    def test_the_url_mask_of_layers(self):
        self.assert_fast(layers._mask_credentials)

    def test_the_mask_of_reports(self):
        self.assert_fast(redact)


if __name__ == "__main__":
    unittest.main()
