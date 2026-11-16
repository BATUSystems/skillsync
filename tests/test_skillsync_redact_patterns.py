"""`common.redact`: JSON pairs, more key names, `Authorization: Basic`, multi-line environment secrets with Windows line ends (161-F, F-3).
What is NOT masked (bare token formats, variables whose names do not say so, values shorter than 8 characters) stays out of this package."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillsync_workflow.common import redact  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
