#!/usr/bin/env python3
from PIL import Image, ImageDraw, ImageFont
import numpy as np, math

MH, MV = 495, 330
CAM_W, CAM_H = 1680, 1080
CANVAS_W, CANVAS_H = 2670, 2435

SHELL  = {"NL": (1185,1905), "NR": (1339,446), "FR": (694,292),  "FL": (96,414)}
SYNBOT = {"NL": (1832,1570), "NR": (1520,1012), "FR": (1513,452), "FL": (98,414)}

# ── Homography transform (DLT, 4-pt exact) ───────────────────────────────────
# H maps raw synbot pixel → raw shell pixel (0-residual at all 4 corners)
def _build_homography(src_dict, dst_dict):
    keys = ["FL","NL","NR","FR"]
    A = []
    for k in keys:
        x,y   = src_dict[k]
        xp,yp = dst_dict[k]
        A += [[-x,-y,-1,0,0,0,xp*x,xp*y,xp],
              [0,0,0,-x,-y,-1,yp*x,yp*y,yp]]
    _, _, Vt = np.linalg.svd(np.array(A, dtype=np.float64))
    H = Vt[-1].reshape(3,3)
    return H / H[2,2]

H_synbot_to_shell = _build_homography(SYNBOT, SHELL)
H_shell_to_synbot = np.linalg.inv(H_synbot_to_shell)
H_shell_to_synbot /= H_shell_to_synbot[2,2]

def synbot_to_shell(pt):
    """Map a raw synbot pixel coord → raw shell pixel coord."""
    v = H_synbot_to_shell @ np.array([pt[0], pt[1], 1.0])
    return (v[0]/v[2], v[1]/v[2])

def shell_to_synbot(pt):
    """Map a raw shell pixel coord → raw synbot pixel coord."""
    v = H_shell_to_synbot @ np.array([pt[0], pt[1], 1.0])
    return (v[0]/v[2], v[1]/v[2])

def c(x, y): return (x + MH, y + MV)
SCV = {k: c(*v) for k, v in SHELL.items()}
XCV = {k: c(*v) for k, v in SYNBOT.items()}

# ── Per-corner offsets (synbot - shell), raw pixel space ─────────────────────
OFFSETS = {}
for k in ("FL","NL","NR","FR"):
    sx, sy = SHELL[k]
    xbx, xby = SYNBOT[k]
    dx, dy = xbx - sx, xby - sy
    dist = math.hypot(dx, dy)
    OFFSETS[k] = {"dx": dx, "dy": dy, "dist": round(dist, 1)}

print("=== Corner offsets (synbot → shell) ===")
for k, v in OFFSETS.items():
    print(f"  {k}: dx={v['dx']:+d}, dy={v['dy']:+d}, dist={v['dist']}px")

# ── Colors ────────────────────────────────────────────────────────────────────
S_TOP  = (10, 50, 130);   S_BOT  = (80, 170, 255)   # shell: deep→bright blue
S_ACC  = (100, 181, 246); S_BG   = (4, 12, 35)
X_TOP  = (120, 25, 0);    X_BOT  = (255, 160, 30)   # synbot: burnt→bright amber
X_ACC  = (255, 190, 10);  X_BG   = (38, 14, 0)
YELLOW = (255, 235, 59)

# ── Load & dark canvas ────────────────────────────────────────────────────────
cam = Image.open("/tmp/h2d_parked_raw.png").convert("RGBA")
canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (8, 10, 18, 255))
canvas.paste(cam, (MH, MV))

# ── Gradient polygon fill ─────────────────────────────────────────────────────
def gpoly(canvas, pts, ctop, cbot, atop=55, abot=145):
    mask_img = Image.new("L", (CANVAS_W, CANVAS_H), 0)
    ImageDraw.Draw(mask_img).polygon(pts, fill=255)
    mask = np.array(mask_img)
    ys   = [p[1] for p in pts]
    ylo, yhi = min(ys), max(ys); span = max(1, yhi - ylo)
    layer = np.zeros((CANVAS_H, CANVAS_W, 4), dtype=np.uint8)
    for y in range(max(0, ylo), min(CANVAS_H, yhi+1)):
        row = mask[y]
        if not row.any(): continue
        t = (y - ylo) / span
        r = int(ctop[0] + t*(cbot[0]-ctop[0]))
        g = int(ctop[1] + t*(cbot[1]-ctop[1]))
        b = int(ctop[2] + t*(cbot[2]-ctop[2]))
        a = int(atop  + t*(abot -atop ))
        layer[y, row>0] = [r, g, b, a]
    return Image.alpha_composite(canvas, Image.fromarray(layer, "RGBA"))

def poly_order(cv):
    pts = list(cv.values())
    cx = sum(p[0] for p in pts)/4; cy = sum(p[1] for p in pts)/4
    return sorted(pts, key=lambda p: math.atan2(p[1]-cy, p[0]-cx))

canvas = gpoly(canvas, poly_order(SCV), S_TOP, S_BOT)
canvas = gpoly(canvas, poly_order(XCV), X_TOP, X_BOT)

draw = ImageDraw.Draw(canvas)

def edge_highlight(pts, hi_color, lo_color, width=6):
    for i in range(len(pts)):
        p1, p2 = pts[i], pts[(i+1)%len(pts)]
        mid_y = (p1[1]+p2[1])/2
        col = hi_color if mid_y < min(p[1] for p in pts)+200 else lo_color
        draw.line([p1, p2], fill=(*col, 255), width=width)

edge_highlight(poly_order(SCV), (160, 210, 255), (20, 80, 160))
edge_highlight(poly_order(XCV), (255, 220, 120), (160, 60, 0))

# Camera frame
draw.rectangle([MH, MV, MH+CAM_W-1, MV+CAM_H-1], outline=(90,90,90,180), width=2)

# ── Fonts ─────────────────────────────────────────────────────────────────────
FP = "/System/Library/Fonts/Helvetica.ttc"
try:
    fN = ImageFont.truetype(FP, 48)   # corner name
    fC = ImageFont.truetype(FP, 28)   # coords
    fD = ImageFont.truetype(FP, 30)   # distance
    fX = ImageFont.truetype(FP, 34)   # axis
except:
    fN = fC = fD = fX = ImageFont.load_default()

# ── Callout box ───────────────────────────────────────────────────────────────
def callout(mxy, offxy, title, sub, acc, bg):
    mx, my = mxy; ox, oy = offxy
    bb1 = fN.getbbox(title); bb2 = fC.getbbox(sub)
    tw = max(bb1[2]-bb1[0], bb2[2]-bb2[0])
    th1 = bb1[3]-bb1[1]; th2 = bb2[3]-bb2[1]
    PAD = 18; bw = tw + PAD*2; bh = th1 + th2 + PAD*2 + 10
    # anchor: offset direction determines which corner of box is near marker
    bx = mx + ox - (bw if ox < 0 else 0)
    by = my + oy - (bh if oy < 0 else 0)
    bx = max(8, min(bx, CANVAS_W - bw - 8))
    by = max(8, min(by, CANVAS_H - bh - 8))
    # Leader endpoint: nearest point on box edge to marker
    lx = max(bx, min(bx+bw, mx)); ly = max(by, min(by+bh, my))
    # Glow on leader
    draw.line([(mx,my),(lx,ly)], fill=(*acc, 80), width=9)
    draw.line([(mx,my),(lx,ly)], fill=(*acc, 220), width=3)
    draw.ellipse([mx-7,my-7,mx+7,my+7], fill=(*acc,255))
    # Drop shadow
    draw.rounded_rectangle([bx+5,by+5,bx+bw+5,by+bh+5], radius=14, fill=(0,0,0,160))
    # Box
    draw.rounded_rectangle([bx,by,bx+bw,by+bh], radius=14,
        fill=(*bg, 235), outline=(*acc, 255), width=3)
    # Accent bar on left side
    draw.rounded_rectangle([bx, by, bx+6, by+bh], radius=4, fill=(*acc, 255))
    # Text
    draw.text((bx+PAD+4, by+PAD),      title, font=fN, fill=(255,255,255,255))
    draw.text((bx+PAD+4, by+PAD+th1+10), sub,  font=fC, fill=(*acc, 210))

# ── Gutter lines + pill badges ────────────────────────────────────────────────
for name, p1, p2 in [("NL",SCV["NL"],XCV["NL"]),("NR",SCV["NR"],XCV["NR"]),
                     ("FR",SCV["FR"],XCV["FR"]),("FL",SCV["FL"],XCV["FL"])]:
    o  = OFFSETS[name]
    d  = int(o["dist"])
    dx, dy = o["dx"], o["dy"]
    mx = (p1[0]+p2[0])//2; my = (p1[1]+p2[1])//2
    draw.line([p1, p2], fill=(*YELLOW, 200), width=2)
    draw.line([p1, p2], fill=(*YELLOW, 255), width=1)
    lbl  = f"{d}px"
    sub  = f"dx={dx:+d} dy={dy:+d}"
    bb1  = fD.getbbox(lbl);  lw1 = bb1[2]-bb1[0]; lh1 = bb1[3]-bb1[1]
    bb2  = fC.getbbox(sub);  lw2 = bb2[2]-bb2[0]; lh2 = bb2[3]-bb2[1]
    pw   = max(lw1, lw2) + 20
    ph   = lh1 + lh2 + 14
    px   = mx - pw//2; py = my - ph//2
    draw.rounded_rectangle([px-3,py-3,px+pw+3,py+ph+3], radius=9, fill=(0,0,0,180))
    draw.rounded_rectangle([px,py,px+pw,py+ph], radius=9,
        fill=(35,30,0,230), outline=(*YELLOW,255), width=2)
    draw.text((px+10, py+4),            lbl, font=fD, fill=(*YELLOW, 255))
    draw.text((px+10, py+4+lh1+6),     sub, font=fC, fill=(*YELLOW, 210))

# ── Draw callouts ─────────────────────────────────────────────────────────────
SPECS = [
    # shell: blue callouts
    (SCV["NL"], (-340,-130), "shell NL",  SHELL["NL"],  S_ACC, S_BG),
    (SCV["NR"], ( 50, -130), "shell NR",  SHELL["NR"],  S_ACC, S_BG),
    (SCV["FR"], ( 50, -140), "shell FR",  SHELL["FR"],  S_ACC, S_BG),
    (SCV["FL"], ( 50,   50), "shell FL",  SHELL["FL"],  S_ACC, S_BG),
    # synbot: gold callouts
    (XCV["NL"], (-340,  60), "synbot NL", SYNBOT["NL"], X_ACC, X_BG),
    (XCV["NR"], ( 50,   60), "synbot NR", SYNBOT["NR"], X_ACC, X_BG),
    (XCV["FR"], ( 50,   50), "synbot FR", SYNBOT["FR"], X_ACC, X_BG),
    (XCV["FL"], ( 50,   50), "synbot FL", SYNBOT["FL"], X_ACC, X_BG),
]
for pt, off, title, raw, acc, bg in SPECS:
    callout(pt, off, title, f"({raw[0]}, {raw[1]})", acc, bg)

# ── Markers ───────────────────────────────────────────────────────────────────
def mk_circle(pt, acc, sz=22):
    x,y = pt
    draw.ellipse([x-sz-6,y-sz-6,x+sz+6,y+sz+6], fill=(*acc,40))   # glow
    draw.ellipse([x-sz,y-sz,x+sz,y+sz], fill=(255,255,255,255), outline=(*acc,255), width=6)
    draw.ellipse([x-7,y-7,x+7,y+7], fill=(*acc,255))

def mk_diamond(pt, acc, sz=22):
    x,y = pt
    glow = [(x,y-sz-6),(x+sz+6,y),(x,y+sz+6),(x-sz-6,y)]
    draw.polygon(glow, fill=(*acc, 40))
    pts = [(x,y-sz),(x+sz,y),(x,y+sz),(x-sz,y)]
    draw.polygon(pts, fill=(*acc,255), outline=(255,255,255,255))
    cp  = [(x,y-8),(x+8,y),(x,y+8),(x-8,y)]
    draw.polygon(cp, fill=(255,255,255,255))

for pt in SCV.values(): mk_circle(pt, S_ACC)
for pt in XCV.values(): mk_diamond(pt, X_ACC)

# ── Warped synbot polygon (homography-corrected, shown in cyan) ───────────────
W_ACC = (0, 230, 220)   # cyan accent
WCV = {k: c(*synbot_to_shell(SYNBOT[k])) for k in ("FL","NL","NR","FR")}
# Draw dashed outline of warped polygon
wpts = [WCV[k] for k in ("FL","FR","NR","NL")]
for i in range(len(wpts)):
    p1, p2 = wpts[i], wpts[(i+1)%len(wpts)]
    # dashed: alternate 12px on / 6px off
    dx2 = p2[0]-p1[0]; dy2 = p2[1]-p1[1]
    seg_len = math.hypot(dx2, dy2)
    if seg_len < 1: continue
    dash, gap = 12, 6
    t = 0
    while t < seg_len:
        t0 = t/seg_len; t1 = min(t+dash, seg_len)/seg_len
        xa,ya = p1[0]+dx2*t0, p1[1]+dy2*t0
        xb,yb = p1[0]+dx2*t1, p1[1]+dy2*t1
        draw.line([(xa,ya),(xb,yb)], fill=(*W_ACC, 220), width=3)
        t += dash + gap
for pt in WCV.values():
    x,y = int(pt[0]), int(pt[1])
    draw.ellipse([x-10,y-10,x+10,y+10], fill=(*W_ACC,180), outline=(255,255,255,200), width=2)

# ── ORIGIN label ──────────────────────────────────────────────────────────────
ox,oy = SCV["NL"]
draw.text((ox+30, oy-36), "ORIGIN", font=fX, fill=(100,230,100,255))

# ── Legend ────────────────────────────────────────────────────────────────────
lx, ly = 30, CANVAS_H - 180
draw.rounded_rectangle([lx, ly, lx+480, ly+195], radius=12,
    fill=(12,14,22,220), outline=(60,60,80,200), width=2)
mk_circle((lx+30, ly+40), S_ACC, sz=14)
draw.text((lx+58, ly+20), "shell  (circle)  — user confirmed", font=fC, fill=(255,255,255,200))
mk_diamond((lx+30, ly+90), X_ACC, sz=14)
draw.text((lx+58, ly+72), "synbot (diamond) — agent estimated", font=fC, fill=(255,255,255,200))
draw.line([(lx+14, ly+130),(lx+50, ly+130)], fill=(*YELLOW,255), width=3)
draw.text((lx+58, ly+120), "gutter distance (pixels)", font=fC, fill=(255,255,255,200))
draw.line([(lx+14, ly+170),(lx+50, ly+170)], fill=(*W_ACC,255), width=3)
draw.text((lx+58, ly+160), "synbot warped → shell (H transform)", font=fC, fill=(*W_ACC,200))

# ── Save ──────────────────────────────────────────────────────────────────────
out = "/tmp/h2d_corrected_corners.png"
canvas.convert("RGB").save(out, quality=95)
print(f"Saved {out}")
