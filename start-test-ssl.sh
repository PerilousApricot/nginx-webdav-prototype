#!/bin/bash

nerdctl run -p 8123:8123 \
   -v ./nginx/conf.d:/etc/nginx/conf.d:Z \
   -v ./nginx/lua:/etc/nginx/lua:Z \
   -v ./testcert.crt:/etc/nginx/hostcert.pem:Z \
   -v ./testcert.key:/etc/nginx/hostkey.pem:Z \
   -v ./data:/var/www/webdav:Z \
   -e PORT=8123 \
   -e USE_SSL=true \
   -e SSL_HOST_CERT=/etc/nginx/hostcert.pem \
   -e SSL_HOST_KEY=/etc/nginx/hostkey.pem \
   -e SSL_CERT_DIR=/etc/pki/ca-trust/extracted/openssl/ \
   -e DEBUG=true \
   nginx-webdav

