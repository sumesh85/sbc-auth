#!/usr/bin/env python3
"""
Analyze auth-api / pay-api log-based metrics for the past N days.
Reports top endpoints by average RPS and by latency (mean, p95, p99).

Usage: python3 top-endpoints.py [dev|test|sandbox|prod] [--days N] [--top N]

Examples:
  python3 top-endpoints.py prod
  python3 top-endpoints.py prod --days 3 --top 30
"""
import json, subprocess, sys, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

ENVS = {
    "dev":     ("gtksf3-dev",   [("auth-api-dev",     "auth_api")]),
    "test":    ("gtksf3-test",  [("auth-api-test",    "auth_api")]),
    "sandbox": ("gtksf3-tools", [("auth-api-sandbox", "auth_api")]),
    "prod":    ("gtksf3-prod",  [("auth-api-prod",    "auth_api"),
                                  ("pay-api-prod",     "pay_api")]),
}

EP_WIDTH = 62


def parse_args():
    args = sys.argv[1:]
    env = next((a for a in args if a in ENVS), "")
    days = int(next((args[i + 1] for i, a in enumerate(args) if a == "--days" and i + 1 < len(args)), 7))
    top_n = int(next((args[i + 1] for i, a in enumerate(args) if a == "--top"  and i + 1 < len(args)), 20))
    return env, days, top_n


def get_token():
    r = subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True)
    t = r.stdout.strip()
    if not t:
        sys.exit("Error: no gcloud token — run 'gcloud auth login' first.")
    return t


def query(project, token, metric_name, service, aligner, reducer, start, end, window_secs):
    url = f"https://monitoring.googleapis.com/v3/projects/{project}/timeSeries"
    group_fields = [
        "metric.labels.route_group",
        "metric.labels.sub_route",
        "metric.labels.has_id",
        "metric.labels.method",
        "metric.labels.api_version",
    ]
    f = (
        f'metric.type="logging.googleapis.com/user/{metric_name}" '
        f'resource.labels.service_name="{service}"'
    )
    parts = (
        [
            f"filter={urllib.parse.quote(f)}",
            f"interval.startTime={urllib.parse.quote(start)}",
            f"interval.endTime={urllib.parse.quote(end)}",
            f"aggregation.alignmentPeriod={window_secs}s",
            f"aggregation.perSeriesAligner={aligner}",
            f"aggregation.crossSeriesReducer={reducer}",
        ]
        + [f"aggregation.groupByFields={urllib.parse.quote(gf)}" for gf in group_fields]
    )
    req = urllib.request.Request(f"{url}?{'&'.join(parts)}", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp).get("timeSeries", [])
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"    API error {e.code}: {body[:400]}")
        return []


def ep_label(labels):
    route   = labels.get("route_group") or "?"
    sub     = labels.get("sub_route")   or ""
    has_id  = labels.get("has_id")      or ""
    method  = labels.get("method")      or "?"
    version = labels.get("api_version") or "?"
    path = f"/api/{version}/{route}"
    if has_id:
        path += "/{id}"
    if sub:
        path += f"/{sub}"
    return f"{method:<7}{path}"


def scalar(ts):
    pts = ts.get("points", [])
    if not pts:
        return None
    v = pts[0]["value"]
    if "doubleValue" in v:
        return v["doubleValue"]
    if "int64Value" in v:
        return float(v["int64Value"])
    if "distributionValue" in v:
        # ALIGN_DELTA returns a merged distribution; extract its mean
        return v["distributionValue"].get("mean", 0.0)
    return None


def print_rps_table(series, top_n):
    rows = [(scalar(ts), ep_label(ts["metric"].get("labels", {}))) for ts in series]
    rows = [(v, ep) for v, ep in rows if v is not None]
    rows.sort(reverse=True)
    print(f"\n  {'Endpoint':<{EP_WIDTH}}  {'avg RPS':>9}")
    print(f"  {'─'*EP_WIDTH}  {'─'*9}")
    for rps, ep in rows[:top_n]:
        print(f"  {ep:<{EP_WIDTH}}  {rps:>9.4f}")
    if not rows:
        print("  (no data)")


def to_map(series):
    m = {}
    for ts in series:
        v = scalar(ts)
        if v is not None:
            m[ep_label(ts["metric"].get("labels", {}))] = v
    return m


def print_latency_table(mean_series, p95_series, p99_series, top_n):
    mean_map = to_map(mean_series)
    p95_map  = to_map(p95_series)
    p99_map  = to_map(p99_series)

    all_eps = set(mean_map) | set(p95_map) | set(p99_map)
    rows = sorted(
        [(p99_map.get(ep, 0), p95_map.get(ep, 0), mean_map.get(ep, 0), ep) for ep in all_eps],
        reverse=True,
    )
    print(f"\n  {'Endpoint':<{EP_WIDTH}}  {'mean':>7}  {'p95':>7}  {'p99':>7}")
    print(f"  {'─'*EP_WIDTH}  {'─'*7}  {'─'*7}  {'─'*7}")
    for p99, p95, mean, ep in rows[:top_n]:
        print(f"  {ep:<{EP_WIDTH}}  {mean:>6.3f}s  {p95:>6.3f}s  {p99:>6.3f}s")
    if not rows:
        print("  (no data)")


def main():
    env, days, top_n = parse_args()
    if not env:
        sys.exit(f"Usage: {sys.argv[0]} [{'|'.join(ENVS)}] [--days N] [--top N]")

    project, services = ENVS[env]
    now    = datetime.now(timezone.utc)
    start  = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end    = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    window = days * 86400
    token  = get_token()

    print(f"Project : {project}")
    print(f"Window  : last {days} day(s)  [{start} → {end}]")
    print(f"Top     : {top_n} endpoints per table")

    for service, prefix in services:
        count_m   = f"{prefix}_request_count"
        latency_m = f"{prefix}_request_latency"

        print(f"\n{'═'*80}")
        print(f"  {service}")
        print(f"{'═'*80}")

        # ── RPS ──────────────────────────────────────────────────────────────
        print(f"\n  [request rate — {count_m}]")
        count_ts = query(project, token, count_m, service,
                         "ALIGN_RATE", "REDUCE_SUM", start, end, window)
        print_rps_table(count_ts, top_n)

        # ── Latency ──────────────────────────────────────────────────────────
        print(f"\n  [latency — {latency_m}]")
        # ALIGN_DELTA merges DELTA distributions within the window → extract .mean
        mean_ts = query(project, token, latency_m, service,
                        "ALIGN_DELTA",         "REDUCE_SUM",  start, end, window)
        p95_ts  = query(project, token, latency_m, service,
                        "ALIGN_PERCENTILE_95", "REDUCE_MEAN", start, end, window)
        p99_ts  = query(project, token, latency_m, service,
                        "ALIGN_PERCENTILE_99", "REDUCE_MEAN", start, end, window)
        print_latency_table(mean_ts, p95_ts, p99_ts, top_n)

    print()


if __name__ == "__main__":
    main()
