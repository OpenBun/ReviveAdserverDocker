#!/bin/sh
set -eu
php /usr/local/lib/revive-runtime.php prepare
case "${1:-}" in
    maintenance) exec gosu www-data revive-maintenance ;;
    install) exec revive-install ;;
esac
exec docker-php-entrypoint "$@"