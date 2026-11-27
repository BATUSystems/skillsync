"""CORE-1200-MASK3: the short names `pat` and `pwd` followed by a suffix (`GITHUB_PAT_RO`) or written in camelCase (`githubPat`), the false alarms
of the first version (the old working directory, code that assigns an expression, `--pwd /app`), and one shared definition of the secret words for the
scans of `feedback`, `prototype` and the URL parameters in the messages of `layers`."""
import os
import time
import unittest
from unittest import mock

from skillsync_workflow import feedback, layers, prototype, secret_words, test_events
from skillsync_workflow.common import redact, secret_key_name

VALUE = "abc" + "def" + str(123456)   # put together at run time: no literal looks like a key
BS = chr(92)


def in_feedback(text):
    return bool(feedback.secret_findings([("body", text)]))


class SuffixAndCamelCase(unittest.TestCase):
    BEFORE = ("GITHUB_PAT_RO=", "GITHUB_PAT_RO: ", "DB_PWD_PROD=", "export ADO_PAT_READ_ONLY=", "PAT_RO=", '"AZURE_PAT_2": "',
              "githubPat=", "githubPat: ", "dbPwd=", "dbPwd: ", "adoPAT=", "saPWD=", '{"githubPat": "', "{'dbPwd': '", "config.githubPat=")

    def test_masked_in_text(self):
        for before in self.BEFORE:
            with self.subTest(before=before):
                masked = redact(before + VALUE)
                self.assertNotIn(VALUE, masked)
                self.assertIn("[REDACTED]", masked)
                self.assertEqual(redact(masked), masked)

    def test_found_by_feedback_and_prototype(self):
        for before in self.BEFORE:
            with self.subTest(before=before):
                self.assertTrue(in_feedback(before + VALUE))
                self.assertTrue(prototype._has_secret(before + VALUE))

    def test_the_plain_short_names_are_found_by_every_scan(self):
        for before in ("pat=", "GITHUB_PAT=", "github-pat: ", "--github-pat=", "DB_PWD=", ";Pwd=", '"pat": "'):
            with self.subTest(before=before):
                self.assertNotIn(VALUE, redact(before + VALUE))
                self.assertTrue(in_feedback(before + VALUE))
                self.assertTrue(prototype._has_secret(before + VALUE))

    def test_report_keys(self):
        for name in ("githubPat", "dbPwd", "adoPAT", "GITHUB_PAT_RO", "DB_PWD_PROD"):
            with self.subTest(name=name):
                self.assertTrue(secret_key_name(name))
        for name in ("PATH", "compat", "xPath", "isPatched", "dispatchPath", "formatPattern", "PWD"):
            with self.subTest(name=name):
                self.assertFalse(secret_key_name(name))

    def test_environment_names(self):
        for name in ("githubPat", "dbPwd", "GITHUB_PAT_RO"):
            with self.subTest(name=name), mock.patch.dict(os.environ, {name: VALUE}):
                self.assertEqual(redact("value " + VALUE), "value [REDACTED]")

    def test_look_alikes_stay_readable(self):
        for text in ("xPath: //div/item", "isPatched=yesyesyes", "dispatchPath=/srv/app/data", "formatPattern=%Y-%m-%d", "COMPAT=full-mode",
                     "PAT_DIR=/srv/app/data", "GITHUB_PAT_FILE=/run/secrets/name", "DB_PWD_PATH=/run/secrets/name", "pat_match = other"):
            with self.subTest(text=text):
                self.assertEqual(redact(text), text)
                if "_PAT_" not in text:   # the gate of `feedback send` stays wider for `_pat...`, as before
                    self.assertFalse(in_feedback(text))


class OldWorkingDirectory(unittest.TestCase):
    TEXTS = ("INIT_PWD=/home/user/project", "OLD_PWD=/home/user/project", "OLDPWD=/home/user/project", "PWD=/home/user/project",
             "OLD_PWD=~/projects/app", "INIT_PWD=C:" + BS + "work" + BS + "project")

    def test_a_path_stays_readable_everywhere(self):
        for text in self.TEXTS:
            with self.subTest(text=text):
                self.assertEqual(redact(text), text)
                self.assertFalse(in_feedback(text))
                self.assertFalse(prototype._has_secret(text))

    def test_anything_else_under_these_names_is_still_masked(self):
        for before in ("INIT_PWD=", "OLD_PWD=", "DB_PWD=/", "DB_OLD_PWD=/", "MY_INIT_PWD=/"):
            with self.subTest(before=before):
                self.assertNotIn(VALUE, redact(before + VALUE))

    def test_the_environment(self):
        path = "/home/user/" + VALUE
        for name in ("INIT_PWD", "OLD_PWD"):
            with self.subTest(name=name), mock.patch.dict(os.environ, {name: path}):
                self.assertEqual(redact("in " + path), "in " + path)
            with self.subTest(name=name, plain=True), mock.patch.dict(os.environ, {name: VALUE}):
                self.assertEqual(redact("value " + VALUE), "value [REDACTED]")
        with mock.patch.dict(os.environ, {"DB_PWD": path}):
            self.assertNotIn(VALUE, redact("in " + path))


class Code(unittest.TestCase):
    TEXTS = ('url_pat = re.compile(r"x+")', "self.pat = compile(source)", "self.pwd = config.values[name]", "pat = self.patterns[0]")

    def test_an_assignment_of_an_expression_stays_readable(self):
        for text in self.TEXTS:
            with self.subTest(text=text):
                self.assertEqual(redact(text), text)
                self.assertFalse(in_feedback(text))
                self.assertFalse(prototype._has_secret(text))

    def test_a_literal_is_still_masked(self):
        for text in ('self.pat = "' + VALUE + '"', "url_pat = " + VALUE, "pwd = " + VALUE, "spring.db.pwd=" + VALUE + "(x)",
                     "pat=" + VALUE + "[0]"):
            with self.subTest(text=text):
                self.assertNotIn(VALUE, redact(text))

    def test_a_secret_inside_the_expression_is_still_masked(self):
        for text in ("url_pat = connect(pwd=" + VALUE + ")", "self.pat = build(token=" + VALUE + ")", "pat = f(githubPat=" + VALUE + ")"):
            with self.subTest(text=text):
                self.assertNotIn(VALUE, redact(text))


class TheExceptionsAreNarrow(unittest.TestCase):
    def test_a_password_that_only_looks_like_a_path_or_code_is_masked(self):
        for text in ("OLD_PWD=$" + VALUE, "pwd = " + VALUE + "(x", "pwd = " + VALUE + "[x", "pwd = Ab(" + VALUE, "pwd=" + VALUE + "()",
                     'pwd = "get(' + VALUE + ')"', "--db-pwd=/" + VALUE, "-pwd=/" + VALUE):
            with self.subTest(text=text):
                self.assertNotIn(VALUE, redact(text))
                self.assertTrue(in_feedback(text))

    def test_a_call_that_goes_on_in_the_next_line(self):
        for text in ("url_pat = re.compile(", "pat = self.table["):
            with self.subTest(text=text):
                self.assertEqual(redact(text), text)

    def test_hostile_input_is_fast(self):
        for text in ("PAT" + "_PAT" * 50000, "X_" + "PWD_A_" * 40000 + "x", "_pat = a(" * 20000, "a" * 100000 + "Pat=" + "b" * 100000,
                     "https://h.example/?" + "a" * 100000, "?pwd" * 50000):
            with self.subTest(text=text[:20]):
                started = time.perf_counter()
                redact(text)
                layers._mask_credentials(text)
                secret_words.has_short_pair(text, 6)   # the part of the prototype scan that this change added
                self.assertLess(time.perf_counter() - started, 5.0)


class ReviewFindings(unittest.TestCase):
    """Cases of the security review: each was masked or found before this change, or was meant to be."""

    def test_a_secret_parameter_nested_in_another_value_is_masked(self):
        for before in ("https://h.example/login?next=/cb?token=", "https://h.example/?a=1#x?password=", "https://h.example/?redirect=https://x/?key=",
                       "https://h.example/?PWD=", "https://h.example/?a=?b=?pat="):
            with self.subTest(before=before):
                self.assertEqual(layers._mask_credentials(before + VALUE), before + "***")

    def test_feedback_still_stops_names_with_a_part_after_pat(self):
        for before in ("github_pat_ro=", "ado_pat_value: ", "GITHUB_PAT2=", "MY_PATS=", "GITHUB_PAT_Ro=", "github_pat-ro=", "azure_pat:" + chr(10) + "  "):
            with self.subTest(before=before):
                self.assertTrue(in_feedback(before + VALUE))

    def test_a_dashed_name_is_not_the_working_directory(self):
        for before in ("db-PWD=/", "Db-PWD: ~", "--db-PWD=/", "x-OLD_PWD=/", "x-PWD=/"):
            with self.subTest(before=before):
                self.assertNotIn(VALUE, redact(before + VALUE))
                self.assertTrue(in_feedback(before + VALUE))

    def test_only_the_long_flag_takes_a_path(self):
        for text in ("tool -pwd /" + VALUE, "tool -pwd=/" + VALUE):
            with self.subTest(text=text):
                self.assertNotIn(VALUE, test_events.mask_command(text))

    def test_a_password_with_brackets_is_not_code(self):
        for text in ("pwd = Qw(er)" + VALUE, "DB_PWD = Ab[1]" + VALUE, "GITHUB_PAT = Ab(c)" + VALUE, "pwd = " + VALUE + "("):
            with self.subTest(text=text):
                self.assertNotIn(VALUE, redact(text))
                self.assertTrue(in_feedback(text))
        for text in ("pwd = Qw(er)" + VALUE, "mypwd = Ab[1]" + VALUE):
            with self.subTest(prototype=text):
                self.assertTrue(prototype._has_secret(text))

    def test_a_harmless_pair_does_not_hide_the_next_one_from_the_prototype_scan(self):
        for text in ("OLDPWD=/a;mypwd=" + VALUE, "self.pwd = f(x,mypwd=" + VALUE + ")"):
            with self.subTest(text=text):
                self.assertTrue(prototype._has_secret(text))

    def test_more_camel_case_forms(self):
        for before in ("githubPat_RO=", "GithubPat_ro=", "ADOPat=", "dbPwd_Prod: "):
            with self.subTest(before=before):
                self.assertNotIn(VALUE, redact(before + VALUE))
        self.assertTrue(secret_key_name("ADOPat"))

    def test_an_empty_key_does_not_take_the_next_key_as_its_value(self):
        self.assertNotIn(VALUE, redact("githubPat: DB_PWD: " + VALUE))
        self.assertNotIn("abc==", redact("pat=abc=="))
        for text in ("DB_PWD=" + VALUE + "Token:", "db-pwd=" + VALUE + "secret: dalej", "pwd=" + VALUE + "Password:"):   # a password that ends with ":"
            with self.subTest(text=text):
                self.assertNotIn(VALUE, redact(text))
                self.assertTrue(in_feedback(text))

    def test_repeated_harmless_pairs_are_linear(self):
        for text in ("OLD_PWD=/" * 100000, "--pwd=/" * 100000, "pwd=" * 100000, "mypwd=/" * 50000):
            with self.subTest(text=text[:10]):
                started = time.perf_counter()
                redact(text)
                secret_words.has_short_pair(text, 8)
                self.assertLess(time.perf_counter() - started, 5.0)


class CommandLine(unittest.TestCase):
    def test_pwd_with_a_path_is_the_working_directory(self):
        for text in ("docker run --pwd /app image", "apptainer exec --pwd=/srv/app image", "run --pwd ./sub image", "run --pwd ~/work image"):
            with self.subTest(text=text):
                self.assertEqual(test_events.mask_command(text), text)

    def test_other_flags_are_still_masked(self):
        for text in ("tool --pwd " + VALUE, "tool --db-pwd /" + VALUE, "tool --githubPat " + VALUE, "tool --dbPwd=" + VALUE,
                     "tool --github-pat " + VALUE, "tool --ADOPat " + VALUE, "tool --password /" + VALUE):
            with self.subTest(text=text):
                self.assertNotIn(VALUE, test_events.mask_command(text))


class UrlParameters(unittest.TestCase):
    def test_secret_parameters_are_masked(self):
        for name in ("token", "access_token", "private_token", "password", "passwd", "pass", "auth", "key", "pat", "PAT", "pwd", "secret",
                     "client_secret", "api_key", "apikey", "api-key", "access_key", "sig", "signature", "githubPat", "GITHUB_PAT_RO"):
            with self.subTest(name=name):
                shown = layers._mask_credentials(f"fatal: https://host.example/r.git?a=1&{name}={VALUE}&b=2")
                self.assertEqual(shown, f"fatal: https://host.example/r.git?a=1&{name}=***&b=2")

    def test_other_parameters_stay_readable(self):
        text = "https://host.example/r?page=2&path=a/b&compat=1&keyword=x&design=y&author=z"
        self.assertEqual(layers._mask_credentials(text), text)


if __name__ == "__main__":
    unittest.main()
