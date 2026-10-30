Revive Adserver 6.0.8 - Official Release Docker Deployment
========================================================

PUBLIC repository: no database dump, private configuration, uploaded creatives,
customer plugin snapshot or credentials are included.

Official package:
  https://download.revive-adserver.com/revive-adserver-6.0.8.zip
SHA-256:
  8ea81978b876bfeeea1a985e2b9a2bc9a62b1e6fc9bad99e4756a933f588b38d

Dockerfile downloads this ZIP over HTTPS and verifies its checksum before
extracting it. It does NOT clone GitHub master or install development source.
The packaged official CLI/dependencies/plugins are used without the old
GitHub-development compatibility patch.
The HTTPS download helper runs in a disposable Python build stage (to handle
the official endpoint reliably); Python is not installed in the runtime image.

Stack: PHP 8.4 + Apache, MariaDB 11.4, optional hourly maintenance worker.
Requirements: Docker Engine, Docker Compose v2.20+; Python 3.10+ only if using
the optional management helper. Record base-image digests if a completely
reproducible build is required: PHP/MariaDB tags can receive updates.

IMPORTANT: This is for new 6.0.8 deployments or a planned supported migration.
Never attach a 6.1.0-dev/newer database or its volumes to this image. This is
not a downgrade for an existing development-version/private deployment.

Files
-----
Dockerfile                    Official ZIP image build
docker-compose.yml            Canonical deployment, no host ports by default
docker-compose.local.yml      Optional localhost:8080 port publishing
compose.yaml                  Compatibility alias for docker-compose.yml
.env.example                  Settings template; real .env must stay private
docker/runtime.php            First-start seeding, permission repair, CLI install
scripts/manage.py             Optional environment/install/backup/check helpers

Docker Compose quick start
--------------------------
Run in a NEW checkout/deployment:
  git clone https://github.com/OpenBun/ReviveAdserverDocker.git
  cd ReviveAdserverDocker
  cp .env.example .env

Edit .env and set DIFFERENT long random values for DB_PASSWORD and
DB_ROOT_PASSWORD. Leave real credentials out of Git and screenshots.
For local access leave REVIVE_HOST=localhost:8080 and REVIVE_SCHEME=http.

Validate, build and launch:
  docker compose -f docker-compose.yml -f docker-compose.local.yml config --quiet
  docker compose -f docker-compose.yml -f docker-compose.local.yml up --build -d

Open http://localhost:8080/app/www/admin/ and complete the browser installer:
  Database host: db
  Port: 3306
  Database/user: DB_NAME and DB_USER from your private .env (default revive)
  Password: DB_PASSWORD from your private .env

Use a strong administrator password, different from the database password.
The installer is only for an EMPTY database. Protect initial setup from
untrusted visitors until installation is finished.

Optional helper instead of manually generating .env passwords:
  python3 scripts/manage.py init
  python3 scripts/manage.py build
  python3 scripts/manage.py --local up
  python3 scripts/manage.py install --email admin@example.com
  python3 scripts/manage.py verify
The install command prompts invisibly for admin credentials, sends them over
stdin rather than command-line arguments and never prints database passwords.
It disables request-triggered maintenance; enable the worker afterwards.

Environment-driven installation
--------------------------------
For a fresh EMPTY database, set these privately before first deployment:
  REVIVE_AUTO_INSTALL=1
  REVIVE_ADMIN_USERNAME=your-unique-admin-name
  REVIVE_ADMIN_PASSWORD=your-long-admin-password
  REVIVE_ADMIN_EMAIL=your-admin-email@example.com
The official CLI installer runs once. Existing configuration is preserved;
it does not recreate the admin on redeploy and never auto-upgrades a schema.
After successful install, remove the admin password from the environment and
set REVIVE_AUTO_INSTALL=0. Do not use automatic installation to restore data.

Easypanel option A - Compose service (includes MariaDB)
------------------------------------------------------
1. Create/open a project -> New Service -> Compose -> Git source.
2. Repository: https://github.com/OpenBun/ReviveAdserverDocker.git
   Branch: main
   Build Path: /
   Docker Compose File: docker-compose.yml
3. In Environment enable Create .env file. Use .env.example as the template.
   Set DB_PASSWORD and DB_ROOT_PASSWORD to two different strong values.
   Set REVIVE_HOST to your actual domain (no scheme or path).
   Set REVIVE_SCHEME=https.
   For no browser DB setup, opt into environment-driven installation above.
4. Deploy. Builds/downloads run during deployment; DB health must pass first.
5. Domains -> your public hostname -> internal service web -> port 80 ->
   internal HTTP, public HTTPS enabled. Point DNS to the Easypanel server.
6. Open https://YOUR-DOMAIN/app/www/admin/. Finish the browser wizard if you
   did not opt into CLI installation; otherwise log in with the admin account.

There are no container_name or host ports in the canonical Compose file.
Easypanel manages routing/TLS. Do not add the local port overlay in Easypanel
and do not route a public domain to the database.
Compose automatically creates all five persistent named volumes. No manual
host folders, secret-file uploads, SSH copying or instance data in Git needed.

Easypanel option B - App service with Dockerfile + separate MariaDB
-----------------------------------------------------------------
1. Create a MariaDB service in the SAME Easypanel project. Leave its database
   EMPTY for a fresh installation; note the internal hostname and credentials.
2. Create an APP service. Source: GitHub OpenBun/ReviveAdserverDocker.
   Branch main, Build Path /, Builder Dockerfile, Dockerfile Path Dockerfile.
   Do NOT choose pasted inline Dockerfile source: docker/ files are required.
3. Storage -> add four persistent VOLUME mounts:
   revive-var           -> /var/www/html/app/var
   revive-images        -> /var/www/html/app/www/images
   revive-plugins       -> /var/www/html/app/plugins
   revive-admin-plugins -> /var/www/html/app/www/admin/plugins
   Do not mount system /var or the entire app directory.
4. In APP Environment set:
   REVIVE_HOST=ads.example.com
   REVIVE_SCHEME=https
   REVIVE_DB_HOST=YOUR_EASYPANEL_INTERNAL_DB_HOST
   REVIVE_DB_PORT=3306
   REVIVE_DB_NAME=YOUR_DATABASE
   REVIVE_DB_USER=YOUR_DATABASE_USER
   REVIVE_DB_PASSWORD=YOUR_DATABASE_PASSWORD
   TZ=UTC
   For automatic fresh install, also set REVIVE_AUTO_INSTALL=1 and the three
   REVIVE_ADMIN_* variables. Keep those values private.
   Use internal DB port 3306, not a published forwarding port or localhost.
5. Domains: actual hostname, container port 80, internal HTTP, public HTTPS.
6. Keep one replica/default start command; deploy and complete setup/login.

The image alone does not start MariaDB. These APP variables are distinct from
the Compose DB_PASSWORD/DB_USER variables: Compose maps them automatically.
The Dockerfile does not consume database/admin passwords as build arguments.
When config exists, runtime settings update DB/domain/local storage paths
without resetting unrelated settings or installed plugins.

Persistence and permissions
----------------------------
The DB uses mariadb-data. Four application volumes hold var, images, plugins
and admin plugin files. Ordinary redeployment seeds packaged defaults once
and preserves existing files. Required mutable paths use www-data 33:33,
directories 0750 and files 0640, including packaged cache README files.
Symlinked mutable paths are refused instead of changing files outside them.
Never delete volumes or run docker compose down -v on data you want to retain.
Back up and review state before changing release/checksum/volume definitions.

Release markers prevent silently overlaying another release's plugin data.
Legacy development/private-bootstrap state is refused. A configured database
newer than the image is also refused. Do not bypass these checks to downgrade.

Maintenance and email
---------------------
With Compose, after installation:
  docker compose -f docker-compose.yml exec --user root web revive-enable-maintenance
  docker compose -f docker-compose.yml --profile maintenance up -d maintenance
Or: python3 scripts/manage.py enable-maintenance

This disables request-triggered maintenance and enables the worker at minute
01 of every UTC hour. You can put COMPOSE_PROFILES=maintenance in Easypanel's
Compose environment AFTER setup, run revive-enable-maintenance in web's shell,
and redeploy so Easypanel manages the worker. Run only one scheduler.

For a single App service use a host scheduler or a dedicated worker with the
same state, running as www-data once each hour:
  php /var/www/html/app/scripts/maintenance/maintenance.php YOUR-DOMAIN
Do not disable built-in automatic maintenance until a reliable scheduler is
in place. CLI installation disables it intentionally: configure scheduling.

PHP sendmail is disabled. Configure the bundled Mailer plugin for SMTP or
another supported provider. Outbound HTTP is not blocked by dummy proxies;
configure production egress policy and any GeoIP account/license deliberately.

Backups, migration and future updates
-------------------------------------
Optional Compose backup:
  python3 scripts/manage.py backup --output /private/revive-backups
It pauses app writers/worker, dumps with table locks, archives the four app
paths plus a secret-bearing environment copy, then restarts running services.
Keep backups private/encrypted/off-server and test restoration separately.
Easypanel DB backups and four App volume backups must be coordinated.
Imported legacy MyISAM tables need locking/write pauses; transactions alone
do not make a consistent backup. Do not copy a live DB data directory.

For older Revive migrations, restore a consistent DB + config + creatives +
matching plugin files into a SEPARATE staging deployment first, adjust the
database/domain settings, provide previous-installation files privately and
follow Revive's authenticated upgrade process. Third-party plugins need review.
Do not publish those files here or run the fresh installer over existing data.

Changing a version/checksum is not a complete upgrade process. Review release
notes, back up CURRENT DB/volumes, rehearse, pause writers and run the supported
upgrade explicitly. Never fix a mismatch by deleting markers or old volumes.
Image rollback does not undo a database migration. Restore matching DB/files,
and account for writes accepted after cutover.

Validation
----------
  python3 -m unittest discover -s tests -v
  python3 -m py_compile scripts/manage.py
  php -l docker/runtime.php
  php tests/runtime_test.php
  sh -n docker/entrypoint.sh docker/install.sh docker/maintenance.sh
  docker compose --env-file .env -f docker-compose.yml config --quiet
  python3 scripts/manage.py verify
Permission tests require a root/container context.
/healthz tests Apache only: also test real zones, delivery, tracking, reports
and maintenance. No default admin account or demo campaigns are bundled.

References
----------
  https://www.revive-adserver.com/download/
  https://www.revive-adserver.com/how-to/install/
  https://www.revive-adserver.com/how-to/update/
  https://www.revive-adserver.com/how-to/secure-your-installation/
  https://easypanel.io/docs/services/compose
  https://easypanel.io/docs/services/app