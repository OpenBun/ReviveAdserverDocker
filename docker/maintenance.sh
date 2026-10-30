#!/bin/sh
set -eu
cd /var/www/html/app
while :; do
    if [ "$(date -u +%M)" = 01 ] && [ -f var/.maintenance-enabled ]; then
        php scripts/maintenance/maintenance.php "$REVIVE_HOST"
        sleep 61
    else
        sleep 15
    fi
done