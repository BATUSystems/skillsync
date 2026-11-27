"""CORE-1200-MASK2: the short secret names `pat` and `pwd` in every mask of the generator (text pairs, report keys of `delivery` and `e2e`, flags of a test
command line), one shared definition of the secret words, and an unterminated quoted value that ends with a backslash just before the end of the line.
The working directory (`PWD=/...`) and names that only contain the letters (`PATH`, `compat`, `--pattern`) stay readable."""
import os
import unittest
from unittest import mock

from skillsync_workflow import delivery, e2e, test_events
from skillsync_workflow.common import redact, secret_key_name

VALUE = "abc" + "def" + str(123456)   # put together at run time: no literal looks like a key
BS = chr(92)


class TextPairs(unittest.TestCase):
    def test_pat_and_pwd_pairs_are_masked(self):
        for before in ("pat=", "pwd=", "PAT=", "Pwd=", "pat = ", "GITHUB_PAT=", "DB_PWD=", "github-pat=", "--pat=", "--db-pwd=", "Server=x;Uid=sa;Pwd=",
                       "Server=x;UID=sa;PWD=", '{"pat": "', "{'pwd': '", "DB_PWD: ", "azure_pat: ", "db-pwd:", "export PAT="):
            with self.subTest(before=before):
                masked = redact(before + VALUE)
                self.assertNotIn(VALUE, masked)
                self.assertIn("[REDACTED]", masked)

    def test_the_result_is_stable(self):
        once = redact("pat=" + VALUE + " pwd=" + VALUE)
        self.assertEqual(once, "pat=[REDACTED] pwd=[REDACTED]")
        self.assertEqual(redact(once), once)

    def test_the_working_directory_stays_readable(self):
        for text in ('docker run -v "$PWD":/app img', "-v '$PWD':/app", 'docker run -v "${PWD}":/app', '{"PWD": "/home/user"}', "PWD = /home/user",
                     "PWD=$(pwd)", "PWD=${PWD}", "PWD=.", "PWD=./sub", 'if pat == other or url_pat == other:', '"pat": {', '"pwd": [',
                     "PWD=/home/user/project", 'PWD="/srv/app"', "PWD=~/project", "PWD=C:" + BS + "work", "PWD=" + BS + BS + "host" + BS + "share",
                     "OLDPWD=/home/user", "env: PWD=/tmp/x HOME=/home/user", "echo $PWD"):
            with self.subTest(text=text):
                self.assertEqual(redact(text), text)

    def test_names_that_only_contain_the_letters_stay_readable(self):
        for text in ("PATH=/usr/bin:/bin", "compat=strict", "repat=1234", "pattern=abc", "patch: 3", "spat = 2", "_pat = re.compile(x)", "if pat == other:",
                     "PAT: zob. dokumentacja", "pwd: print the working directory", "UPWD=abc", "pat, pwd", "UI_COMPAT=full"):
            with self.subTest(text=text):
                self.assertEqual(redact(text), text)


class TheShortKeyDoesNotHideTheNextOne(unittest.TestCase):
    """A short key with an empty value must not take the NAME of the next key as its value and leave that secret in the open."""

    def test_the_secret_of_the_following_key_is_masked(self):
        for text in ("pat= token: " + VALUE, "pwd=" + "\n" + "password: " + VALUE, "DB_PWD= token=" + VALUE, '{"pat": {"token": "' + VALUE + '"}}',
                     '{"pwd": [{"password": "' + VALUE + '"}]}', "pat=token=" + VALUE):
            with self.subTest(text=text):
                self.assertNotIn(VALUE, redact(text))

    def test_a_value_on_the_next_line_is_not_taken(self):
        self.assertEqual(redact("pat=" + "\n" + "next line"), "pat=" + "\n" + "next line")

    def test_a_braced_password_of_a_connection_string_is_masked(self):
        for text in ("Uid=sa;Pwd={" + VALUE + "}", "pat=[" + VALUE):
            with self.subTest(text=text):
                self.assertNotIn(VALUE, redact(text))


class UnterminatedQuote(unittest.TestCase):
    def test_a_backslash_just_before_the_line_end_does_not_stop_the_mask(self):
        for quote in ('"', "'"):
            for end in ("\n", "\r\n", "\r", ""):
                with self.subTest(quote=quote, end=end):
                    tail = end + "next line" if end else ""
                    self.assertEqual(redact("password=" + quote + VALUE + BS + tail), "password=" + quote + "[REDACTED]" + tail)

    def test_several_backslashes(self):
        for count in (1, 2, 3, 4):
            with self.subTest(count=count):
                masked = redact('password="' + VALUE + BS * count + "\nnext")
                self.assertEqual(masked, 'password="[REDACTED]\nnext')

    def test_escapes_inside_a_closed_value_are_as_before(self):
        self.assertEqual(redact('token="a' + BS + '"' + VALUE + '" rest'), 'token="[REDACTED]" rest')
        self.assertEqual(redact('token="' + VALUE + BS + BS + '" rest'), 'token="[REDACTED]" rest')

    def test_a_long_run_of_backslashes_is_linear(self):
        redact('password="' + BS * 200_001 + "\n")   # would not finish in time with a backtracking pattern


class ReportKeys(unittest.TestCase):
    SECRET = ("pat", "pwd", "PAT", "Pwd", "github_pat", "GITHUB_PAT", "db-pwd", "azure.pat", "PAT_TOKEN_X", "personal pat", "pwd_prod", "token", "password",
              "passwd", "client_secret", "apiKey", "api-key", "api_key", "credentials", "Authorization")
    PLAIN = ("path", "PATH", "PWD", "OLDPWD", "compat", "pattern", "patch", "spatial", "name", "paths", "api key")

    def test_one_definition_decides(self):
        for key in self.SECRET:
            with self.subTest(key=key):
                self.assertTrue(secret_key_name(key))
        for key in self.PLAIN:
            with self.subTest(key=key):
                self.assertFalse(secret_key_name(key))

    def test_delivery_and_e2e_mask_the_values_of_those_keys(self):
        for scrub in (delivery._scrub, e2e._step_redact):
            for key in self.SECRET:
                with self.subTest(scrub=scrub.__name__, key=key):
                    self.assertEqual(scrub({"outer": [{key: VALUE}]}), {"outer": [{key: "[REDACTED]"}]})
            for key in self.PLAIN:
                with self.subTest(scrub=scrub.__name__, key=key):
                    self.assertEqual(scrub({key: "visible"}), {key: "visible"})

    def test_delivery_refuses_them_as_parameters(self):
        for key in ("pat", "github_pat", "pwd"):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "Credentials must come from environment"):
                    delivery._params({key: VALUE}, {"required_params": [key]})


class CommandFlags(unittest.TestCase):
    def test_pat_and_pwd_flags_are_masked(self):
        for flag in ("--pat", "--pwd", "-pat", "--PAT", "--github-pat", "--db_pwd", "--azure.pat", "--pat-value"):
            for joint in (" ", "="):
                with self.subTest(flag=flag, joint=joint):
                    self.assertEqual(test_events.mask_command("tool " + flag + joint + VALUE + " run"), "tool " + flag + joint + "[REDACTED] run")

    def test_flags_that_only_contain_the_letters_stay(self):
        for flag in ("--path", "--pattern", "--compat", "--patch", "--spat", "--updated", "--cwd"):
            with self.subTest(flag=flag):
                text = "tool " + flag + " visible run"
                self.assertEqual(test_events.mask_command(text), text)

    def test_the_old_words_are_as_before(self):
        for flag in ("--token", "--api-key", "--passphrase", "--auth", "--keyword", "--credential", "--secret", "--passwd", "--password"):
            with self.subTest(flag=flag):
                self.assertEqual(test_events.mask_command("tool " + flag + " " + VALUE), "tool " + flag + " [REDACTED]")


class EnvironmentUnchanged(unittest.TestCase):
    def test_environment_names_as_before(self):
        for name, secret in (("MY_API_KEY", True), ("APIKEY", True), ("MY-API-KEY", False), ("AUTHORIZATION", False), ("GITHUB_PAT", True), ("PWD", False),
                             ("SOME_PASS", False), ("SSH_AUTH_SOCK", False)):
            with self.subTest(name=name), mock.patch.dict(os.environ, {name: VALUE}):
                self.assertEqual(redact("a " + VALUE + " b"), "a [REDACTED] b" if secret else "a " + VALUE + " b")


if __name__ == "__main__":
    unittest.main()
