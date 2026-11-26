"""CORE-1180 M-1: the nested `dashboard.worktrees` section (`exclude`, `inactive_days`) in the validator of the `dashboard:` section
(`skillsync_workflow/dashboard_listen.py`): types, the refusal in the machine config, the defaults of `settings()`.

Nothing here listens on a port or touches the real state of Skillsync (`guard_real_state`, `SKILLSYNC_CONFIG` in a temporary tree)."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from skillsync_workflow import dashboard_listen as listen  # noqa: E402
from layers_helpers import guard_real_state, home_env  # noqa: E402

SECRET_LOOKING = "10.99.99.99-not-a-day-count"
DEFAULTS = {"exclude": [], "inactive_days": 7}


def wt(**values) -> dict:
    return {"worktrees": values}


class Validator(unittest.TestCase):
    def test_a_valid_section_has_no_errors_and_no_warnings(self) -> None:
        for section in (wt(exclude=["../other-wt", "/abs/path/wt"], inactive_days=3), wt(exclude=[]), wt(inactive_days=1), wt()):
            with self.subTest(section=section):
                self.assertEqual(listen.validate_dashboard(section, "dashboard", narrow_only=True), ([], []))

    def test_worktrees_next_to_the_flat_keys(self) -> None:
        section = {"local_only": True, "read_bind": "127.0.0.1", "worktrees": {"exclude": ["a"], "inactive_days": 2}}
        self.assertEqual(listen.validate_dashboard(section, "dashboard", narrow_only=True), ([], []))

    def test_worktrees_is_a_known_key_and_does_not_block_serve(self) -> None:
        errors, warnings = listen.validate_dashboard(wt(exclude=["a"]), "dashboard", narrow_only=True)
        self.assertEqual((errors, warnings), ([], []))
        self.assertIsNone(listen.blocking_unknown_keys({"dashboard": wt(exclude=["a"], inactive_days=2)}, machine={}))

    def test_an_unknown_flat_key_is_still_blocking(self) -> None:
        self.assertIn("bind_all", listen.blocking_unknown_keys({"dashboard": {"bind_all": 1, **wt(exclude=[])}}, machine={}))

    def test_worktrees_must_be_a_mapping(self) -> None:
        for value in ("text", ["exclude"], 5, True):
            with self.subTest(value=value):
                errors, _ = listen.validate_dashboard({"worktrees": value}, "dashboard", narrow_only=True)
                self.assertTrue(errors)
                self.assertTrue(any("worktrees" in message for message in errors), errors)

    def test_exclude_must_be_a_list_of_nonempty_strings(self) -> None:
        for value in ("just/one/path", {"a": 1}, 5, True, [""], ["   "], ["ok", ""], [5], ["ok", None], [["nested"]], [True]):
            with self.subTest(value=value):
                errors, _ = listen.validate_dashboard(wt(exclude=value), "dashboard", narrow_only=True)
                self.assertTrue(errors)
                self.assertTrue(any("exclude" in message for message in errors), errors)

    def test_exclude_none_is_a_type_error_not_a_crash(self) -> None:
        errors, _ = listen.validate_dashboard(wt(exclude=None), "dashboard", narrow_only=True)
        self.assertTrue(isinstance(errors, list))

    def test_inactive_days_must_be_an_integer_of_at_least_one(self) -> None:
        for value in (0, -1, -30, 1.5, 7.0, "7", "", None, True, False, [7], {"d": 7}):
            with self.subTest(value=value):
                errors, _ = listen.validate_dashboard(wt(inactive_days=value), "dashboard", narrow_only=True)
                self.assertTrue(errors)
                self.assertTrue(any("inactive_days" in message for message in errors), errors)

    def test_inactive_days_accepts_the_boundary_and_large_values(self) -> None:
        for value in (1, 2, 7, 365, 10000):
            with self.subTest(value=value):
                self.assertEqual(listen.validate_dashboard(wt(inactive_days=value), "dashboard", narrow_only=True)[0], [])

    def test_messages_name_the_key_and_never_the_value(self) -> None:
        for section in (wt(inactive_days=SECRET_LOOKING), wt(exclude=SECRET_LOOKING), wt(exclude=[5, SECRET_LOOKING, ""]), {"worktrees": SECRET_LOOKING}):
            with self.subTest(section=section):
                errors, warnings = listen.validate_dashboard(section, "dashboard", narrow_only=True)
                self.assertTrue(errors)
                self.assertTrue(all(SECRET_LOOKING not in message for message in errors + warnings), errors)
                with self.assertRaises(ValueError) as caught:
                    listen.settings({"dashboard": section}, machine={})
                self.assertNotIn(SECRET_LOOKING, str(caught.exception))

    def test_errors_carry_the_place_of_the_section(self) -> None:
        errors, _ = listen.validate_dashboard(wt(inactive_days=0), "dashboard (manifest)", narrow_only=True)
        self.assertTrue(all(message.startswith("dashboard (manifest)") for message in errors), errors)

    def test_a_bad_worktrees_value_does_not_hide_a_bind_error(self) -> None:
        errors, _ = listen.validate_dashboard({"read_bind": "0.0.0.0", **wt(inactive_days=0)}, "dashboard", narrow_only=True)
        joined = " ".join(errors)
        self.assertIn("read_bind", joined)
        self.assertIn("inactive_days", joined)


class SettingsOfTheManifest(unittest.TestCase):
    def setUp(self) -> None:
        guard_real_state(self)
        self.tmp = Path(tempfile.mkdtemp(prefix="dashboard-listen-wt-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.config = self.tmp / "machine.yaml"
        patch = mock.patch.dict(os.environ, {"SKILLSYNC_CONFIG": str(self.config), **home_env(self.tmp / "state")})
        patch.start()
        self.addCleanup(patch.stop)

    def test_defaults_without_a_manifest_section(self) -> None:
        for manifest in ({}, {"dashboard": {}}, {"dashboard": {"local_only": True}}, {"dashboard": wt()}, {"skills": ["dashboard"]}):
            with self.subTest(manifest=manifest):
                self.assertEqual(listen.worktree_settings(manifest), DEFAULTS)

    def test_defaults_come_as_fresh_objects(self) -> None:
        first = listen.worktree_settings({})
        first["exclude"].append("polluted")
        self.assertEqual(listen.worktree_settings({})["exclude"], [])

    def test_values_come_from_the_manifest(self) -> None:
        manifest = {"dashboard": wt(exclude=["../wt-a", "/abs/wt-b"], inactive_days=14)}
        self.assertEqual(listen.worktree_settings(manifest), {"exclude": ["../wt-a", "/abs/wt-b"], "inactive_days": 14})

    def test_a_missing_half_gets_its_default(self) -> None:
        self.assertEqual(listen.worktree_settings({"dashboard": wt(inactive_days=2)}), {"exclude": [], "inactive_days": 2})
        self.assertEqual(listen.worktree_settings({"dashboard": wt(exclude=["x"])}), {"exclude": ["x"], "inactive_days": 7})

    def test_the_listen_keys_are_unchanged_next_to_worktrees(self) -> None:
        result = listen.settings({"dashboard": {"local_only": True, **wt(exclude=["x"])}}, machine={})
        self.assertEqual((result["read_bind"], result["approve_bind"]), ("127.0.0.1", "127.0.0.1"))
        self.assertEqual(result["worktrees"]["exclude"], ["x"])

    def test_type_errors_in_the_manifest_raise_value_error(self) -> None:
        for section in (wt(inactive_days=0), wt(inactive_days=True), wt(inactive_days="7"), wt(exclude="x"), wt(exclude=[""]), {"worktrees": []}):
            with self.subTest(section=section), self.assertRaises(ValueError):
                listen.settings({"dashboard": section}, machine={})

    def test_the_manifest_of_the_project_is_not_widened_by_the_machine_default(self) -> None:
        # `worktrees` is read from the manifest only; an empty machine section must not change it.
        self.assertEqual(listen.settings({"dashboard": wt(inactive_days=3)}, machine={"local_only": True})["worktrees"],
                         {"exclude": [], "inactive_days": 3})


class MachineConfigRefusesWorktrees(unittest.TestCase):
    def setUp(self) -> None:
        guard_real_state(self)
        self.tmp = Path(tempfile.mkdtemp(prefix="dashboard-listen-wt-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.config = self.tmp / "machine.yaml"
        patch = mock.patch.dict(os.environ, {"SKILLSYNC_CONFIG": str(self.config), **home_env(self.tmp / "state")})
        patch.start()
        self.addCleanup(patch.stop)

    def assert_refused(self, call) -> None:
        with self.assertRaises(ValueError) as caught:
            call()
        message = str(caught.exception)
        self.assertIn("worktrees", message)
        self.assertIn("machine", message)

    def test_settings_refuses_the_section_with_an_explicit_message(self) -> None:
        for machine in (wt(exclude=["x"]), wt(inactive_days=3), wt(), {"local_only": True, **wt(exclude=[])}):
            with self.subTest(machine=machine):
                self.assert_refused(lambda: listen.settings({}, machine=machine))

    def test_settings_refuses_it_when_the_machine_config_is_read_from_the_file(self) -> None:
        self.config.write_text("dashboard:\n  worktrees:\n    exclude: [a]\n", encoding="utf-8")
        self.assertEqual(listen.machine_section(), wt(exclude=["a"]))     # the reader itself stays a plain reader
        self.assert_refused(lambda: listen.settings({}))

    def test_a_valid_manifest_does_not_rescue_a_machine_config_with_worktrees(self) -> None:
        self.assert_refused(lambda: listen.settings({"dashboard": wt(inactive_days=3)}, machine=wt(inactive_days=3)))

    def test_the_refusal_names_the_key_never_its_value(self) -> None:
        with self.assertRaises(ValueError) as caught:
            listen.settings({}, machine=wt(exclude=[SECRET_LOOKING]))
        self.assertNotIn(SECRET_LOOKING, str(caught.exception))

    def test_the_validator_of_the_machine_section_reports_an_error_naming_the_key(self) -> None:
        errors, _ = listen.validate_dashboard(wt(exclude=["x"]), "dashboard (machine config)")     # as `doctor` validates the machine
        self.assertTrue(errors)
        self.assertTrue(any("worktrees" in message for message in errors), errors)

    def test_a_machine_config_without_worktrees_is_still_fine(self) -> None:
        self.assertEqual(listen.worktree_settings({}), DEFAULTS)
        self.config.write_text("dashboard:\n  local_only: true\n", encoding="utf-8")
        self.assertEqual(listen.worktree_settings({}), DEFAULTS)


if __name__ == "__main__":
    unittest.main()
