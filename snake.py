"""
Race-breakdown visualisation designs.

The classic 'snake' chart plots cumulative time-behind-leader through the
5 segments of a triathlon (Swim / T1 / Bike / T2 / Run).  Because gaps
accumulate monotonically, the run dominates the display: a 30-second swim
gap is invisible next to a 3-minute run gap.  This file explores five
alternative designs that each attack the scaling problem differently.

Designs
-------
A. Per-segment strip panels      - 5 independent x-axes, small multiples
B. Per-segment delta snake       - gap resets to 0 at the start of each leg
C. Rank-lane bump chart          - positions only, top-N highlighted
D. Sqrt-scaled cumulative snake  - non-linear axis compresses big gaps
E. Interactive discipline-focus  - Plotly HTML: click a leg to zoom in

Run the file to view each plot and write an interactive HTML alongside.

The original 25-athlete field from the API dump below is blended with a
synthetic ~60-athlete field so the designs can be stress-tested against
the kind of crowded age-group race where this matters most.
"""
import ast
import io

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go

# ---------------------------------------------------------------------------
# Real field (from an actual World Triathlon results dump)
# ---------------------------------------------------------------------------
csv_text = """athlete_id,athlete_title,athlete_country_name,athlete_yob,splits,position,total_time,start_num
135829,Gabriel Barac,Croatia,2004.0,"['00:09:29', '00:00:42', '00:26:47', '00:00:36', '00:16:18']",1,00:53:55,3
130086,Niko Matas,Croatia,2003.0,"['00:09:27', '00:00:41', '00:26:51', '00:00:38', '00:18:13']",2,00:55:52,2
157909,Tin Rebic,Croatia,2004.0,"['00:09:17', '00:00:44', '00:26:59', '00:00:40', '00:19:10']",3,00:56:52,4
30634,Jacopo Butturini,Croatia,1991.0,"['00:09:32', '00:00:44', '00:26:43', '00:00:38', '00:19:15']",4,00:56:53,1
106537,Martin Stefan,Croatia,1996.0,"['00:10:00', '00:00:45', '00:27:07', '00:00:38', '00:18:51']",5,00:57:22,10
163918,Zeljko Cota,Croatia,1997.0,"['00:10:36', '00:00:48', '00:27:43', '00:00:43', '00:17:56']",6,00:57:48,11
96039,Luka Dumancic,Croatia,1998.0,"['00:09:45', '00:00:44', '00:28:37', '00:00:38', '00:18:38']",7,00:58:24,8
131451,Matko Saric,Croatia,2002.0,"['00:10:53', '00:00:42', '00:27:30', '00:00:40', '00:18:51']",8,00:58:37,61
170134,Pablo Benko,Croatia,2006.0,"['00:09:32', '00:00:40', '00:28:49', '00:00:40', '00:19:34']",9,00:59:17,12
75939,Tin Kauric,Croatia,1997.0,"['00:09:39', '00:00:51', '00:28:38', '00:00:44', '00:19:45']",10,00:59:39,14
174905,Filip Carevic,Croatia,2005.0,"['00:10:23', '00:00:51', '00:27:52', '00:00:44', '00:19:51']",11,00:59:44,15
163932,Marko Ivancic,Croatia,2004.0,"['00:10:35', '00:00:50', '00:27:43', '00:00:47', '00:20:23']",12,01:00:20,17
157917,Mislav Hanza,Croatia,2004.0,"['00:10:22', '00:00:56', '00:27:49', '00:00:53', '00:20:31']",13,01:00:33,21
131437,Adrian Zgaljic,Croatia,1992.0,"['00:09:45', '00:00:44', '00:28:36', '00:00:44', '00:21:55']",14,01:01:46,9
161816,Marin Stipcevic,Croatia,2005.0,"['00:09:38', '00:00:41', '00:28:46', '00:00:40', '00:23:40']",15,01:03:27,5
170135,Vito Obrovac,Croatia,2006.0,"['00:10:36', '00:00:43', '00:29:35', '00:00:54', '00:21:52']",16,01:03:43,13
174906,Loris Faustini,Croatia,,"['00:10:43', '00:00:50', '00:29:35', '00:00:48', '00:21:45']",17,01:03:44,23
163931,Mark Surina,Croatia,2005.0,"['00:11:43', '00:00:50', '00:29:50', '00:00:47', '00:20:53']",18,01:04:04,26
163904,Mario Sporcic,Croatia,1975.0,"['00:12:07', '00:00:58', '00:29:26', '00:00:52', '00:21:17']",19,01:04:42,22
174907,Borna Matuzalem,Croatia,2006.0,"['00:11:14', '00:00:51', '00:30:28', '00:00:42', '00:21:38']",20,01:04:55,40
174908,Patrik Smejkal,Croatia,,"['00:11:08', '00:00:51', '00:30:24', '00:00:50', '00:21:59']",21,01:05:14,18
170840,Davor Varga,Croatia,1995.0,"['00:13:39', '00:00:50', '00:30:07', '00:01:02', '00:20:09']",22,01:05:49,32
170841,Matej Dolibasic,Croatia,,"['00:12:13', '00:01:27', '00:29:42', '00:01:14', '00:21:52']",23,01:06:29,25
174909,Borna Dobravac,Croatia,2006.0,"['00:10:55', '00:00:46', '00:30:49', '00:00:51', '00:23:26']",24,01:06:48,29
130823,Kristian Hrbic,Croatia,1993.0,"['00:09:59', '00:00:50', '00:29:57', '00:00:51', '00:25:14']",25,01:06:53,33
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
SEGMENT_LABELS = ["Swim", "T1", "Bike", "T2", "Run"]
CHECKPOINTS    = ["Start", "After Swim", "After T1", "After Bike", "After T2", "After Run"]
# Narrow T1/T2 for visual rhythm; swim/bike/run equal width.
SEG_WIDTHS = np.array([1.0, 0.25, 1.0, 0.25, 1.0])
X_NODES    = np.concatenate(([0.0], np.cumsum(SEG_WIDTHS)))
SEG_MIDS   = (X_NODES[:-1] + X_NODES[1:]) / 2


def hms_to_seconds(t):
    if not isinstance(t, str) or not t.strip():
        return np.nan
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def fmt_mmss(sec):
    sec = int(round(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def fmt_gap(sec):
    if sec < 60:
        return f"+{sec:.0f}s"
    m, s = divmod(int(round(sec)), 60)
    return f"+{m}:{s:02d}"


def synthesise_field(n_extra=60, seed=7):
    """Generate a realistic extra field to stress-test the designs on ~80 athletes.

    A single latent 'skill' drives swim/bike/run loosely correlated (triathletes
    who are strong on one leg tend to be strong on others, but not perfectly).
    Transition times are nearly uncorrelated with skill.
    """
    rng = np.random.default_rng(seed)
    skill = rng.normal(0, 1, n_extra)

    def leg(mean, std, corr):
        return mean + std * (corr * skill + np.sqrt(1 - corr ** 2) * rng.normal(0, 1, n_extra))

    swim = leg(600,  50, 0.55)   # 10:00 +- 50s
    t1   = leg( 45,   8, 0.10)
    bike = leg(1700, 110, 0.70)  # 28:20 +- 110s
    t2   = leg( 42,   7, 0.10)
    run  = leg(1100, 130, 0.75)  # 18:20 +- 130s

    rows = []
    for i in range(n_extra):
        splits_list = [
            f"00:{fmt_mmss(swim[i])}",
            f"00:{fmt_mmss(t1[i])}",
            f"00:{fmt_mmss(bike[i])}",
            f"00:{fmt_mmss(t2[i])}",
            f"00:{fmt_mmss(run[i])}",
        ]
        total = swim[i] + t1[i] + bike[i] + t2[i] + run[i]
        h = int(total // 3600); rem = total - h * 3600
        m = int(rem // 60); s = int(rem - m * 60)
        rows.append({
            "athlete_id":    900000 + i,
            "athlete_title": f"Synth {i+1:02d}",
            "athlete_country_name": "SYN",
            "athlete_yob":   2000,
            "splits":        str(splits_list),
            "position":      0,      # filled in below by total_time rank
            "total_time":    f"{h:02d}:{m:02d}:{s:02d}",
            "start_num":     200 + i,
        })
    return pd.DataFrame(rows)


def load_data():
    real = pd.read_csv(io.StringIO(csv_text))
    synth = synthesise_field()
    df = pd.concat([real, synth], ignore_index=True)
    df = df[df["position"] != "DNF"].copy()
    df["splits_list"] = df["splits"].apply(ast.literal_eval)

    splits = np.vstack(df["splits_list"].apply(
        lambda xs: [hms_to_seconds(x) for x in xs]
    ).to_numpy())
    split_df = pd.DataFrame(splits, columns=["swim", "t1", "bike", "t2", "run"],
                            index=df.index)

    cum = pd.DataFrame(index=df.index)
    cum["Start"] = 0.0
    cum["After Swim"] = split_df["swim"]
    cum["After T1"]   = cum["After Swim"] + split_df["t1"]
    cum["After Bike"] = cum["After T1"]   + split_df["bike"]
    cum["After T2"]   = cum["After Bike"] + split_df["t2"]
    cum["After Run"]  = cum["After T2"]   + split_df["run"]

    # Re-derive position from cumulative finish time (real field had positions 1-25,
    # synth had 0; after blending everyone gets a global finish rank).
    df["position"] = cum["After Run"].rank(method="first").astype(int)

    gap = pd.DataFrame(index=df.index)
    for c in CHECKPOINTS:
        gap[c] = cum[c] - cum[c].min()

    pos = pd.DataFrame(index=df.index)
    df["start_num"] = pd.to_numeric(df["start_num"], errors="coerce")
    pos["Start"] = df["start_num"].rank(method="first").astype(int)
    for c in CHECKPOINTS[1:]:
        pos[c] = cum[c].rank(method="first").astype(int)

    return df, split_df, cum, gap, pos


def add_transition_bg(ax):
    ax.axvspan(X_NODES[1], X_NODES[2], color="#f2f2f2", alpha=0.6, zorder=0)
    ax.axvspan(X_NODES[3], X_NODES[4], color="#f2f2f2", alpha=0.6, zorder=0)


# ---------------------------------------------------------------------------
# Design A - Per-segment strip panels (small multiples)
# ---------------------------------------------------------------------------
# Each discipline gets its own panel with its own x-axis; athlete dots are
# sorted by finish position so the eye scans vertically from winner down.
# A faint polyline connects each athlete's dots across panels so individual
# stories still read.  This is the most direct answer to the scaling problem:
# five axes, five scales, no segment can bully another.
def design_a_strip_panels(df, split_df, gap):
    order = df.sort_values("position").index
    n = len(order)
    y_of = {idx: rank for rank, idx in enumerate(order)}

    # Highlight the top-3 overall plus any big movers.
    highlight_ids = list(order[:3])

    fig, axes = plt.subplots(1, 5, figsize=(16, 10), sharey=True,
                             gridspec_kw={"width_ratios": [1, 0.45, 1, 0.45, 1]})

    leg_cols = ["swim", "t1", "bike", "t2", "run"]
    for ax, leg, label in zip(axes, leg_cols, SEGMENT_LABELS):
        leg_min = split_df[leg].min()
        for idx in order:
            gap_s = split_df.loc[idx, leg] - leg_min
            is_hl = idx in highlight_ids
            ax.scatter(gap_s, y_of[idx],
                       s=32 if is_hl else 14,
                       c="#d62728" if is_hl else "#4477aa",
                       alpha=0.95 if is_hl else 0.55,
                       zorder=3 if is_hl else 2,
                       edgecolors="white", linewidths=0.6)
        ax.set_title(f"{label}\n(leader {fmt_mmss(leg_min)})",
                     fontsize=11, fontweight="bold")
        ax.axvline(0, color="#222", linewidth=0.8, alpha=0.5)
        ax.grid(True, axis="y", alpha=0.25, linestyle="--", linewidth=0.5)
        ax.set_xlabel("gap vs leg leader (s)", fontsize=9)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)

    # Thin polyline per athlete across the 5 panels, drawn over all axes
    # using axis-coord transforms.  Only do this for the highlights so the
    # chart stays legible at n=85.
    for hid in highlight_ids:
        xs_disp, ys_disp = [], []
        for ax, leg in zip(axes, leg_cols):
            gx = split_df.loc[hid, leg] - split_df[leg].min()
            gy = y_of[hid]
            pt = ax.transData.transform((gx, gy))
            xs_disp.append(pt[0]); ys_disp.append(pt[1])
        inv = fig.transFigure.inverted()
        pts = [inv.transform((x, y)) for x, y in zip(xs_disp, ys_disp)]
        line = plt.Line2D([p[0] for p in pts], [p[1] for p in pts],
                          transform=fig.transFigure,
                          linewidth=1.6, alpha=0.4, color="#d62728", zorder=4)
        fig.lines.append(line)

    axes[0].invert_yaxis()
    axes[0].set_ylabel("finish rank", fontsize=11, fontweight="bold")
    fig.suptitle("Design A — per-segment strip panels "
                 "(each leg on its own scale; top 3 highlighted)",
                 fontsize=13, fontweight="bold", y=0.995)
    fig.tight_layout()
    plt.show()


# ---------------------------------------------------------------------------
# Design B - Per-segment delta snake (gap resets each leg)
# ---------------------------------------------------------------------------
# Y-axis is gap within the current leg only, resetting to zero at the start
# of every segment.  The curve reads as 'how much time did this athlete
# lose on THIS leg'.  The run stops dominating because the y-axis is never
# cumulative.  Keeps the left-to-right race narrative of the original snake.
def design_b_delta_snake(df, split_df):
    order = df.sort_values("position").index
    top_k = list(order[:8])

    fig, ax = plt.subplots(figsize=(15, 8))
    add_transition_bg(ax)

    leg_cols = ["swim", "t1", "bike", "t2", "run"]
    leg_mins = {leg: split_df[leg].min() for leg in leg_cols}
    # max within-leg gap across entire field, for y-axis limits
    max_gap_any_leg = max((split_df[leg] - leg_mins[leg]).max() for leg in leg_cols)

    cmap = plt.cm.tab10
    for rank, idx in enumerate(order):
        xs, ys = [], []
        for i, leg in enumerate(leg_cols):
            start_gap = 0.0
            end_gap   = split_df.loc[idx, leg] - leg_mins[leg]
            xs.extend([X_NODES[i], X_NODES[i + 1]])
            ys.extend([start_gap, end_gap])

        is_top = idx in top_k
        ax.plot(xs, ys,
                linewidth=2.2 if is_top else 0.9,
                alpha=0.9 if is_top else 0.25,
                color=cmap(top_k.index(idx) % 10) if is_top else "#888",
                zorder=3 if is_top else 1)
        if is_top:
            name = df.loc[idx, "athlete_title"]
            ax.text(X_NODES[-1] + 0.04, ys[-1], f"{df.loc[idx, 'position']:>2}  {name}",
                    color=cmap(top_k.index(idx) % 10),
                    va="center", ha="left", fontsize=9.5,
                    clip_on=False, zorder=4)

    ax.invert_yaxis()
    ax.set_xticks(SEG_MIDS); ax.set_xticklabels(SEGMENT_LABELS, fontsize=11)
    ax.set_ylabel("Within-leg gap (s, behind leg leader)", fontsize=11, fontweight="bold")
    ax.set_title("Design B — per-segment delta snake "
                 "(gap resets to zero at the start of each leg)",
                 fontsize=13, fontweight="bold", pad=14)
    ax.set_xlim(X_NODES.min() - 0.05, X_NODES.max() + 1.2)
    ax.set_ylim(max_gap_any_leg * 1.05, -max_gap_any_leg * 0.05)
    ax.grid(True, axis="y", alpha=0.25, linestyle="--", linewidth=0.5)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)

    # Annotate each leg's max gap so the viewer knows the real scale.
    for i, leg in enumerate(leg_cols):
        mx = (split_df[leg] - leg_mins[leg]).max()
        ax.text(SEG_MIDS[i], -max_gap_any_leg * 0.02,
                f"max {fmt_gap(mx)}", ha="center", va="bottom",
                fontsize=8.5, color="#555",
                bbox=dict(boxstyle="round,pad=0.25", facecolor="white",
                          edgecolor="#ccc", linewidth=0.6, alpha=0.9))
    plt.tight_layout(); plt.show()


# ---------------------------------------------------------------------------
# Design C - Rank-lane bump chart (positions only)
# ---------------------------------------------------------------------------
# Side-steps the scaling problem entirely by plotting rank, not time.
# A 10-second swim gap in a tight race still causes position changes, which
# the eye picks up. Large fields benefit from top-K highlighting + faded rest.
def design_c_rank_bump(df, pos):
    order = df.sort_values("position").index
    top_k = list(order[:10])
    n = len(order)

    fig, ax = plt.subplots(figsize=(14, 9))
    add_transition_bg(ax)
    cmap = plt.cm.tab10

    for idx in order:
        y = pos.loc[idx, CHECKPOINTS].to_numpy(dtype=float)
        is_top = idx in top_k
        ax.plot(X_NODES, y,
                linewidth=2.5 if is_top else 0.8,
                alpha=0.9 if is_top else 0.15,
                color=cmap(top_k.index(idx) % 10) if is_top else "#888",
                zorder=3 if is_top else 1,
                solid_capstyle="round")
        if is_top:
            name = df.loc[idx, "athlete_title"]
            ax.text(X_NODES[-1] + 0.04, y[-1],
                    f"{int(y[-1]):>2}  {name}",
                    color=cmap(top_k.index(idx) % 10),
                    va="center", ha="left", fontsize=9.5,
                    clip_on=False, zorder=4)
            # Dot markers at each checkpoint for the highlighted lines
            ax.scatter(X_NODES, y, s=22,
                       color=cmap(top_k.index(idx) % 10),
                       edgecolors="white", linewidths=0.8, zorder=4)

    ax.invert_yaxis()
    ax.set_xticks(SEG_MIDS); ax.set_xticklabels(SEGMENT_LABELS, fontsize=11)
    ax.set_ylabel("Position", fontsize=11, fontweight="bold")
    ax.set_title(f"Design C — rank-lane bump chart "
                 f"(top 10 of {n} highlighted)",
                 fontsize=13, fontweight="bold", pad=14)
    ax.set_xlim(X_NODES.min() - 0.05, X_NODES.max() + 1.4)
    ax.set_ylim(n + 1, 0)
    ax.grid(True, axis="y", alpha=0.25, linestyle="--", linewidth=0.5)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    plt.tight_layout(); plt.show()


# ---------------------------------------------------------------------------
# Design D - Sqrt-scaled cumulative snake
# ---------------------------------------------------------------------------
# Keep the familiar cumulative-gap layout but bend the y-axis with sqrt(gap)
# so early-race tens-of-seconds are visible against late-race minutes.
# Same data as the baseline snake; only the axis is non-linear.
def design_d_sqrt_cumulative(df, gap):
    order = df.sort_values("position").index
    top_k = list(order[:10])
    cmap = plt.cm.tab10

    fig, ax = plt.subplots(figsize=(15, 8))
    add_transition_bg(ax)

    for idx in order:
        raw = gap.loc[idx, CHECKPOINTS].to_numpy(dtype=float)
        ys  = np.sqrt(raw)
        is_top = idx in top_k
        ax.plot(X_NODES, ys,
                linewidth=2.2 if is_top else 0.8,
                alpha=0.9 if is_top else 0.18,
                color=cmap(top_k.index(idx) % 10) if is_top else "#888",
                zorder=3 if is_top else 1)
        if is_top:
            name = df.loc[idx, "athlete_title"]
            ax.text(X_NODES[-1] + 0.04, ys[-1],
                    f"{df.loc[idx, 'position']:>2}  {name}  "
                    f"(+{fmt_mmss(raw[-1])})",
                    color=cmap(top_k.index(idx) % 10),
                    va="center", ha="left", fontsize=9.5,
                    clip_on=False, zorder=4)

    ax.invert_yaxis()
    ax.set_xticks(SEG_MIDS); ax.set_xticklabels(SEGMENT_LABELS, fontsize=11)

    # Convert sqrt-space y-ticks back into real-time labels.
    max_gap = gap["After Run"].max()
    tick_secs = [0, 30, 90, 180, 300, 600, 900, 1200]
    tick_secs = [t for t in tick_secs if t <= max_gap * 1.1]
    ax.set_yticks(np.sqrt(tick_secs))
    ax.set_yticklabels([fmt_mmss(t) if t else "leader" for t in tick_secs])
    ax.set_ylabel("Cumulative gap (sqrt-scaled time axis)",
                  fontsize=11, fontweight="bold")
    ax.set_title("Design D — sqrt-scaled cumulative snake "
                 "(compresses late-race gaps without hiding early ones)",
                 fontsize=13, fontweight="bold", pad=14)
    ax.set_xlim(X_NODES.min() - 0.05, X_NODES.max() + 1.6)
    ax.grid(True, axis="y", alpha=0.25, linestyle="--", linewidth=0.5)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    plt.tight_layout(); plt.show()


# ---------------------------------------------------------------------------
# Design E - Interactive discipline-focus (Plotly HTML)
# ---------------------------------------------------------------------------
# A single Plotly figure whose buttons swap the view between:
#   * All - the full cumulative snake, sqrt-scaled (like Design D)
#   * Swim / Bike / Run - zoom to that leg's gap distribution + rank bump
#   * Rank - the rank-only bump chart (Design C)
# Hover surfaces athlete / gap / position; the user moves left/right through
# disciplines by clicking the tabs.  Written to ./snake_interactive.html.
def design_e_plotly_focus(df, split_df, cum, gap, pos, out_path="snake_interactive.html"):
    order = df.sort_values("position").index

    # ---- Trace bank: for each discipline focus, one trace per athlete.
    # We build 5 view-variants and toggle visibility via buttons.
    views = {}

    # View "All": sqrt-scaled cumulative gap vs x (same layout as Design D).
    view_all = []
    for idx in order:
        raw = gap.loc[idx, CHECKPOINTS].to_numpy(dtype=float)
        view_all.append(go.Scatter(
            x=X_NODES, y=np.sqrt(raw),
            mode="lines",
            name=df.loc[idx, "athlete_title"],
            line=dict(width=1.6, color="rgba(68,119,170,0.35)"),
            hovertemplate=(
                f"<b>{df.loc[idx, 'athlete_title']}</b><br>"
                f"Finish: #{df.loc[idx, 'position']}  ({df.loc[idx, 'total_time']})<br>"
                "%{text}<extra></extra>"
            ),
            text=[f"{cp}: +{fmt_mmss(raw[i])}" for i, cp in enumerate(CHECKPOINTS)],
            showlegend=False,
        ))
    views["All"] = view_all

    # Per-discipline focus: x = within-leg gap (s), y = finish rank.
    rank_of = {idx: int(df.loc[idx, "position"]) for idx in order}
    for leg, label in [("swim", "Swim"), ("bike", "Bike"), ("run", "Run")]:
        leg_min = split_df[leg].min()
        traces = []
        for idx in order:
            gap_s = split_df.loc[idx, leg] - leg_min
            traces.append(go.Scatter(
                x=[gap_s], y=[rank_of[idx]],
                mode="markers",
                name=df.loc[idx, "athlete_title"],
                marker=dict(size=9, color="#4477aa",
                            line=dict(color="white", width=0.8)),
                hovertemplate=(
                    f"<b>{df.loc[idx, 'athlete_title']}</b><br>"
                    f"{label} gap: +{fmt_mmss(gap_s)}<br>"
                    f"{label} split: {fmt_mmss(split_df.loc[idx, leg])}<br>"
                    f"Finish: #{rank_of[idx]}<extra></extra>"
                ),
                showlegend=False,
            ))
        views[label] = traces

    # Rank bump (like Design C).
    view_rank = []
    for idx in order:
        y = pos.loc[idx, CHECKPOINTS].to_numpy(dtype=float)
        view_rank.append(go.Scatter(
            x=X_NODES, y=y,
            mode="lines",
            name=df.loc[idx, "athlete_title"],
            line=dict(width=1.4, color="rgba(68,119,170,0.45)"),
            hovertemplate=(
                f"<b>{df.loc[idx, 'athlete_title']}</b><br>"
                "%{text}<extra></extra>"
            ),
            text=[f"{cp}: P{int(y[i])}" for i, cp in enumerate(CHECKPOINTS)],
            showlegend=False,
        ))
    views["Rank"] = view_rank

    # Assemble all traces in a fixed order; we toggle visibility per view.
    view_order = ["All", "Swim", "Bike", "Run", "Rank"]
    all_traces, slices = [], {}
    for v in view_order:
        start = len(all_traces)
        all_traces.extend(views[v])
        slices[v] = (start, len(all_traces))

    # Initial visibility = "All".
    visibility = {
        v: [slices[v][0] <= i < slices[v][1] for i in range(len(all_traces))]
        for v in view_order
    }

    # Per-view axis configuration so the same figure can represent
    # 'cumulative sqrt snake', 'strip plot' and 'rank bump' convincingly.
    tick_secs = [0, 30, 90, 180, 300, 600, 900, 1200]
    sqrt_axis = dict(
        tickmode="array",
        tickvals=[float(np.sqrt(t)) for t in tick_secs],
        ticktext=[fmt_mmss(t) if t else "leader" for t in tick_secs],
        autorange="reversed",
        title="Cumulative gap",
    )
    rank_axis = dict(autorange="reversed", title="Position",
                     dtick=max(1, len(order) // 10))
    leg_axis  = dict(autorange="reversed", title="Finish rank",
                     dtick=max(1, len(order) // 10))

    view_layouts = {
        "All":  dict(xaxis=dict(tickmode="array", tickvals=list(SEG_MIDS),
                                ticktext=SEGMENT_LABELS, title=""),
                     yaxis=sqrt_axis),
        "Swim": dict(xaxis=dict(title="Swim gap vs leg leader (s)"),
                     yaxis=leg_axis),
        "Bike": dict(xaxis=dict(title="Bike gap vs leg leader (s)"),
                     yaxis=leg_axis),
        "Run":  dict(xaxis=dict(title="Run gap vs leg leader (s)"),
                     yaxis=leg_axis),
        "Rank": dict(xaxis=dict(tickmode="array", tickvals=list(SEG_MIDS),
                                ticktext=SEGMENT_LABELS, title=""),
                     yaxis=rank_axis),
    }

    buttons = [
        dict(
            label=v,
            method="update",
            args=[{"visible": visibility[v]},
                  {"xaxis": view_layouts[v]["xaxis"],
                   "yaxis": view_layouts[v]["yaxis"],
                   "title": f"Design E — discipline focus: {v}"}],
        )
        for v in view_order
    ]

    fig = go.Figure(data=all_traces)
    fig.update_layout(
        title="Design E — discipline focus: All",
        width=1200, height=700,
        template="simple_white",
        updatemenus=[dict(
            type="buttons", direction="right",
            x=0.5, y=1.12, xanchor="center",
            buttons=buttons,
            pad=dict(t=2, b=2),
            bgcolor="#f4f4f4",
        )],
        margin=dict(t=90, l=60, r=40, b=60),
        **view_layouts["All"],
    )
    # Initial traces for "All" already visible; hide the rest.
    for i, trace in enumerate(fig.data):
        trace.visible = visibility["All"][i]

    fig.write_html(out_path, include_plotlyjs="cdn", full_html=True)
    print(f"Design E written to {out_path} (open in a browser)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    df, split_df, cum, gap, pos = load_data()
    print(f"Loaded {len(df)} athletes (real + synthetic)")

    design_a_strip_panels(df, split_df, gap)
    design_b_delta_snake(df, split_df)
    design_c_rank_bump(df, pos)
    design_d_sqrt_cumulative(df, gap)
    design_e_plotly_focus(df, split_df, cum, gap, pos)
