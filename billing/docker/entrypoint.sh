#!/bin/sh
set -e
cd /app
mkdir -p \
  storage/framework/cache/data \
  storage/framework/sessions \
  storage/framework/views \
  storage/logs \
  storage/app/public \
  storage/app/private \
  storage/app/livewire-tmp \
  bootstrap/cache
if [ ! -e public/storage ]; then
  php artisan storage:link || true
fi
exec frankenphp run --config /etc/caddy/Caddyfile
