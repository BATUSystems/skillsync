"""`common.redact`: JSON pairs, more key names, `Authorization: Basic`, multi-line environment secrets with Windows line ends (161-F, F-3).
What is NOT masked (bare token formats, variables whose names do not say so, values shorter than 8 characters) stays out of this package."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillsync_workflow.common import cut_whole, redact  # noqa: E402

VALUE = "hunter2hunter2"


class KeyValuePairs(unittest.TestCase):
    def test_json_pairs_keep_the_quotes_and_lose_the_value(self) -> None:
        self.assertEqual(redact(f'{{"password": "{VALUE}", "n": 1}}'), '{"password": "[REDACTED]", "n": 1}')
        self.assertEqual(redact(f"{{'token':'{VALUE}'}}"), "{'token':'[REDACTED]'}")
        self.assertEqual(redact(f'"api_key" : "{VALUE} with spaces"'), '"api_key" : "[REDACTED]"')   # a quoted value is masked whole
        self.assertEqual(redact(f'password="{VALUE}"'), 'password="[REDACTED]"')

    def test_more_key_names(self) -> None:
        for key in ("secret_key", "secret-key", "api-key", "apikey", "API_KEY", "access_key", "private-key", "client_secret", "x_auth_token"):
            for sep in (": ", "=", " = "):
                with self.subTest(key=key, sep=sep):
                    self.assertEqual(redact(f"{key}{sep}{VALUE}"), f"{key}{sep}[REDACTED]")

    def test_authorization_basic_and_digest(self) -> None:
        self.assertEqual(redact("Authorization: Basic dXNlcjpwYXNzd29yZA=="), "Authorization: Basic [REDACTED]")
        self.assertEqual(redact("authorization=Digest abcdef"), "authorization=Digest [REDACTED]")
        self.assertEqual(redact("Authorization: Bearer abcdefgh1234"), "Authorization: Bearer [REDACTED]")

    def test_quotes_inside_a_value_and_an_unterminated_quote(self) -> None:
        self.assertEqual(redact(f'password: ab"{VALUE} rest'), "password: [REDACTED] rest")   # a quote inside a bare value belongs to it
        self.assertEqual(redact(f'token="{VALUE} and more\nnext line'), 'token="[REDACTED]\nnext line')   # no closing quote: the rest of the line, none added
        self.assertEqual(redact(f'secret=\'{VALUE}\' x'), "secret='[REDACTED]' x")

    def test_an_unterminated_quote_masks_the_rest_of_the_line_with_lf_and_crlf_line_ends(self) -> None:
        for quote in ('"', "'"):
            for end, name in (("\n", "LF"), ("\r\n", "CRLF"), ("", "end of text")):
                with self.subTest(quote=quote, ends=name):
                    following = "next line" if end else ""
                    masked = redact(f"password: {quote}{VALUE} tail{end}{following}")
                    self.assertNotIn(VALUE, masked)
                    self.assertNotIn("tail", masked)
                    self.assertEqual(masked, f"password: {quote}[REDACTED]{end}{following}")   # no closing quote added, the line end stays as it was
                masked = redact(f"x\r\nsecret={quote}{VALUE}")
                self.assertEqual(masked, f"x\r\nsecret={quote}[REDACTED]")

    def test_a_backslash_escapes_a_quote_in_a_quoted_value(self) -> None:
        self.assertEqual(redact('{"password": "ab\\"' + VALUE + '", "n": 1}'), '{"password": "[REDACTED]", "n": 1}')   # \" stays in the value
        self.assertEqual(redact("{'token': 'ab\\'" + VALUE + "'}"), "{'token': '[REDACTED]'}")
        closed = redact('{"password": "ab\\\\"} ' + VALUE)   # \\" : the backslash is escaped, the quote closes the value
        self.assertEqual(closed, '{"password": "[REDACTED]"} ' + VALUE)
        self.assertEqual(redact('password: "' + VALUE + '\\"'), 'password: "[REDACTED]')   # the last quote is escaped: still unterminated, no closing quote added

    def test_a_plain_sentence_is_not_changed_more_than_before(self) -> None:
        text = "the token is used: later, we rotate it; fine"
        self.assertEqual(redact(text), text)
        self.assertEqual(redact("a secret: later, we rotate it; fine"), "a secret: [REDACTED], we rotate it; fine")   # a bare value stops at `,` as before

    def test_masking_stays_linear_on_hostile_input(self) -> None:
        import time
        size = 1_000_000
        for name, text in (("unterminated quote", 'password: "' + "a" * size), ("backslashes", 'password: "' + "\\" * size),
                           ("escaped quotes", '{"password": "' + 'ab\\"' * (size // 4) + '"}'), ("many unterminated lines", ('token: "' + "a" * 60 + "\r\n") * (size // 68))):
            with self.subTest(name):
                started = time.perf_counter()
                redact(text)
                self.assertLess(time.perf_counter() - started, 5.0)   # about 0.1-0.3 s here: far from a quadratic blow-up

    def test_unchanged_as_before(self) -> None:
        for text in ("tokens are fine", "my token is here", "basic setup of the secret santa", "password", "token:", "# Plan\n\nStatus: draft\n",
                     "api keys are rotated", "private key rotation: yearly"):
            with self.subTest(text=text):
                self.assertEqual(redact(text), text)
        self.assertEqual(redact(f"password: {VALUE} rest"), "password: [REDACTED] rest")   # a bare value stops at the first space, as before
        self.assertEqual(redact("https://user:pw@example.com/x"), "https://[REDACTED]@example.com/x")

    def test_masking_twice_changes_nothing(self) -> None:
        once = redact(f'{{"password": "{VALUE}"}} api-key: {VALUE} Authorization: Basic abcdef')
        self.assertEqual(redact(once), once)


class MultiLineEnvironmentSecret(unittest.TestCase):
    SECRET = "-----BEGIN KEY-----\nabcdefghijklmnop\n-----END KEY-----"

    def test_lf_and_crlf_files_are_masked(self) -> None:
        with mock.patch.dict(os.environ, {"DEPLOY_PRIVATE_TOKEN": self.SECRET}):
            for name, text in (("lf", f"przed\n{self.SECRET}\npo"), ("crlf", f"przed\r\n{self.SECRET.replace(chr(10), chr(13) + chr(10))}\r\npo")):
                with self.subTest(name):
                    masked = redact(text)
                    self.assertNotIn("abcdefghijklmnop", masked)
                    self.assertNotIn("BEGIN KEY", masked)
                    self.assertIn("[REDACTED]", masked)
                    self.assertTrue(masked.startswith("przed") and masked.rstrip().endswith("po"))


class EnvironmentReadOnce(unittest.TestCase):
    """180-A, F5.2: a tree of thousands of strings (the `tests` of the status) reads the environment once per call, not once per string."""

    def test_a_tree_reads_the_environment_once_and_masks_the_same(self) -> None:
        from skillsync_workflow import common
        secret = "-".join(["zq9x", "wv8y", "ut7s"])
        tree = {"a": [f"x {secret} y"] * 50, "b": {"c": f"token={VALUE}"}, "n": 3}
        with mock.patch.dict(os.environ, {"DEPLOY_TOKEN": secret}), mock.patch.object(common, "_environment_secrets", wraps=common._environment_secrets) as spy:
            masked = redact(tree)
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(masked["a"][49], "x [REDACTED] y")
        self.assertEqual(masked["b"]["c"], "token=[REDACTED]")
        self.assertEqual(masked["n"], 3)

    def test_a_change_of_the_environment_is_seen_by_the_next_call(self) -> None:
        secret = "-".join(["qw1e", "rt2y", "ui3o"])
        self.assertIn(secret, redact(secret))
        with mock.patch.dict(os.environ, {"DEPLOY_SECRET": secret}):
            self.assertEqual(redact(secret), "[REDACTED]")
        self.assertIn(secret, redact(secret))


class CutWhole(unittest.TestCase):
    """CORE-2000 U-3b-3 (O3): `common.cut_whole` is `text[:limit]` that never cuts through an e-mail address (a piece would pass a later mask)."""

    ADDRESS = "jan.kowalski@poczta.example.com"

    def test_an_address_across_the_limit_is_dropped_whole(self) -> None:
        text = f"napisz do {self.ADDRESS} dzisiaj"
        start = text.index(self.ADDRESS)
        for limit in range(start + 1, start + len(self.ADDRESS)):
            with self.subTest(limit=limit):
                self.assertEqual(cut_whole(text, limit), "napisz do")
        self.assertEqual(cut_whole(self.ADDRESS, 5), "")

    def test_everything_else_is_the_plain_cut(self) -> None:
        text = f"napisz do {self.ADDRESS} dzisiaj"
        start, end = text.index(self.ADDRESS), text.index(self.ADDRESS) + len(self.ADDRESS)
        for limit in (0, start - 1, start, end, end + 3, len(text), len(text) + 5):
            with self.subTest(limit=limit):
                self.assertEqual(cut_whole(text, limit), text[:limit])
        plain = "zwykły tekst bez adresu, tylko @dekorator i a @ b"
        for limit in range(len(plain) + 1):
            self.assertEqual(cut_whole(plain, limit), plain[:limit])

    def test_the_second_address_of_a_text_is_protected_too(self) -> None:
        text = f"{self.ADDRESS} oraz anna@example.org"
        self.assertEqual(cut_whole(text, text.index("anna") + 6), f"{self.ADDRESS} oraz")


if __name__ == "__main__":
    unittest.main()
