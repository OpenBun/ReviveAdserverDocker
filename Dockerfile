FROM php:8.4-apache-bookworm AS php-base

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl unzip gosu libicu-dev libonig-dev libxml2-dev \
        libzip-dev libpng-dev libjpeg62-turbo-dev libfreetype6-dev \
    && docker-php-ext-configure gd --with-freetype --with-jpeg \
    && docker-php-ext-install -j"$(nproc)" intl mbstring mysqli xml zip gd opcache ftp \
    && rm -rf /var/lib/apt/lists/*

# Use the official packaged distribution, never a Git checkout or master ZIP.
FROM php-base AS distribution
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*
COPY docker/download-release.py /usr/local/bin/download-release.py
ARG REVIVE_VERSION=6.0.8
ARG REVIVE_SHA256=8ea81978b876bfeeea1a985e2b9a2bc9a62b1e6fc9bad99e4756a933f588b38d
RUN test -n "$REVIVE_SHA256" \
    && python3 /usr/local/bin/download-release.py \
        --url "https://download.revive-adserver.com/revive-adserver-${REVIVE_VERSION}.zip" \
        --sha256 "$REVIVE_SHA256" --output /tmp/revive.zip \
    && printf '%s  /tmp/revive.zip\n' "$REVIVE_SHA256" | sha256sum --check --strict \
    && unzip -q /tmp/revive.zip -d /tmp/revive-release \
    && test -f "/tmp/revive-release/revive-adserver-${REVIVE_VERSION}/constants.php" \
    && mv "/tmp/revive-release/revive-adserver-${REVIVE_VERSION}" /var/www/html/app \
    && rm -rf /tmp/revive.zip /tmp/revive-release \
    && mkdir -p /opt/revive-seed \
    && cp -a /var/www/html/app/var /opt/revive-seed/var \
    && cp -a /var/www/html/app/plugins /opt/revive-seed/plugins \
    && cp -a /var/www/html/app/www/admin/plugins /opt/revive-seed/admin-plugins \
    && cp -a /var/www/html/app/www/images /opt/revive-seed/images \
    && printf '%s\n' "$REVIVE_VERSION" > /opt/revive-version \
    && printf '%s:%s\n' "$REVIVE_VERSION" "$REVIVE_SHA256" > /opt/revive-release

FROM php-base AS runtime
ARG REVIVE_VERSION=6.0.8
ARG REVIVE_SHA256=8ea81978b876bfeeea1a985e2b9a2bc9a62b1e6fc9bad99e4756a933f588b38d
LABEL org.opencontainers.image.title="Revive Adserver official distribution" \
      org.opencontainers.image.url="https://www.revive-adserver.com/download/" \
      org.opencontainers.image.version="${REVIVE_VERSION}" \
      org.opencontainers.image.revision="${REVIVE_SHA256}"
COPY --from=distribution /var/www/html/app /var/www/html/app
COPY --from=distribution /opt/revive-seed /opt/revive-seed
COPY --from=distribution /opt/revive-version /opt/revive-version
COPY --from=distribution /opt/revive-release /opt/revive-release
RUN printf 'ok\n' > /var/www/html/healthz

COPY docker/apache.conf /etc/apache2/sites-available/000-default.conf
COPY docker/php.ini /usr/local/etc/php/conf.d/revive.ini
COPY docker/entrypoint.sh /usr/local/bin/revive-entrypoint
COPY docker/install.sh /usr/local/bin/revive-install
COPY docker/enable-maintenance.sh /usr/local/bin/revive-enable-maintenance
COPY docker/maintenance.sh /usr/local/bin/revive-maintenance
COPY docker/runtime.php /usr/local/lib/revive-runtime.php
RUN chmod 755 /usr/local/bin/revive-entrypoint /usr/local/bin/revive-install \
    /usr/local/bin/revive-enable-maintenance /usr/local/bin/revive-maintenance

VOLUME ["/var/www/html/app/var", "/var/www/html/app/www/images", "/var/www/html/app/plugins", "/var/www/html/app/www/admin/plugins"]
WORKDIR /var/www/html/app
ENTRYPOINT ["revive-entrypoint"]
CMD ["apache2-foreground"]