#!/usr/bin/env python3
"""Get a service account access token from Keycloak (client_credentials grant).

Usage:
    ./keycloak_service_account_token.py                 # prints the raw access token
    ./keycloak_service_account_token.py --decode        # also prints the decoded claims to stderr
    ./keycloak_service_account_token.py --full          # prints the full token response as JSON

    export TOKEN=$(./keycloak_service_account_token.py)
    curl -H "Authorization: Bearer $TOKEN" ...

Required env vars:
    KEYCLOAK_BASE_URL   e.g. https://dev.loginproxy.gov.bc.ca
    KEYCLOAK_REALMNAME  e.g. bcregistry
    KC_CLIENT_ID        service-account client id
    KC_CLIENT_SECRET    service-account client secret

Optional env vars:
    KEYCLOAK_PATH_PREFIX  Default: /auth (use "" for Quarkus-based Keycloak)
    KC_SCOPE              Extra scopes to request, space separated

The client must have "Service accounts roles" enabled in Keycloak.
"""
import argparse
import base64
import json
import os
import sys

import requests

TIMEOUT = 30


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        sys.exit(f"Missing required env var: {name}")
    return value


def get_token_response(token_url: str, client_id: str, client_secret: str, scope: str | None) -> dict:
    data = {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}
    if scope:
        data["scope"] = scope
    resp = requests.post(token_url, data=data, timeout=TIMEOUT)
    if resp.status_code != 200:
        sys.exit(f"Token request failed: HTTP {resp.status_code} {resp.text}")
    return resp.json()


def decode_claims(token: str) -> dict:
    """Decode the JWT payload without verifying the signature (inspection only)."""
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--decode", action="store_true", help="print decoded token claims to stderr")
    parser.add_argument("--full", action="store_true", help="print the full token response as JSON")
    args = parser.parse_args()

    base = _env("KEYCLOAK_BASE_URL").rstrip("/")
    realm = _env("KEYCLOAK_REALMNAME")
    prefix = os.getenv("KEYCLOAK_PATH_PREFIX", "/auth")
    token_url = f"{base}{prefix}/realms/{realm}/protocol/openid-connect/token"

    token_response = get_token_response(
        token_url, _env("KC_CLIENT_ID"), _env("KC_CLIENT_SECRET"), os.getenv("KC_SCOPE")
    )
    access_token = token_response["access_token"]

    if args.decode:
        print(json.dumps(decode_claims(access_token), indent=2), file=sys.stderr)

    if args.full:
        print(json.dumps(token_response, indent=2))
    else:
        print(access_token)


if __name__ == "__main__":
    main()
