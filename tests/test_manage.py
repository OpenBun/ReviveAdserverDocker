import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location(
    "manage", Path(__file__).resolve().parents[1] / "scripts/manage.py")
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)


class MigrationSafetyTests(unittest.TestCase):
    def test_ini_retains_guard_and_replaces_only_named_settings(self):
        original = ';<?php exit; ?>\n[database]\npassword = "old"\nname = old\n[plugins]\nfoo = 0\n'
        updated = manage.patch_ini(original, {"database": {"password": '"new"', "host": "db"},
                                              "maintenance": {"autoMaintenance": "0"}})
        self.assertTrue(updated.startswith(";<?php exit; ?>"))
        self.assertNotIn('"old"', updated)
        self.assertEqual(updated.count("password ="), 1)
        self.assertIn("foo = 0", updated)
        self.assertIn("[maintenance]\nautoMaintenance = 0", updated)
        self.assertIn("host = db\n[plugins]", updated)

    def test_unsafe_archives_are_rejected(self):
        for name in ("../escape", "/absolute/file", "images/../../escape", "images\\escape"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                archive = Path(tmp) / "bad.zip"
                with zipfile.ZipFile(archive, "w") as z:
                    z.writestr(name, "x")
                with self.assertRaises(ValueError):
                    manage.checked_zip(archive)

    def test_valid_creative_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "ok.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("images/banner.jpg", b"example")
            with manage.checked_zip(archive) as z:
                self.assertEqual(z.read("images/banner.jpg"), b"example")

    def test_private_write_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secret"
            manage.private_write(path, "first")
            with self.assertRaises(FileExistsError):
                manage.private_write(path, "second")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_ini_injection_is_rejected(self):
        for value in ('bad"\n[database]', "line\nbreak", "windows\\path"):
            with self.assertRaises(ValueError):
                manage.quote_ini(value)


    def test_disabled_obsolete_plugin_registration_is_removed(self):
        original = (";<?php exit; ?>\n[plugins]\nopenX3rdPartyServers = 0\n"
                    "openXReports = 0\n[pluginGroupComponents]\nox3rdPartyServers = 0\n"
                    "oxReports = 0\n")
        result = manage.remove_obsolete_tracking_plugin(original)
        self.assertNotIn("3rdPartyServers", result)
        self.assertIn("openXReports = 0", result)
        self.assertIn("oxReports = 0", result)
        self.assertTrue(result.startswith(";<?php exit; ?>"))

    def test_enabled_obsolete_plugin_requires_review(self):
        with self.assertRaises(ValueError):
            manage.remove_obsolete_tracking_plugin("[plugins]\nopenX3rdPartyServers = 1\n")


if __name__ == "__main__":
    unittest.main()