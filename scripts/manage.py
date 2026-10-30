#!/usr/bin/env python3
"""Optional Docker Compose helpers; never embeds instance data in the image."""
import argparse
import datetime
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
LOCAL = False
VERSION = "6.0.8"
ZIP_SHA256 = "8ea81978b876bfeeea1a985e2b9a2bc9a62b1e6fc9bad99e4756a933f588b38d"


def private_write(path, contents):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(contents.encode() if isinstance(contents, str) else contents)


def settings():
    if not ENV_FILE.is_file():
        raise ValueError("Create a private environment file using init or .env.example first.")
    values = dict(line.split("=", 1) for line in ENV_FILE.read_text().splitlines()
                  if line and not line.startswith("#") and "=" in line)
    if "REVIVE_COMMIT" in values:
        raise ValueError("Legacy development environment detected. Use a NEW env/project/volume set; do not downgrade existing data.")
    return values


def compose(*args, **kwargs):
    settings()
    command = ["docker", "compose", "--project-directory", str(ROOT),
               "--env-file", str(ENV_FILE), "-f", str(ROOT / "docker-compose.yml")]
    if LOCAL:
        command += ["-f", str(ROOT / "docker-compose.local.yml")]
    return subprocess.run([*command, *args], check=True, **kwargs)


def query(sql):
    result = compose(
        "exec", "-T", "db", "sh", "-c",
        'export MYSQL_PWD="$MARIADB_PASSWORD"; '
        'exec mariadb --user="$MARIADB_USER" --database="$MARIADB_DATABASE" -N -B',
        input=sql.encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.decode()


def init(args):
    if ENV_FILE.exists():
        raise ValueError("Refusing to overwrite an existing environment file.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.project):
        raise ValueError("Invalid project name.")
    if not 1 <= args.port <= 65535:
        raise ValueError("Invalid local port.")
    text = (ROOT / ".env.example").read_text()
    replacements = {
        "COMPOSE_PROJECT_NAME": args.project,
        "DB_PASSWORD": secrets.token_hex(32),
        "DB_ROOT_PASSWORD": secrets.token_hex(32),
        "HTTP_PORT": str(args.port),
        "REVIVE_HOST": args.host or f"localhost:{args.port}",
        "REVIVE_SCHEME": args.scheme,
    }
    if not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?", replacements["REVIVE_HOST"]):
        raise ValueError("Use a hostname without a URL scheme/path.")
    for key, value in replacements.items():
        text = re.sub(r"(?m)^" + key + r"=.*$", key + "=" + value, text)
    private_write(ENV_FILE, text.rstrip() + "\n")
    print(f"Private environment initialized: {ENV_FILE}; passwords were not printed.")


def install(args):
    username = getpass.getpass("New administrator username (hidden): ")
    password = getpass.getpass("New administrator password: ")
    if not username or len(password) < 12 or any(c in username + password + args.email for c in "\r\n"):
        raise ValueError("Use single-line values and a password of at least 12 characters.")
    compose("exec", "-T", "--user", "root", "web", "sh", "-c",
            'IFS= read -r REVIVE_ADMIN_USERNAME; IFS= read -r REVIVE_ADMIN_PASSWORD; '
            'IFS= read -r REVIVE_ADMIN_EMAIL; '
            'export REVIVE_ADMIN_USERNAME REVIVE_ADMIN_PASSWORD REVIVE_ADMIN_EMAIL; '
            'exec revive-install',
            input=(username + "\n" + password + "\n" + args.email + "\n").encode())


def backup(args):
    destination = Path(args.output).expanduser().resolve()
    if destination == ROOT or ROOT in destination.parents:
        raise ValueError("Put backups outside the source/build directory.")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = destination / stamp
    folder.mkdir(mode=0o700)
    running = compose("--profile", "maintenance", "ps", "--services", "--status",
                      "running", stdout=subprocess.PIPE).stdout.decode().splitlines()
    compose("--profile", "maintenance", "stop", "web", "maintenance")
    try:
        with (folder / "database.sql").open("xb") as output:
            compose("exec", "-T", "db", "sh", "-c",
                    'export MYSQL_PWD="$MARIADB_ROOT_PASSWORD"; '
                    'exec mariadb-dump -u root --lock-all-tables --hex-blob '
                    '--triggers "$MARIADB_DATABASE"', stdout=output)
        with (folder / "application-state.tar.gz").open("xb") as output:
            compose("run", "--rm", "--no-deps", "-T", "--entrypoint", "tar", "web",
                    "-czf", "-", "-C", "/var/www/html/app",
                    "var", "www/images", "plugins", "www/admin/plugins", stdout=output)
        private_write(folder / "deployment.env", ENV_FILE.read_bytes())
        private_write(folder / "metadata.json", json.dumps({
            "version": settings().get("REVIVE_VERSION", VERSION),
            "snapshot_utc": stamp,
            "note": "Restore matching database and application state together; deployment.env contains secrets.",
        }, indent=2))
    finally:
        for service in ("web", "maintenance"):
            if service in running:
                compose("--profile", "maintenance", "start", service)
    for path in folder.iterdir():
        if path.is_file():
            path.chmod(0o600)
    print(f"Consistent private backup: {folder}")


def verify(args):
    print("MariaDB:", query("SELECT VERSION();").strip())
    print("Revive version:", query(
        "SELECT value FROM rv_application_variable WHERE name='oa_version';").strip())
    code = (
        '$GLOBALS["argv"][1]=getenv("REVIVE_HOST"); require "init.php"; '
        'require_once "lib/OA/Upgrade/EnvironmentManager.php"; '
        '$manager=new OA_Environment_Manager(); $failed=[]; '
        'foreach ($manager->getFilePermissionErrors() as $check) { '
        'if (!empty($check["error"])) { $failed[]=$check["file"]; } } '
        'echo "Permission failures: ",count($failed),PHP_EOL; '
        'if ($failed) { throw new RuntimeException("Required paths are not writable"); }'
    )
    compose("exec", "-T", "--user", "www-data", "web", "php", "-r", code)
    compose("exec", "-T", "web", "curl", "--fail", "--silent", "http://127.0.0.1/healthz")


def enable_maintenance(args):
    compose("exec", "-T", "--user", "root", "web", "revive-enable-maintenance")
    compose("--profile", "maintenance", "up", "-d", "maintenance")


def main():
    global ENV_FILE, LOCAL
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--local", action="store_true", help="Include localhost port publishing.")
    sub = parser.add_subparsers(required=True)
    p = sub.add_parser("init")
    p.add_argument("--project", default="revive")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host")
    p.add_argument("--scheme", choices=("http", "https"), default="http")
    p.set_defaults(func=init)
    sub.add_parser("build").set_defaults(func=lambda args: compose("build", "web"))
    sub.add_parser("up").set_defaults(func=lambda args: compose("up", "-d", "--wait", "db", "web"))
    sub.add_parser("config").set_defaults(func=lambda args: compose("config", "--quiet"))
    sub.add_parser("verify").set_defaults(func=verify)
    sub.add_parser("enable-maintenance").set_defaults(func=enable_maintenance)
    p = sub.add_parser("install")
    p.add_argument("--email", required=True)
    p.set_defaults(func=install)
    p = sub.add_parser("backup")
    p.add_argument("--output", required=True)
    p.set_defaults(func=backup)
    args = parser.parse_args()
    ENV_FILE = Path(args.env_file).expanduser().resolve()
    LOCAL = args.local
    try:
        args.func(args)
    except subprocess.CalledProcessError as error:
        print(f"Container operation failed (exit {error.returncode}). Inspect private logs before retrying.", file=sys.stderr)
        return 1
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())