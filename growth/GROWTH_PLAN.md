# Growth plan: 10x traffic in 3 months (Aug - Oct 2026)

## 0. Honest framing

Baseline from Search Console: ~250 search clicks/week, ~5k impressions/week,
average position 8-9. Total traffic is UNKNOWN because the site has no
analytics - fixing that is literally step one. Assume total is ~2x search
(direct + referral), so ~500-700 visits/week. 10x = 5-7k visits/week by end
of October.

SEO compounding alone will not 10x anything in 3 months; indexing moves on
Google's clock. What CAN move that fast:

1. **Event spikes.** Traffic is demand-driven and the demand is "<race>
   results" within 48h of a race. Aug-Oct is the season climax: WTCS Finals,
   70.3 Worlds, T100 finals, and Kona in October. These weekends are where a
   10x week actually happens. Everything below is organised around capturing
   them.
2. **Community distribution.** Triathlon nerds congregate in exactly three
   places: r/triathlon (~1M members), the Slowtwitch forum, and a handful of
   Discords/podcasts. A good stats post there outperforms months of SEO.
3. **Return loops.** Email (favourites -> race alerts) and shareable pages
   (OG images) turn spike visitors into repeat visitors.

The multipliers stack: season+fast results (~2-3x on event weeks) x the
already-shipped SEO fixes (~1.5-2x as indexing recovers) x community posts
(spiky but large) x email retention (compounds late). October is the
make-or-break month; August is for building the machine.

Weekly time budget: ~4-6h. Money budget: <=£30/mo (plan uses ~£10-16).

---

## 1. Week 1: measurement + free services (one evening, ~2h)

You cannot steer this without numbers. All free:

- [ ] **Cloudflare Web Analytics** - one script tag in base.html, free,
      privacy-clean, shows total visits + referrers. (GA4 is the heavier
      alternative; you don't need it.)
- [ ] **Bing Webmaster Tools** - import straight from GSC in one click.
      Bing feeds DuckDuckGo, Yahoo, and ChatGPT/Copilot search. Free index,
      zero ongoing work. Enable **IndexNow** (Cloudflare has a one-toggle
      integration) so new race pages get discovered within minutes.
- [ ] **Ahrefs Webmaster Tools** (free tier for your own site) - backlink
      monitoring + site audit. This is how you'll see which outreach lands.
- [ ] **GSC API query miner** - the UI caps at 1,000 rows; the API doesn't.
      Small script: pull all queries weekly into DuckDB, flag (a) queries
      ranking 5-15 (striking distance - title/content tweaks push these to
      page 1) and (b) rising queries with no matching page. ~1h to write,
      runs alongside weekly.sh forever.
- [ ] **Google Alerts** on "protridata" and "pro tri data" - see who mentions
      you; every unlinked mention is an easy link ask.
- [ ] Baseline note: record this week's clicks/impressions/visits so the 10x
      claim is measurable.

Competitor recon (also free, ~1h once):
- **ProCyclingStats** is the model to copy - a solo-started stats DB that
  became canonical for cycling. Note what they do: first with results,
  exhaustive rider pages, "statistics" landing pages for every conceivable
  list, and they're cited across Wikipedia and every cycling forum.
- Triathlon-specific: TriRating (long-course ratings, Kona focus - your
  most direct analogue), obstri, PTO's own stats pages, World Triathlon's
  rankings pages. `site:trirating.com` in Google shows what page types earn
  their traffic. SimilarWeb free tier gives rough traffic for each.
- Keyword volumes: Google Keyword Planner (free with an Ads account, no
  spend needed) for "kona 2026", "ironman world championship 2026 results",
  "wtcs finals" etc. Optional: Keywords Everywhere (~£8 in credits) shows
  volumes inline in search results.

---

## 2. Capture the event spike (the single biggest lever)

**Problem:** ingest runs weekly, by hand. A race on Saturday might not be on
the site until the following Thursday - the demand curve is dead by then.
Meanwhile "<race> 2026 results" queries are exactly where you already rank
6-9 and get 3-15% CTR.

**Fix: race-day mode.** Not full automation - a scoped script:

- [ ] `scripts/raceday.sh <event-ids...>`: ingest just those events, extend
      ratings incrementally (no full rebuild), deploy DB, ping IndexNow for
      the affected URLs. Target: results live within 1-2h of the finish.
      Sunday evening, 15 minutes, glass of wine.
- [ ] Maintain a small calendar of the ~2-4 races per weekend that matter
      (WTCS, T100, Ironman/70.3 champs, World Cups). The upcoming_races
      table already knows them - a `weekly.sh` step can print "this
      weekend: run raceday for X, Y" so it's zero-thought.
- [ ] **Long-course start lists** are the gap: upcoming/pre-race machinery is
      short-course only, yet 70.3 Worlds and Kona are the biggest spikes of
      the window. Build an ingest tool for Ironman pro start lists
      (ironman.com/community/pro-athletes) - manual CSV first, scrape if
      stable - so long-course races get pre-race pages, predictions and
      cards too. Details in TODO.md P1.
- [ ] Pre-race pages are equally valuable and FULLY automatic already:
      start lists + predictions are live and now in sitemap-upcoming.
      "start list" queries convert at 60% CTR when you rank. Make sure the
      prediction content on those pages is crawlable server-side HTML.
- [ ] Post-race, the same URL flips from start list to results (301s and
      titles already handle this) - the pre-race ranking carries over to
      the results demand. This URL-continuity is a genuine structural
      advantage; protect it.

October dry-run: treat Kona weekend as launch day. Predictions page live the
Monday before, Reddit preview thread Thursday, results ingested same-day,
recap thread Sunday.

---

## 3. SEO round 2 (builds on what shipped this week)

Already done (let it cook, verify in GSC):
noindex tiering, internal links, titles, upcoming sitemap, error fixes,
edge cache. Expect indexed count to climb toward ~50k over 4-8 weeks.

New work, in priority order:

- [ ] **Striking-distance sweep** (weekly, 20 min, driven by the query
      miner): queries at position 5-15 get title/H1/description tweaks on
      their target page. This is the highest ROI per minute in all of SEO.
- [ ] **Event/recurring page titles**: apply the same intent treatment race
      pages got. "Challenge Roth" should land on the recurring page titled
      "Challenge Roth - Results, History & Records (1985-2026)". Evergreen
      queries, zero competition from anyone with actual data.
- [ ] **H2H rivalry pages** (1-2 evenings): the athlete-compare tool is
      robots-blocked (correctly - infinite combinations). But "X vs Y" is a
      real query pattern. Create crawlable static paths /h2h/<id>-vs-<id>
      for a CURATED set: top ~500 rivalry pairs by combined rating +
      head-to-head count. Server-rendered summary (record, last meeting,
      rating gap) + the existing compare UI. In sitemap, cross-linked from
      both athletes' pages. Do NOT generate all pairs - that recreates the
      thin-page problem we just fixed.
- [ ] **"Statistics" landing pages** (PCS's playbook): all-time win lists,
      youngest winners, current streaks, fastest swims/bikes/runs by
      distance, national records of the rating era. Each is one query +
      one template + evergreen. These earn links from forums organically
      ("actually, per protridata, the record is...").
- [ ] **Rich results**: race pages already have SportsEvent + Breadcrumb
      JSON-LD. Add ItemList of top finishers on results pages. Check
      GSC's enhancement reports monthly.

Search Console habits (answering your question directly):
- Weekly: Performance -> compare last 7 days vs previous, sort by position,
  work the 5-15 band (or let the API script do this).
- After each deploy that adds page types: URL Inspection on one example,
  then Request Indexing.
- Monthly: Coverage report - watch "Crawled - not indexed" drain;
  per-sitemap indexed ratios (Sitemaps report) tell you which page types
  Google accepts. Crawl Stats (Settings) - watch avg response time drop
  from the caching work.
- One-time: verify a **Domain property** (DNS) if the current property is
  URL-prefix only - it merges www/non-www/http data.

---

## 4. Distribution: go where the audience already is (2h/week, highest ROI)

Your social API posts don't grow because platform algorithms bury
low-engagement template content - correct diagnosis. Redirect that effort:

- [ ] **Reddit r/triathlon**: one post per big race weekend. Thursday
      "Weekend preview by the numbers" (predictions, 3 storylines the data
      shows - e.g. "X's run rating has climbed 120pts in 6 races") or
      Sunday "results in context" (biggest rating gains, upset index).
      Write as a fan sharing analysis, not a site promoting itself; link
      the relevant race page as the source. Respect the sub's self-promo
      norms (mostly participate, occasionally link). One good post =
      thousands of visits and follows-on links.
- [ ] **Slowtwitch forum**: same content, long-course races especially.
      Slowtwitch users are exactly the demographic that bookmarks a stats
      site. Become "the ratings person" in race threads.
- [ ] **Prediction accountability as a format**: post predictions BEFORE
      the race, own the results after ("we said 6.3 places average miss -
      this weekend: 4.9"). FiveThirtyEight built a brand on this loop.
      It's also unfakeable credibility for the federation outreach.
- [ ] **Dynamic OG images** (one evening, big lever): the social/ card
      renderer already makes beautiful race/athlete cards. Serve them as
      og:image on race + athlete pages (/og/race/<id>.png, cached at the
      edge). Every WhatsApp group, Discord, tweet, and forum post that
      shares a PTD link becomes a rich preview. This makes OTHER people's
      sharing do the marketing - fits the "can't post daily" constraint
      perfectly.

## 5. Social pipeline: repurpose, don't push harder

- [ ] Keep IG/FB alive but batch: one album/carousel per race (pre-cards +
      podium recap) instead of individual images - your instinct is right.
      Auto-generated is fine when it's clearly a data product.
- [ ] **Social-handle tool** (enabler for the two items below): internal
      lookup to find + manually confirm athletes' IG/FB handles, saved
      against athlete_id so tagging/DMs are one click at post time.
      Details in TODO.md P2.
- [ ] **Tag the athletes in every card.** The card of athlete X is content
      athlete X wants to reshare to their followers. Mid-tier pros resharing
      their own prediction/recap cards is the actual growth mechanism, not
      your follower count.
- [ ] This merges with your athlete-DM idea: DM mid-level athletes their
      card before/after their race - "made this for your race, feel free to
      share". Response rate will be high because you're giving, not asking.
      ~10 DMs per race weekend, 20 minutes. Track who reshares; those are
      your future ambassadors.
- [ ] Stop measuring social by follower count. Measure by referral visits
      (Cloudflare Analytics will show it) and athlete reshares.

## 6. Outreach (the templates are ready - send them)

Sequencing matters less than starting. From analysis/outreach_emails.md:

- [ ] **Media first** (tri247, 220 Triathlon, Slowtwitch news, Triathlete,
      TRI-TODAY): offer stats/charts for their race previews with
      attribution. Publications need content weekly; you have infinite
      charts. One recurring "powered by Pro Tri Data" graphic in tri247
      race previews is worth more than any backlink campaign. These are
      also the DR60+ links that lift every ranking.
- [ ] **Podcasts** (That Triathlon Show, Real Triathlon podcast etc.):
      pitch a "what the data says about the season" episode. Coach-heavy
      audiences; long tail of listeners typing the URL.
- [ ] **Federations/teams** (templates ready): slower burn, more
      credibility than traffic. 5/week, personalised line each.
- [ ] Wikipedia: do NOT add your own links (editors treat it as spam and
      it can get the domain blacklisted). Instead make stat pages so
      citable that forum users and editors do it themselves - the
      statistics landing pages above are exactly what gets cited.

## 7. Email + user system (ship in September, harvest in October)

The USER_SYSTEM.md design is right-sized. Build the 20% that drives return
visits, skip the rest for now:

- [ ] Phase 1 (2-3 evenings): magic-link auth + favourite athletes/races +
      ONE email: "athletes you follow race this weekend" (pre-race, with
      prediction) and "results are in" (post-race, with link). Resend free
      tier (3k emails/mo) is plenty. Skip comments, profiles, feeds -
      they're moderation surface with no traffic return.
- [ ] Weekly digest as a v2 (the social cards make good email content).
- [ ] Every race page gets a "Follow this race / these athletes" hook while
      the October traffic is flowing - that's how spike visitors become
      permanent audience. Shipping this BEFORE Kona is the whole point of
      September.

## 8. Hosting (only when it blocks something above)

Render 512MB + the caching work is adequate for reading. It becomes the
bottleneck when: race-day deploys need to be fast/scripted, OG image
rendering lands, or Postgres for users arrives (Render's free Postgres
expires after 90 days; paid starts $7/mo).

Recommendation when the time comes: **Hetzner CPX21 (~£8/mo, 4GB RAM)** -
runs the app + Postgres + OG renderer comfortably, native (no Docker),
Cloudflare stays in front, deploy stays rsync+systemd. That plus Resend
free tier keeps total spend ~£10/mo, well under budget. Migrate in late
August/early September, NOT in October (never migrate during your Super
Bowl).

---

## Weekly operating rhythm (the whole plan in one box)

| When | What | Time |
|---|---|---|
| Mon | weekly.sh (as now) + glance at GSC query miner output, tweak 1-3 striking-distance titles | 1h |
| Thu | Preview post (Reddit/Slowtwitch) for the weekend's big race + DM cards to ~5 racing athletes | 1h |
| Sun | raceday.sh for the weekend's races + results/recap post + IG album | 1.5h |
| Monthly | One outreach batch (5-10 emails), one statistics landing page, check Ahrefs/coverage | 2h |

## Milestones

- **End Aug**: analytics live, raceday.sh working, first 4 Reddit/forum
  posts, OG images shipped, 2x baseline (~500 clicks/wk search).
- **End Sep**: user system + race emails live, 2 media placements, H2H +
  2 stats pages indexed, 3-4x baseline.
- **End Oct**: Kona + champs season executed end-to-end (predictions ->
  same-day results -> recap -> email), 10x weeks during events, retention
  base (email list, followers-of-athletes) that holds ~4-5x after season.

If the 10x sticks only during race weeks and baseline holds at 4-5x, that
is the realistic honest outcome - the retention loops are what ratchet the
floor up through 2027.
