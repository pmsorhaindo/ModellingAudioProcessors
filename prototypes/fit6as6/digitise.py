"""Reproducible digitiser for the GE 6AS6 datasheet (ET-T706, 12-49).

Pipeline: pdftoppm -r 200 -> greyscale threshold -> remove grid lines ->
column-wise curve tracking from a hand-placed seed -> pixel->data calibration
from detected grid-line positions. Writes data/*.csv and an overlay PNG per page.
Seeds are only starting hints; every point comes from dark pixels in the scan.
"""
import csv, hashlib, json, os, subprocess
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi

HERE = os.path.dirname(os.path.abspath(__file__))
DS = os.path.join(HERE, "datasheets")
PDF = os.path.join(DS, "084_6_6AS6.pdf")
SRC_URL = "https://frank.pocnet.net/sheets/084/6/6AS6.pdf"
DPI = 200


def render(page):
    out = os.path.join(DS, f"ge-{page}.png")
    if not os.path.exists(out):
        subprocess.run(["pdftoppm", "-r", str(DPI), "-f", str(page), "-l", str(page), "-png",
                        PDF, os.path.join(DS, "ge")], check=True)
    return Image.open(out).convert("L")


def gridlines(mask, axis, lo, hi, frac):
    """Centres of rows/cols (within [lo,hi] of the other axis) that are mostly dark."""
    prof = mask[lo:hi, :].mean(0) if axis == "col" else mask[:, lo:hi].mean(1)
    idx = np.where(prof > frac)[0]
    groups = np.split(idx, np.where(np.diff(idx) > 2)[0] + 1)
    return np.array([g.mean() for g in groups if len(g)])


def track(mask, x0, y0, x1, ignore_cols=(), w=7, max_gap=1, merge=4, start_w=6, tmax=99):
    """Follow a curve column by column from (x0,y0) to x1. Returns list of (x, y_centre)."""
    step = 1 if x1 >= x0 else -1
    pts, pred, gap, hist = [], float(y0), 0, []
    ign = set(int(c) for c in ignore_cols)
    for k, x in enumerate(range(x0, x1 + step, step)):
        if x in ign:
            continue
        win = start_w if not pts else w
        lo, hi = int(pred - win), int(pred + win) + 1
        col = mask[lo:hi, x]
        ys = np.where(col)[0]
        if len(ys) == 0:
            gap += 1
            if gap > max_gap and pts:
                break
        else:
            runs = np.split(ys, np.where(np.diff(ys) > merge)[0] + 1)
            runs = [r for r in runs if len(r) <= tmax]   # over-thick run = crossing / label: skip
            cents = [lo + r.mean() for r in runs]
            c = min(cents, key=lambda v: abs(v - pred)) if cents else 1e9
            if abs(c - pred) > win:
                gap += 1; continue
            pts.append((x, c)); hist.append((x, c)); gap = 0
        # linear prediction from last ~15 accepted points
        if len(hist) >= 4:
            h = np.array(hist[-15:]); p = np.polyfit(h[:, 0], h[:, 1], 1)
            pred = np.polyval(p, x + step)
        elif hist:
            pred = hist[-1][1]
    return pts


def track2(mask, xs, ys, xlo, xhi, **kw):
    """Track left and right from a seed placed where the curve is well separated."""
    return track(mask, xs, ys, xlo, **kw)[::-1] + track(mask, xs, ys, xhi, **kw)


def sample(pts, xs_px):
    a = np.array(pts); o = np.argsort(a[:, 0]); a = a[o]
    keep = [(x, np.interp(x, a[:, 0], a[:, 1])) for x in xs_px if a[0, 0] <= x <= a[-1, 0]]
    # only keep samples that have a real traced pixel within 2 columns
    return [(x, y) for x, y in keep if np.min(np.abs(a[:, 0] - x)) <= 2]


def page3():
    """AVERAGE PLATE CHARACTERISTICS: Ef=6.3, Ec2=120, Ec3=0. Solid = Ib, dashed = Ic2."""
    im = render(3); g = np.array(im) < 128
    cols = gridlines(g, "col", 590, 1330, 0.4); rows = gridlines(g, "row", 300, 1330, 0.4)
    cols = cols[(cols > 280) & (cols < 1345)]; rows = rows[(rows > 580) & (rows < 1345)]
    # keep only rows on the regular 1 mA lattice (a near-horizontal curve can also pass the threshold)
    pitch = np.median(np.diff(rows)); top = rows[0]
    rows = np.array([r for r in rows if abs(((r - top) / pitch) - round((r - top) / pitch)) < 0.1
                     and (r - top) / pitch > -0.5])
    rows = np.array([min(rows, key=lambda r: abs(r - (top + k * pitch))) for k in range(21)])
    # calibration: vertical lines every 10 V from 0 V; horizontal every 1 mA from 0 mA (bottom)
    kx = np.polyfit(np.arange(len(cols)) * 10.0, cols, 1)            # px = a*V + b
    ry = rows[::-1]; ky = np.polyfit(np.arange(len(ry)) * 1.0, ry, 1)  # px = a*mA + b
    assert len(cols) == 29 and len(rows) == 21, (len(cols), len(rows))
    V2x = lambda v: np.polyval(kx, v); x2V = lambda x: (x - kx[1]) / kx[0]
    I2y = lambda i: np.polyval(ky, i); y2I = lambda y: (y - ky[1]) / ky[0]
    m = g.copy()
    for r in rows: m[int(r) - 2:int(r) + 3, :] = False   # strip horizontal grid
    ign = [c + d for c in cols for d in range(-3, 4)]     # skip vertical grid columns
    # Coarse guide polylines (Va V, mA) for each curve. Each node was read from a printed list of
    # dark-pixel runs at Va = 25, 35, ... 235 V (see README) and assigned to a curve by eye.
    # The guide only picks WHICH run belongs to a curve; every saved value is a run centroid.
    G = {
     ("Ib", 0): "65 10.52 85 11.11 105 11.65 125 12.16 145 12.60 165 12.98 185 13.28 205 13.52 225 13.81 240 14.03",
     ("Ib", -1): "42 7.05 55 7.45 75 7.98 95 8.44 115 8.80 135 9.04 155 9.28 175 9.47 195 9.68 215 9.86 228 9.99",
     ("Ib", -2): "22 3.62 35 4.04 55 4.48 75 4.79 95 5.00 115 5.16 135 5.31 155 5.39 175 5.50 195 5.60 215 5.70 240 5.84",
     ("Ib", -3): "22 2.00 45 2.18 65 2.32 85 2.40 105 2.49 125 2.58 145 2.64 165 2.69 185 2.76 205 2.81 240 2.99",
     ("Ib", -4): "22 0.80 45 0.92 65 0.96 85 0.99 155 1.00 235 1.04 240 1.04",
     ("Ic2", 0): "72 11.95 85 11.53 95 11.34 125 10.66 145 10.32 155 10.19 185 9.82 215 9.55 240 9.40",
     ("Ic2", -1): "22 9.10 35 8.57 55 7.94 85 7.26 115 6.77 135 6.51 155 6.30 185 6.06 205 5.98 240 5.84",
     ("Ic2", -2): "22 4.70 35 4.29 55 3.97 85 3.65 115 3.45 145 3.29 175 3.14 215 3.04 240 3.00",
     ("Ic2", -3): "25 2.00 45 1.89 75 1.74 105 1.69 145 1.63 185 1.54 215 1.49 240 1.45",
     ("Ic2", -4): "22 0.72 45 0.66 75 0.58 105 0.54 155 0.51 205 0.47 240 0.43"}
    rows_out, ov = [], im.convert("RGB"); d = ImageDraw.Draw(ov)
    gridrows = set(int(round(r)) + k for r in rows for k in (-1, 0, 1))
    for (kind, ec1), txt in G.items():
        nodes = np.array(txt.split(), float).reshape(-1, 2)
        name = f"{kind}_Ec1_{ec1}"
        for v in np.arange(np.ceil(nodes[0, 0] / 2.5) * 2.5, nodes[-1, 0] + 1e-9, 2.5):
            x = int(round(V2x(v)))
            if any(abs(x - c) <= 3 for c in cols):
                continue
            yg = I2y(np.interp(v, nodes[:, 0], nodes[:, 1]))
            lo = int(yg) - 8; ys = np.where(g[lo:int(yg) + 9, x])[0] + lo
            if not len(ys):
                continue
            runs = np.split(ys, np.where(np.diff(ys) > 1)[0] + 1)
            # drop pure grid-row runs (<=3 px, all on a grid row)
            runs = [r for r in runs if not (len(r) <= 3 and all(int(p) in gridrows for p in r))]
            runs = [r for r in runs if abs(r.mean() - yg) <= 4]
            if len(runs) != 1 or len(runs[0]) > 7:   # ambiguous (crossing, label) -> skip
                continue
            y = runs[0].mean(); near = bool(any(int(p) in gridrows for p in runs[0]))
            rows_out.append(dict(page=3, curve=name, quantity=kind, Ec1_V=ec1, Ec2_V=120, Ec3_V=0,
                                 Eb_V=round(x2V(x), 2), I_mA=round(y2I(y), 4), x_px=x, y_px=round(y, 1),
                                 on_gridline=int(near)))
    # Quality filters, applied identically to every curve:
    #  (a) crossings: drop a point if any OTHER curve's guide is within 6 px (0.16 mA) there;
    #  (b) label/stray hits: drop points > 3 px from a degree-4 polynomial fit of the same curve.
    guides = {f"{k}_Ec1_{e}": np.array(t.split(), float).reshape(-1, 2) for (k, e), t in G.items()}
    def gy(nm, v):
        n = guides[nm]
        return I2y(np.interp(v, n[:, 0], n[:, 1])) if n[0, 0] - 1 <= v <= n[-1, 0] + 1 else None
    kept = []
    for r in rows_out:
        others = [gy(nm, r["Eb_V"]) for nm in guides if nm != r["curve"]]
        if any(o is not None and abs(o - r["y_px"]) < 6 for o in others):
            continue
        kept.append(r)
    rows_out = []
    for nm in guides:
        pts = [r for r in kept if r["curve"] == nm]
        x = np.array([r["x_px"] for r in pts], float); y = np.array([r["y_px"] for r in pts])
        for _ in range(3):
            p = np.polyfit(x, y, 4); res = np.abs(y - np.polyval(p, x)); ok = res <= 3
            pts = [r for r, o in zip(pts, ok) if o]; x, y = x[ok], y[ok]
        rows_out += pts
    for r in rows_out:
        col = (255, 0, 0) if r["quantity"] == "Ib" else (0, 120, 255)
        x, y = r["x_px"], r["y_px"]; d.ellipse([x - 4, y - 4, x + 4, y + 4], outline=col, width=2)
    # draw calibration ticks
    for v in range(0, 281, 50): d.line([V2x(v), I2y(0) + 8, V2x(v), I2y(0) + 20], fill=(0, 160, 0), width=3)
    ov.crop((250, 560, 1370, 1360)).save(os.path.join(HERE, "plots", "overlay_page3_plate.png"))
    return rows_out, dict(px_per_V=kx[0], x_at_0V=kx[1], px_per_mA=ky[0], y_at_0mA=ky[1])


def page4():
    """Top-right: Ib vs Ec3 at Ef=6.3, Eb=120, Ec2=120, Ec1 = -1, -2, -3."""
    im = render(4); g = np.array(im) < 128
    # this plot is on fine graph paper (1-2 px lines); curves are ~5 px -> opening removes grid
    m = ndi.binary_opening(g, structure=np.ones((4, 4)))
    # calibration from the heavy axis/major lines: Ec3=-10 V and +10 V borders, 0/5/10 mA rows
    sub = g[200:800, 880:1370]
    c = sub.mean(0); r = sub.mean(1)
    def peak(prof, lo, hi, off):
        seg = prof[lo - off:hi - off]; return lo + int(np.argmax(seg))
    xm10 = peak(c, 905, 916, 880); xp10 = peak(c, 1348, 1358, 880); x0 = peak(c, 1128, 1138, 880)
    y0 = peak(r, 790, 799, 200); y5 = peak(r, 568, 577, 200); y10 = peak(r, 345, 353, 200)
    kx = np.polyfit([-10, 0, 10], [xm10, x0, xp10], 1); ky = np.polyfit([0, 5, 10], [y0, y5, y10], 1)
    V2x = lambda v: np.polyval(kx, v); x2V = lambda x: (x - kx[1]) / kx[0]
    I2y = lambda i: np.polyval(ky, i); y2I = lambda y: (y - ky[1]) / ky[0]
    curves = [("Ib_vs_Ec3_Ec1_-1", -1, 9.8, 10.95, -8.0), ("Ib_vs_Ec3_Ec1_-2", -2, 9.8, 6.25, -8.0),
              ("Ib_vs_Ec3_Ec1_-3", -3, 9.8, 3.1, -8.0)]
    rows_out, ov = [], im.convert("RGB"); d = ImageDraw.Draw(ov)
    for name, ec1, v0, i0, v1 in curves:
        pts = track(m, int(V2x(v0)), I2y(i0), int(V2x(v1)), (), w=6, max_gap=6)
        sv = np.arange(-7.5, 9.8 + 1e-9, 0.5)
        for x, y in sample(pts, [int(round(V2x(v))) for v in sv]):
            rows_out.append(dict(page=4, curve=name, quantity="Ib", Ec1_V=ec1, Ec2_V=120,
                                 Ec3_V=round(x2V(x), 3), Eb_V=120, I_mA=round(y2I(y), 4),
                                 x_px=x, y_px=round(y, 1), on_gridline=0))
            d.ellipse([x - 4, y - 4, x + 4, y + 4], outline=(255, 0, 0), width=2)
    ov.crop((880, 190, 1370, 830)).save(os.path.join(HERE, "plots", "overlay_page4_suppressor.png"))
    return rows_out, dict(px_per_V=kx[0], x_at_0V=kx[1], px_per_mA=ky[0], y_at_0mA=ky[1])


# ---------------------------------------------------------------------------------------------
# Raytheon 5725/6AS6W, 15 December 1960 (13 pp.), pages 8-11: screen-voltage families
# (Ec2 = 60..140 V), transfer curves down to Ec1 = -5 V and Ib/Ic2 vs Ec3. The PDF pages are
# 1-bit 300 ppi scans (pdfimages -list), so they are rendered at 300 dpi = native resolution.
# Curves are 5-9 px strokes and graph-paper lines 1-5 px on the fine-grid graphs, so the grid is
# removed by a morphological opening with a 5 px disc; the coarse-grid graphs (p.8 top, p.10
# bottom, p.11 top) are drawn with a thinner pen (3.5-5 px) on a 1-2 px grid and use a 3x3
# opening. Every graph has an overlay in plots/overlay_raytheon_*.png. The scan is slightly sheared (vertical lines drift ~5 px over the
# graph height) and the printed grid is not perfectly uniform, so calibration interpolates
# between the individually located major grid lines (see lattice()).
# Issue #48 added p.8 bottom (plate family at Ec3 = -14..+10 V, g3 current) and the five gm graphs of
# pp. 11-13. These are written to their own CSVs (held out of every fit) and drawn on a 2-3 px grid, which a
# 4 px disc opening removes.
RAY_PDF = os.path.join(DS, "138_5_5725.pdf")
RAY_URL = "https://frank.pocnet.net/sheets/138/5/5725.pdf"
RAY_SHA256 = "6f226c6b7b6fb04fdb8830044afc2b13478c9d0fc8bf26316bacb9793d7649c8"
RAY_DPI = 300
DISC5 = np.array([[0, 1, 1, 1, 0], [1, 1, 1, 1, 1], [1, 1, 1, 1, 1], [1, 1, 1, 1, 1], [0, 1, 1, 1, 0]], bool)
DISC4 = np.array([[0, 1, 1, 0], [1, 1, 1, 1], [1, 1, 1, 1], [0, 1, 1, 0]], bool)


def render_ray(page):
    out = os.path.join(DS, f"ray-{page:02d}.png")
    if not os.path.exists(out):
        with open(RAY_PDF, "rb") as f:
            assert hashlib.sha256(f.read()).hexdigest() == RAY_SHA256, "run datasheets/fetch_datasheets.sh"
        subprocess.run(["pdftoppm", "-r", str(RAY_DPI), "-f", str(page), "-l", str(page), "-png",
                        RAY_PDF, os.path.join(DS, "ray")], check=True)
    return Image.open(out).convert("L")


def _line_fit(g, nominal, along, vertical, win=6, strip=60, min_dark=0.6):
    """Refine one major grid line near pixel `nominal`: per strip along the line, take the
    darkness-weighted centre of the profile peak; robust linear fit pos = a + b*along_coord."""
    pts = []
    for s in range(along[0], along[1] - strip + 1, strip):
        lo = int(round(nominal)) - win
        prof = (g[s:s + strip, lo:lo + 2 * win + 1].mean(0) if vertical
                else g[lo:lo + 2 * win + 1, s:s + strip].mean(1))
        k = int(np.argmax(prof))
        if prof[k] < min_dark:
            continue
        a, b = max(k - 2, 0), min(k + 3, len(prof))
        pts.append((s + strip / 2, lo + np.sum(np.arange(a, b) * prof[a:b]) / np.sum(prof[a:b])))
    p = np.array(pts)
    if len(p) < 5:
        return None, len(p)
    for _ in range(3):
        c = np.polyfit(p[:, 0], p[:, 1], 1)
        ok = np.abs(p[:, 1] - np.polyval(c, p[:, 0])) < 1.5
        if ok.all():
            break
        p = p[ok]
    return c, len(p)


def lattice(g, m, xspec, yspec):
    """Pixel <-> data calibration from the major grid lines. spec = (px at first major, px at last
    major, value at first, value at last, number of intervals); the nominal lines are evenly spaced
    between the two given pixels and each is then located in the scan as a straight (slightly
    tilted) line. The printed paper is not perfectly uniform (lines sit up to ~4 px off a single
    affine map), so to_data interpolates linearly between the two located lines either side of
    the point. to_px (used only for seeds and sampling columns) is the least-squares affine map.
    Lines are located in g & ~m (the thin-line residue left after removing the curve strokes m), so a
    curve running next to a grid line cannot pull it; heavy lines (axes) that the opening keeps are
    located in the raw scan g instead."""
    xl = np.linspace(xspec[0], xspec[1], xspec[4] + 1); uv = np.linspace(xspec[2], xspec[3], xspec[4] + 1)
    yl = np.linspace(yspec[0], yspec[1], yspec[4] + 1); vv = np.linspace(yspec[2], yspec[3], yspec[4] + 1)
    ylo, yhi = int(min(yl)), int(max(yl)); xlo, xhi = int(min(xl)), int(max(xl))
    gi = g & ~m

    def locate(nominal, along, vertical):
        c, n = _line_fit(gi, nominal, along, vertical)
        return (c, n, "thin") if n >= 5 else (*_line_fit(g, nominal, along, vertical), "raw")
    V = [locate(x, (ylo, yhi), True) for x in xl]; H = [locate(y, (xlo, xhi), False) for y in yl]
    vert = np.array([v[0] for v in V]); hori = np.array([h[0] for h in H])   # x = c0*y + c1; y = d0*x + d1
    P, D = [], []
    for (c0, c1), u in zip(vert, uv):
        for (d0, d1), v in zip(hori, vv):
            y = (d0 * c1 + d1) / (1 - d0 * c0); x = c0 * y + c1
            P.append((x, y)); D.append((u, v, 1.0))
    P, D = np.array(P), np.array(D)
    A, *_ = np.linalg.lstsq(D, P, rcond=None)            # [x y] = [u v 1] @ A
    res = np.hypot(*(D @ A - P).T)
    ou, ov = np.argsort(vert[:, 1] * np.sign(A[0, 0])), np.argsort(hori[:, 1] * np.sign(A[1, 1]))

    def to_data(x, y):
        xs = vert[:, 0] * y + vert[:, 1]; ys = hori[:, 0] * x + hori[:, 1]
        u = np.interp(x * np.sign(A[0, 0]), (xs * np.sign(A[0, 0]))[ou], uv[ou])
        v = np.interp(y * np.sign(A[1, 1]), (ys * np.sign(A[1, 1]))[ov], vv[ov])
        return float(u), float(v)
    to_px = lambda u, v: tuple(np.array([u, v, 1.0]) @ A)
    info = dict(n_vertical=len(xl), n_horizontal=len(yl), px_per_u=float(A[0, 0]), px_per_v=float(A[1, 1]),
                affine_rms_residual_px=float(np.sqrt(np.mean(res ** 2))), affine_max_residual_px=float(res.max()),
                line_x_at_mid=[round(float(c0 * (ylo + yhi) / 2 + c1), 2) for c0, c1 in vert],
                line_y_at_mid=[round(float(d0 * (xlo + xhi) / 2 + d1), 2) for d0, d1 in hori],
                lines_from_raw_scan=[f"{'u' if i < len(V) else 'v'}={val:g}" for i, (L, val) in
                                     enumerate(zip(V + H, list(uv) + list(vv))) if L[2] == "raw"])
    return to_px, to_data, info


def runs_at(m, x, lo, hi):
    """Dark runs (top, bottom) in column x between rows lo..hi of mask m."""
    ys = np.where(m[lo:hi, x])[0] + lo
    if not len(ys):
        return []
    return [(int(r[0]), int(r[-1])) for r in np.split(ys, np.where(np.diff(ys) > 1)[0] + 1)]


def trace_runs(m, x0, y0, x1, w=8, max_gap=60, tmax=22, skip_cols=(), hist_n=12):
    """Follow a stroke column by column from (x0, y0) towards x1 in the cleaned mask. In each column
    take the run nearest a prediction from the last hist_n accepted columns (linear; quadratic once
    they span > 60 columns, used for dashed curves) measured as the distance from the
    prediction to the run's extent, so steep strokes with tall runs are followed); runs taller than
    tmax (crossings, vertical lines, labels) are not accepted. Returns
    [(x, y_centre, top, bottom)]; every value is a run of dark pixels in the scan."""
    step = 1 if x1 >= x0 else -1
    out, hist, pred, gap = [], [], float(y0), 0
    for x in range(x0, x1 + step, step):
        if x in skip_cols:
            continue
        win = 5 if not hist else w
        dist = lambda r: max(r[0] - pred, pred - r[1], 0.0)
        cand = [(t, b) for t, b in runs_at(m, x, int(pred - win - tmax), int(pred + win + tmax) + 1)
                if b - t + 1 <= tmax and dist((t, b)) <= win]
        if cand:
            t, b = min(cand, key=lambda r: (dist(r), abs((r[0] + r[1]) / 2 - pred)))
            out.append((x, (t + b) / 2, t, b)); hist.append((x, (t + b) / 2)); gap = 0
        else:
            gap += 1
            if gap > max_gap and hist:
                break
        if len(hist) >= 4:
            h = np.array(hist[-hist_n:]); deg = 2 if abs(h[-1, 0] - h[0, 0]) > 60 else 1
            pred = float(np.polyval(np.polyfit(h[:, 0] - x, h[:, 1], deg), step))
        elif hist:
            pred = hist[-1][1]
    return out


def _smooth_ok(tr, half=30, tol=2.5):
    """Drop columns whose centre is > tol px from a local quadratic through the neighbouring
    columns (label strokes, grid remnants)."""
    a = np.array([(x, y) for x, y, _, _ in tr], float); keep = np.ones(len(a), bool)
    for _ in range(2):
        for i, (x, y) in enumerate(a):
            nb = keep & (np.abs(a[:, 0] - x) <= half); nb[i] = False
            if nb.sum() < 8:
                keep[i] = False; continue
            c = np.polyfit(a[nb, 0] - x, a[nb, 1], 2)
            keep[i] = abs(np.polyval(c, 0) - y) <= tol
    return [t for t, k in zip(tr, keep) if k]


# Graph definitions. Lattice specs: (px of first major, px of last major, value, value, intervals),
# read from the grid-line darkness profiles (each line is then re-located in the scan).
# Curves: (quantity, label value, seed (u, v), u range, fixed conditions). The seed is only a starting
# hint where the stroke is isolated; every saved point is a run centroid found by trace_runs.
RAY_GRAPHS = {
    "p8_plate_vs_Eb_Ec2": dict(
        page=8, sweep="Eb_V", fixed=dict(Ec1_V=0, Ec3_V=0), label="Ec2_V", dashed="Ic2",
        title="Ib, Ic2 vs Eb; Ec1 = Ec3 = 0; Ec2 = 60..140 V (p.8 top)",
        x=(559, 1947, 0, 560, 28), y=(1631, 651, 0, 20, 20), step=5.0, crop=(480, 600, 2000, 1680),
        open=np.ones((3, 3), bool),     # thin pen on this graph (solid strokes ~4 px, grid 1-2 px)
        curves=[("Ib", 140, (200, 16.043), (40, 560)), ("Ib", 120, [(200, 13.139), (60, 9.503)], (40, 560)),
                ("Ib", 100, (200, 10.405), (40, 560)), ("Ib", 80, (200, 7.621), (40, 560)),
                ("Ib", 60, [(200, 5.161), (100, 4.267)], (40, 560)),
                ("Ic2", 140, [(200, 12.254), (60, 15.324)], None), ("Ic2", 120, (200, 9.55), None),
                ("Ic2", 100, [(200, 7.048), (100, 8.279)], None), ("Ic2", 80, [(200, 4.835), (100, 5.659)], None),
                ("Ic2", 60, [(200, 2.781), (100, 3.346)], (30, 560))]),
    "p9_plate_vs_Eb_Ec1": dict(
        page=9, sweep="Eb_V", fixed=dict(Ec2_V=120, Ec3_V=0), label="Ec1_V", dashed="Ic2",
        title="Ib, Ic2 vs Eb; Ec2 = 120 V, Ec3 = 0; Ec1 = 0..-5 V (p.9 top)",
        x=(527.5, 1924.5, 0, 400, 20), y=(1617.4, 648, 0, 14, 14), step=5.0, crop=(480, 600, 1960, 1660),
        urange=(20, 400),               # below ~20 V all curves run together into the origin
        curves=[("Ib", 0, (250, 13.887), None), ("Ib", -0.5, [(250, 11.792), (80, 9.125)], None), ("Ib", -1, [(150, 8.824), (290, 9.995)], None),
                ("Ib", -1.5, (250, 7.721), None), ("Ib", -2, (150, 5.378), None), ("Ib", -2.5, (250, 4.019), None),
                ("Ib", -3, (250, 2.514), None), ("Ib", -3.5, (250, 1.488), None), ("Ib", -4, (250, 0.754), None),
                ("Ib", -5, (250, 0.141), None),
                ("Ic2", 0, [(150, 10.24), (80, 11.417)], None), ("Ic2", -1, (150, 6.394), None), ("Ic2", -2, (150, 3.32), None),
                ("Ic2", -3, [(250, 1.30), (80, 1.536)], None), ("Ic2", -4, (250, 0.367), None)]),
    "p9_Ib_vs_Ec1_Ec2": dict(
        page=9, sweep="Ec1_V", fixed=dict(Eb_V=120, Ec3_V=0), label="Ec2_V",
        title="Ib vs Ec1; Eb = 120 V, Ec3 = 0; Ec2 = 60..140 V (p.9 bottom)",
        x=(510, 1905, -5, 0, 20), y=(2880, 1909, 0, 14, 14), max_gap=15, step=0.05, crop=(470, 1860, 1960, 2920),
        curves=[("Ib", 140, (-0.3, 13.227), None), ("Ib", 120, (-0.3, 10.762), None), ("Ib", 100, (-0.3, 8.339), None),
                ("Ib", 80, (-0.3, 5.903), None), ("Ib", 60, (-0.3, 3.631), None)]),
    "p10_Ic2_vs_Ec1_Ec2": dict(
        page=10, sweep="Ec1_V", fixed=dict(Eb_V=120, Ec3_V=0), label="Ec2_V",
        title="Ic2 vs Ec1; Eb = 120 V, Ec3 = 0; Ec2 = 60..140 V (p.10 top)",
        x=(518, 1910, -5, 0, 20), y=(1632, 661, 0, 14, 14), max_gap=15, step=0.05, crop=(470, 610, 1960, 1680),
        curves=[("Ic2", 140, (-0.3, 12.208), None), ("Ic2", 120, (-0.3, 9.343), None), ("Ic2", 100, (-0.3, 6.802), None),
                ("Ic2", 80, (-0.3, 4.503), None), ("Ic2", 60, (-0.3, 2.461), None)]),
    "p10_vs_Ec3_Ec2": dict(
        page=10, sweep="Ec3_V", fixed=dict(Eb_V=120, Ec1_V=-2), label="Ec2_V", dashed="Ic2",
        title="Ib, Ic2 vs Ec3; Eb = 120 V, Ec1 = -2 V; Ec2 = 60..140 V (p.10 bottom)",
        x=(557, 1928, -14, 14, 28), y=(2879, 1915, 0, 10, 20), max_gap=15, step=0.25, crop=(500, 1870, 1980, 2920),
        open=np.ones((3, 3), bool),     # thin pen (solid strokes ~3.5-5 px, grid 1-2 px)
        curves=[("Ib", 140, [(11, 9.66), (-8.8, 0.15, -9.3, -7.5)], (-9.3, 14)), ("Ib", 120, (11, 6.602), (-8.3, 14)),
                ("Ib", 100, [(11, 3.856), (-6.8, 0.13, -7.3, -5.5)], (-7.3, 14)), ("Ib", 80, (11, 1.484), (-6.3, 14)),
                ("Ib", 60, [(11, 0.289), (-3.5, 0.12, -4.5, -1.5)], (-4.5, 14)),
                # ranges end at each toe's start; the extra seeds (u, v, u_lo, u_hi) pick up the toes
                ("Ic2", 140, (-3, 7.295), None), ("Ic2", 120, (-11, 7.205), None), ("Ic2", 100, (-11, 4.398), None),
                ("Ic2", 80, (-11, 1.976), None), ("Ic2", 60, (-6, 0.463), None)]),
    "p11_vs_Ec3_Ec1": dict(
        page=11, sweep="Ec3_V", fixed=dict(Eb_V=120, Ec2_V=120), label="Ec1_V", dashed="Ic2",
        title="Ib, Ic2 vs Ec3; Eb = Ec2 = 120 V; Ec1 = 0..-5 V (p.11 top)",
        x=(551, 1945, -14, 14, 28), y=(1653, 673, 0, 20, 20), max_gap=15, step=0.25, crop=(500, 620, 2000, 1700),
        open=np.ones((3, 3), bool),     # thin pen (solid strokes ~3.5-5 px, grid 1-2 px)
        curves=[("Ib", 0, (11, 15.28), (-5.7, 14)), ("Ib", -1, (8, 10.768), (-6.5, 14)), ("Ib", -2, (8, 6.435), (-6.5, 14)),
                ("Ib", -3, (8, 2.723), (-6.5, 14)), ("Ib", -4, (8, 0.721), (-6.5, 14)), ("Ib", -5, (8, 0.214), (-6.5, 14)),
                # all Ib curves leave the axis together at Ec3 = -7.8 V and stay within ~10 px of each other (so
                # would be dropped as crossings) up to about -6.5 V; tracing there lets tracers swap strokes
                ("Ic2", 0, (8, 5.829), None), ("Ic2", -1, (8, 3.784), None), ("Ic2", -2, (8, 2.204), None),
                ("Ic2", -3, (8, 0.999), None), ("Ic2", -4, [(8, 0.412), (-9, 1.02, -12.5, -0.5)], None)]),
    "p8_plate_vs_Eb_Ec3": dict(
        page=8, sweep="Eb_V", fixed=dict(Ec1_V=-2, Ec2_V=120), label="Ec3_V", dashed=("Ic2", "Ic3"),
        title="Ib, Ic2, Ic3 vs Eb; Ec2 = 120 V, Ec1 = -2 V; Ec3 = -14..+10 V (p.8 bottom)",
        x=(547.5, 1928.5, 0, 560, 28), y=(2882.5, 1907.5, 0, 10, 20), max_gap=50, step=5.0,
        solid_kw=dict(tmax=12, hist_n=20), dash_kw=dict(w=6, max_gap=30),
        crop=(480, 1850, 2000, 2930), csv="raytheon_5725_p8bottom.csv",
        open=DISC4, dash_open=DISC4,    # grid lines here are 2-3 px (a 3x3 opening keeps some), strokes 4-7 px
        offset={"Ic3": -8.0},           # Ic3 is read on the right-hand axis: 0 / 1 / 2 mA at the 8 / 9 / 10 mA lines
        # Ib: seeds at the right-hand ends (labels -1.0 ... -14). The Ic2 labels in mid-graph interrupt several Ib
        # strokes for 20-40 columns (e.g. -1 under "-10", -3 under "-8.0"), hence max_gap 50; tmax 12 stops the
        # tracer taking a dash merged into a stroke at a crossing. Not traced: Ec3 = +10, +6, +4, +2 and 0. +10
        # and +6 are one stroke up to Eb = 330 V and 6 px apart after it, all five merge into one stroke at the
        # knee and are cut by their inline labels, and tracers seeded on +6/+4 and on +2/0 ended on the same
        # strokes (shared-stroke guard). Ec3 = 0 is p.9 top's Ec1 = -2 V curve anyway. -12 and -13 run together
        # along the axis below Eb = 270 / 300 V.
        curves=[("Ib", -1, (385, 6.111), None),
                ("Ib", -2, (395, 5.846), None), ("Ib", -3, (395, 5.52), None), ("Ib", -4, (395, 5.119), None),
                ("Ib", -5, (395, 4.607), None), ("Ib", -6, (395, 4.105), None), ("Ib", -7, (395, 3.599), None),
                ("Ib", -8, (395, 3.093), None), ("Ib", -9, (395, 2.503), None), ("Ib", -10, (395, 1.922), None),
                ("Ib", -11, (395, 1.371), None), ("Ib", -12, (395, 0.897), (270, 560)), ("Ib", -13, (395, 0.529), (300, 560)),
                ("Ib", -14, (395, 0.215), None),
                # Ic2: the eleven dashed curves cross 14 solid curves and each other; the dashed tracer is only
                # allowed 30-column gaps here (longer bridges hopped between curves) and only -14 and -4 give
                # 10 or more points. -14 and -12 run together along the 7.5 mA line below Eb = 240 V.
                ("Ic2", -14, (348, 7.173), (240, 560)), ("Ic2", -4, (366, 3.91), (35, 560)),
                # Ic3 (right-hand axis, seeds in left-axis units): +10 and +4 run 6-9 px apart (Eb 200-300 V)
                # and as one stroke below 120 V, so only +2, +1 and +0.5 are traced
                ("Ic3", 2, (250, 8.706), None), ("Ic3", 1, (250, 8.426), None), ("Ic3", 0.5, (250, 8.128), None)]),
    # Transconductance graphs (pp. 11-13), all at Eb = 120 V: solid curves only, values in micromho. Thin pen on
    # a 2-3 px grid, so the same 4 px disc opening as p.8 bottom. The heavy Ec3 = 0 line merges with every curve
    # it crosses (runs taller than tmax), so there is no point at Ec3 = 0 itself.
    "p11_gm1_vs_Ec3_Ec2": dict(
        page=11, sweep="Ec3_V", fixed=dict(Eb_V=120, Ec1_V=-2), label="Ec2_V", value="gm_umho",
        title="gm(g1) vs Ec3; Eb = 120 V, Ec1 = -2 V; Ec2 = 60..140 V (p.11 bottom)",
        x=(547.5, 1941.5, -14, 14, 28), y=(2881.5, 1903.5, 0, 5000, 20), max_gap=15, step=0.25,
        crop=(500, 1870, 2000, 2920), csv="raytheon_5725_gm_p11-13.csv", open=DISC4, clear_axis=True,
        curves=[("gm1", 140, (10.5, 4287), None), ("gm1", 120, (10.5, 4101), None), ("gm1", 100, (10.5, 3454), None),
                ("gm1", 80, (10.5, 2403), None), ("gm1", 60, [(10.5, 897), (-3.5, 294)], None)]),
    "p12_gm1_vs_Ec1_Ec2": dict(
        page=12, sweep="Ec1_V", fixed=dict(Eb_V=120, Ec3_V=0), label="Ec2_V", value="gm_umho",
        title="gm(g1) vs Ec1; Eb = 120 V, Ec3 = 0; Ec2 = 60..140 V (p.12 top)",
        x=(566.5, 1924, -7, 0, 28), y=(1633, 657.5, 0, 4000, 20), max_gap=15, step=0.05,
        crop=(520, 620, 1960, 1680), csv="raytheon_5725_gm_p11-13.csv", open=DISC4, clear_axis=True,
        curves=[("gm1", 140, (-0.4, 3314), None), ("gm1", 120, (-0.4, 3211), None), ("gm1", 100, (-0.4, 3110), None),
                ("gm1", 80, (-0.4, 2986), None), ("gm1", 60, (-0.4, 2860), None)]),
    "p12_gm1_vs_Ec3_Ec1": dict(
        page=12, sweep="Ec3_V", fixed=dict(Eb_V=120, Ec2_V=120), label="Ec1_V", value="gm_umho",
        title="gm(g1) vs Ec3; Eb = Ec2 = 120 V; Ec1 = 0..-5 V (p.12 bottom)",
        x=(566, 1919.5, -14, 14, 28), y=(2904, 1925, 0, 5000, 20), max_gap=15, step=0.25,
        crop=(520, 1890, 1960, 2940), csv="raytheon_5725_gm_p11-13.csv", open=DISC4, clear_axis=True,
        # Ec1 = 0, -1 and -2 cross each other repeatedly between Ec3 = -7 and +5.5 V (strokes within a few px), and
        # every curve runs into the common toe at Ec3 = -8..-6 V; each range starts where its stroke is on its own.
        curves=[("gm1", 0, (8.5, 4435), (6.5, 14)), ("gm1", -1, (8.5, 4302), (6.5, 14)), ("gm1", -2, (8.5, 3994), (4, 14)),
                ("gm1", -3, (8.5, 2856), (-2.5, 14)), ("gm1", -3.5, (8.5, 2010), (-4, 14)),
                ("gm1", -4, (8.5, 1284), (-5.5, 14)), ("gm1", -4.5, (8.5, 669), (-6, 14)), ("gm1", -5, (8.5, 293), (-6.5, 14))]),
    "p13_gm3_vs_Ec3_Ec2": dict(
        page=13, sweep="Ec3_V", fixed=dict(Eb_V=120, Ec1_V=-2), label="Ec2_V", value="gm_umho",
        title="gm(g3) vs Ec3; Eb = 120 V, Ec1 = -2 V; Ec2 = 60..140 V (p.13 top)",
        x=(570.5, 1932.5, -14, 14, 28), y=(1632, 661, 0, 2000, 20), max_gap=15, step=0.25,
        crop=(520, 620, 1980, 1680), csv="raytheon_5725_gm_p11-13.csv", open=DISC4, clear_axis=True,
        curves=[("gm3", 140, (-4.5, 1029), None), ("gm3", 120, (-4.5, 810), None), ("gm3", 100, (-4.5, 577), None),
                ("gm3", 80, (-4.5, 280), None), ("gm3", 60, (-4.5, 54), None)]),
    "p13_gm3_vs_Ec3_Ec1": dict(
        page=13, sweep="Ec3_V", fixed=dict(Eb_V=120, Ec2_V=120), label="Ec1_V", value="gm_umho",
        title="gm(g3) vs Ec3; Eb = Ec2 = 120 V; Ec1 = 0..-4 V (p.13 bottom)",
        x=(587, 1945.5, -14, 14, 28), y=(2908.5, 1938.5, 0, 2000, 20), max_gap=15, step=0.25,
        crop=(540, 1900, 1990, 2950), csv="raytheon_5725_gm_p11-13.csv", open=DISC4, clear_axis=True,
        # -4 V stops at Ec3 = 0.5 V: beyond that its tail runs into the -3 V tail (one stroke, 10-30 umho)
        curves=[("gm3", 0, (-4.5, 1661), None), ("gm3", -1, (-4.5, 1259), None), ("gm3", -2, (-4.5, 805), None),
                ("gm3", -3, (-4.5, 346), None), ("gm3", -4, (-4.5, 102), (-14, 0.5))]),
}


def ray_probe(key, us, vlo, vhi):
    """Debug aid: print the cleaned-mask runs (in data units) at sweep values `us`. The seeds in
    RAY_GRAPHS were chosen from this listing."""
    G = RAY_GRAPHS[key]; g = np.array(render_ray(G["page"])) < 128
    m = ndi.binary_opening(g, structure=G.get("open", DISC5))
    to_px, to_data, info = lattice(g, m, G["x"], G["y"])
    for u in us:
        x = int(round(to_px(u, (vlo + vhi) / 2)[0]))
        ylo = int(to_px(u, vhi)[1]); yhi = int(to_px(u, vlo)[1])
        print(u, [(round(to_data(x, (t + b) / 2)[1], 3), b - t + 1) for t, b in runs_at(m, x, ylo, yhi)])


CROSS_PX = 10      # drop a column if another curve's stroke centre is this close (strokes are 5-8 px)
AXIS_PX = 4        # drop a column if its run reaches within this many px of the zero-current line
DASH_PX = 60       # connected strokes shorter than this (bounding box) are dashes


def ray_graph(key):
    """Digitise every curve of one Raytheon graph. Returns (rows, calibration info)."""
    G = RAY_GRAPHS[key]; im = render_ray(G["page"]); g = np.array(im) < 128
    m = ndi.binary_opening(g, structure=G.get("open", DISC5))
    to_px, to_data, info = lattice(g, m, G["x"], G["y"])
    ulo, uhi = sorted(G["x"][2:4]); vmin = min(G["y"][2:4])
    # Solid curves are long connected strokes; dashes are short pieces (< DASH_PX). Tracing each kind
    # on its own mask stops a tracer from switching to the other family at shallow crossings.
    # Components are taken after a 2 px dilation, which rejoins thin solid strokes that the opening
    # broke (gaps 1-4 px) but not dashes (gaps 7-15 px). Solid curves are traced first; dashes are
    # then the short pieces of the 5 px disc opening (dashes are bold on every page) once the pixels
    # around the traced solid strokes are removed.
    def split(mm):
        lab_im, n = ndi.label(ndi.binary_dilation(mm, iterations=2))
        ext = np.array([max(s[0].stop - s[0].start, s[1].stop - s[1].start) for s in ndi.find_objects(lab_im)])
        long_ = np.isin(lab_im, 1 + np.where(ext >= DASH_PX)[0])
        return mm & long_, mm & ~long_
    solid = split(m)[0]
    if G.get("clear_axis"):        # toes would otherwise be traced along the heavy zero line (and shared there)
        for x in range(solid.shape[1]):
            solid[int(to_px(to_data(x, 0)[0], vmin)[1]) - AXIS_PX:, x] = False
    m5 = m if G.get("open") is None else ndi.binary_opening(g, structure=G.get("dash_open", DISC5))
    dashed_q = G.get("dashed") or ()
    dashed_q = (dashed_q,) if isinstance(dashed_q, str) else tuple(dashed_q)
    off = G.get("offset", {})          # value = read-off value + offset (Ic3 is on a shifted right-hand axis)
    edges = [int(round(to_px(u, (G["y"][2] + G["y"][3]) / 2)[0])) for u in (ulo, uhi)]
    skip = set(c + k for c in edges for k in range(-6, 7))      # frame lines
    traces, dmask = {}, None
    for q, lab, seeds, urange in sorted(G["curves"], key=lambda c: c[0] in dashed_q):
        dashed = q in dashed_q
        if dashed and dmask is None:
            near_solid = np.zeros_like(m)
            for tr in traces.values():
                for x, y, t, b in tr:
                    near_solid[t - 3:b + 4, x] = True
            dmask = split(m5 & ~ndi.binary_dilation(near_solid, iterations=2))[1]
        mm = dmask if dashed else solid
        kw = (dict(dict(skip_cols=skip, max_gap=100, hist_n=40), **G.get("dash_kw", {})) if dashed
              else dict(skip_cols=skip, max_gap=G.get("max_gap", 60), **G.get("solid_kw", {})))
        tr = {}
        for seed in ([seeds] if isinstance(seeds[0], (int, float)) else seeds):   # one or more seeds
            x0, y0 = to_px(*seed[:2]); x0 = int(round(x0))
            rng = seed[2:4] if len(seed) == 4 else (urange or G.get("urange") or (ulo, uhi))
            xa, xb = (int(round(to_px(u, seed[1])[0])) for u in rng)
            for p in trace_runs(mm, x0, y0, xa, **kw) + trace_runs(mm, x0, y0, xb, **kw):
                tr.setdefault(p[0], p)
        traces[(q, lab)] = _smooth_ok(sorted(tr.values()))
    shared = []                            # guard: two curves must never have followed the same stroke
    for k1, t1 in traces.items():
        for k2, t2 in traces.items():
            if k1 < k2 and t1 and t2:
                a1, a2 = dict((p[0], p[1]) for p in t1), dict((p[0], p[1]) for p in t2)
                same = sum(abs(a1[x] - a2[x]) < 3 for x in set(a1) & set(a2))
                if same > 30:
                    shared.append((k1, k2, same))
    kept = {}
    for k, tr in traces.items():
        others = [np.array(o, float)[:, :2] for kk, o in traces.items() if kk != k and len(o)]
        out = []
        for x, y, t, b in tr:
            near = False
            for o in others:
                j = np.abs(o[:, 0] - x) <= 15
                if j.any() and o[j, 0].min() <= x <= o[j, 0].max() and \
                        abs(np.interp(x, o[j, 0], o[j, 1]) - y) < CROSS_PX:
                    near = True; break
            if near or b >= to_px(to_data(x, y)[0], vmin - off.get(k[0], 0.0))[1] - AXIS_PX:
                continue
            out.append((x, y, t, b))
        kept[k] = out
    rows, ov = [], im.convert("RGB"); d = ImageDraw.Draw(ov)
    for (q, lab), tr in kept.items():
        if not tr:
            continue
        uvs = np.array([to_data(x, y) for x, y, _, _ in tr])
        tol = 1.5 / abs(info["px_per_u"])
        grid = np.arange(np.ceil(uvs[:, 0].min() / G["step"]) * G["step"], uvs[:, 0].max() + 1e-9, G["step"])
        for ut in grid:
            i = int(np.argmin(np.abs(uvs[:, 0] - ut)))
            if abs(uvs[i, 0] - ut) > tol:
                continue
            u, v = uvs[i]; x, y = tr[i][0], tr[i][1]
            r = dict(page=G["page"], graph=key, curve=f"{q}_{G['label'][:-2]}_{lab:g}", quantity=q,
                     Ec1_V=None, Ec2_V=None, Ec3_V=None, Eb_V=None)
            r[G.get("value", "I_mA")] = round(v + off.get(q, 0.0), 4)
            r.update(x_px=x, y_px=round(y, 1), stroke_px=tr[i][3] - tr[i][2] + 1)
            r.update(G["fixed"]); r[G["label"]] = lab; r[G["sweep"]] = round(u, 3)
            rows.append(r)
            rgb = {"Ic2": (0, 120, 255), "Ic3": (220, 0, 220)}.get(q, (255, 0, 0))
            d.ellipse([x - 4, y - 4, x + 4, y + 4], outline=rgb, width=2)
        for x, y, _, _ in traces[(q, lab)]:
            d.point((x, y), fill=(0, 200, 0))
    os.makedirs(os.path.join(HERE, "plots"), exist_ok=True)
    ov.crop(G["crop"]).save(os.path.join(HERE, "plots", f"overlay_raytheon_{key}.png"))
    assert not shared, (key, shared)
    return rows, info


if __name__ == "__main__":
    r3, c3 = page3(); r4, c4 = page4()
    keys = ["page", "curve", "quantity", "Ec1_V", "Ec2_V", "Ec3_V", "Eb_V", "I_mA", "x_px", "y_px", "on_gridline"]
    for fn, rows in (("plate_characteristics_p3.csv", r3), ("suppressor_characteristics_p4.csv", r4)):
        with open(os.path.join(HERE, "data", fn), "w", newline="") as f:
            f.write(f"# source: {SRC_URL} (GE 6AS6, ET-T706 12-49), rendered at {DPI} dpi; "
                    "voltages in V w.r.t. cathode, current in mA\n")
            w = csv.DictWriter(f, keys, lineterminator="\n"); w.writeheader(); w.writerows(rows)
    rr, cr = [], {}
    for k in RAY_GRAPHS:
        rows, info = ray_graph(k); rr += rows
        cr[k] = dict(page=RAY_GRAPHS[k]["page"], conditions=RAY_GRAPHS[k]["title"], points=len(rows),
                     **{a: info[a] for a in ("px_per_u", "px_per_v", "affine_rms_residual_px",
                                             "affine_max_residual_px", "lines_from_raw_scan")})
    # p.8-11 is the #25 fit data. p.8 bottom (Ib/Ic2/Ic3 vs Eb at fixed Ec3) and the gm graphs are kept in
    # their own files: they are held out of every fit and the gm files hold micromho, not mA.
    RAY_CSV = {"raytheon_5725_p8-11.csv": ("I_mA", "pages 8-11", "current in mA"),
               "raytheon_5725_p8bottom.csv": ("I_mA", "page 8 bottom",
                                              "current in mA (Ic3 read on the right-hand axis)"),
               "raytheon_5725_gm_p11-13.csv": ("gm_umho", "pages 11 bottom, 12 and 13",
                                               "transconductance in micromho")}
    for fn, (val, pages, unit) in RAY_CSV.items():
        keys = ["page", "graph", "curve", "quantity", "Ec1_V", "Ec2_V", "Ec3_V", "Eb_V", val, "x_px", "y_px",
                "stroke_px"]
        with open(os.path.join(HERE, "data", fn), "w", newline="") as f:
            f.write(f"# source: {RAY_URL} (Raytheon 5725/6AS6W, 15 Dec 1960) SHA256 {RAY_SHA256}, {pages} "
                    f"rendered at {RAY_DPI} dpi; voltages in V w.r.t. cathode, {unit}\n")
            w = csv.DictWriter(f, keys, lineterminator="\n"); w.writeheader()
            w.writerows(r for r in rr if RAY_GRAPHS[r["graph"]].get("csv", "raytheon_5725_p8-11.csv") == fn)
    with open(os.path.join(HERE, "data", "calibration.json"), "w") as f:
        json.dump(dict(page3=c3, page4=c4, raytheon_5725=cr), f, indent=1)
    from collections import Counter
    print(Counter(r["curve"] for r in r3 + r4)); print(c3, c4)
    for k, c in cr.items():
        print(k, c["points"], "points,", sum(0.1 <= r.get("I_mA", -1) <= 0.6 for r in rr if r["graph"] == k),
              "in 0.1-0.6 mA")
