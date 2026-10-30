#!/bin/sh
set -eu
app=/var/www/html/app
commit=$(cat /opt/revive-commit)
marker="$app/var/.docker-commit"
if [ -f "$marker" ] && [ "$(cat "$marker")" != "$commit" ]; then
    printf '%s\n' 'Commit mismatch: back up and run manage.py stage-release before starting this image.' >&2
    exit 1
fi
seed() {
    source="/opt/revive-seed/$1"
    target="$2"
    mkdir -p "$target"
    if [ ! -f "$target/.docker-seeded" ]; then
        cp -an "$source/." "$target/"
        touch "$target/.docker-seeded"
        chown -R www-data:www-data "$target"
    fi
}
seed var "$app/var"
seed plugins "$app/plugins"
seed admin-plugins "$app/www/admin/plugins"
seed images "$app/www/images"
printf '%s\n' "$commit" > "$marker"
chown www-data:www-data "$marker"
if [ "${1:-}" = maintenance ]; then
    exec gosu www-data revive-maintenance
fi
exec docker-php-entrypoint "$@"