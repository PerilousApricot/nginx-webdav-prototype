#!/bin/bash
#
# Can be used to generate self-signed cert for testing
#

# See if a cert exists with 30 days validity left
if ! openssl x509 -checkend 2592000 -noout -in testcert.cert &>/dev/null; then
  openssl req -x509 -sha256 -days 365 -nodes -out testcert.crt -keyout testcert.key -subj "/CN=localhost"
fi
