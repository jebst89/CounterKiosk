#!/usr/bin/env bash
# Generate a self-signed TLS certificate for the Counter Kiosk admin web app.
#
# The admin panel is served over HTTPS so the password and session cookie never
# cross the LAN in cleartext. Because this is a self-signed cert, your browser
# will show a one-time "not trusted" warning — that is expected for a LAN device
# with no public certificate authority. You accept it once per browser.
#
# Usage:
#   ./generate_admin_cert.sh            # uses this machine's hostname
#   ./generate_admin_cert.sh 192.168.1.50   # also add an IP to the cert
#
# Writes admin_cert.pem and admin_key.pem next to this script (the paths in
# config.py: ADMIN_TLS_CERT / ADMIN_TLS_KEY). The private key is created 0600.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERT="$DIR/admin_cert.pem"
KEY="$DIR/admin_key.pem"

HOSTNAME_FQDN="$(hostname)"
EXTRA_IP="${1:-}"

# Build the subjectAltName list. Browsers validate the host against the SAN, so
# include the hostname, a .local mDNS variant, localhost, and an optional IP.
SAN="DNS:${HOSTNAME_FQDN},DNS:${HOSTNAME_FQDN}.local,DNS:localhost,IP:127.0.0.1"
if [ -n "$EXTRA_IP" ]; then
  SAN="${SAN},IP:${EXTRA_IP}"
fi

echo "Generating self-signed certificate..."
echo "  Subject CN : ${HOSTNAME_FQDN}"
echo "  SAN        : ${SAN}"

umask 077  # ensure the key is created with tight permissions
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$KEY" \
  -out "$CERT" \
  -days 3650 \
  -subj "/CN=${HOSTNAME_FQDN}" \
  -addext "subjectAltName=${SAN}"

chmod 600 "$KEY"
chmod 644 "$CERT"

echo
echo "Wrote:"
echo "  $CERT (certificate)"
echo "  $KEY  (private key, 0600)"
echo
echo "Restart the admin service to pick up the certificate:"
echo "  sudo systemctl restart counter-kiosk-admin.service"
