#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_SH="${ROOT}/deploy.sh"

fail() {
  echo "[test_deploy_nginx_backend_routes] FAIL: $*" >&2
  exit 1
}

grep -Fq 'for route in "/docs" "/redoc" "/openapi.json" "/healthz" "/readyz" "/version" "/api-docs" "/api-docs/redoc" "/api-docs/openapi.json"' "${DEPLOY_SH}" ||
  fail "backend-only exact routes are not all proxied"

grep -Fq 'deploy_write_nginx_proxy_location "^~ /api-docs/"' "${DEPLOY_SH}" ||
  fail "Chinese API docs asset prefix is not proxied"

grep -Fq 'deploy_probe_json_url_expect_200' "${DEPLOY_SH}" ||
  fail "backend JSON probe is not enforced"

grep -Fq 'for path in "/healthz" "/readyz" "/version" "/openapi.json"' "${DEPLOY_SH}" ||
  fail "health/version/OpenAPI JSON paths are not probed"

grep -Fq '/api-docs/openapi.json' "${DEPLOY_SH}" ||
  fail "localized OpenAPI JSON path is not probed"

grep -Fq 'deploy_checklist_canonical_openapi "${port_note}"' "${DEPLOY_SH}" ||
  fail "canonical OpenAPI route gate is not called from the HTTP checklist"

grep -Fq '7gai_openapi_contract.json' "${DEPLOY_SH}" ||
  fail "canonical OpenAPI contract file is not wired into deploy.sh"

grep -Fq '已退休路径仍暴露' "${DEPLOY_SH}" ||
  fail "retired OpenAPI route rejection is not enforced"

grep -Fq '[[ "${DEPLOY_CHK_FAIL}" -eq 0 ]]' "${DEPLOY_SH}" ||
  fail "deploy_finalize does not fail closed on checklist failures"

echo "[test_deploy_nginx_backend_routes] OK"
