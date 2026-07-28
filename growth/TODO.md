# Growth TODO

Ordered by urgency. Tick things off; keep dates next to done items.
Full reasoning for every item is in GROWTH_PLAN.md.

## P0 - This week (measurement + free services, ~2-3h total)

Everything else gets steered by these numbers; do them first.

- [ ] Add Cloudflare Web Analytics script tag to base.html and deploy
- [ ] Record baseline in this file: search clicks/wk, impressions/wk, total visits/wk
- [ ] Set up Bing Webmaster Tools (import from GSC), enable IndexNow via the Cloudflare toggle
- [ ] Sign up for Ahrefs Webmaster Tools (free tier), run first site audit
- [ ] Create Google Alerts for "protridata" and "pro tri data"
- [ ] Verify a GSC Domain property (DNS record) if current property is URL-prefix only
- [ ] Create Google Ads account (no spend) for Keyword Planner access; pull volumes for
      "kona 2026", "ironman world championship 2026 results", "wtcs finals", top athlete names
- [ ] Competitor recon notes (1h): site: searches + SimilarWeb free on
      procyclingstats.com, trirating.com, obstri.com - list page types that earn their traffic

## P1 - Next 2 weeks (capture the event spike)

- [ ] Write `scripts/gsc_query_miner.py`: pull all queries via GSC API into DuckDB weekly;
      output (a) position 5-15 striking-distance list, (b) rising queries with no matching page
- [ ] Hook query miner into weekly.sh (or run alongside it)
- [ ] Write `scripts/raceday.sh <event-ids...>`: scoped ingest of given events,
      incremental ratings extend (no full rebuild), DB deploy, IndexNow ping for affected URLs
- [ ] Add a weekly.sh step that prints "this weekend's big races: run raceday for X, Y"
      from upcoming_races (WTCS / T100 / Ironman champs / World Cups)
- [ ] Verify prediction content on upcoming race pages is server-rendered crawlable HTML
      (not JS-only), since start-list queries hit these pages
- [ ] Dry-run raceday.sh on the next race weekend and time it (target: results live <2h after finish)
- [ ] Long-course start-list ingest tool: get Ironman pro start lists into upcoming_races +
      start_list_entries so long-course races get pre-race pages, predictions, and social cards
      (currently short-course only - and 70.3 Worlds + Kona are the biggest demand spikes of
      the window). Source: https://www.ironman.com/community/pro-athletes#pro-start-lists
      - First pass: manual CSV entry via a small script (paste from the page, match names to
        athlete_ids with the existing matcher logic, flag non-matches for review)
      - Second pass if the page structure is stable: scrape it directly (it is HTML, not
        images - OCR shouldn't be needed; verify), keep manual CSV as fallback
      - Name-matching review step is essential: silently mismatched athletes would generate
        wrong predictions/cards under the wrong person's name

## P2 - Weeks 2-4 (distribution + shareability)

- [ ] Dynamic OG images: serve social/ card renders as og:image on race and athlete pages
      (/og/race/<id>.png, /og/athlete/<id>.png), edge-cached; fall back to logo where no card
- [ ] First Reddit r/triathlon post on a big race weekend (Thursday preview "by the numbers"
      or Sunday results-in-context); participate in comments, link race page as source
- [ ] Create Slowtwitch forum account, post same content in the race thread (long-course focus)
- [ ] Establish the prediction-accountability format: predictions posted before the race,
      honest accuracy recap after
- [ ] Switch IG/FB pipeline from individual images to one album/carousel per race
- [ ] Tag athletes in every posted card
- [ ] Athlete social-handle tool: internal page/script to search an athlete's IG/FB
      (prefill a search from name + country, paste the confirmed handle) and save to DB
      for later tagging and DMs
      - Schema: athlete_socials table (athlete_id, platform, handle, verified_at) - lives
        in the analytics DuckDB via a data/ CSV like corrections.csv so it survives rebuilds
      - Manual confirm only - do NOT auto-save scraped guesses; a card tagging the wrong
        person is worse than no tag
      - Work through athletes in upcoming start lists first (that's who gets tagged next)
- [ ] DM ~5-10 mid-level athletes per race weekend with their own pre/post-race card
      ("made this for your race, feel free to share"); keep a list of who reshares
      (the reshare list can live in athlete_socials as a notes/reshared column)
- [ ] Start measuring social by referral visits (Cloudflare Analytics) + athlete reshares,
      not follower count

## P3 - Weeks 3-6 (SEO round 2)

- [ ] Weekly habit (20 min): tweak titles/H1/descriptions for 1-3 striking-distance queries
      from the miner output
- [ ] Apply intent-matched titles to recurring-event pages:
      "<Event> - Results, History & Records (<first year>-<last year>)"
- [ ] H2H rivalry pages: crawlable /h2h/<id>-vs-<id> for a curated ~500 pairs
      (by combined rating + meeting count), server-rendered summary, in sitemap,
      cross-linked from both athlete pages. Do NOT generate all pairs
- [ ] First two "statistics" landing pages (PCS playbook), e.g. all-time WTCS win list +
      fastest run splits by distance; add to sitemap
- [ ] Add ItemList JSON-LD of top finishers on race results pages
- [ ] Monthly GSC check: coverage drain, per-sitemap indexed ratios, Crawl Stats response time

## P4 - September (outreach + email retention)

Outreach (templates ready in analysis/outreach_emails.md):
- [ ] Media batch first: tri247, 220 Triathlon, Slowtwitch news, Triathlete - offer
      stats/charts for race previews with attribution; propose recurring
      "powered by Pro Tri Data" graphic
- [ ] Podcast pitches: That Triathlon Show + 2 others - "what the data says about the season"
- [ ] Federation/team emails: 5/week with personalised [PERSONALISE] line
- [ ] Do NOT self-add Wikipedia links; let stats pages earn citations

User system (build the 20% that returns visits, skip comments/profiles/feeds):
- [ ] Set up Postgres + Resend account (free tier, DKIM on protridata.com)
- [ ] Magic-link auth per USER_SYSTEM.md (sessions, rate limits, honeypot)
- [ ] Favourite athletes + races (star buttons on athlete/race pages)
- [ ] Email 1: "athletes you follow race this weekend" (pre-race, with prediction)
- [ ] Email 2: "results are in" (post-race, with link)
- [ ] "Follow" hooks prominent on race pages BEFORE October
- [ ] v2 (only if time): weekly digest reusing social card renders

## P5 - Late Aug / early Sep (hosting, only when it blocks the above)

- [ ] Decide trigger: race-day deploy speed, OG renderer load, or Postgres need
- [ ] Provision Hetzner CPX21 (~£8/mo, 4GB), native setup (no Docker), Cloudflare in front
- [ ] Migrate app + set up systemd + rsync deploy; keep Render until DNS cutover verified
- [ ] NEVER migrate in October

## P6 - October (execution month, no new builds)

- [ ] Kona + champs season run end-to-end per race: predictions page live Monday before,
      Reddit/Slowtwitch preview Thursday, athlete card DMs, same-day results via raceday.sh,
      recap post + IG album Sunday, follow-up email to users
- [ ] Track weekly against milestones: end Aug ~2x baseline, end Sep ~3-4x,
      October event weeks 10x, post-season floor 4-5x

## Baseline (fill in P0)

| Date | Search clicks/wk | Impressions/wk | Total visits/wk | Email subs | Notes |
|---|---|---|---|---|---|
| 2026-07-28 | ~250 | ~5,000 | unknown (no analytics) | 0 | pre-plan baseline |
