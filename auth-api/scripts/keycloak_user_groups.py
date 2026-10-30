#!/usr/bin/env python3
"""Pull Keycloak group allocations for a list of usernames.

Usage:
    ./keycloak_user_groups.py user1 user2 ...
    ./keycloak_user_groups.py -f usernames.txt [--format csv|json] [-o out.csv]

Required env vars:
    KEYCLOAK_BASE_URL   e.g. https://dev.loginproxy.gov.bc.ca
    KEYCLOAK_REALMNAME  e.g. bcregistry
    KC_CLIENT_ID        service-account client id (needs view-users / query-groups realm-management roles)
    KC_CLIENT_SECRET    service-account client secret

Optional env vars:
    KEYCLOAK_PATH_PREFIX  Default: /auth (use "" for Quarkus-based Keycloak)

Usernames are matched exactly. Output has one row per (username, group); users
that are not found, or have no groups, still get a row so nothing is silently dropped.
"""
import argparse
import csv
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


def get_token(base: str, realm: str, client_id: str, client_secret: str) -> str:
    resp = requests.post(
        f"{base}/realms/{realm}/protocol/openid-connect/token",
        data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        sys.exit(f"Token request failed: HTTP {resp.status_code} {resp.text}")
    return resp.json()["access_token"]


def get_user(session: requests.Session, admin: str, username: str) -> dict | None:
    resp = session.get(f"{admin}/users", params={"username": username, "exact": "true"}, timeout=TIMEOUT)
    resp.raise_for_status()
    users = [u for u in resp.json() if u.get("username", "").lower() == username.lower()]
    return users[0] if users else None


def get_groups(session: requests.Session, admin: str, user_id: str) -> list[dict]:
    resp = session.get(f"{admin}/users/{user_id}/groups", timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def read_usernames(args: argparse.Namespace) -> list[str]:
    names = list(args.usernames)
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            names += [line.strip() for line in f]
    seen = set()
    return [n for n in names if n and not (n in seen or seen.add(n))]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("usernames", nargs="*", help="Keycloak usernames")
    parser.add_argument("-f", "--file", help="File with one username per line")
    parser.add_argument("--format", choices=["csv", "json"], default="csv")
    parser.add_argument("-o", "--output", help="Output file (default: stdout)")
    args = parser.parse_args()

    usernames = read_usernames(args)
    if not usernames:
        parser.error("Provide at least one username (positional or via -f)")

    prefix = os.getenv("KEYCLOAK_PATH_PREFIX", "/auth")
    base = _env("KEYCLOAK_BASE_URL").rstrip("/") + prefix
    realm = _env("KEYCLOAK_REALMNAME")
    token = get_token(base, realm, _env("KC_CLIENT_ID"), _env("KC_CLIENT_SECRET"))

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"
    admin = f"{base}/admin/realms/{realm}"

    results = []
    for username in usernames:
        try:
            user = get_user(session, admin, username)
            if not user:
                results.append({"username": username, "user_id": None, "groups": None, "status": "NOT_FOUND"})
                continue
            groups = get_groups(session, admin, user["id"])
            results.append(
                {
                    "username": user["username"],
                    "user_id": user["id"],
                    "groups": [g["path"] for g in groups],
                    "status": "OK",
                }
            )
        except requests.RequestException as e:
            results.append({"username": username, "user_id": None, "groups": None, "status": f"ERROR: {e}"})

    out = open(args.output, "w", encoding="utf-8", newline="") if args.output else sys.stdout
    try:
        if args.format == "json":
            json.dump(results, out, indent=2)
            out.write("\n")
        else:
            writer = csv.writer(out)
            writer.writerow(["username", "user_id", "group_path", "status"])
            for r in results:
                for path in r["groups"] or [""]:
                    writer.writerow([r["username"], r["user_id"] or "", path, r["status"]])
    finally:
        if args.output:
            out.close()


if __name__ == "__main__":
    main()
