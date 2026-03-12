"""
Convergence visualization: synbot corners warped through H → shell corners.
The merged polygon at Z=0 becomes the canonical PLATE BOUNDARY.
"""
import numpy as np, math, sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

# ── data ──────────────────────────────────────────────────────────────────────
SHELL  = {"FL":(96,414),  "NL":(1185,1905),"NR":(1339,446), "FR":(694,292)}
SYNBOT = {"FL":(98,414),  "NL":(1832,1570),"NR":(1520,1012),"FR":(1513,452)}
ORDER  = ["FL","FR","NR","NL"]
SCALE  = 300.0
MAX_Y  = 2100.0

def n(d):
    return {k:(v[0]/SCALE, (MAX_Y-v[1])/SCALE) for k,v in d.items()}
SH, SY = n(SHELL), n(SYNBOT)

# Homography DLT (synbot→shell)
def _dlt(src, dst):
    keys=["FL","NL","NR","FR"]; A=[]
    for k in keys:
        x,y=src[k]; xp,yp=dst[k]
        A+=[[-x,-y,-1,0,0,0,xp*x,xp*y,xp],[0,0,0,-x,-y,-1,yp*x,yp*y,yp]]
    _,_,Vt=np.linalg.svd(np.array(A,dtype=np.float64))
    H=Vt[-1].reshape(3,3); return H/H[2,2]
H = _dlt(SY, SH)   # synbot-normalised → shell-normalised

def warp(H, pt):
    v = H @ np.array([pt[0],pt[1],1.0])
    return (float(v[0]/v[2]), float(v[1]/v[2]))

# Warped synbot (should land exactly on SH)
SW = {k: warp(H, SY[k]) for k in ORDER}

Z_SH, Z_SY = 0.0, 7.0
XMIN,XMAX,YMIN,YMAX = -1.5,9.0,-1.5,9.0

fig = plt.figure(figsize=(22,16), facecolor='#04060e')
ax  = fig.add_subplot(111, projection='3d', facecolor='#04060e')

# ── background planes ─────────────────────────────────────────────────────────
def bg(z, fc, ec):
    verts=[(XMIN,YMIN,z),(XMAX,YMIN,z),(XMAX,YMAX,z),(XMIN,YMAX,z)]
    p=Poly3DCollection([verts],facecolors=[fc],edgecolors=[ec],linewidths=0)
    p.set_alpha(0.30); ax.add_collection3d(p)

bg(Z_SH,(0.04,0.14,0.38,1),(0,0,0,0))
bg(Z_SY,(0.38,0.10,0.04,1),(0,0,0,0))

# ── grid on both planes ───────────────────────────────────────────────────────
def grid(z,col,step=1.0):
    a1,a2=0.15,0.30
    for g in np.arange(XMIN,XMAX+.01,step):
        ax.plot([g,g],[YMIN,YMAX],[z,z],color=(*col,a1),lw=0.5,zorder=1)
    for g in np.arange(YMIN,YMAX+.01,step):
        ax.plot([XMIN,XMAX],[g,g],[z,z],color=(*col,a1),lw=0.5,zorder=1)
    for g in np.arange(XMIN,XMAX+.01,step*2):
        ax.plot([g,g],[YMIN,YMAX],[z,z],color=(*col,a2),lw=1.0,zorder=2)
    for g in np.arange(YMIN,YMAX+.01,step*2):
        ax.plot([XMIN,XMAX],[g,g],[z,z],color=(*col,a2),lw=1.0,zorder=2)

grid(Z_SH,(0.25,0.55,1.0))
grid(Z_SY,(1.00,0.55,0.25))

# ── synbot original quad (top plane, degenerate wedge) ────────────────────────
sy_q=[(SY[k][0],SY[k][1],Z_SY) for k in ORDER]
p=Poly3DCollection([sy_q],facecolors=[(1.0,0.45,0.15,0.45)],
                   edgecolors=[(1.0,0.7,0.3,1.0)],linewidths=3.0)
ax.add_collection3d(p)

# ── convergence arrows: synbot corner → warped (shell) corner ─────────────────
ARROW_C={"FL":"#00ffaa","NL":"#ff44cc","NR":"#44ccff","FR":"#ffee22"}
for k in ("FL","NL","NR","FR"):
    x1,y1=SY[k]; x2,y2=SW[k]
    # multi-segment: from synbot at top, down to shell at bottom
    # use 3 waypoints to make a curved-ish drop
    zs=[Z_SY, Z_SY*0.65, Z_SY*0.33, Z_SH+0.05]
    xs=[x1, x1*0.7+x2*0.3, x1*0.3+x2*0.7, x2]
    ys=[y1, y1*0.7+y2*0.3, y1*0.3+y2*0.7, y2]
    ax.plot(xs,ys,zs,color=ARROW_C[k],lw=2.8,alpha=0.92,ls='--',zorder=20)
    # arrowhead at bottom
    dx=xs[-1]-xs[-2]; dy=ys[-1]-ys[-2]; dz=zs[-1]-zs[-2]
    ax.quiver(xs[-2],ys[-2],zs[-2],dx,dy,dz,
              color=ARROW_C[k],arrow_length_ratio=0.5,linewidth=2.5,zorder=25)

# ── shell quad at Z=0 (the converged PLATE BOUNDARY) ─────────────────────────
sh_q=[(SH[k][0],SH[k][1],Z_SH) for k in ORDER]
p2=Poly3DCollection([sh_q],facecolors=[(0.20,0.55,1.0,0.60)],
                    edgecolors=[(0.6,0.9,1.0,1.0)],linewidths=4.5)
ax.add_collection3d(p2)

# bright outline on top of that
ax.plot([SH[k][0] for k in ORDER+["FL"]],
        [SH[k][1] for k in ORDER+["FL"]],
        [Z_SH]*(len(ORDER)+1),
        color='#ffffff',lw=1.5,alpha=0.5,zorder=30,ls=':')

# ── corner markers ────────────────────────────────────────────────────────────
for k in ("FL","NL","NR","FR"):
    x,y=SY[k]
    ax.scatter([x],[y],[Z_SY],color='#ff8833',s=180,zorder=35,
               edgecolors='white',linewidths=2.0,marker='D')
    x,y=SH[k]
    ax.scatter([x],[y],[Z_SH],color='#00ccff',s=220,zorder=35,
               edgecolors='white',linewidths=2.5)

# ── labels: corner names ──────────────────────────────────────────────────────
for k in ("FL","NL","NR","FR"):
    x,y=SY[k]
    ax.text(x,y,Z_SY+0.25,f" {k}",color='#ffaa77',fontsize=13,
            fontweight='bold',ha='center',va='bottom',zorder=40)
    x,y=SH[k]
    ax.text(x,y,Z_SH-0.28,f" {k}",color='#88ddff',fontsize=13,
            fontweight='bold',ha='center',va='top',zorder=40)

# ── distance labels mid-connector ─────────────────────────────────────────────
OFFSETS={k:round(math.hypot(SYNBOT[k][0]-SHELL[k][0],
                             SYNBOT[k][1]-SHELL[k][1]),0)
         for k in ("FL","NL","NR","FR")}
for k in ("FL","NL","NR","FR"):
    mx=(SY[k][0]+SW[k][0])/2; my=(SY[k][1]+SW[k][1])/2; mz=Z_SY*0.5
    ax.text(mx+0.2,my+0.1,mz,f"{int(OFFSETS[k])}px",
            color=ARROW_C[k],fontsize=10,fontweight='bold',alpha=0.9,zorder=40)

# ── PLATE BOUNDARY label (big, prominent) ─────────────────────────────────────
ax.text(4.0, -1.0, Z_SH,
        'PLATE  BOUNDARY',
        color='#00ddff', fontsize=20, fontweight='black',
        ha='center', va='top', zorder=50,
        bbox=dict(boxstyle='round,pad=0.4',facecolor='#001830',
                  edgecolor='#00aacc',linewidth=2.0,alpha=0.85))

# ── plane name labels ─────────────────────────────────────────────────────────
ax.text(XMIN+0.3,YMAX-0.5,Z_SY,'SYNBOT  (source)',
        color='#ff8833',fontsize=18,fontweight='black',alpha=0.85,zorder=45)
ax.text(XMIN+0.3,YMAX-0.5,Z_SH,'SHELL  =  converged',
        color='#44bbff',fontsize=18,fontweight='black',alpha=0.85,zorder=45)

# ── axes / panes ──────────────────────────────────────────────────────────────
ax.set_xlim(XMIN,XMAX); ax.set_ylim(YMIN,YMAX); ax.set_zlim(-1.5,9.0)
ax.set_xticks([]); ax.set_yticks([]); ax.set_zticks([])
ax.xaxis.pane.fill=False; ax.yaxis.pane.fill=False; ax.zaxis.pane.fill=False
ax.xaxis.pane.set_edgecolor('#080a14')
ax.yaxis.pane.set_edgecolor('#080a14')
ax.zaxis.pane.set_edgecolor('#080a14')
ax.xaxis._axinfo['grid']['color']=(0,0,0,0)
ax.yaxis._axinfo['grid']['color']=(0,0,0,0)
ax.zaxis._axinfo['grid']['color']=(0,0,0,0)
ax.tick_params(colors='#111')
ax.view_init(elev=26, azim=-50)

# ── legend ─────────────────────────────────────────────────────────────────────
import matplotlib.patches as mpt, matplotlib.lines as mli
handles=[
    mpt.Patch(facecolor='#ff8833',edgecolor='white',label='synbot (raw)'),
    mpt.Patch(facecolor='#44aaff',edgecolor='white',label='shell = plate boundary (converged)'),
    mli.Line2D([0],[0],color='#aaaaaa',ls='--',lw=1.5,
               label='convergence path  H(synbot) → shell'),
]
ax.legend(handles=handles,loc='upper left',framealpha=0.3,
          facecolor='#0a0c18',edgecolor='#334466',labelcolor='white',
          fontsize=11,bbox_to_anchor=(0.01,0.97))

# ── title ─────────────────────────────────────────────────────────────────────
fig.text(0.5,0.97,'Synbot → Shell Convergence  |  Plate Boundary Reference',
         ha='center',color='white',fontsize=20,fontweight='black')
fig.text(0.5,0.940,
    'H maps every synbot corner exactly onto shell  ·  shell polygon = canonical plate boundary',
    ha='center',color='#778899',fontsize=11)

plt.tight_layout(rect=[0,0,1,0.93])
plt.savefig('/tmp/h2d_converge.png',dpi=160,bbox_inches='tight',
            facecolor='#04060e',edgecolor='none')
print("saved /tmp/h2d_converge.png")
