#!/usr/bin/env python3
"""Pull Keycloak groups, group-derived roles and direct role mappings for users.

Usage:
    ./keycloak_user_roles.py "id1,id2,id3" -o roles.csv      # Keycloak user ids (UUIDs) and/or usernames
    ./keycloak_user_roles.py user1 user2 ...
    ./keycloak_user_roles.py -f users.txt                    # one per line, or comma separated
    ./keycloak_user_roles.py --search "crowe"                # fuzzy match on username/email/first/last name
    ./keycloak_user_roles.py "id1,id2" --diff-only           # only groups/roles that differ between the users
    ./keycloak_user_roles.py "id1,id2" --group-roles         # also add rows for roles that come via groups
    ./keycloak_user_roles.py "id1,id2" --effective           # also list effective (composite) realm roles
    ./keycloak_user_roles.py "id1,id2" --layout summary      # one row per user, roles as " | " lists
    ./keycloak_user_roles.py "id1,id2" --layout detail       # long format: one row per (user, group, role)

Each identifier that looks like a UUID is looked up as a Keycloak user id, anything else as an exact username.

Required env vars:
    KEYCLOAK_BASE_URL   e.g. https://dev.loginproxy.gov.bc.ca
    KEYCLOAK_REALMNAME  e.g. bcregistry
    KC_CLIENT_ID        service-account client id (needs view-users / query-groups / view-clients
                        realm-management roles)
    KC_CLIENT_SECRET    service-account client secret

Optional env vars:
    KEYCLOAK_PATH_PREFIX  Default: /auth (use "" for Quarkus-based Keycloak)

Layouts (--layout):

compare (default) - built for comparing users side by side. One row per group the users belong to
and per role mapped directly onto a user, and one column per user saying Yes/No:
    item         /group/path, realm role name, or client:role for client roles
    type         group | direct role | group role (group role rows only with --group-roles)
    users_with   how many of the found users have it, e.g. 3/5
    <username>   Yes / No for each user (blank if the user was not found)
  The first row is `status` (OK / NO_ROLES / NOT_FOUND / ERROR) for each user.
  --diff-only drops rows where every user has the same answer.

summary - one row per user, multi-value cells joined with " | ". Client roles are written as
client:role, and group roles as /group/path=>role (or client:role).
    groups                 groups the user is a member of
    group_roles            roles mapped onto those groups or their parent groups (subgroups inherit them)
    direct_realm_roles     realm roles mapped straight onto the user
    direct_client_roles    client roles mapped straight onto the user
    all_roles              de-duplicated union of group and direct roles
    effective_realm_roles  realm roles after composites are expanded (only with --effective)

detail - one row per (user, source, group, role), where `source` is one of
direct, group, inherited (from a parent group), effective, or membership (one row per group joined).
Identifiers that are not found still get a row so nothing is silently dropped.
"""
import argparse
import csv
import json
import os
import re
import sys
from urllib.parse import quote

import requests

TIMEOUT = 30
SEP = " | "
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
DETAIL_FIELDS = ["username", "user_id", "source", "group_path", "role_type", "client", "role", "status"]
SUMMARY_FIELDS = [
    "input", "username", "user_id", "first_name", "last_name", "email", "enabled",
    "groups", "group_roles", "direct_realm_roles", "direct_client_roles", "all_roles",
    "effective_realm_roles", "status",
]


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


def _get(session: requests.Session, url: str, **params):
    resp = session.get(url, params=params or None, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def find_users(session: requests.Session, admin: str, name: str, search: bool) -> list[dict]:
    if search:
        return _get(session, f"{admin}/users", search=name, max=100)
    if UUID_RE.match(name):
        resp = session.get(f"{admin}/users/{name}", timeout=TIMEOUT)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return [resp.json()]
    users = _get(session, f"{admin}/users", username=name, exact="true")
    return [u for u in users if u.get("username", "").lower() == name.lower()]


def flatten_mappings(mappings: dict) -> list[tuple[str, str, str]]:
    """Turn a Keycloak role-mappings payload into (role_type, client, role) tuples."""
    rows = [("realm", "", r["name"]) for r in mappings.get("realmMappings", [])]
    for client_id, client in (mappings.get("clientMappings") or {}).items():
        rows += [("client", client_id, r["name"]) for r in client.get("mappings", [])]
    return rows


class GroupRoles:
    """Caches group role lookups so shared groups are only fetched once."""

    def __init__(self, session: requests.Session, admin: str):
        self.session = session
        self.admin = admin
        self._by_path: dict[str, list[tuple[str, str, str]]] = {}

    def roles_for_path(self, path: str) -> list[tuple[str, str, str]]:
        if path not in self._by_path:
            group = _get(self.session, f"{self.admin}/group-by-path/{quote(path.lstrip('/'))}")
            self._by_path[path] = flatten_mappings(
                _get(self.session, f"{self.admin}/groups/{group['id']}/role-mappings")
            )
        return self._by_path[path]


def user_rows(session: requests.Session, admin: str, groups: GroupRoles, user: dict, effective: bool) -> list[dict]:
    base = {"username": user["username"], "user_id": user["id"], "status": "OK"}
    rows = []

    direct = _get(session, f"{admin}/users/{user['id']}/role-mappings")
    for role_type, client, role in flatten_mappings(direct):
        rows.append({**base, "source": "direct", "group_path": "", "role_type": role_type, "client": client, "role": role})

    for group in _get(session, f"{admin}/users/{user['id']}/groups"):
        path = group["path"]
        group_roles = [("group", path, r) for r in groups.roles_for_path(path)]
        # Subgroups inherit role mappings from every ancestor group.
        parts = path.strip("/").split("/")
        for i in range(1, len(parts)):
            parent = "/" + "/".join(parts[:i])
            group_roles += [("inherited", parent, r) for r in groups.roles_for_path(parent)]

        rows.append({**base, "source": "membership", "group_path": path, "role_type": "", "client": "", "role": ""})
        for source, from_path, (role_type, client, role) in group_roles:
            label = path if from_path == path else f"{path} (via {from_path})"
            rows.append(
                {**base, "source": source, "group_path": label, "from_group": from_path,
                 "role_type": role_type, "client": client, "role": role}
            )

    if effective:
        for r in _get(session, f"{admin}/users/{user['id']}/role-mappings/realm/composite"):
            rows.append(
                {**base, "source": "effective", "group_path": "", "role_type": "realm", "client": "", "role": r["name"]}
            )
    return rows


def _role_label(row: dict) -> str:
    return f"{row['client']}:{row['role']}" if row["role_type"] == "client" else row["role"]


def _uniq(values) -> list[str]:
    return list(dict.fromkeys(values))


def summarize(name: str, user: dict, rows: list[dict]) -> dict:
    by_source = lambda *sources: [r for r in rows if r["source"] in sources]  # noqa: E731
    group_rows = by_source("group", "inherited")
    direct_rows = by_source("direct")
    return {
        "input": name,
        "username": user["username"],
        "user_id": user["id"],
        "first_name": user.get("firstName", ""),
        "last_name": user.get("lastName", ""),
        "email": user.get("email", ""),
        "enabled": user.get("enabled", ""),
        "groups": _uniq(r["group_path"] for r in by_source("membership")),
        "group_roles": _uniq(f"{r['from_group']}=>{_role_label(r)}" for r in group_rows),
        "direct_realm_roles": _uniq(r["role"] for r in direct_rows if r["role_type"] == "realm"),
        "direct_client_roles": _uniq(_role_label(r) for r in direct_rows if r["role_type"] == "client"),
        "all_roles": sorted(set(_role_label(r) for r in group_rows + direct_rows)),
        "effective_realm_roles": _uniq(r["role"] for r in by_source("effective")),
        "status": "OK" if group_rows or direct_rows else "NO_ROLES",
    }


def user_items(rows: list[dict], group_roles: bool) -> set[tuple[str, str]]:
    """The (type, name) items a user has: their groups, direct roles and optionally group-derived roles."""
    items = set()
    for r in rows:
        if r["source"] == "membership":
            items.add(("group", r["group_path"]))
        elif r["source"] == "direct":
            items.add(("direct role", _role_label(r)))
        elif group_roles and r["source"] in ("group", "inherited"):
            items.add(("group role", _role_label(r)))
    return items


def build_compare(entries: list[dict], diff_only: bool) -> tuple[list[str], list[dict]]:
    """One row per group / direct role, one Yes/No column per user."""
    # Column headers are usernames; fall back to the input when two users share one (e.g. --search hits).
    headers = []
    for e in entries:
        header = e["username"]
        if header in headers:
            header = f"{header} ({e.get('user_id') or e['input']})"
        headers.append(header)

    found = [e for e in entries if "items" in e]
    order = {"group": 0, "direct role": 1, "group role": 2}
    all_items = sorted({i for e in found for i in e["items"]}, key=lambda i: (order[i[0]], i[1].lower()))

    rows = [{"item": "status", "type": "", "users_with": "", **{h: e["status"] for h, e in zip(headers, entries)}}]
    for item_type, name in all_items:
        has = {h: (item_type, name) in e["items"] for h, e in zip(headers, entries) if "items" in e}
        if diff_only and len(set(has.values())) == 1:
            continue
        rows.append(
            {
                "item": name,
                "type": item_type,
                "users_with": f"{sum(has.values())}/{len(has)}",
                **{h: ("Yes" if has[h] else "No") if h in has else "" for h in headers},
            }
        )
    return ["item", "type", "users_with", *headers], rows


def read_names(args: argparse.Namespace) -> list[str]:
    raw = list(args.usernames)
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            raw += f.read().splitlines()
    names = [n.strip() for item in raw for n in item.split(",")]
    seen = set()
    return [n for n in names if n and not (n in seen or seen.add(n))]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("usernames", nargs="*", help="Keycloak user ids and/or usernames, comma or space separated")
    parser.add_argument("-f", "--file", help="File with user ids/usernames, one per line or comma separated")
    parser.add_argument("--search", action="store_true", help="Fuzzy search on username/email/first/last name")
    parser.add_argument("--effective", action="store_true", help="Also list effective (composite) realm roles")
    parser.add_argument("--layout", choices=["compare", "summary", "detail"], default="compare")
    parser.add_argument("--diff-only", action="store_true", help="compare: only rows that differ between users")
    parser.add_argument("--group-roles", action="store_true", help="compare: also add rows for roles that come via groups")
    parser.add_argument("--format", choices=["csv", "json"], default="csv")
    parser.add_argument("-o", "--output", help="Output file (default: stdout)")
    args = parser.parse_args()

    names = read_names(args)
    if not names:
        parser.error("Provide at least one user id or username (positional or via -f)")

    prefix = os.getenv("KEYCLOAK_PATH_PREFIX", "/auth")
    base = _env("KEYCLOAK_BASE_URL").rstrip("/") + prefix
    realm = _env("KEYCLOAK_REALMNAME")
    token = get_token(base, realm, _env("KC_CLIENT_ID"), _env("KC_CLIENT_SECRET"))

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"
    admin = f"{base}/admin/realms/{realm}"
    groups = GroupRoles(session, admin)

    # One entry per user found (or per identifier that failed), holding the raw detail rows.
    entries = []
    for i, name in enumerate(names, 1):
        print(f"[{i}/{len(names)}] {name}", file=sys.stderr)
        try:
            users = find_users(session, admin, name, args.search)
            if not users:
                entries.append({"input": name, "username": name, "status": "NOT_FOUND"})
            for user in users:
                rows = user_rows(session, admin, groups, user, args.effective)
                has_roles = any(r["source"] in ("direct", "group", "inherited") for r in rows)
                entries.append(
                    {
                        "input": name, "user": user, "rows": rows, "items": user_items(rows, args.group_roles),
                        "username": user["username"], "user_id": user["id"], "email": user.get("email", ""),
                        "status": "OK" if has_roles else "NO_ROLES",
                    }
                )
        except requests.RequestException as e:
            entries.append({"input": name, "username": name, "status": f"ERROR: {e}"})

    if args.layout == "compare":
        fields, results = build_compare(entries, args.diff_only)
    elif args.layout == "summary":
        fields = SUMMARY_FIELDS
        results = [
            summarize(e["input"], e["user"], e["rows"]) if "user" in e
            else {"input": e["input"], "username": e["username"], "status": e["status"]}
            for e in entries
        ]
    else:
        fields = DETAIL_FIELDS
        results = []
        for e in entries:
            if e.get("rows"):
                results += e["rows"]
            else:
                results.append({"username": e["username"], "user_id": e.get("user_id", ""), "status": e["status"]})

    out = open(args.output, "w", encoding="utf-8", newline="") if args.output else sys.stdout
    try:
        if args.format == "json":
            json.dump(results, out, indent=2)
            out.write("\n")
        else:
            writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for r in results:
                writer.writerow({k: SEP.join(v) if isinstance(v, list) else v for k, v in r.items()})
    finally:
        if args.output:
            out.close()


if __name__ == "__main__":
    main()
