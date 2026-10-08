#!/bin/sh
set -eu

if [ "$(id -u)" -eq 0 ]; then
    for path in /app/data /app/model-cache; do
        if [ -d "$path" ] && [ "$(stat -c '%u' "$path")" != "$(id -u bookdistill)" ]; then
            chown -R bookdistill:bookdistill "$path"
        fi
    done
    exec su -s /bin/sh bookdistill -c 'exec "$@"' sh -- "$@"
fi

exec "$@"
