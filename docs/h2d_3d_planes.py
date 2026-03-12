import numpy as np, math, sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection, Line3DCollection

# ── data ──────────────────────────────────────────────────────────────────────
SHELL  = {"FL":(96,414),  "NL":(1185,1905),"NR":(1339,446), "FR":(694,292)}
SYNBOT = {"FL":(98,414),  "NL":(1832,1570),"NR":(1520,1012),"FR":(1513,452)}
ORDER  = ["FL","FR","NR","NL"]
SCALE  = 300.0
Z_SH, Z_SY = 0.0, 6.0        # plane separation

# flip Y so near=front, normalise
MAX_Y = 2100.0
def n(d):
    return {k: (v[0]/SCALE, (MAX_Y - v[1])/SCALE) for k,v in d.items()}
SH, SY = n(SHELL), n(SYNBOT)

# extended plane bounds (past all corners)
XMIN, XMAX = -2.0, 10.0
YMIN, YMAX = -2.0, 10.0

# ── canvas ────────────────────────────────────────────────────────────────────
fig = plt.figure(figsize=(22, 16), facecolor='#04060e')
ax  = fig.add_subplot(111, projection='3d', facecolor='#04060e')

# ── helper: filled polygon ────────────────────────────────────────────────────
def add_poly(verts, fc, ec, lw=1.5, alpha=1.0):
    p = Poly3DCollection([verts], facecolors=[fc], edgecolors=[ec], linewidths=lw)
    p.set_alpha(alpha)
    ax.add_collection3d(p)

# ── BACKGROUND PLANES (large, extend past viewport) ───────────────────────────
def bg_plane(z, fc, ec):
    verts = [(XMIN,YMIN,z),(XMAX,YMIN,z),(XMAX,YMAX,z),(XMIN,YMAX,z)]
    add_poly(verts, fc, ec, lw=0, alpha=0.38)

bg_plane(Z_SH, (0.04,0.14,0.38,1), (0.15,0.35,0.80,1))
bg_plane(Z_SY, (0.38,0.10,0.04,1), (0.80,0.35,0.15,1))

# ── GRID LINES on each plane ──────────────────────────────────────────────────
def grid(z, col, step=1.0):
    for g in np.arange(XMIN, XMAX+0.01, step):
        ax.plot([g,g],[YMIN,YMAX],[z,z], color=(*col, 0.18), lw=0.6, zorder=1)
    for g in np.arange(YMIN, YMAX+0.01, step):
        ax.plot([XMIN,XMAX],[g,g],[z,z], color=(*col, 0.18), lw=0.6, zorder=1)
    # bold every 2
    for g in np.arange(XMIN, XMAX+0.01, step*2):
        ax.plot([g,g],[YMIN,YMAX],[z,z], color=(*col, 0.35), lw=1.0, zorder=2)
    for g in np.arange(YMIN, YMAX+0.01, step*2):
        ax.plot([XMIN,XMAX],[g,g],[z,z], color=(*col, 0.35), lw=1.0, zorder=2)

grid(Z_SH, (0.25, 0.55, 1.0))
grid(Z_SY, (1.00, 0.55, 0.25))

# ── CORNER QUADS (highlighted regions) ───────────────────────────────────────
sh_q = [(SH[k][0], SH[k][1], Z_SH) for k in ORDER]
sy_q = [(SY[k][0], SY[k][1], Z_SY) for k in ORDER]

add_poly(sh_q, (0.20, 0.50, 1.00, 0.55), (0.5, 0.8, 1.0, 1.0), lw=3.5)
add_poly(sy_q, (1.00, 0.50, 0.20, 0.55), (1.0, 0.75, 0.35, 1.0), lw=3.5)

# ── VERTICAL EDGE PILLARS (box sides for depth) ───────────────────────────────
for k in ORDER:
    x1,y1 = SH[k]; x2,y2 = SY[k]
    # light vertical at shell corner
    ax.plot([x1,x1],[y1,y1],[Z_SH,Z_SY], color=(0.4,0.6,1.0,0.25), lw=1.2, ls=':', zorder=3)
    # light vertical at synbot corner
    ax.plot([x2,x2],[y2,y2],[Z_SH,Z_SY], color=(1.0,0.6,0.4,0.25), lw=1.2, ls=':', zorder=3)

# ── CONNECTOR LINES (corner pair offsets) ─────────────────────────────────────
C_CONN = {"FL":"#00ffaa","NL":"#ff44cc","NR":"#44ccff","FR":"#ffee22"}
for k in ("FL","NL","NR","FR"):
    x1,y1 = SH[k]; x2,y2 = SY[k]
    ax.plot([x1,x2],[y1,y2],[Z_SH,Z_SY],
            color=C_CONN[k], lw=3.0, alpha=0.95, ls='--', zorder=20)

# ── CORNER MARKERS ────────────────────────────────────────────────────────────
for k in ("FL","NL","NR","FR"):
    x,y = SH[k]
    ax.scatter([x],[y],[Z_SH], color='#5599ff', s=180, zorder=25,
               edgecolors='white', linewidths=2.0)
    x,y = SY[k]
    ax.scatter([x],[y],[Z_SY], color='#ff8833', s=180, zorder=25,
               edgecolors='white', linewidths=2.0, marker='D')

# ── CORNER LABELS ─────────────────────────────────────────────────────────────
OFFSETS = {k: {"dx": SYNBOT[k][0]-SHELL[k][0],
               "dy": SYNBOT[k][1]-SHELL[k][1],
               "dist": round(math.hypot(SYNBOT[k][0]-SHELL[k][0],
                                        SYNBOT[k][1]-SHELL[k][1]),0)}
           for k in ("FL","NL","NR","FR")}

for k in ("FL","NL","NR","FR"):
    x,y = SH[k]
    ax.text(x, y, Z_SH-0.22, f" {k}", color='#88bbff', fontsize=14,
            fontweight='bold', ha='center', va='top', zorder=30)
    x,y = SY[k]
    ax.text(x, y, Z_SY+0.22, f" {k}", color='#ffaa77', fontsize=14,
            fontweight='bold', ha='center', va='bottom', zorder=30)

# ── OFFSET BADGES (midpoint of each connector) ───────────────────────────────
for k in ("FL","NL","NR","FR"):
    mx = (SH[k][0]+SY[k][0])/2
    my = (SH[k][1]+SY[k][1])/2
    mz = (Z_SH+Z_SY)/2
    o = OFFSETS[k]
    lbl = f"{int(o['dist'])}px"
    ax.text(mx+0.15, my+0.15, mz, lbl, color=C_CONN[k],
            fontsize=11, fontweight='bold', alpha=0.95, zorder=30)

# ── PLANE TITLE LABELS ────────────────────────────────────────────────────────
ax.text(XMIN+0.3, YMAX-0.4, Z_SH, 'SHELL', color='#5599ff',
        fontsize=22, fontweight='black', alpha=0.9, zorder=40)
ax.text(XMIN+0.3, YMAX-0.4, Z_SY, 'SYNBOT', color='#ff8833',
        fontsize=22, fontweight='black', alpha=0.9, zorder=40)

# ── VERTICAL SCALE BAR ────────────────────────────────────────────────────────
ax.plot([XMAX-0.5]*2, [YMIN+0.5]*2, [Z_SH, Z_SY],
        color='white', lw=2, alpha=0.5, zorder=35)
ax.text(XMAX-0.4, YMIN+0.6, (Z_SH+Z_SY)/2,
        'Δ plane\nseparation', color='#aaaaaa', fontsize=9, va='center')

# ── AXES STYLE ────────────────────────────────────────────────────────────────
ax.set_xlim(XMIN, XMAX)
ax.set_ylim(YMIN, YMAX)
ax.set_zlim(-1.2, 8.5)
ax.set_xticks([]); ax.set_yticks([]); ax.set_zticks([])
ax.set_xlabel(''); ax.set_ylabel(''); ax.set_zlabel('')
ax.xaxis.pane.fill = False
ax.yaxis.pane.fill = False
ax.zaxis.pane.fill = False
ax.xaxis.pane.set_edgecolor('#0a0c18')
ax.yaxis.pane.set_edgecolor('#0a0c18')
ax.zaxis.pane.set_edgecolor('#0a0c18')
ax.xaxis._axinfo['grid']['color'] = (0,0,0,0)
ax.yaxis._axinfo['grid']['color'] = (0,0,0,0)
ax.zaxis._axinfo['grid']['color'] = (0,0,0,0)
ax.tick_params(colors='#222')

ax.view_init(elev=28, azim=-48)

# ── LEGEND ────────────────────────────────────────────────────────────────────
import matplotlib.patches as mpt
import matplotlib.lines as mli
handles = [
    mpt.Patch(facecolor='#4488ff', edgecolor='white', label='shell corners'),
    mpt.Patch(facecolor='#ff8833', edgecolor='white', label='synbot corners'),
    mli.Line2D([0],[0], color='#aaaaaa', ls='--', lw=1.5, label='connector = offset vector'),
]
leg = ax.legend(handles=handles, loc='upper left', framealpha=0.25,
                facecolor='#0a0c18', edgecolor='#333355', labelcolor='white',
                fontsize=11, bbox_to_anchor=(0.01,0.97))

# ── TITLE ─────────────────────────────────────────────────────────────────────
fig.text(0.5, 0.97,
    'Shell vs Synbot — Two Congruent Calibration Planes',
    ha='center', color='white', fontsize=20, fontweight='black')
fig.text(0.5, 0.935,
    'dashed lines = per-corner offset vector  |  FL is near-perfect (2px)  |  other corners diverge up to 835px',
    ha='center', color='#888899', fontsize=11)

plt.tight_layout(rect=[0,0,1,0.93])
plt.savefig('/tmp/h2d_3d_planes.png', dpi=160, bbox_inches='tight',
            facecolor='#04060e', edgecolor='none')
print("saved /tmp/h2d_3d_planes.png")
