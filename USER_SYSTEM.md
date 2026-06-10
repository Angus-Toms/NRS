# User System Design Draft

Goal: accounts with email verification, profiles, follows (athletes and races), a personal
feed, comments under races, and account deletion. Constraints: solo-maintained, lean,
fail fast, and it must not interfere with the weekly DuckDB rebuild/sync.

## Architecture principle

The analytics DuckDB is rebuilt on the laptop and rsynced to Render weekly. It is
read-only at runtime and gets replaced wholesale. User data therefore lives in a second,
write-heavy store that survives deploys. The two never join in SQL; user rows reference
athlete/race ids (stable CRC32-of-slug ids, so weekly rebuilds do not break references),
and the app stitches the two sides together in Python exactly like the routers already
stitch query results.

## 1. Database

Recommendation: managed Postgres in Frankfurt (Render Postgres, same region as the app;
Neon free tier is the zero-cost alternative).

Why not the alternatives:
- DuckDB: single-writer, and the file is replaced weekly. Wrong tool for user writes.
- SQLite on the Render disk: workable at small scale (WAL mode), but writes serialize,
  the disk is already the DB-sync target, backups are manual, and it caps out exactly
  when the feature succeeds. Postgres removes the ceiling for one config var.

Access layer: asyncpg connection pool (size ~10), raw SQL, no ORM. New package
`ptd_users/` mirroring `ptd_data/`: `db.py` (pool + migration runner), `queries.py`.
Migrations are numbered .sql files in `ptd_users/migrations/`, applied at startup;
a `schema_migrations` table records what ran. Crash on failure.

## 2. Auth: passwordless magic links

Account creation and email verification collapse into one flow: enter email, receive a
single-use link, click it, session starts. New emails create the account on first
verify; existing emails log in. No passwords stored means no hashing, no reset flow,
no breach surface. Tradeoff: logging in on a new device needs an email round trip,
mitigated by 90-day rolling sessions.

- Session: opaque 32-byte token, SHA-256 hash stored server-side, sent as an
  httpOnly Secure SameSite=Lax cookie. Rolling expiry, bumped at most once a day.
- Login tokens: single use, 15-minute expiry, hashed at rest.
- Email sending: one `send_email(to, subject, html)` function. Recommendation: Resend
  (simple API, 3k emails/month free, DKIM on protridata.com). SES is the cheap-at-scale
  alternative since boto3 is already a dependency, but needs production-access approval.
- Abuse limits: 3 link emails per address per hour, honeypot field plus minimum
  form-fill time on the request form. No CAPTCHA initially.

## 3. Schema

```sql
create table users (
    user_id      bigint generated always as identity primary key,
    email        citext unique not null,
    display_name text not null,
    country      char(3),                  -- optional, alpha-3, shown next to comments
    is_admin     boolean not null default false,
    is_banned    boolean not null default false,
    email_digest boolean not null default true,
    created_at   timestamptz not null default now()
);

create table login_tokens (
    token_hash  bytea primary key,
    email       citext not null,
    expires_at  timestamptz not null
);

create table sessions (
    token_hash   bytea primary key,
    user_id      bigint not null references users on delete cascade,
    created_at   timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    expires_at   timestamptz not null
);

create table follows (
    user_id    bigint not null references users on delete cascade,
    kind       text not null check (kind in ('athlete', 'race')),
    ref_id     bigint not null,            -- id in the analytics DuckDB
    created_at timestamptz not null default now(),
    primary key (user_id, kind, ref_id)
);

create table comments (
    comment_id bigint generated always as identity primary key,
    race_id    bigint not null,            -- validated against DuckDB at write time
    user_id    bigint not null references users on delete cascade,
    body       text not null check (char_length(body) between 1 and 2000),
    created_at timestamptz not null default now(),
    hidden_at  timestamptz                 -- set by auto-hide or moderator
);
create index comments_by_race on comments (race_id, created_at desc);

create table comment_reports (
    comment_id bigint not null references comments on delete cascade,
    user_id    bigint not null references users on delete cascade,
    created_at timestamptz not null default now(),
    primary key (comment_id, user_id)
);
```

`ref_id` and `race_id` get existence-checked against DuckDB in the route before insert.
No cross-database FK is possible; deterministic ids make this safe in practice.

## 4. Anonymous pages, client-side hydration (the key decision)

The in-process page cache has been removed (it was destabilizing Render), but the
principle it enforced stays: HTML pages render identically for everyone, which keeps
them CDN-cacheable and keeps user state out of every existing router. The logged-in
experience is hydrated client-side:

- `GET /me` returns `{display_name, follows: {athletes: [...], races: [...]}}` or 401.
  Fetched once per page load, cached in sessionStorage for a few minutes. Follow
  buttons and the nav account chip render in a neutral state and correct themselves
  from this payload (same pattern as the asset-versioned static JS already in use).
- Comments are loaded as a separate partial (`/race/{id}/comments`), so race HTML
  never contains user state.
- `/account` and `/feed` are session-gated and sent with Cache-Control: no-store.

## 5. Routes and pages

Auth (`app/routers/auth.py`)
- `GET /login` - single page for both signup and login (email box)
- `POST /auth/request-link` - send magic link
- `GET /auth/verify?token=...` - create account if new, start session, redirect;
  first-time users land on a one-field "pick a display name" step
- `POST /auth/logout`

Account (`app/routers/account.py`)
- `GET /account` - display name, email, country, digest toggle, followed athletes and
  races with unfollow controls, danger zone
- `POST /account/update`
- `POST /account/delete` - hard delete: user row, sessions, follows, comments and
  reports all go (cascades). Re-confirmation via typed phrase. No soft-delete state
  to maintain, nothing retained.

Follows (`app/routers/follows.py`)
- `POST /follow` body `{kind, ref_id}` - toggle, returns new state
- Buttons: athlete hero, race hero, leaderboard cards. Logged-out click routes to /login
  with a `next` redirect.

Feed (`app/routers/feed.py`)
- `GET /feed` - server-rendered, session-gated. Sections in order: upcoming races
  (followed races plus races with followed athletes on the startlist, with predicted
  podiums), recent results from followed athletes (last 90 days, position, time,
  rating change), recent comments on followed races. Reuses existing queries.py
  functions; one bulk query per section.

Comments (`app/routers/comments.py`)
- `GET /race/{id}/comments` - partial, newest first, paginated 50 at a time
- `POST /race/{id}/comments` - session required, account older than 1 hour,
  rate limit 1 per 30s and 20 per day, plain text only (escaped, line breaks kept)
- `POST /comments/{id}/delete` - own comment or admin
- `POST /comments/{id}/report` - 3 unique reports auto-hides pending review
- `GET /admin/moderation` - reported and hidden queue, is_admin only
- Races only, per the decision; athlete pages stay comment-free. Flat, no threads.

## 6. Extras worth adding

- Weekly digest email (high value, cheap): after the weekly build finishes, send each
  opted-in user their followed athletes' results and upcoming races. One script in
  scripts/ called at the end of weekly.sh. The unsubscribe link flips email_digest.
- Saved comparisons: trivial later (one table, a Save button on the comparison page).
- Deliberately skipped: OAuth providers (magic link is sufficient and keyless), public
  profile pages (privacy and moderation burden for near-zero value), avatars (display
  name plus optional flag), threaded comments, likes/reactions, push notifications.

## 7. Build order

1. Infra: Postgres instance, ptd_users package, pool, migrations, session middleware,
   /me endpoint, login/verify flow, email sending. The auth core.
2. Account page and deletion.
3. Follows, buttons, /feed.
4. Comments, rate limiting, reports, moderation queue.
5. Digest email after the rest has settled.

Phases 1-2 ship together as the smallest useful unit; 3 is the retention payoff;
4 is the community bet and the only part with ongoing moderation cost.

## Open questions

- Postgres host: Render ($7/month starter, same private network) vs Neon (free, over
  the public internet with TLS)?
- Email provider: Resend (simplest) vs SES (already have AWS tooling)?
- Display names: enforce uniqueness (handles, needed only if profiles ever go public)
  or freeform with the user_id as the real identity?
- Comment policy line: do DNF/DQ discussions about named athletes count as athlete
  commentary? Suggest a short posted rule: discuss the race, not the person.
