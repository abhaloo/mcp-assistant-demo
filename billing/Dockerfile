# syntax=docker/dockerfile:1

FROM node:20-bookworm AS assets
WORKDIR /app

COPY package.json package-lock.json ./
COPY vite.config.js ./
COPY build ./build
COPY resources ./resources
COPY public ./public
COPY app ./app

RUN npm ci --no-audit --no-fund \
    && npm run build

FROM dunglas/frankenphp:php8.4-bookworm AS vendor
WORKDIR /app

RUN install-php-extensions \
        pdo_mysql \
        gd \
        zip \
        bcmath \
        intl \
        opcache \
        redis \
        mbstring \
    && curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer

COPY composer.json composer.lock ./
RUN composer install --no-dev --no-scripts --no-interaction --prefer-dist

COPY . .
COPY --from=assets /app/public/build ./public/build
COPY --from=assets /app/public/css ./public/css

RUN mkdir -p \
        storage/framework/cache/data \
        storage/framework/sessions \
        storage/framework/views \
        storage/logs \
        bootstrap/cache \
    && printf '%s\n' \
        'APP_NAME=Laravel' \
        'APP_ENV=production' \
        'APP_KEY=base64:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=' \
        'APP_DEBUG=false' \
        'DB_CONNECTION=sqlite' \
        'DB_DATABASE=:memory:' \
        'CACHE_DRIVER=array' \
        'SESSION_DRIVER=array' \
        'QUEUE_CONNECTION=sync' \
        'BROADCAST_DRIVER=log' \
        > .env \
    && composer dump-autoload -o --no-scripts \
    && php artisan package:discover --ansi

FROM dunglas/frankenphp:php8.4-bookworm AS runtime
WORKDIR /app

RUN install-php-extensions \
        pdo_mysql \
        gd \
        zip \
        bcmath \
        intl \
        opcache \
        redis \
        mbstring \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        default-mysql-client \
        fontconfig \
        libfontconfig1 \
        libfreetype6 \
        libjpeg62-turbo \
        libpng16-16 \
        libx11-6 \
        libxcb1 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=vendor /app /app
COPY docker/Caddyfile /etc/caddy/Caddyfile
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
    && chmod +x /usr/local/bin/entrypoint.sh


EXPOSE 80
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
