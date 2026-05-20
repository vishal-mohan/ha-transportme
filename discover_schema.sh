#!/bin/bash
# ============================================================
# TransportMe GraphQL Schema Discovery
# Run this to find the exact query and field names available.
# Usage:
#   chmod +x discover_schema.sh
#   ./discover_schema.sh                          # no auth
#   ./discover_schema.sh "YOUR_BEARER_TOKEN"      # with auth
# ============================================================

TOKEN=${1:-""}
AUTH_HEADER=""
if [ -n "$TOKEN" ]; then
  AUTH_HEADER="-H 'Authorization: Bearer $TOKEN'"
fi

echo "=== Querying root type names ==="
curl -s \
  -H 'Content-Type: application/json' \
  ${TOKEN:+-H "Authorization: Bearer $TOKEN"} \
  --url 'https://production.api2.transportme.com.au/' \
  --data '{"query":"{ __schema { queryType { fields { name description args { name type { name kind ofType { name kind } } } } } } }"}' \
  | python3 -m json.tool 2>/dev/null || cat

echo ""
echo "=== Done. Look for fields related to: vehicle, tracking, subscription, trip, location ==="
