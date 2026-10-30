#!/usr/bin/env python3
"""Private, single-host Revive staging lifecycle; Python standard library only."""
import argparse
import base64
import datetime
import getpass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
COMMIT = "5cc7dd9673af30302061c62a0207cbdfd03b7026"
VERSION = "6.1.0-dev"
PREVIOUS_SHA256 = "0cf81aecb90ddaabd54975bca3f7bf120aa908a75e111d082d87ceed3ad8bc6c"
STATE_PARTS = ("var", "images", "plugins", "admin-plugins", "previous")


def private_write(path, data):
    """Never replace an existing backup or secret implicitly."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data.encode() if isinstance(data, str) else data)


def settings():
    path = ENV_FILE
    if not path.exists():
        raise ValueError("Run init first.")
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if line and not line.startswith("#"))


def compose(*args, **kwargs):
    cfg = settings()
    files = cfg.get("COMPOSE_FILE", "compose.yaml").split(os.pathsep)
    file_args = []
    for name in files:
        if name not in ("compose.yaml", "compose.live.yaml"):
            raise ValueError("Only the supplied Compose files are supported.")
        file_args.extend(("-f", str(ROOT / name)))
    return subprocess.run(
        ["docker", "compose", "--project-directory", str(ROOT),
         "--env-file", str(ENV_FILE), *file_args, *args],
        check=True, **kwargs)


def query(sql):
    result = compose(
        "exec", "-T", "db", "sh", "-c",
        'export MYSQL_PWD="$(cat /run/secrets/db_password)"; '
        'exec mariadb -u revive -N -B revive',
        input=sql.encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.decode()


def init(args):
    state = Path(args.state_dir).expanduser().resolve()
    if state == ROOT or ROOT in state.parents:
        raise ValueError("State must be outside the source/build directory.")
    if ENV_FILE.exists() or state.exists():
        raise ValueError("Refusing to overwrite an existing .env or state directory.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.project):
        raise ValueError("Invalid Compose project name.")
    if not 1 <= args.port <= 65535:
        raise ValueError("Invalid port.")
    if any(c.isspace() or c in "$#'\"" for c in str(state)):
        raise ValueError("Use a state path without whitespace or shell metacharacters.")
    state.mkdir(parents=True, mode=0o700)
    for part in (*STATE_PARTS, "secrets", "backups"):
        (state / part).mkdir(mode=0o700)
    for name in ("db_password", "db_root_password"):
        private_write(state / "secrets" / name, secrets.token_hex(32))
    private_write(ENV_FILE, (
        f"COMPOSE_PROJECT_NAME={args.project}\nREVIVE_COMMIT={COMMIT}\n"
        f"REVIVE_VERSION={VERSION}\nMARIADB_IMAGE=mariadb:11.4\n"
        f"STATE_DIR={state}\nHTTP_PORT={args.port}\nPUBLIC_HOST=localhost:{args.port}\n"))
    print(f"Initialized private state: {state}")


def checked_zip(path):
    archive = zipfile.ZipFile(path)
    entries = archive.infolist()
    if sum(i.file_size for i in entries) > 512 * 1024 * 1024:
        archive.close()
        raise ValueError("Archive exceeds 512 MiB uncompressed review limit.")
    for entry in entries:
        p = PurePosixPath(entry.filename)
        if p.is_absolute() or ".." in p.parts or "\\" in entry.filename:
            archive.close()
            raise ValueError("Unsafe archive path.")
        if stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
            archive.close()
            raise ValueError("Symlink/encrypted archive entries are not supported.")
    if archive.testzip() is not None:
        archive.close()
        raise ValueError("Archive integrity check failed.")
    return archive


def patch_ini(text, changes):
    """Preserve Revive's PHP guard and unknown settings; only replace named keys."""
    remaining = {section: dict(values) for section, values in changes.items()}
    section = None
    output = []

    def flush():
        if section in remaining:
            output.extend(f"{key} = {value}\n"
                          for key, value in remaining[section].items())
            remaining[section].clear()

    for line in text.splitlines(keepends=True):
        match = re.match(r"\s*\[([^]]+)\]", line)
        if match:
            flush()
            section = match.group(1)
        key = re.match(r"\s*([\w.-]+)\s*=", line)
        if key and key.group(1) in remaining.get(section, {}):
            value = remaining[section].pop(key.group(1))
            line = f"{key.group(1)} = {value}\n"
        output.append(line if line.endswith("\n") else line + "\n")
    flush()
    for name, values in remaining.items():
        if values:
            output.append(f"\n[{name}]\n")
            output.extend(f"{key} = {value}\n" for key, value in values.items())
    return "".join(output)


def quote_ini(value):
    if any(c in value for c in '"\r\n\\'):
        raise ValueError("Unsupported INI value.")
    return f'"{value}"'


def remove_obsolete_tracking_plugin(text):
    """Revive removed this bundled plugin in 5.1; disabled entries can linger."""
    section = ""
    disabled = False
    for line in text.splitlines():
        heading = re.match(r"\s*\[([^]]+)\]", line)
        if heading:
            section = heading.group(1)
        entry = re.match(r"\s*openX3rdPartyServers\s*=\s*(.*?)\s*$", line)
        if section == "plugins" and entry:
            if entry.group(1).strip("'\"").lower() not in ("0", "false", ""):
                raise ValueError("Obsolete tracking plugin is enabled; manual migration review required.")
            disabled = True
    if not disabled:
        return text
    output = []
    section = ""
    for line in text.splitlines(keepends=True):
        heading = re.match(r"\s*\[([^]]+)\]", line)
        if heading:
            section = heading.group(1)
        key = re.match(r"\s*([\w.-]+)\s*=", line)
        if key and (section, key.group(1)) in {
            ("plugins", "openX3rdPartyServers"),
            ("pluginGroupComponents", "ox3rdPartyServers"),
        }:
            continue
        output.append(line)
    return "".join(output)


def previous_release(state):
    """Only the original 5.5.0 baseline; future Docker upgrades snapshot live code."""
    target = state / "previous"
    marker = target / ".baseline-5.5.0"
    if marker.is_file() and marker.read_text().strip() == PREVIOUS_SHA256:
        return
    if any(target.iterdir()):
        raise ValueError("Previous-instance directory is not empty.")
    url = "https://download.revive-adserver.com/revive-adserver-5.5.0.tar.gz"
    req = urllib.request.Request(url, headers={"User-Agent": "Revive-Migration"})
    with urllib.request.urlopen(req, timeout=90) as response:
        if not response.url.startswith("https://"):
            raise ValueError("Refusing unencrypted release download.")
        data = response.read(128 * 1024 * 1024 + 1)
    if hashlib.sha256(data).hexdigest() != PREVIOUS_SHA256:
        raise ValueError("Previous release SHA-256 mismatch.")
    with tempfile.TemporaryDirectory(dir=state) as tmp:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            if sum(i.size for i in archive.getmembers()) > 512 * 1024 * 1024:
                raise ValueError("Previous release is unexpectedly large.")
            archive.extractall(tmp, filter="data")
        source = Path(tmp) / "revive-adserver-5.5.0"
        if not (source / "constants.php").is_file():
            raise ValueError("Unexpected previous-release layout.")
        shutil.copytree(source, target, dirs_exist_ok=True)
    # Reconstruct bundled plugin code from the matching official distribution.
    # This does not recover custom plugins or modifications absent from backups.
    for bundle in (target / "etc/plugins").glob("*.zip"):
        with checked_zip(bundle) as archive:
            archive.extractall(target)
    private_write(marker, PREVIOUS_SHA256)


def restore(args):
    cfg = settings()
    state = Path(cfg["STATE_DIR"])
    if list((state / "var").glob("*.conf.php")):
        raise ValueError("Refusing to overwrite an existing instance configuration.")
    # Reject common dump statements that could affect another schema or host.
    sql_path = Path(args.sql).resolve()
    with sql_path.open(errors="replace") as stream:
        for line in stream:
            if re.match(r"\s*(USE\s|CREATE\s+DATABASE|GRANT\s|SOURCE\s|SYSTEM\s|"
                        r"CREATE\s+USER|INSTALL\s+PLUGIN)", line, re.I):
                raise ValueError("Dump contains statements requiring manual review.")
    with checked_zip(args.var) as archive:
        name = "var/" + args.config
        if name not in archive.namelist():
            raise ValueError("Specified configuration is missing from the archive.")
        text = archive.read(name).decode()
        os.environ["REVIVE_DB_PASSWORD"] = (state / "secrets/db_password").read_text()
        host = cfg["PUBLIC_HOST"]
        changes = {
            "database": {"type": "mysqli", "host": "db", "port": "3306",
                         "socket": "", "protocol": "tcp", "name": "revive",
                         "username": "revive",
                         "password": quote_ini(os.environ["REVIVE_DB_PASSWORD"]),
                         "ssl": "false"},
            "openads": {"requireSSL": str(cfg.get("PUBLIC_SCHEME") == "https").lower(),
                        "sslPort": "443"},
            "webpath": {key: quote_ini(f"{host}/app/www/{part}") for key, part in
                        (("admin", "admin"), ("delivery", "delivery"),
                         ("deliverySSL", "delivery"), ("images", "images"),
                         ("imagesSSL", "images"))},
            "store": {"mode": "0", "webDir": '"/var/www/html/app/www/images"',
                      "ftpHost": "", "ftpPath": "", "ftpUsername": "", "ftpPassword": ""},
            "maintenance": {"autoMaintenance": "0"},
            "cookie": {"domain": "", "viewerIdDomain": "", "adminDomain": ""},
            "delivery": {"cachePath": "", "secret": quote_ini(
                base64.b64encode(secrets.token_bytes(32)).decode())},
            "email": {"logOutgoing": "false"},
        }
        sanitized = remove_obsolete_tracking_plugin(patch_ini(text, changes))
        # Copy plugin metadata/configuration, not old logs, caches or compiled PHP.
        metadata = {}
        for entry in archive.infolist():
            if not entry.is_dir() and entry.filename.startswith(
                    ("var/plugins/config/", "var/plugins/DataObjects/")):
                metadata[entry.filename] = archive.read(entry)
    with checked_zip(args.images) as archive:
        creatives = {}
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            path = PurePosixPath(entry.filename)
            if not path.parts or path.parts[0] != "images":
                raise ValueError("Creative archive must contain an images/ directory.")
            if re.search(r"\.(?:php\d*|phtml|pht|phar|cgi|pl|sh)(?:\.|$)",
                         entry.filename, re.I):
                raise ValueError("Executable creative file requires manual review.")
            if path.name.startswith("."):
                continue
            creatives[Path(*path.parts[1:])] = archive.read(entry)
    compose("up", "-d", "--wait", "db")
    if query("SELECT COUNT(*) FROM information_schema.tables "
             "WHERE table_schema='revive';").strip() != "0":
        raise ValueError("Refusing to import into a nonempty database.")
    collations = set(re.findall(r"\bCOLLATE[=\s]+([a-zA-Z0-9_]+)",
                                sql_path.read_text(errors="replace"), re.I))
    available = set(query("SELECT COLLATION_NAME FROM information_schema.COLLATIONS;").splitlines())
    if collations - available:
        raise ValueError("Dump uses unsupported collations; choose a compatible MariaDB version.")
    previous_release(state)
    with sql_path.open("rb") as source:
        compose("exec", "-T", "db", "sh", "-c",
                'export MYSQL_PWD="$(cat /run/secrets/db_password)"; '
                'exec mariadb -u revive --binary-mode=1 revive', stdin=source)
    for directory in (state / "var", state / "previous/var"):
        private_write(directory / "localhost.conf.php", sanitized)
        private_write(directory / "default.conf.php", ';<?php exit; ?>\nrealConfig = "localhost"\n')
    for name, data in metadata.items():
        for root in (state, state / "previous"):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            private_write(path, data)
    for relative, data in creatives.items():
        path = state / "images" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        private_write(path, data)
    for part in STATE_PARTS:
        for directory, dirs, files in os.walk(state / part):
            os.chown(directory, 33, 33)
            os.chmod(directory, 0o750)
            for name in files:
                path = Path(directory) / name
                os.chown(path, 33, 33)
                os.chmod(path, 0o640)
    private_write(state / "restore.json", json.dumps({
        "source_version": "5.5.0", "target_commit": cfg["REVIVE_COMMIT"],
        "image_archive_files": len(creatives),
        "sql_sha256": hashlib.sha256(sql_path.read_bytes()).hexdigest(),
        "status": "restored; administrator-authenticated upgrade required",
    }, indent=2))
    print("Restored isolated database, staging configuration and creatives.")
    print("Administrator-authenticated upgrade is still required.")


def backup(args=None, restart=True):
    cfg = settings()
    state = Path(cfg["STATE_DIR"])
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = state / "backups" / stamp
    dest.mkdir(mode=0o700)
    running = compose("--profile", "maintenance", "ps", "--services", "--status",
                      "running", stdout=subprocess.PIPE).stdout.decode().splitlines()
    compose("--profile", "maintenance", "stop", "web", "maintenance")
    try:
        with (dest / "database.sql").open("xb") as output:
            compose("exec", "-T", "db", "sh", "-c",
                    'export MYSQL_PWD="$(cat /run/secrets/db_root_password)"; '
                    'exec mariadb-dump -u root --lock-all-tables --hex-blob '
                    '--triggers revive', stdout=output)
        with tarfile.open(dest / "state.tar.gz", "w:gz") as archive:
            for part in STATE_PARTS:
                archive.add(state / part, arcname=part)
        private_write(dest / "deployment.env", ENV_FILE.read_bytes())
        with tarfile.open(dest / "secrets.tar.gz", "w:gz") as archive:
            archive.add(state / "secrets", arcname="secrets")
        if "web" in running:
            compose("cp", "web:/var/www/html/app", str(dest / "application"))
    finally:
        if restart:
            for service in ("web", "maintenance"):
                if service in running:
                    compose("--profile", "maintenance", "start", service)
    for path in dest.iterdir():
        if path.is_file():
            path.chmod(0o600)
    print(f"Consistent local backup: {dest}")
    return dest


def upgrade(args):
    # Passwords are prompted invisibly and passed on stdin, never CLI arguments.
    username = getpass.getpass("Existing Revive administrator username (hidden): ")
    password = getpass.getpass("Existing Revive administrator password: ")
    if any(c in username + password for c in "\r\n") or not username or not password:
        raise ValueError("Nonempty single-line credentials are required.")
    backup(restart=False)
    enabled = Path(settings()["STATE_DIR"]) / "var/.maintenance-enabled"
    if enabled.exists():
        enabled.unlink()
    compose("stop", "web")
    try:
        compose(
            "run", "--rm", "--no-deps", "-T", "--user", "www-data",
            "--entrypoint", "sh", "web", "-c",
            'IFS= read -r REVIVE_USERNAME; IFS= read -r REVIVE_PASSWORD; '
            'export REVIVE_USERNAME REVIVE_PASSWORD; '
            'exec php scripts/installer upgrade /previous -H "$REVIVE_HOST" --force',
            input=(username + "\n" + password + "\n").encode())
    except subprocess.CalledProcessError:
        print("Upgrade failed; web is left stopped. Restore the matching backup before retrying.",
              file=sys.stderr)
        raise
    compose("up", "-d", "--wait", "web")
    print("Upgrade command finished; verify plugins, delivery and reports before enabling maintenance.")

def install(args):
    """Fresh databases only; never use this on a migration."""
    cfg = settings()
    state = Path(cfg["STATE_DIR"])
    if list((state / "var").glob("*.conf.php")):
        raise ValueError("Existing configuration found; use upgrade, not install.")
    compose("up", "-d", "--wait", "db", "web")
    if query("SELECT COUNT(*) FROM information_schema.tables "
             "WHERE table_schema='revive';").strip() != "0":
        raise ValueError("Fresh install requires an empty database.")
    username = os.environ.get("REVIVE_NEW_USERNAME") or getpass.getpass("New admin username (hidden): ")
    password = os.environ.get("REVIVE_NEW_PASSWORD") or getpass.getpass("New admin password: ")
    if not username or len(password) < 12:
        raise ValueError("Use a nonempty username and a password of at least 12 characters.")
    os.environ["REVIVE_DB_PASSWORD"] = (state / "secrets/db_password").read_text()
    host = cfg["PUBLIC_HOST"]
    text = patch_ini(";<?php exit; ?>\n", {
        "database": {"type": "mysqli", "host": "db", "port": "3306", "socket": "",
                     "name": "revive", "username": "revive",
                     "password": quote_ini(os.environ["REVIVE_DB_PASSWORD"])},
        "table": {"prefix": "rv_", "type": "INNODB"},
        "admin": {"username": quote_ini(username), "password": quote_ini(password),
                  "email": quote_ini(args.email), "language": "en", "timezone": "UTC"},
        "paths": {"requireSSL": str(int(cfg.get("PUBLIC_SCHEME") == "https")),
                  "admin": quote_ini(host + "/app/www/admin"),
                  "delivery": quote_ini(host + "/app/www/delivery"),
                  "images": quote_ini(host + "/app/www/images"),
                  "imageStore": '"/var/www/html/app/www/images"'},
    })
    conf = state / "var/.docker-installer.conf.php"
    private_write(conf, text)
    os.chown(conf, 33, 33)
    try:
        compose("exec", "-T", "--user", "www-data", "web", "php", "scripts/installer",
                "install", "/var/www/html/app/var/.docker-installer.conf.php", "--force")
    finally:
        conf.unlink()
    for path in (state / "var").glob("*.conf.php"):
        if "[maintenance]" in path.read_text():
            path.write_text(patch_ini(path.read_text(), {"maintenance": {"autoMaintenance": "0"}}))
    print("Fresh install completed; scheduled maintenance remains opt-in.")


def stage_release(args):
    """Snapshot matching old code/plugins before explicitly changing image commits."""
    cfg = settings()
    state = Path(cfg["STATE_DIR"])
    marker = state / "var/.docker-commit"
    if not re.fullmatch(r"[a-f0-9]{40}", args.commit):
        raise ValueError("Use a full 40-character Git commit.")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", args.version):
        raise ValueError("Invalid application version label.")
    if marker.read_text().strip() != cfg["REVIVE_COMMIT"]:
        raise ValueError("Current image and state must match before staging an upgrade.")
    image = "local/revive-adserver:" + args.commit
    subprocess.run(["docker", "build", "--build-arg", "REVIVE_COMMIT=" + args.commit,
                    "--build-arg", "REVIVE_VERSION=" + args.version,
                    "-t", image, str(ROOT)], check=True)
    dest = backup(restart=False)
    compose("--profile", "maintenance", "stop", "web", "maintenance")
    if (state / "var/.maintenance-enabled").exists():
        (state / "var/.maintenance-enabled").unlink()
    stamp = dest.name
    snapshot = state / ("previous-next-" + stamp)
    compose("cp", "web:/var/www/html/app", str(snapshot))
    os.rename(state / "previous", state / ("previous-retired-" + stamp))
    os.rename(snapshot, state / "previous")
    # Old plugin code stays paired with /previous, not mounted over the new image.
    for part in ("plugins", "admin-plugins"):
        os.rename(state / part, state / (part + "-retired-" + stamp))
        (state / part).mkdir(mode=0o750)
        os.chown(state / part, 33, 33)
    marker.write_text(args.commit + "\n")
    text = ENV_FILE.read_text()
    text = re.sub(r"(?m)^REVIVE_COMMIT=.*$", "REVIVE_COMMIT=" + args.commit, text)
    text = re.sub(r"(?m)^REVIVE_VERSION=.*$", "REVIVE_VERSION=" + args.version, text)
    ENV_FILE.write_text(text)
    # Seed directories without exposing the not-yet-upgraded application.
    compose("run", "--rm", "--no-deps", "web", "true")
    print("New source image staged; web remains stopped. Run upgrade next.")


def enable_maintenance(args):
    cfg = settings()
    version = query("SELECT value FROM rv_application_variable WHERE name='oa_version';").strip()
    if version != cfg["REVIVE_VERSION"]:
        raise ValueError("Database version does not match the image; finish the upgrade first.")
    enabled = Path(cfg["STATE_DIR"]) / "var/.maintenance-enabled"
    if not enabled.exists():
        private_write(enabled, "Scheduled maintenance enabled explicitly.\n")
        os.chown(enabled, 33, 33)
    compose("--profile", "maintenance", "up", "-d", "--wait", "maintenance")
    print("Scheduled maintenance enabled: minute 01 each hour, UTC.")


def verify(args):
    cfg = settings()
    state = Path(cfg["STATE_DIR"])
    prefix = args.prefix
    if not re.fullmatch(r"[a-zA-Z0-9_]+", prefix):
        raise ValueError("Invalid table prefix.")
    print("MariaDB:", query("SELECT VERSION();").strip())
    print("Tables:", query("SELECT COUNT(*) FROM information_schema.tables "
                           "WHERE table_schema='revive';").strip())
    print("Application version markers:")
    print(query(f"SELECT name,value FROM `{prefix}application_variable` "
                "WHERE name IN ('oa_version','openads_version','ox_version','version');").strip())
    filenames = query(f"SELECT filename FROM `{prefix}banners` "
                      "WHERE storagetype='web' AND filename<>'';").splitlines()
    missing = [name for name in filenames if
               PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
               or not (state / "images" / name).is_file()]
    print(f"Filesystem creative references: {len(filenames)}; missing: {len(missing)}")
    if missing:
        raise ValueError("Missing creative files; inspect locally before cutover.")
    host = f"http://127.0.0.1:{cfg['HTTP_PORT']}"
    paths = {"/healthz": 200, "/app/var/localhost.conf.php": 403,
             "/app/lib/": 403, "/app/etc/": 403, "/app/tests/": 403,
             "/app/www/images/probe.php": 403}
    for path, expected in paths.items():
        try:
            with urllib.request.urlopen(host + path, timeout=15) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        if status != expected:
            raise ValueError(f"HTTP protection failed: {path}: {status} != {expected}")
        print(f"HTTP {status}: {path}")


def main():
    global ENV_FILE
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(ROOT / ".env"),
                        help="Deployment environment file (allows independent staging/fresh instances).")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("init")
    p.add_argument("--state-dir", default=str(Path.home() / ".local/share/revive-staging"))
    p.add_argument("--project", default="revive-staging")
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(func=init)
    p = commands.add_parser("restore")
    for name in ("sql", "var", "images", "config"):
        p.add_argument("--" + name, required=True)
    p.set_defaults(func=restore)
    for name, func in (("backup", backup), ("upgrade", upgrade)):
        commands.add_parser(name).set_defaults(func=func)
    p = commands.add_parser("install")
    p.add_argument("--email", required=True)
    p.set_defaults(func=install)
    p = commands.add_parser("stage-release")
    p.add_argument("--commit", required=True)
    p.add_argument("--version", required=True)
    p.set_defaults(func=stage_release)
    commands.add_parser("enable-maintenance").set_defaults(func=enable_maintenance)
    p = commands.add_parser("verify")
    p.add_argument("--prefix", default="rv_")
    p.set_defaults(func=verify)
    p = commands.add_parser("up")
    p.set_defaults(func=lambda args: compose("up", "-d", "--wait", "db", "web"))
    p = commands.add_parser("build")
    p.set_defaults(func=lambda args: compose("build", "web"))
    args = parser.parse_args()
    ENV_FILE = Path(args.env_file).expanduser().resolve()
    try:
        args.func(args)
    except (ValueError, OSError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        # SQL/config contents and credentials are intentionally not interpolated.
        if isinstance(error, subprocess.CalledProcessError):
            print(f"Container operation failed (exit {error.returncode}).", file=sys.stderr)
        else:
            print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())