"""
coord_transform.py — Synbot ↔ Shell coordinate transform
=========================================================
Computes a 4-point homography (DLT, exact) from the 4 named corner pairs.
Use synbot_to_shell() / shell_to_synbot() anywhere a coordinate needs to
cross between the two calibration reference frames.

Corner assignments (current, after all name swaps):
  FL = far-left    NL = near-left    NR = near-right    FR = far-right
  ORIGIN = shell NL = (1480, 1256) — below camera frame

SHELL corners calibrated 2026-03-12 at Z=2, chamber light OFF, N=30 snaps, TRIM_COUNT=7.
NL is extrapolated via hotspot offset — physically below the camera frame.
"""
import numpy as np, math

# ── Control points (raw camera pixel coords) ──────────────────────────────────
SHELL  = {"FL": (695,616),   "NL": (1480,1256), "NR": (1187,299),  "FR": (860,205)}
SYNBOT = {"FL": (98,414),   "NL": (1832,1570), "NR": (1520,1012), "FR": (1513,452)}
ORIGIN = SHELL["NL"]   # (1480, 1256) — physical bed origin

# ── Per-corner offset vectors ─────────────────────────────────────────────────
OFFSETS = {k: {
    "dx": SYNBOT[k][0] - SHELL[k][0],
    "dy": SYNBOT[k][1] - SHELL[k][1],
    "dist": round(math.hypot(SYNBOT[k][0]-SHELL[k][0], SYNBOT[k][1]-SHELL[k][1]), 1)
} for k in ("FL","NL","NR","FR")}

# ── DLT homography ─────────────────────────────────────────────────────────────
def _build_H(src, dst):
    keys = ["FL","NL","NR","FR"]
    A = []
    for k in keys:
        x,y   = src[k]
        xp,yp = dst[k]
        A += [[-x,-y,-1,0,0,0,xp*x,xp*y,xp],
              [0,0,0,-x,-y,-1,yp*x,yp*y,yp]]
    _, _, Vt = np.linalg.svd(np.array(A, dtype=np.float64))
    H = Vt[-1].reshape(3,3)
    return H / H[2,2]

H_synbot_to_shell = _build_H(SYNBOT, SHELL)
H_shell_to_synbot = np.linalg.inv(H_synbot_to_shell)
H_shell_to_synbot /= H_shell_to_synbot[2,2]

def synbot_to_shell(pt):
    """Map raw synbot pixel (x,y) → raw shell pixel (x,y)."""
    v = H_synbot_to_shell @ np.array([pt[0], pt[1], 1.0])
    return (float(v[0]/v[2]), float(v[1]/v[2]))

def shell_to_synbot(pt):
    """Map raw shell pixel (x,y) → raw synbot pixel (x,y)."""
    v = H_shell_to_synbot @ np.array([pt[0], pt[1], 1.0])
    return (float(v[0]/v[2]), float(v[1]/v[2]))

if __name__ == "__main__":
    print("Residuals (all should be ~0):")
    for k in ("FL","NL","NR","FR"):
        mp = synbot_to_shell(SYNBOT[k])
        err = math.hypot(mp[0]-SHELL[k][0], mp[1]-SHELL[k][1])
        print(f"  {k}: {SYNBOT[k]} → ({mp[0]:.1f},{mp[1]:.1f})  "
              f"expect {SHELL[k]}  err={err:.4f}px")
    print("\nH (synbot→shell):")
    print(H_synbot_to_shell)

# ── Canonical PLATE BOUNDARY ──────────────────────────────────────────────────
# Shell corners are the authoritative plate boundary in raw camera pixel space.
# Synbot corners, when mapped through H_synbot_to_shell, land exactly here.
# All vision / spaghetti / object-in-frame checks should use this polygon.

PLATE_BOUNDARY = {
    "FL": SHELL["FL"],   # (695,  616)  far-left
    "FR": SHELL["FR"],   # (860,  205)  far-right
    "NR": SHELL["NR"],   # (1187, 299)  near-right
    "NL": SHELL["NL"],   # (1480, 1256) near-left  ← ORIGIN (extrapolated below frame)
}
PLATE_ORDER = ["FL","FR","NR","NL"]  # convex hull quad, clockwise from FL

# numpy array (N×2) for use in point-in-polygon, cv2.pointPolygonTest, etc.
PLATE_POLY = np.array([PLATE_BOUNDARY[k] for k in PLATE_ORDER], dtype=np.float32)


def is_on_plate(pt, margin_px=0):
    """Return True if camera pixel pt=(x,y) falls inside the plate boundary."""
    from matplotlib.path import Path
    poly = PLATE_POLY if margin_px == 0 else _erode_poly(PLATE_POLY, -margin_px)
    return Path(poly).contains_point(pt)


def normalize_to_plate(pt):
    """
    Map camera pixel (x,y) to plate-relative (u,v) in [0,1]×[0,1].
    Uses bilinear inverse mapping:
      u=0,v=0 → FL (far-left)    u=1,v=0 → FR (far-right)
      u=0,v=1 → NL (near-left)   u=1,v=1 → NR (near-right)
    """
    FL = np.array(PLATE_BOUNDARY["FL"], dtype=float)
    FR = np.array(PLATE_BOUNDARY["FR"], dtype=float)
    NL = np.array(PLATE_BOUNDARY["NL"], dtype=float)
    NR = np.array(PLATE_BOUNDARY["NR"], dtype=float)
    p  = np.array(pt, dtype=float)
    # iterative bilinear solver (Newton, 5 steps)
    u, v = 0.5, 0.5
    for _ in range(10):
        q  = FL*(1-u)*(1-v) + FR*u*(1-v) + NL*(1-u)*v + NR*u*v
        dq_du = -FL*(1-v) + FR*(1-v) - NL*v + NR*v
        dq_dv = -FL*(1-u) - FR*u     + NL*(1-u) + NR*u
        res   = q - p
        det   = dq_du[0]*dq_dv[1] - dq_du[1]*dq_dv[0]
        if abs(det) < 1e-9: break
        u -= ( dq_dv[1]*res[0] - dq_dv[0]*res[1]) / det
        v -= (-dq_du[1]*res[0] + dq_du[0]*res[1]) / det
    return (float(u), float(v))


def _erode_poly(pts, amount):
    cx, cy = pts[:,0].mean(), pts[:,1].mean()
    dirs = pts - np.array([cx,cy])
    norms = np.linalg.norm(dirs, axis=1, keepdims=True)
    return pts + (dirs / norms) * amount


if __name__ == "__main__":
    print("\nPlate boundary (canonical):")
    for k in PLATE_ORDER:
        print(f"  {k}: {PLATE_BOUNDARY[k]}")
    print("\nnormalize_to_plate tests:")
    for k,uv_expect in [("FL",(0,0)),("FR",(1,0)),("NL",(0,1)),("NR",(1,1))]:
        uv = normalize_to_plate(PLATE_BOUNDARY[k])
        print(f"  {k}: {PLATE_BOUNDARY[k]} → ({uv[0]:.3f}, {uv[1]:.3f})  expect {uv_expect}")
    print(f"\nis_on_plate centre: {is_on_plate((700,900))}")
    print(f"is_on_plate outside: {is_on_plate((1800,100))}")
