"""GSC query miner. Pulls Search Analytics rows into a local DuckDB and writes
a weekly report of striking-distance queries, coverage gaps and snippet failures.

The Search Console UI caps exports at 1,000 rows; the API does not. It does not
lift query anonymisation, so at current traffic only a minority of clicks carry
a query string. The series is still the only per-query history we have.

Auth: a service account with Restricted access on the Domain property. Key path
comes from GSC_CREDENTIALS.

    python scripts/gsc_query_miner.py                # last 10 days + report
    python scripts/gsc_query_miner.py --backfill     # full 16 months, then report
    python scripts/gsc_query_miner.py --report-only  # no API calls
"""

import argparse
import datetime as dt
import os
from pathlib import Path

import duckdb
from google.oauth2 import service_account
from googleapiclient.discovery import build

PROPERTY = "sc-domain:protridata.com"
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
CREDS_PATH = Path(os.getenv("GSC_CREDENTIALS", Path.home() / ".config/ptd/gsc-service-account.json"))

GROWTH_DIR = Path(__file__).resolve().parent.parent / "growth"
DB_PATH = GROWTH_DIR / "gsc.duckdb"
REPORT_PATH = GROWTH_DIR / "gsc_report.md"

# API hard cap per request; anything larger is silently truncated.
PAGE_SIZE = 25_000

# GSC finalises data ~2 days back but keeps amending for a few more, so the
# default window re-pulls more than it strictly needs. Days are deleted and
# reinserted wholesale, so re-pulling is free correctness.
DEFAULT_DAYS = 10
BACKFILL_DAYS = 480

# Report thresholds. WINDOW is the reporting window and also the comparison
# period for new-vs-standing queries (last WINDOW days vs the WINDOW before that).
WINDOW = 28
MIN_IMPRESSIONS = 15
STRIKING_LOW, STRIKING_HIGH = 5.0, 20.0

# Coverage gaps need their own, much lower floor. A query we rank 40th for barely
# accrues impressions at all - the best one in a typical window is single digits -
# so MIN_IMPRESSIONS applied here empties the section rather than filtering it.
GAP_MIN_IMPRESSIONS = 4

# CTR by position, roughly, for estimating what a query is leaving on the table.
# Only used to rank the striking-distance list, so approximate is fine.
CTR_CURVE = {1: 0.28, 2: 0.15, 3: 0.10, 4: 0.07, 5: 0.05, 6: 0.04, 7: 0.03,
             8: 0.025, 9: 0.02, 10: 0.02}


def expected_ctr(position):
    return CTR_CURVE.get(int(round(position)), 0.01)


def fetch_day(service, day):
    """All (query, page) rows for one date, paged. One day per request keeps the
    stored series day-level: a single 28-day request would return the aggregate
    and lose the ability to diff windows later."""
    rows = []
    start_row = 0
    while True:
        response = service.searchanalytics().query(
            siteUrl=PROPERTY,
            body={
                "startDate": day.isoformat(),
                "endDate": day.isoformat(),
                "dimensions": ["query", "page"],
                "type": "web",
                "rowLimit": PAGE_SIZE,
                "startRow": start_row,
            },
        ).execute()
        batch = response.get("rows", [])
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
        start_row += PAGE_SIZE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"days back to pull (default {DEFAULT_DAYS})")
    parser.add_argument("--backfill", action="store_true",
                        help=f"pull the full {BACKFILL_DAYS}-day history GSC retains")
    parser.add_argument("--report-only", action="store_true",
                        help="rebuild the report from stored rows, no API calls")
    args = parser.parse_args()

    con = duckdb.connect(str(DB_PATH))
    con.execute("""
        CREATE TABLE IF NOT EXISTS gsc_rows (
            date DATE,
            query VARCHAR,
            page VARCHAR,
            clicks INTEGER,
            impressions INTEGER,
            position DOUBLE
        )
    """)
    # Site totals, no query dimension and so no anonymisation. Two jobs: the
    # weekly clicks/impressions ANALYTICS.md wants, and the denominator that
    # says how much of the traffic the query rows above actually account for.
    con.execute("""
        CREATE TABLE IF NOT EXISTS gsc_totals (
            date DATE,
            clicks INTEGER,
            impressions INTEGER
        )
    """)

    # ---- fetch ----------------------------------------------------------
    if not args.report_only:
        credentials = service_account.Credentials.from_service_account_file(
            str(CREDS_PATH), scopes=SCOPES
        )
        service = build("searchconsole", "v1", credentials=credentials, cache_discovery=False)

        days_back = BACKFILL_DAYS if args.backfill else args.days
        today = dt.date.today()
        # Skip the most recent 2 days: GSC has nothing there yet, and storing an
        # empty day would look like a real zero in the series.
        days = [today - dt.timedelta(days=n) for n in range(2, days_back + 2)]

        total_rows = 0
        for day in days:
            rows = fetch_day(service, day)
            con.execute("DELETE FROM gsc_rows WHERE date = ?", [day])
            if rows:
                con.executemany(
                    "INSERT INTO gsc_rows VALUES (?, ?, ?, ?, ?, ?)",
                    [(day, r["keys"][0], r["keys"][1], r["clicks"], r["impressions"], r["position"])
                     for r in rows],
                )
            total_rows += len(rows)
            print(f"  {day}: {len(rows)} rows")
        print(f"Ingested {total_rows} rows across {len(days)} days")

        # Totals always cover the full retention window rather than the requested
        # days: the date dimension alone is one request and a few hundred rows
        # whatever the range, so there is no reason to make history depend on
        # having remembered to --backfill.
        totals_start = today - dt.timedelta(days=BACKFILL_DAYS)
        response = service.searchanalytics().query(
            siteUrl=PROPERTY,
            body={
                "startDate": totals_start.isoformat(),
                "endDate": max(days).isoformat(),
                "dimensions": ["date"],
                "type": "web",
                "rowLimit": PAGE_SIZE,
            },
        ).execute()
        con.execute("DELETE FROM gsc_totals WHERE date >= ? AND date <= ?", [totals_start, max(days)])
        con.executemany(
            "INSERT INTO gsc_totals VALUES (?, ?, ?)",
            [(dt.date.fromisoformat(r["keys"][0]), r["clicks"], r["impressions"])
             for r in response.get("rows", [])],
        )
        print(f"Ingested totals for {len(response.get('rows', []))} days")

    # ---- report ---------------------------------------------------------
    latest = con.execute("SELECT max(date) FROM gsc_rows").fetchone()[0]
    if latest is None:
        raise SystemExit("No rows stored. Run without --report-only first.")

    cur_start = latest - dt.timedelta(days=WINDOW - 1)
    prev_start = cur_start - dt.timedelta(days=WINDOW)

    # Impression-weighted position: a query ranking 4 on a page nobody sees and
    # 30 on one everybody sees averages to something meaningless unweighted.
    striking = con.execute("""
        SELECT query,
               sum(clicks) AS clicks,
               sum(impressions) AS impressions,
               sum(position * impressions) / sum(impressions) AS position,
               arg_max(page, impressions) AS top_page
        FROM gsc_rows
        WHERE date >= ?
        GROUP BY query
        HAVING sum(impressions) >= ?
           AND sum(position * impressions) / sum(impressions) BETWEEN ? AND ?
        ORDER BY impressions DESC
    """, [cur_start, MIN_IMPRESSIONS, STRIKING_LOW, STRIKING_HIGH]).fetchall()

    # Rank by clicks available if the query moved to position 3, not by raw
    # impressions: a 3,000-impression query at position 19 is worth more work
    # than a 300-impression one at 6.
    striking = sorted(
        striking,
        key=lambda r: r[2] * (expected_ctr(3) - expected_ctr(r[3])),
        reverse=True,
    )[:60]

    # Genuine coverage gaps: demand exists and nothing of ours is in the top 20.
    # No "rising" filter here - a standing gap is as much a missing page as a new
    # one, and requiring growth on top of position > 20 empties the section.
    gaps = con.execute("""
        WITH cur AS (
            SELECT query,
                   sum(clicks) AS clicks,
                   sum(impressions) AS impressions,
                   sum(position * impressions) / sum(impressions) AS position,
                   arg_max(page, impressions) AS top_page
            FROM gsc_rows WHERE date >= ? GROUP BY query
        ),
        prev AS (
            SELECT query, sum(impressions) AS impressions
            FROM gsc_rows WHERE date >= ? AND date < ? GROUP BY query
        )
        SELECT cur.query, cur.impressions, coalesce(prev.impressions, 0),
               cur.position, cur.top_page
        FROM cur LEFT JOIN prev USING (query)
        WHERE cur.impressions >= ? AND cur.position > ?
        ORDER BY cur.impressions DESC
        LIMIT 25
    """, [cur_start, prev_start, cur_start, GAP_MIN_IMPRESSIONS, STRIKING_HIGH]).fetchall()

    # Ranking top 5 and still earning nothing is a title/snippet failure, not a
    # ranking one, so it needs a different fix from the striking list. Anonymisation
    # drops whole query rows rather than zeroing clicks on the rows that survive,
    # so a zero here is a real zero.
    zero_click = con.execute("""
        SELECT query, impressions, position, top_page FROM (
            SELECT query,
                   sum(clicks) AS clicks,
                   sum(impressions) AS impressions,
                   sum(position * impressions) / sum(impressions) AS position,
                   arg_max(page, impressions) AS top_page
            FROM gsc_rows WHERE date >= ? GROUP BY query
        )
        WHERE impressions >= ? AND position < ? AND clicks = 0
        ORDER BY impressions DESC
        LIMIT 25
    """, [cur_start, MIN_IMPRESSIONS, STRIKING_LOW]).fetchall()

    named = con.execute("""
        SELECT sum(clicks), sum(impressions), count(DISTINCT query)
        FROM gsc_rows WHERE date >= ?
    """, [cur_start]).fetchone()
    site = con.execute(
        "SELECT sum(clicks), sum(impressions) FROM gsc_totals WHERE date >= ?", [cur_start]
    ).fetchone()

    # Per-week site totals, straight into the ANALYTICS.md weekly-log format.
    weekly = con.execute("""
        SELECT date_trunc('week', date) AS week, sum(clicks), sum(impressions)
        FROM gsc_totals WHERE date >= ?
        GROUP BY week ORDER BY week
    """, [latest - dt.timedelta(days=8 * 7)]).fetchall()

    # Queries that were on last week's striking list and are not on this one,
    # so a week of title tweaks can be judged rather than just repeated.
    previous_report = REPORT_PATH.read_text() if REPORT_PATH.exists() else ""
    previous_queries = set()
    in_striking = False
    for line in previous_report.splitlines():
        if line.startswith("## "):
            in_striking = line.startswith("## Striking distance")
        elif in_striking and line.startswith("| ") and not line.startswith("| Query"):
            previous_queries.add(line.split("|")[1].strip())
    current_queries = {r[0] for r in striking}
    dropped = sorted(previous_queries - current_queries - {"Query"})

    coverage = 100 * named[0] / site[0] if site[0] else 0
    lines = [
        "# GSC query miner",
        "",
        f"Generated from data through {latest}. Window: {cur_start} to {latest} ({WINDOW} days).",
        f"Site total: {site[0]} clicks, {site[1]} impressions.",
        f"Carrying a query: {named[0]} clicks ({coverage:.0f}%), {named[1]} impressions, "
        f"{named[2]} distinct queries. The rest is anonymised and invisible here.",
        "",
        "## Weekly totals",
        "",
        "Site-wide, no anonymisation. These are the numbers ANALYTICS.md logs.",
        "",
        "| Week of | Clicks | Impressions |",
        "|---|---|---|",
    ]
    for week, clicks, impressions in weekly:
        lines.append(f"| {week} | {clicks} | {impressions} |")

    lines += [
        "",
        "## Striking distance (position 5-20)",
        "",
        "Ranked by clicks available at position 3. Work these with title, H1 and",
        "description tweaks on the page listed.",
        "",
        "| Query | Clicks | Impr | CTR | Pos | Est. gain | Top page |",
        "|---|---|---|---|---|---|---|",
    ]
    for query, clicks, impressions, position, top_page in striking:
        gain = impressions * (expected_ctr(3) - expected_ctr(position))
        lines.append(
            f"| {query} | {clicks} | {impressions} | {100 * clicks / impressions:.1f}% | "
            f"{position:.1f} | {gain:.0f} | {top_page} |"
        )

    lines += [
        "",
        "## Coverage gaps (nothing in the top 20)",
        "",
        "Demand exists and no page of ours ranks. Candidates for new pages. Prev impr",
        "of 0 means the query is new this window.",
        "",
        "| Query | Impr | Prev impr | Pos | Best page |",
        "|---|---|---|---|---|",
    ]
    for query, impressions, prev_impressions, position, top_page in gaps:
        lines.append(
            f"| {query} | {impressions} | {prev_impressions} | {position:.1f} | {top_page} |"
        )

    lines += [
        "",
        "## Top 5 and zero clicks",
        "",
        "Ranking is not the problem, the snippet is. Rewrite the title and description",
        "before touching anything else on these.",
        "",
        "| Query | Impr | Pos | Page |",
        "|---|---|---|---|",
    ]
    for query, impressions, position, top_page in zero_click:
        lines.append(f"| {query} | {impressions} | {position:.1f} | {top_page} |")

    if dropped:
        lines += [
            "",
            "## Left the striking list since last run",
            "",
            "Either they broke into the top 5 or they lost impressions. Worth a look",
            "when you tweaked their page last week.",
            "",
        ] + [f"- {query}" for query in dropped]

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(f"Wrote {REPORT_PATH}: {len(striking)} striking, {len(gaps)} gaps, {len(zero_click)} zero-click, {len(dropped)} dropped")
    con.close()


if __name__ == "__main__":
    main()
