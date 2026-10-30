#!/usr/bin/env bash
# Create an auto-provisioned GOVM org via a service-account (SYSTEM) token.
#
# Usage:
#   Set the env vars below, then run: ./create_auto_provisioned_govm.sh
#
# Required env vars:
#   AUTH_API_URL       e.g. https://auth-api-dev.apps.silver.devops.gov.bc.ca
#   KEYCLOAK_ISSUER    e.g. https://dev.loginproxy.gov.bc.ca/auth/realms/bcregistry
#   KC_CLIENT_ID       Keycloak service-account client id (must have the "system" role)
#   KC_CLIENT_SECRET   Keycloak service-account client secret
#
# Optional env vars:
#   ORG_NAME           Default: "Auto GOVM smoke test <timestamp>"
#   BRANCH_NAME        Default: "auto-provisioned"
#   PRODUCT_CODE       Default: "BUSINESS_SEARCH"
#   MAILING_STREET     Default: "1 Test Street"
#   MAILING_CITY       Default: "Victoria"
#   MAILING_REGION     Default: "BC"
#   MAILING_POSTAL     Default: "V8V 1V1"
#   MAILING_COUNTRY    Default: "CA"

set -euo pipefail

: "${AUTH_API_URL:?AUTH_API_URL is required}"
: "${KEYCLOAK_ISSUER:?KEYCLOAK_ISSUER is required}"
: "${KC_CLIENT_ID:?KC_CLIENT_ID is required}"
: "${KC_CLIENT_SECRET:?KC_CLIENT_SECRET is required}"

ORG_NAME="${ORG_NAME:-Auto GOVM smoke test $(date +%s)}"
BRANCH_NAME="${BRANCH_NAME:-auto-provisioned}"
PRODUCT_CODE="${PRODUCT_CODE:-BUSINESS_SEARCH}"
MAILING_STREET="${MAILING_STREET:-1 Test Street}"
MAILING_CITY="${MAILING_CITY:-Victoria}"
MAILING_REGION="${MAILING_REGION:-BC}"
MAILING_POSTAL="${MAILING_POSTAL:-V8V 1V1}"
MAILING_COUNTRY="${MAILING_COUNTRY:-CA}"

command -v jq >/dev/null || { echo "jq is required (brew install jq)" >&2; exit 1; }

echo "==> Fetching service-account token from $KEYCLOAK_ISSUER"
TOKEN=$(curl -sS --fail \
  -X POST "$KEYCLOAK_ISSUER/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials" \
  -u "$KC_CLIENT_ID:$KC_CLIENT_SECRET" \
  | jq -r .access_token)

if [[ -z "$TOKEN" || "$TOKEN" == "null" ]]; then
  echo "Failed to obtain access token" >&2
  exit 1
fi

echo "==> Creating auto-provisioned GOVM org: $ORG_NAME"
RESPONSE=$(curl -sS -w "\nHTTP_STATUS:%{http_code}" \
  -X POST "$AUTH_API_URL/api/v1/orgs" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "x-apikey: 65CBrSvCD6cwmUUyNiRZ7gPpwrM8XOlu" \
  -d "$(jq -n \
        --arg name "$ORG_NAME" \
        --arg branch "$BRANCH_NAME" \
        --arg product "$PRODUCT_CODE" \
        --arg street "$MAILING_STREET" \
        --arg city "$MAILING_CITY" \
        --arg region "$MAILING_REGION" \
        --arg postal "$MAILING_POSTAL" \
        --arg country "$MAILING_COUNTRY" \
        '{name: $name, branchName: $branch, accessType: "GOVM",
          productSubscriptions: [{productCode: $product}],
          mailingAddress: {street: $street, streetAdditional: "",
                           city: $city, region: $region,
                           postalCode: $postal, country: $country}}')")

HTTP_STATUS=$(echo "$RESPONSE" | sed -n 's/^HTTP_STATUS://p')
BODY=$(echo "$RESPONSE" | sed '/^HTTP_STATUS:/d')

echo "  POST /orgs -> HTTP $HTTP_STATUS"
echo "$BODY" | jq . 2>/dev/null || echo "$BODY"

if [[ "$HTTP_STATUS" != "201" ]]; then
  exit 1
fi

ORG_STATUS=$(echo "$BODY" | jq -r '.orgStatus // empty')
if [[ "$ORG_STATUS" != "ACTIVE" ]]; then
  echo "WARNING: expected orgStatus=ACTIVE, got '$ORG_STATUS'" >&2
fi
