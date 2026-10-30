import argparse
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "manage", Path(__file__).resolve().parents[1] / "scripts/manage.py")
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)


class PublicDeploymentTests(unittest.TestCase):
    def test_official_release_defaults(self):
        self.assertEqual(manage.VERSION, "6.0.8")
        self.assertEqual(len(manage.ZIP_SHA256), 64)
        dockerfile = (manage.ROOT / "Dockerfile").read_text()
        self.assertIn("https://download.revive-adserver.com/revive-adserver-", dockerfile)
        self.assertNotIn("git fetch", dockerfile)
        self.assertNotIn("composer:", dockerfile)
        self.assertIn("sha256sum --check --strict", dockerfile)

    def test_secret_files_are_private_and_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            manage.private_write(path, "private")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                manage.private_write(path, "replacement")

    def test_init_generates_distinct_passwords(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(manage, "ENV_FILE", Path(tmp) / ".env"):
            manage.init(argparse.Namespace(project="test-revive", port=8090, host=None, scheme="http"))
            cfg = manage.settings()
            self.assertNotEqual(cfg["DB_PASSWORD"], cfg["DB_ROOT_PASSWORD"])
            self.assertEqual(cfg["REVIVE_VERSION"], "6.0.8")
            self.assertEqual(cfg["REVIVE_HOST"], "localhost:8090")

    def test_legacy_development_environment_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(manage, "ENV_FILE", Path(tmp) / ".env"):
            manage.private_write(manage.ENV_FILE, "REVIVE_COMMIT=old-development-state\n")
            with self.assertRaises(ValueError):
                manage.settings()

    def test_compose_default_does_not_publish_database_or_http(self):
        text = (manage.ROOT / "docker-compose.yml").read_text()
        self.assertNotIn("ports:", text)
        self.assertNotIn("container_name:", text)
        self.assertNotIn("STATE_DIR", text)
        self.assertIn("DB_PASSWORD:?", text)
        self.assertIn("REVIVE_AUTO_INSTALL", text)
        alias = (manage.ROOT / "compose.yaml").read_text()
        self.assertIn("./docker-compose.yml", alias)

    def test_downloader_refuses_nonofficial_sources_and_missing_checksums(self):
        downloader_spec = importlib.util.spec_from_file_location(
            "download_release", manage.ROOT / "docker/download-release.py")
        downloader = importlib.util.module_from_spec(downloader_spec)
        downloader_spec.loader.exec_module(downloader)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "release.zip"
            with self.assertRaises(ValueError):
                downloader.download("https://github.com/example/master.zip", "a" * 64, target)
            with self.assertRaises(ValueError):
                downloader.download(
                    "https://download.revive-adserver.com/revive-adserver-6.0.8.zip", "", target)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()