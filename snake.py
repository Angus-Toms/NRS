import pandas as pd
import ast
import io
import numpy as np
import matplotlib.pyplot as plt

# ---------- Paste your CSV rows here ----------
csv_text = """athlete_id,athlete_title,athlete_country_name,athlete_yob,splits,position,total_time,start_num,athlete_profile_image
135829,Gabriel Barac,Croatia,2004.0,"['00:09:29', '00:00:42', '00:26:47', '00:00:36', '00:16:18']",1,00:53:55,3,https://prod-tri-assets.imgix.net/ja.png
130086,Niko Matas,Croatia,2003.0,"['00:09:27', '00:00:41', '00:26:51', '00:00:38', '00:18:13']",2,00:55:52,2,
157909,Tin Rebic,Croatia,2004.0,"['00:09:17', '00:00:44', '00:26:59', '00:00:40', '00:19:10']",3,00:56:52,4,https://prod-tri-assets.imgix.net/db163568-2bb8-47df-89b1-c46e0888d5a9.JPG
30634,Jacopo Butturini,Croatia,1991.0,"['00:09:32', '00:00:44', '00:26:43', '00:00:38', '00:19:15']",4,00:56:53,1,https://prod-tri-assets.imgix.net/WhatsApp_Image_2020-10-29_at_15.45_.38_.jpeg
106537,Martin Štefan,Croatia,1996.0,"['00:10:00', '00:00:45', '00:27:07', '00:00:38', '00:18:51']",5,00:57:22,10,
163918,Zeljko Cota,Croatia,1997.0,"['00:10:36', '00:00:48', '00:27:43', '00:00:43', '00:17:56']",6,00:57:48,11,
96039,Luka Dumančić,Croatia,1998.0,"['00:09:45', '00:00:44', '00:28:37', '00:00:38', '00:18:38']",7,00:58:24,8,https://prod-tri-assets.imgix.net/241314097_1294139597695866_7195337243548614270_n.jpeg
131451,Matko Saric,Croatia,2002.0,"['00:10:53', '00:00:42', '00:27:30', '00:00:40', '00:18:51']",8,00:58:37,61,
170134,Pablo Benko,Croatia,2006.0,"['00:09:32', '00:00:40', '00:28:49', '00:00:40', '00:19:34']",9,00:59:17,12,
75939,Tin Kaurić,Croatia,1997.0,"['00:09:39', '00:00:51', '00:28:38', '00:00:44', '00:19:45']",10,00:59:39,14,https://prod-tri-assets.imgix.net/FB_IMG_15624345167514509.jpg
174905,Filip Carevic,Croatia,2005.0,"['00:10:23', '00:00:51', '00:27:52', '00:00:44', '00:19:51']",11,00:59:44,15,
163932,Marko Ivancic,Croatia,2004.0,"['00:10:35', '00:00:50', '00:27:43', '00:00:47', '00:20:23']",12,01:00:20,17,
157917,Mislav Hanza,Croatia,2004.0,"['00:10:22', '00:00:56', '00:27:49', '00:00:53', '00:20:31']",13,01:00:33,21,
131437,Adrian Zgaljic,Croatia,1992.0,"['00:09:45', '00:00:44', '00:28:36', '00:00:44', '00:21:55']",14,01:01:46,9,
161816,Marin Stipčević,Croatia,2005.0,"['00:09:38', '00:00:41', '00:28:46', '00:00:40', '00:23:40']",15,01:03:27,5,
170135,Vito Obrovac,Croatia,2006.0,"['00:10:36', '00:00:43', '00:29:35', '00:00:54', '00:21:52']",16,01:03:43,13,
174906,Loris Faustini,Croatia,,"['00:10:43', '00:00:50', '00:29:35', '00:00:48', '00:21:45']",17,01:03:44,23,
163931,Mark Surina,Croatia,2005.0,"['00:11:43', '00:00:50', '00:29:50', '00:00:47', '00:20:53']",18,01:04:04,26,
163904,Mario Šporčić,Croatia,1975.0,"['00:12:07', '00:00:58', '00:29:26', '00:00:52', '00:21:17']",19,01:04:42,22,
174907,Borna Matuzalem,Croatia,2006.0,"['00:11:14', '00:00:51', '00:30:28', '00:00:42', '00:21:38']",20,01:04:55,40,
174908,Patrik Smejkal,Croatia,,"['00:11:08', '00:00:51', '00:30:24', '00:00:50', '00:21:59']",21,01:05:14,18,
170840,Davor Varga,Croatia,1995.0,"['00:13:39', '00:00:50', '00:30:07', '00:01:02', '00:20:09']",22,01:05:49,32,
170841,Matej Dolibašić,Croatia,,"['00:12:13', '00:01:27', '00:29:42', '00:01:14', '00:21:52']",23,01:06:29,25,
174909,Borna Dobravac,Croatia,2006.0,"['00:10:55', '00:00:46', '00:30:49', '00:00:51', '00:23:26']",24,01:06:48,29,
130823,Kristian Hrbić,Croatia,1993.0,"['00:09:59', '00:00:50', '00:29:57', '00:00:51', '00:25:14']",25,01:06:53,33,
"""
# ---------------------------------------------------

SEGMENT_LABELS = ["Swim", "T1", "Bike", "T2", "Run"]
CHECKPOINTS = ["Start", "After Swim", "After T1", "After Bike", "After T2", "After Run"]

def hms_to_seconds(t: str) -> float:
    if not isinstance(t, str) or t.strip() == "" or t == "DNF":
        return np.nan
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)

def fmt_mmss(sec: float) -> str:
    sec = int(round(sec))
    m = sec // 60
    s = sec % 60
    return f"{m:02d}:{s:02d}"

# ---- Read / parse ----
df = pd.read_csv(io.StringIO(csv_text))
df = df[df["position"] != "DNF"].copy()
df["splits_list"] = df["splits"].apply(ast.literal_eval)

split_secs = np.vstack(df["splits_list"].apply(lambda xs: [hms_to_seconds(x) for x in xs]).to_numpy())
split_df = pd.DataFrame(split_secs, columns=["swim", "t1", "bike", "t2", "run"], index=df.index)

# Cumulative times at checkpoints
cum = pd.DataFrame(index=df.index)
cum["After Swim"] = split_df["swim"]
cum["After T1"]   = split_df["swim"] + split_df["t1"]
cum["After Bike"] = split_df["swim"] + split_df["t1"] + split_df["bike"]
cum["After T2"]   = split_df["swim"] + split_df["t1"] + split_df["bike"] + split_df["t2"]
cum["After Run"]  = split_df["swim"] + split_df["t1"] + split_df["bike"] + split_df["t2"] + split_df["run"]

# Positions at checkpoints
df["start_num"] = pd.to_numeric(df["start_num"], errors="coerce")
pos = pd.DataFrame(index=df.index)
pos["Start"]      = df["start_num"].rank(method="first").astype(int)
pos["After Swim"] = cum["After Swim"].rank(method="first").astype(int)
pos["After T1"]   = cum["After T1"].rank(method="first").astype(int)
pos["After Bike"] = cum["After Bike"].rank(method="first").astype(int)
pos["After T2"]   = cum["After T2"].rank(method="first").astype(int)
pos["After Run"]  = cum["After Run"].rank(method="first").astype(int)

# ---- X layout with narrow transitions ----
seg_widths = np.array([1.0, 0.25, 1.0, 0.25, 1.0])
x = np.concatenate(([0.0], np.cumsum(seg_widths)))
seg_mids = (x[:-1] + x[1:]) / 2

t1_span = (x[1], x[2])
t2_span = (x[3], x[4])

def add_transition_bg(ax):
    ax.axvspan(*t1_span, color="#f0f0f0", alpha=0.5, zorder=0)
    ax.axvspan(*t2_span, color="#f0f0f0", alpha=0.5, zorder=0)

# Plot order by finish time
order = cum["After Run"].sort_values().index

# =======================
# Plot 1: Positions (cleaner version)
# =======================
fig, ax = plt.subplots(figsize=(14, 8))
add_transition_bg(ax)

for idx in order:
    y = pos.loc[idx, CHECKPOINTS].to_numpy(dtype=float)
    (line,) = ax.plot(x, y, linewidth=2.0, alpha=0.75, zorder=2)

    name = df.loc[idx, "athlete_title"]
    ax.text(
        x[-1] + 0.08, y[-1], name,
        color=line.get_color(),
        va="center", ha="left",
        fontsize=9.5,
        clip_on=False,
        zorder=3
    )

ax.invert_yaxis()
ax.set_ylabel("Position", fontsize=12, fontweight='bold')
ax.set_xlabel("")
ax.set_title("Race Position Through Each Discipline", fontsize=14, fontweight='bold', pad=20)

ax.grid(True, axis='y', alpha=0.3, linestyle='--', linewidth=0.5)
ax.set_axisbelow(True)

ax.set_xticks(seg_mids)
ax.set_xticklabels(SEGMENT_LABELS, fontsize=11)

ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
ax.spines['left'].set_linewidth(0.5)
ax.spines['bottom'].set_linewidth(0.5)

ax.set_xlim(x.min() - 0.1, x.max() + 1.1)

plt.tight_layout()
plt.show()

# =======================
# Plot 2: Time gaps with rescaled segments but continuous lines
# =======================
# Calculate time gaps behind leader at each checkpoint
gap = pd.DataFrame(index=df.index)
gap["Start"] = 0.0
for c in ["After Swim", "After T1", "After Bike", "After T2", "After Run"]:
    gap[c] = cum[c] - cum[c].min()

# Define scaling factors for each segment to normalize display
# We'll scale each segment so its max gap = 1.0
segment_info = [
    ("Swim", "Start", "After Swim", 1.0),
    ("T1", "After Swim", "After T1", 0.25),
    ("Bike", "After T1", "After Bike", 1.0),
    ("T2", "After Bike", "After T2", 0.25),
    ("Run", "After T2", "After Run", 1.0)
]

# Calculate max gaps for scaling
max_gaps = {}
for seg_name, start_cp, end_cp, width in segment_info:
    if seg_name in ["T1", "T2"]:
        # For transitions, use the max gap at start
        max_gaps[seg_name] = max(gap[start_cp].max(), 0.001)
    else:
        max_gaps[seg_name] = max(gap[end_cp].max(), 0.001)

# Build scaled x and y coordinates for each athlete
fig, ax = plt.subplots(figsize=(15, 8))
add_transition_bg(ax)

x_scaled = x.copy()

for idx in order:
    x_points = []
    y_points = []
    
    for i, (seg_name, start_cp, end_cp, width) in enumerate(segment_info):
        start_gap = gap.loc[idx, start_cp]
        end_gap = gap.loc[idx, end_cp]
        
        # Scale gaps to 0-1 based on max for this segment
        start_scaled = start_gap / max_gaps[seg_name]
        end_scaled = end_gap / max_gaps[seg_name]
        
        x_points.extend([x_scaled[i], x_scaled[i+1]])
        y_points.extend([start_scaled, end_scaled])
    
    (line,) = ax.plot(x_points, y_points, linewidth=2.0, alpha=0.75, zorder=2)
    
    # Add name at end
    name = df.loc[idx, "athlete_title"]
    ax.text(
        x_points[-1] + 0.08, y_points[-1], name,
        color=line.get_color(),
        va="center", ha="left",
        fontsize=9,
        clip_on=False,
        zorder=3
    )

ax.invert_yaxis()
ax.set_ylabel("Time Behind Leader (scaled per segment)", fontsize=12, fontweight='bold')
ax.set_xlabel("")
ax.set_title("Time Gaps Evolution Through Race (each segment independently scaled)", 
             fontsize=14, fontweight='bold', pad=20)

# Add segment labels
ax.set_xticks(seg_mids)
ax.set_xticklabels(SEGMENT_LABELS, fontsize=11)

# Y-axis: show 0, 0.5, 1.0 normalized scale
ax.set_yticks([0.0, 0.5, 1.0])
ax.set_yticklabels(["Leader", "0.5×", "1.0×"], fontsize=10)

ax.grid(True, axis='y', alpha=0.3, linestyle='--', linewidth=0.5)
ax.set_axisbelow(True)

ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
ax.spines['left'].set_linewidth(0.5)
ax.spines['bottom'].set_linewidth(0.5)

ax.set_xlim(x.min() - 0.1, x.max() + 1.0)

# Add annotations showing actual max gap for each main discipline
y_pos = 1.05
for seg_name in ["Swim", "Bike", "Run"]:
    seg_idx = [s[0] for s in segment_info].index(seg_name)
    x_pos = seg_mids[seg_idx]
    max_gap_time = max_gaps[seg_name]
    ax.text(
        x_pos, y_pos,
        f"Max: {fmt_mmss(max_gap_time)}",
        transform=ax.get_xaxis_transform(),
        ha="center",
        va="bottom",
        fontsize=9,
        bbox=dict(boxstyle='round,pad=0.4', facecolor='white', 
                  edgecolor='gray', alpha=0.9, linewidth=0.8)
    )

plt.tight_layout()
plt.show()