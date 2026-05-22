#!/bin/bash
# init-letsencrypt.sh  — run ONCE on the VPS before the first `docker compose up`
# Gets real Let's Encrypt certificates for both domains.
#
# Usage:
#   cd /path/to/proxy
#   chmod +x init-letsencrypt.sh
#   ./init-letsencrypt.sh

set -e

EMAIL="mervsen@gmail.com"
DOMAINS=("sciensinsta.com" "bulkinsta.com")
STAGING=0   # set to 1 to use LE staging server while testing

# ── Create the shared Docker network (idempotent) ────────────────────────────
docker network create web 2>/dev/null || echo "Network 'web' already exists, skipping."

# ── Download recommended TLS options (once) ──────────────────────────────────
docker compose run --rm --entrypoint "" certbot sh -c "
  if [ ! -f /etc/letsencrypt/options-ssl-nginx.conf ]; then
    curl -s https://raw.githubusercontent.com/certbot/certbot/master/certbot-nginx/certbot_nginx/_internal/tls_configs/options-ssl-nginx.conf \
      -o /etc/letsencrypt/options-ssl-nginx.conf
  fi
  if [ ! -f /etc/letsencrypt/ssl-dhparams.pem ]; then
    openssl dhparam -out /etc/letsencrypt/ssl-dhparams.pem 2048
  fi
"

STAGING_FLAG=""
[ "$STAGING" = "1" ] && STAGING_FLAG="--staging"

# ── Get a certificate for each domain ────────────────────────────────────────
for DOMAIN in "${DOMAINS[@]}"; do

  echo ""
  echo "### Processing $DOMAIN …"

  # Skip if cert already exists
  if docker compose run --rm --entrypoint "" certbot \
       test -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" 2>/dev/null; then
    echo "Certificate already exists for $DOMAIN — skipping."
    continue
  fi

  # Dummy cert so nginx can start before the real cert is issued
  echo "  Creating dummy certificate …"
  docker compose run --rm --entrypoint "" certbot sh -c "
    mkdir -p /etc/letsencrypt/live/$DOMAIN
    openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
      -keyout /etc/letsencrypt/live/$DOMAIN/privkey.pem \
      -out    /etc/letsencrypt/live/$DOMAIN/fullchain.pem \
      -subj   '/CN=localhost'
  "

done

# ── Start nginx with dummy certs ──────────────────────────────────────────────
echo ""
echo "### Starting nginx with dummy certificates …"
docker compose up -d nginx-proxy
echo "### Waiting for nginx to be ready …"
sleep 5

# ── Replace dummy certs with real ones ───────────────────────────────────────
for DOMAIN in "${DOMAINS[@]}"; do
  echo ""
  echo "### Requesting real certificate for $DOMAIN …"

  docker compose run --rm --entrypoint "" certbot \
    certbot certonly \
    --webroot --webroot-path /var/www/certbot \
    $STAGING_FLAG \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    --force-renewal \
    -d "$DOMAIN" -d "www.$DOMAIN"
done

# ── Reload nginx to use real certs ───────────────────────────────────────────
echo ""
echo "### Reloading nginx with real certificates …"
docker compose exec nginx-proxy nginx -s reload

echo ""
echo "✓  Done! Both domains are now secured."
echo ""
echo "   Add this cron job on the VPS for automatic renewal (twice daily):"
echo "   0 0,12 * * *  cd $(pwd) && docker compose run --rm certbot && docker compose exec nginx-proxy nginx -s reload >> /var/log/certbot-renew.log 2>&1"
