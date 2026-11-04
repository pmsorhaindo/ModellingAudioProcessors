"""Reproducible digitiser for the GE 6AS6 datasheet (ET-T706, 12-49).

Pipeline: pdftoppm -r 200 -> greyscale threshold -> remove grid lines ->
column-wise curve tracking from a hand-placed seed -> pixel->data calibration
from detected grid-line positions. Writes data/*.csv and an overlay PNG per page.
Seeds are only starting hints; every point comes from dark pixels in the scan.
"""
import csv, os, subprocess
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


if __name__ == "__main__":
    r3, c3 = page3(); r4, c4 = page4()
    keys = ["page", "curve", "quantity", "Ec1_V", "Ec2_V", "Ec3_V", "Eb_V", "I_mA", "x_px", "y_px", "on_gridline"]
    for fn, rows in (("plate_characteristics_p3.csv", r3), ("suppressor_characteristics_p4.csv", r4)):
        with open(os.path.join(HERE, "data", fn), "w", newline="") as f:
            f.write(f"# source: {SRC_URL} (GE 6AS6, ET-T706 12-49), rendered at {DPI} dpi; "
                    "voltages in V w.r.t. cathode, current in mA\n")
            w = csv.DictWriter(f, keys); w.writeheader(); w.writerows(rows)
    import json
    json.dump(dict(page3=c3, page4=c4), open(os.path.join(HERE, "data", "calibration.json"), "w"), indent=1)
    from collections import Counter
    print(Counter(r["curve"] for r in r3 + r4)); print(c3, c4)
