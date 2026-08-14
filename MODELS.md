# Prediction models

How the site predicts race results. For any race we predict each athlete's time
in four disciplines (swim, bike, run, overall) and sort by predicted overall
time to get predicted positions. Times are the priority; positions are just the
sorted times.

Short course (sprint, standard) and long course (middle, t100, long) use
different approaches.

## Building blocks

**ELO ratings (`ptd_data/ratings.py`)** — a pairwise rating per athlete, per
discipline, per course. After each race, athletes who beat the field gain rating
and losers drop, scaled by the margin (in log-time) and by confidence (new
athletes move faster; beating an experienced athlete counts more; sparse old
eras get a boost). Ratings rank a field well, but the rating-to-time conversion
(`SCALE`) is compressed, so on their own they predict everyone close to elite
times.

**Anchor times (`app/routers/race_page.py`, `prediction_models` table)** — for
each (gender, distance, discipline) we pool the recent history of the top-rated
athletes in the field, take a course-specific quantile as the leader's time,
then ELO-scale the rest of the field down from there.

**Form model (`ptd_data/form.py`)** — tracks each athlete's current form per
discipline: their split vs the field median (log space), with field strength
removed by alternating least squares, smoothed by a Kalman filter. Predicted
split = exp(form + event course constant). Weak, poorly-connected tiers
(Development Regional Cups) get their course constant anchored to the neutral
baseline, since ALS can't gauge their field strength. The form model is used for
**long-course predictions** and the **athlete-page form display** (short-course
swim/run only).

## Short course

Short-course predictions use each athlete's **own observed splits**, not the
form model. The insight: with only a few races, an athlete's actual times are a
better signal than a rating that gets inflated by beating weak fields or
deflated for strong-field newcomers.

- **Swim, run** (course-stable): a recency-weighted geometric mean of the
  athlete's prior splits at that distance. As their split count grows
  (`CONF_LO`→`CONF_HI`) the prediction fades toward the ELO anchor — once we have
  enough cross-field races, the rating's wider view wins.
- **Bike**: always the ELO anchor. Draft-legal racing means everyone rides in a
  pack, so an individual's bike time is mostly noise; the anchor's field-relative
  pack time is the better signal.
- **Overall**: summed from the legs (swim + bike + run) plus a typical transition
  allowance. Never predicted separately.
- **Debuts / no prior split for a leg**: stand in with the field median of that
  leg across the race (the anchor predicts debutants poorly).

See `analysis/absolute_eval.py` for the backtest behind this.

## Long course

The form model replaces the anchor's times entirely — it is well-calibrated for
long course and beats the anchor on both ordering and outright times. Athletes
without enough form history keep the anchor, rescaled onto the form level.

## Win and podium probabilities

Both are read off the predicted overall times, so they never disagree with the
predicted order. Each athlete's margin is `pred_t / leader_pred_t - 1`, and the
Plackett-Luce strength is `exp(-beta * margin)`. Win probability is the softmax
over those strengths; podium probability is P(top 3) under the same weights,
computed by Gumbel sampling (sampling a PL ordering is just an argsort of log
weight plus Gumbel noise), seeded on `race_id` so rebuilds reproduce.

Podium uses its own, lower beta (`PODIUM_BETA` in `ptd_data/predictions.py`):
a 3-wide target spreads flatter across the field, so podium probability is not
a rescaling of win probability. Betas were fitted on pre-2025 races and scored
on 2025+ in `analysis/win_probability.py` and `analysis/podium_probability.py`
(see `analysis/WIN_PROBABILITY.md` for the tables). Both are well calibrated
across the range; the top bin runs a few points hot.

Probabilities cover every athlete in the stored field with a predicted time,
including - on completed races - those who went on to DNF, so they read as a
genuine pre-race view. Win sums to 1 and podium to 3 per race.

## Where it runs

All of it is rebuilt by `python -m ptd_data.ratings` (phases: `ratings`, then
`rankings`, then `models`; the anchor models and form model are the `models`
phase and read the ratings table). The live site and the social-media post
generator call the same prediction code, so they agree.

Predicted times and the win/podium columns are then precomputed per race into
`race_predictions` by `python -m ptd_data.predictions` (step 11 of
`scripts/build_db.sh`), which wipes and repopulates the whole table every run.
