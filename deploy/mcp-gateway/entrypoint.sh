#!/bin/sh
set -eu
: "${MCP_UPSTREAM:?MCP_UPSTREAM is required, e.g. host.docker.internal:8931}"
: "${MCP_TOKEN:?MCP_TOKEN is required}"
export MCP_UPSTREAM_HOST="${MCP_UPSTREAM}"
envsubst '${MCP_UPSTREAM} ${MCP_UPSTREAM_HOST} ${MCP_TOKEN}' \
  < /etc/nginx/nginx.conf.template > /tmp/nginx.conf
exec nginx -c /tmp/nginx.conf -g 'daemon off;'
