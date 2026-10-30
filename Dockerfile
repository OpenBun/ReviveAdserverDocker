FROM php:8.4-apache-bookworm AS php-base
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl gosu libicu-dev libonig-dev libxml2-dev \
        libzip-dev libpng-dev libjpeg62-turbo-dev libfreetype6-dev \
    && docker-php-ext-configure gd --with-freetype --with-jpeg \
    && docker-php-ext-install -j"$(nproc)" intl mbstring mysqli xml zip gd opcache ftp \
    && rm -rf /var/lib/apt/lists/*

FROM php-base AS build
COPY --from=composer:2.8 /usr/bin/composer /usr/local/bin/composer
RUN apt-get update \
    && apt-get install -y --no-install-recommends git default-jre-headless unzip \
    && rm -rf /var/lib/apt/lists/* \
    && curl --fail --location --proto '=https' --proto-redir '=https' \
        https://archive.apache.org/dist/ant/binaries/apache-ant-1.7.1-bin.tar.gz \
        -o /tmp/ant.tar.gz \
    && printf '%s  /tmp/ant.tar.gz\n' 6344bb150f22dc335462bafa87de45085d51a5be | sha1sum --check --strict \
    && tar -xzf /tmp/ant.tar.gz -C /opt \
    && rm /tmp/ant.tar.gz
ENV PATH="/opt/apache-ant-1.7.1/bin:${PATH}" \
    COMPOSER_ALLOW_SUPERUSER=1
ARG REVIVE_COMMIT=5cc7dd9673af30302061c62a0207cbdfd03b7026
ARG REVIVE_VERSION=6.1.0-dev
WORKDIR /src
COPY docker/installer-compat.patch /tmp/installer-compat.patch
RUN git init . \
    && git remote add origin https://github.com/revive-adserver/revive-adserver.git \
    && git fetch --depth=1 origin "$REVIVE_COMMIT" \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "$REVIVE_COMMIT" \
    && git apply /tmp/installer-compat.patch \
    && ant package "-Dversion=${REVIVE_VERSION}" \
    && mkdir /dist \
    && tar -xzf "build/test-results/revive-adserver-${REVIVE_VERSION}.tar.gz" \
        --strip-components=1 -C /dist \
    && printf '%s\n' "$REVIVE_COMMIT" > /dist/SOURCE_COMMIT

FROM php-base AS runtime
ARG REVIVE_COMMIT=5cc7dd9673af30302061c62a0207cbdfd03b7026
ARG REVIVE_VERSION=6.1.0-dev
LABEL org.opencontainers.image.source="https://github.com/revive-adserver/revive-adserver" \
      org.opencontainers.image.revision="${REVIVE_COMMIT}" \
      org.opencontainers.image.version="${REVIVE_VERSION}"
LABEL local.revive.patches="installer-compat.patch"
COPY --from=build /dist /var/www/html/app
RUN mkdir -p /opt/revive-seed \
    && cp -a /var/www/html/app/var /opt/revive-seed/var \
    && cp -a /var/www/html/app/plugins /opt/revive-seed/plugins \
    && cp -a /var/www/html/app/www/admin/plugins /opt/revive-seed/admin-plugins \
    && cp -a /var/www/html/app/www/images /opt/revive-seed/images \
    && printf '%s\n' "$REVIVE_COMMIT" > /opt/revive-commit \
    && printf 'ok\n' > /var/www/html/healthz
COPY docker/apache.conf /etc/apache2/sites-available/000-default.conf
COPY docker/php.ini /usr/local/etc/php/conf.d/revive.ini
COPY docker/entrypoint.sh /usr/local/bin/revive-entrypoint
COPY docker/maintenance.sh /usr/local/bin/revive-maintenance
RUN chmod 755 /usr/local/bin/revive-entrypoint /usr/local/bin/revive-maintenance
WORKDIR /var/www/html/app
ENTRYPOINT ["revive-entrypoint"]
CMD ["apache2-foreground"]