Revive Adserver Docker
=====================

PHP 8.4 / Apache + MariaDB 11.4 LTS, with explicit installation, migration,
backup and upgrade commands. No database, configuration backup, credentials
or uploaded creatives are included in this repository.

Source build
------------
Builds https://github.com/revive-adserver/revive-adserver at commit:
  5cc7dd9673af30302061c62a0207cbdfd03b7026
The source version is 6.1.0-dev: this is a development build, not an official
stable release. The build runs upstream's Ant package target with locked
Composer dependencies and serves only the generated distribution.

docker/installer-compat.patch names the installer commands explicitly for
the pinned Symfony dependency. Future commits may need a different patch.
PHP/Composer/MariaDB base-image tags can move: record image digests for
production reproducibility and rollback.

Requirements
------------
Linux, Docker Engine, Docker Compose v2+, Python 3.12+, and permission to
manage Docker and assign state files to container UID/GID 33 (www-data).
The management utility uses only Python's standard library.

Fresh installation
------------------
Run from this repository:
  python3 scripts/manage.py init --state-dir /private/revive-state
  python3 scripts/manage.py build
  python3 scripts/manage.py install --email admin@example.com
  python3 scripts/manage.py verify
  python3 scripts/manage.py enable-maintenance

Install prompts invisibly for the new admin username/password and refuses
a nonempty database. Database passwords are generated privately; they are
not printed or stored in .env. New databases use InnoDB.
Open http://localhost:8080/app/www/admin/ for the default local deployment.

Migrating an existing Revive 5.5.0 installation
----------------------------------------------
Keep source backups outside this checkout and outside public web directories.
This restore helper specifically supports a MySQL SQL dump, a ZIP containing
var/, and a ZIP containing images/. Existing custom code/plugins require a
separate compatibility review; they cannot be recovered from these files.

  python3 scripts/manage.py init --state-dir /private/revive-migration
  python3 scripts/manage.py build
  python3 scripts/manage.py restore \
    --sql /private/legacy.sql \
    --var /private/legacy-var.zip \
    --images /private/legacy-images.zip \
    --config ads.example.com.conf.php
  python3 scripts/manage.py up
  python3 scripts/manage.py verify
  python3 scripts/manage.py upgrade

The last command prompts for the EXISTING Revive admin login and uses the
official CLI upgrader. Alternatively finish the browser wizard, specifying
/previous if asked for the previous installation path. Do not initialize a
fresh database over existing data. A failed upgrade must not be retried
blindly: inspect the log and restore a consistent snapshot if necessary.

The helper reconstructs previous bundled plugin files from the verified
official 5.5.0 archive. It excludes old logs/caches/compiled templates,
changes URLs to the staging hostname, disables automatic maintenance and
regenerates the staging delivery-signing secret.
Disabled obsolete openX3rdPartyServers registrations are removed because
that plugin was retired in 5.1. Enabled obsolete entries require review.

Plugin settings are preserved, not blindly enabled. If Invocation Code is
blank, check whether openXInvocationTags is disabled. Delivery, logging,
cache, targeting and reports also require their corresponding plugins.
The development build's optional apVideo requires the VAST component; an
old disabled video plugin can cause that post-upgrade task to fail.
An upgrade completion screen is not a substitute for plugin diagnostics.

MariaDB 11.4.5+ accepts MySQL 8 utf8mb4_0900_* collation names as aliases
for newer Unicode collations. SQL is not rewritten. Verify comparison,
sorting and uniqueness behavior on real data before production use.
Imported MyISAM tables remain MyISAM: --single-transaction alone does not
provide a consistent backup for them.

After upgrade, verify plugin diagnostics, sample campaign delivery, image
assets, clicks/conversions and historical statistics, then:
  python3 scripts/manage.py verify
  python3 scripts/manage.py enable-maintenance
The scheduler runs at minute 01 of each UTC hour as www-data. It refuses
to start while the database version differs from the configured image.

Private storage and backups
---------------------------
State is stored OUTSIDE the source/build directory. .env is private local
settings; .env.example is the only environment file intended for Git.
MariaDB data is in a named volume. No database port is exposed.
Never run "docker compose down -v" on data you want to retain.

  python3 scripts/manage.py backup

Backup stops local app writers, uses --lock-all-tables, archives matching
configuration/creatives/plugins, records deployment settings and secrets,
and restarts previously running services. The dated private path is printed.
Encrypt off-host copies and test restoration. Retain the matching image.
Recovery of a dated snapshot is an operator procedure: use a NEW isolated
project/database, restore database.sql and the matching state together, and
align DB secrets and URLs before starting the matching application image.
The restore subcommand handles the legacy input format, not dated snapshots.

Do not run an old image against an upgraded database. Production cutover
needs a final consistent snapshot and a plan for writes during DNS changes;
rolling back after new traffic arrives may discard new events.

Future GitHub source updates
----------------------------
Review upstream compatibility changes, then:
  python3 scripts/manage.py stage-release \
    --commit FULL_40_CHARACTER_COMMIT --version MATCHING_SOURCE_VERSION
  python3 scripts/manage.py upgrade
  python3 scripts/manage.py verify
  python3 scripts/manage.py enable-maintenance
The staging step builds first, backs up, snapshots previous code, retires
old plugin directories and stops web. Database migrations are never run
silently on ordinary container startup.

Multiple deployments
---------------------
  python3 scripts/manage.py --env-file /private/other.env init \
    --state-dir /private/other-state --project revive-other --port 8082
Supply the same --env-file before every subsequent subcommand. Use unique
project names, state directories and ports.

HTTPS / reverse proxy
---------------------
Default web access is loopback-only. compose.live.yaml optionally joins web
to an existing Docker proxy network. Set in the private .env:
  COMPOSE_FILE=compose.yaml:compose.live.yaml
  PROXY_NETWORK=your-existing-proxy-network
  PUBLIC_HOST=ads.example.com
  PUBLIC_SCHEME=https
Set these before restore/install. The supplied overlay's backend alias is
revive-adbun-staging on port 80; configure your proxy's domain/TLS route to it.
Apache recognizes the proxy's X-Forwarded-Proto: https header. Configure
trusted proxy access appropriately. The overlay alone does not issue TLS
certificates or configure a domain. Keep existing production tag paths stable.

Staging disables PHP sendmail and rejects external HTTP quickly using local
proxy variables. The frontend bridge disables IP masquerading, while DB and
maintenance use an internal network. These are not a complete egress firewall.
SMTP, GeoIP downloads and external integrations need deliberate production
configuration, secrets and network policy. Protect the management interface,
use HTTPS, and do not publish backup archives.

Checks
------
  python3 -m unittest discover -s tests -v
  python3 -m py_compile scripts/manage.py
  sh -n docker/entrypoint.sh docker/maintenance.sh
  docker compose config --quiet
  python3 scripts/manage.py verify

References
----------
  https://github.com/revive-adserver/revive-adserver
  https://www.revive-adserver.com/how-to/install/
  https://www.revive-adserver.com/how-to/update/
  https://www.revive-adserver.com/how-to/secure-your-installation/