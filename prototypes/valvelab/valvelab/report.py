"""Build the self-contained static report page (index.html) from results/."""
import base64, html, json, os
import numpy as np

SRC = {
    "koren": ("N. L. Koren, 'Improved vacuum tube models for SPICE simulations', Glass Audio 8(5), 1996 (web version, 2003)",
              "https://www.normankoren.com/Audio/Tubemodspice_article.html"),
    "korenlib": ("N. L. Koren, Tubemods.zip (Tube.lib PSpice library)", "https://www.normankoren.com/Audio/Tubemods.zip"),
    "ltwiki": ("LTspice wiki, nk_pentode.sub (Koren-format models fitted to data sheets by library contributors, 2008)",
               "https://ltwiki.org/files/LTspiceIV/lib/sub/nk_pentode.sub"),
    "dz": ("K. Dempwolf, U. Zoelzer, 'A physically-motivated triode model for circuit simulations', Proc. DAFx-11, Paris, 2011",
           "https://www.dafx.de/paper-archive/2011/Papers/76_e.pdf"),
    "parker": ("J. D. Parker, V. Zavalishin, E. Le Bivic, 'Reducing the aliasing of nonlinear waveshaping using continuous-time convolution', Proc. DAFx-16, Brno, 2016",
               "https://www.dafx.de/paper-archive/2016/dafxpapers/20-DAFx-16_paper_41-PN.pdf"),
    "bilbao": ("S. Bilbao, F. Esqueda, J. D. Parker, V. Valimaki, 'Antiderivative antialiasing for memoryless nonlinearities', IEEE Signal Processing Letters 24(7), 2017 (author copy, Aaltodoc)",
               "https://aaltodoc.aalto.fi/server/api/core/bitstreams/87839e85-c165-4bd9-aeed-7c96ff64026a/content"),
    "kahles": ("J. Kahles, F. Esqueda, V. Valimaki, 'Oversampling for nonlinear waveshaping: choosing the right filters', J. Audio Eng. Soc. 67(6), 2019",
               "https://doi.org/10.17743/jaes.2019.0012"),
    "holters": ("M. Holters, 'Antiderivative antialiasing for stateful systems', Applied Sciences 10(1):20, 2019",
                "https://doi.org/10.3390/app10010020"),
    "6as6": ("Tung-Sol 6AS6 data sheet (Frank's Electron Tube Data Sheets)", "https://frank.pocnet.net/sheets/127/6/6AS6.pdf"),
    "5963": ("RCA 5963 data sheet (Frank's Electron Tube Data Sheets)", "https://frank.pocnet.net/sheets/049/5/5963.pdf"),
}


def cite(k):
    t, u = SRC[k]
    return f'<a href="{u}">[{list(SRC).index(k) + 1}]</a>'


def img(path, alt):
    with open(path, "rb") as fh:
        b = base64.b64encode(fh.read()).decode()
    return f'<img alt="{html.escape(alt)}" src="data:image/png;base64,{b}">'


def f1(x):
    return f"{x:.1f}"


def build(root):
    R = json.load(open(os.path.join(root, "results", "results.json")))
    F = R["figures"]
    aa = R["antialias"]
    f0s = list(aa["results"].keys())
    f5 = [k for k in f0s if abs(float(k) - 5000) < 10][0]
    f10 = [k for k in f0s if abs(float(k) - 10000) < 10][0]
    f1k = [k for k in f0s if abs(float(k) - 1000) < 10][0]
    A = aa["results"]
    short = {"12AX7 (Koren)": "12AX7 (Koren)", "12AX7 (Dempwolf-Zoelzer)": "12AX7 (Dempwolf-Zoelzer)",
             "12AU7 (Koren) - stand-in for 5963": "12AU7 (Koren), 5963 stand-in",
             "EF86 (Koren-format) - stand-in for 6AS6": "EF86 (Koren-format), 6AS6 stand-in"}

    # ---- derived findings from the bias sweep
    bs = R["bias_sweep"]
    rows_bias = []
    for name, d in bs.items():
        q = np.array(d["q"])
        e2 = np.array(d["by_drive"]["2.0"]["eo"]); e1 = np.array(d["by_drive"]["1.0"]["eo"])
        e03 = np.array(d["by_drive"]["0.3"]["eo"])
        i2 = int(np.argmin(e2)); icold = int(np.argmin(np.abs(q - 0.85)))
        rows_bias.append((short[name], q[i2], e2[i2], e2[icold], e1[icold], float(np.median(e03))))
    bars = R["bars_drive_2xA10"]

    def bars_row(name):
        b = bars[name]
        out = []
        for lab in ("hot", "centre", "cold"):
            v = b[lab]
            out.append(f"<td>{v['thd_pct']:.1f}%</td><td>{v['H2_H7_dB'][0]:.1f} / {v['H2_H7_dB'][1]:.1f}</td><td>{v['even_minus_odd_dB']:+.1f}</td>")
        return "".join(out)

    dc = R["dc_points_textbook_Rk"]
    dc_rows = "".join(
        f"<tr><td>{short[n]}</td><td>{v['B']:.0f}</td><td>{v['Rp']/1e3:.0f}k</td><td>{v['Rk']:.0f}</td>"
        f"<td>{'' if v['Vg2'] is None else f'{v[chr(86)+chr(103)+chr(50)]:.0f}'}</td>"
        f"<td>{v['Vk0']:.2f}</td><td>{v['Va0']:.1f}</td><td>{v['Ia0_mA']:.2f}</td><td>{v['gain']:.1f}</td></tr>"
        for n, v in dc.items())
    cen = R["configs"]
    cen_rows = "".join(
        f"<tr><td>{short[n]}</td><td>{v['centre']['Vgk0']:.2f}</td><td>{v['centre']['Rk']:.0f}</td>"
        f"<td>{v['centre']['gain']:.1f}</td><td>{v['A10_centre_V']:.2f}</td></tr>" for n, v in cen.items())

    meths = list(A[f5].keys())
    aa_rows = "".join(
        "<tr><td>%s</td>%s<td>%.2f</td><td>%.0f</td></tr>" % (
            m, "".join(f"<td>{A[f][m]['alias_to_harm_dB']:.1f}</td>" for f in f0s),
            A[f10][m]["fund_rel_ref_dB"], aa["python_ns_per_sample"][m]) for m in meths)
    cb = R.get("c_bench", {})
    cbench = html.escape(cb.get("output", cb.get("error", "not run")))
    ma = R["model_agreement_12AX7"]

    ps = R["param_sources"]
    def ptab(d, keys):
        rows = ""
        for name, v in d.items():
            rows += f"<tr><td>{name}</td>" + "".join(f"<td>{v[k]}</td>" for k in keys) + \
                    f"<td><a href=\"{v['source']}\">source</a> ({html.escape(v['note'])})</td></tr>"
        return rows
    kt = ptab(ps["koren_triode"], ["MU", "EX", "KG1", "KP", "KVB"])
    kp = ptab(ps["koren_pentode"], ["MU", "EX", "KG1", "KG2", "KP", "KVB"])
    dzt = ptab(ps["dempwolf"], ["G", "mu", "gamma", "C", "Gg", "xi", "Cg", "Ig0"])
    si = "".join(f"<li><b>{k}</b>: modelled with {html.escape(v['stand_in'])}. {html.escape(v['why'])} "
                 f"<a href=\"{v['datasheet']}\">data sheet</a></li>" for k, v in ps["stand_ins"].items())

    bias_rows = "".join(
        f"<tr><td>{n}</td><td>{q:.3f}</td><td>{e:+.1f}</td><td>{ec:+.1f}</td><td>{e1:+.1f}</td><td>{e03:+.1f}</td></tr>"
        for n, q, e, ec, e1, e03 in rows_bias)

    demo = json.dumps(R["demo"], separators=(",", ":"))
    # numbers quoted in the prose, all computed here from results.json
    eo = {n: (np.array(d["q"]), {m: np.array(v["eo"]) for m, v in d["by_drive"].items()}) for n, d in bs.items()}
    pent = "EF86 (Koren-format) - stand-in for 6AS6"
    tri = [n for n in eo if n != pent]
    at = lambda n, m, q0: float(eo[n][1][m][np.argmin(np.abs(eo[n][0] - q0))])
    cold1 = [at(n, "1.0", 0.85) for n in eo]
    odd2 = [-float(eo[n][1]["2.0"].min()) for n in eo]
    qmin = lambda n, m: float(eo[n][0][np.argmin(eo[n][1][m])])
    qp, qp2 = sorted([qmin(pent, "1.0"), qmin(pent, "2.0")])
    qts = [qmin(n, m) for n in tri for m in ("1.0", "2.0")]
    p1 = float(eo[pent][1]["1.0"].min()); t1 = [float(eo[n][1]["1.0"].min()) for n in tri]
    gr = abs(cen[pent]["centre"]["gain"] / cen["12AX7 (Koren)"]["centre"]["gain"])
    import re
    cns = dict(re.findall(r"^(naive|ADAA1 \+ 2x OS|ADAA1|2x OS|4x OS|8x OS)\s+([0-9.]+)", cb.get("output", ""), re.M))
    c_ad2 = float(cns.get("ADAA1 + 2x OS", "nan")); c_o4 = float(cns.get("4x OS", "nan")); c_ad = float(cns.get("ADAA1", "nan"))
    chain = 100 * c_ad2 * 1e-9 * 3 * 2 * 48000
    adg = [A[f]["naive"]["alias_to_harm_dB"] - A[f]["ADAA1"]["alias_to_harm_dB"] for f in f0s]
    n1k = A[f1k]["naive"]["alias_to_harm_dB"]; a1k = A[f1k]["ADAA1"]["alias_to_harm_dB"]
    os1k = max(v["alias_to_harm_dB"] for k, v in A[f1k].items() if "OS" in k)
    cold1_lo, cold1_hi, odd2_lo, odd2_hi = min(cold1), max(cold1), min(odd2), max(odd2)
    qt_lo, qt_hi = min(qts), max(qts); t1_lo, t1_hi = min(t1), max(t1); adg_lo, adg_hi = min(adg), max(adg)
    src_list = "".join(f'<li id="s{i+1}">{html.escape(t)}. <a href="{u}">{html.escape(u)}</a></li>'
                       for i, (t, u) in enumerate(SRC.values()))

    nv, a4, ad, ad2, o8 = (A[f5][k]["alias_to_harm_dB"] for k in ("naive", "4x OS", "ADAA1", "ADAA1 + 2x OS", "8x OS"))
    o2 = A[f5]["2x OS"]["alias_to_harm_dB"]; o8l = A[f5]["8x OS, long FIR"]["alias_to_harm_dB"]

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="robots" content="noindex">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Valve stage lab: triode vs pentode saturation, bias and antialiasing</title>
<style>
body{{font:15px/1.55 -apple-system,system-ui,Segoe UI,Roboto,sans-serif;max-width:1040px;margin:2rem auto;padding:0 1rem;color:#1d1d1f}}
h1{{font-size:1.6rem;margin-bottom:.2rem}} h2{{margin-top:2.2rem;border-bottom:1px solid #ddd;padding-bottom:.2rem}}
img{{max-width:100%;border:1px solid #eee;margin:.6rem 0}}
table{{border-collapse:collapse;font-size:13px;margin:.6rem 0}} td,th{{border:1px solid #ddd;padding:3px 7px;text-align:right}}
td:first-child,th:first-child{{text-align:left}} th{{background:#f5f5f7}}
.eq{{font-family:Georgia,serif;background:#f7f7fa;padding:.5rem .9rem;margin:.5rem 0;border-left:3px solid #8a8af0;overflow-x:auto}}
.note{{background:#fff8e6;border-left:3px solid #e0a800;padding:.5rem .9rem}}
.key{{background:#eef7ee;border-left:3px solid #3a3;padding:.5rem .9rem}}
pre{{background:#f5f5f7;padding:.6rem;overflow-x:auto;font-size:12px}}
#demo canvas{{border:1px solid #ddd;background:#fff}} #demo label{{display:inline-block;min-width:230px}}
small{{color:#666}}
</style></head><body>
<h1>Valve stage lab: triode vs pentode saturation, bias and antialiasing</h1>
<p><small>Ohm (analog-modelling research notes) &middot; generated {html.escape(R['generated'])} &middot; research toward a
saturator <i>inspired by</i> the Thermionic Culture Vulture. Nothing here is measured from, or claimed to match, the original unit.</small></p>

<div class="key"><b>In one paragraph.</b> I built a small NumPy lab that solves a single common-cathode valve stage with
published valve models (Koren triode and pentode, plus the Dempwolf&ndash;Z&ouml;lzer triode), then measured its harmonics and
how much different antialiasing schemes help. Three findings matter for the plugin. (1) <b>Bias steers even vs odd
harmonics in every model.</b> At moderate drive (1&times;A<sub>10%</sub>) and a cold bias (plate at 0.85&thinsp;B+), even-harmonic energy
exceeds odd by {cold1_lo:.0f} to {cold1_hi:.0f}&nbsp;dB. Near a "centre" bias the 2nd harmonic cancels and odd harmonics take over: at 2&times; drive the
most odd-dominant point is {odd2_lo:.0f} to {odd2_hi:.0f}&nbsp;dB odd-over-even, depending on the valve. That is the mechanism we need behind a
Culture-Vulture-style bias knob. (2) <b>The pentode stand-in's symmetric, odd-dominant point sits at a hotter bias than the triodes'</b>
(V<sub>a0</sub> &asymp; {qp:.2f}&ndash;{qp2:.2f}&thinsp;B+ vs {qt_lo:.2f}&ndash;{qt_hi:.2f}), and its gain is about {gr:.1f}&times; the 12AX7's in the same stage. (3) <b>Antialiasing:</b> at 5&nbsp;kHz and 2&times; drive, first-order ADAA alone
removes {nv-ad:.0f}&nbsp;dB of aliasing (from {f1(nv)} to {f1(ad)}&nbsp;dB). 2&times; oversampling plus ADAA1 reaches {f1(ad2)}&nbsp;dB, and 4&times; oversampling
reaches {f1(a4)}&nbsp;dB. In scalar C that is about {c_ad2:.0f}&nbsp;ns vs {c_o4:.0f}&nbsp;ns per sample. Either is cheap. The real cost driver is the quality of the oversampling filter, not the
oversampling factor.</div>

<h2>1. Why this, and what's missing</h2>
<p>No public schematic for any Culture Vulture revision exists. Per the manuals and published descriptions, the valves are an EF86 input, a 6AS6
dual-control pentode doing the distortion, and a 5963 output. So before guessing a circuit, I wanted to understand what
a single valve stage does to a signal as bias and drive change, using only published valve data, and how to make that
alias-free cheaply. Everything below is a <b>single stage with textbook component values</b>, not the unit's circuit.</p>
<div class="note"><b>Stand-ins (important).</b> I found no published model parameters for the 6AS6 or the 5963.
<ul>{si}</ul>
The 6AS6's defining feature, control from its suppressor grid (g3), is not covered by any Koren pentode equation. Modelling it will need
6AS6 measurements or curves digitised from the data sheet, fitted to a model with a g3 term.</div>

<h2>2. Valve models (with every parameter's source)</h2>
<p><b>Koren triode</b> {cite('koren')} (eq. 4; the factor 2 is Koren's (1+sgn E<sub>1</sub>) for E<sub>1</sub>&ge;0):</p>
<div class="eq">E<sub>1</sub> = (V<sub>P</sub>/K<sub>P</sub>) &middot; ln(1 + exp(K<sub>P</sub>(1/&mu; + V<sub>G</sub>/&radic;(K<sub>VB</sub> + V<sub>P</sub><sup>2</sup>)))) &nbsp;&nbsp;&nbsp;
I<sub>P</sub> = 2&thinsp;E<sub>1</sub><sup>X</sup> / K<sub>G1</sub></div>
<p><b>Koren pentode</b> {cite('koren')} (eq. 5 and eq. 3 with the errata's /&mu;):</p>
<div class="eq">E<sub>1</sub> = (V<sub>G2</sub>/K<sub>P</sub>) &middot; ln(1 + exp(K<sub>P</sub>(1/&mu; + V<sub>G1</sub>/V<sub>G2</sub>))) &nbsp;&nbsp;&nbsp;
I<sub>P</sub> = 2&thinsp;E<sub>1</sub><sup>X</sup>/K<sub>G1</sub> &middot; arctan(V<sub>P</sub>/K<sub>VB</sub>) &nbsp;&nbsp;&nbsp;
I<sub>G2</sub> = (V<sub>G2</sub>/&mu; + V<sub>G1</sub>)<sup>X</sup>/K<sub>G2</sub></div>
<p><b>Dempwolf&ndash;Z&ouml;lzer triode</b> {cite('dz')} (eqs. 10&ndash;12), physically motivated, with a fitted grid current:</p>
<div class="eq">I<sub>K</sub> = G&thinsp;[ln(1 + e<sup>C(V<sub>A</sub>/&mu; + V<sub>G</sub>)</sup>)/C]<sup>&gamma;</sup> &nbsp;&nbsp;
I<sub>G</sub> = G<sub>g</sub>&thinsp;[ln(1 + e<sup>C<sub>g</sub>V<sub>G</sub></sup>)/C<sub>g</sub>]<sup>&xi;</sup> + I<sub>g0</sub> &nbsp;&nbsp;
I<sub>A</sub> = I<sub>K</sub> &minus; I<sub>G</sub></div>
<p>Grid current for the Koren valves uses Koren's own SPICE network: R<sub>GI</sub> = 2&nbsp;k&Omega; in series with a diode (IS = 1&nbsp;nA, RS = 1&nbsp;&Omega;) {cite('korenlib')}.</p>
<table><tr><th>Koren triode</th><th>MU</th><th>EX</th><th>KG1</th><th>KP</th><th>KVB</th><th>source</th></tr>{kt}</table>
<table><tr><th>Koren pentode</th><th>MU</th><th>EX</th><th>KG1</th><th>KG2</th><th>KP</th><th>KVB</th><th>source</th></tr>{kp}</table>
<table><tr><th>Dempwolf&ndash;Z&ouml;lzer</th><th>G</th><th>&mu;</th><th>&gamma;</th><th>C</th><th>G<sub>g</sub></th><th>&xi;</th><th>C<sub>g</sub></th><th>I<sub>g0</sub></th><th>source</th></tr>{dzt}</table>
<p><small>The EF86 set {cite('ltwiki')} is a community fit to the Philips data sheet in Koren's format, not by Koren himself. Its KG2 = 4500 is a value
repeated across that library, not a per-valve fit, so I hold the screen at a fixed 100&nbsp;V and screen current only affects the DC cathode current.
6AN8P (Koren's own small-signal pentode fit) is also in the code as a second pentode reference.</small></p>
{img(F['f1_plate_curves'], 'plate curves')}
<p>The two 12AX7 models agree to a median of {ma['median_abs_rel_diff_pct']:.0f}% in plate current over the region this stage uses
({html.escape(ma['region'])}). The 90th percentile is {ma['p90_abs_rel_diff_pct']:.0f}%, and they diverge most near cutoff, which is exactly where bias tricks live.
That spread is a useful warning: <b>results near cutoff depend on which model and which fit you pick.</b> Dempwolf&ndash;Z&ouml;lzer's set is a fit to one measured valve, and Koren's is a fit to published curves.</p>

<h2>3. The stage and how it is solved</h2>
<p>B+ &rarr; R<sub>p</sub> &rarr; plate, AC-coupled into R<sub>L</sub> (the next grid leak). The cathode resistor R<sub>k</sub> is fully bypassed, and the input feeds the grid
through R<sub>s</sub>. Values: B+ = 250&nbsp;V, R<sub>p</sub> = 100&nbsp;k (22&nbsp;k for the 12AU7), R<sub>L</sub> = 1&nbsp;M, R<sub>s</sub> = 33&nbsp;k, and V<sub>G2</sub> = 100&nbsp;V for the pentode.
<b>These are illustrative textbook values, not Culture Vulture values.</b></p>
<p>DC point: solve V<sub>k</sub> = R<sub>k</sub>&middot;I<sub>K</sub>(&minus;V<sub>k</sub>, V<sub>a</sub>) with V<sub>a</sub> on the DC load line. Dynamic, memoryless transfer
(the coupling and bypass capacitors are treated as large, so V<sub>k0</sub> and V<sub>a0</sub> hold over a cycle):</p>
<div class="eq">v<sub>g</sub> = v<sub>in</sub> &minus; R<sub>s</sub>&thinsp;I<sub>G</sub>(v<sub>g</sub> &minus; V<sub>k0</sub>) &nbsp;&nbsp;&nbsp;
(B &minus; V<sub>a</sub>)/R<sub>p</sub> = I<sub>P</sub>(v<sub>g</sub> &minus; V<sub>k0</sub>, V<sub>a</sub> &minus; V<sub>k0</sub>) + (V<sub>a</sub> &minus; V<sub>a0</sub>)/R<sub>L</sub> &nbsp;&nbsp;&nbsp; y = V<sub>a</sub> &minus; V<sub>a0</sub></div>
<p>Each equation is monotone in its unknown, so a bracketed Newton&ndash;Raphson (bisection fallback) always converges. The tests check it
against the small-signal answer &minus;g<sub>m</sub>(R<sub>p</sub>&#8741;R<sub>L</sub>&#8741;r<sub>a</sub>) from the model's own derivatives, to within 0.2%.</p>
<table><tr><th>DC point, textbook R<sub>k</sub></th><th>B+</th><th>R<sub>p</sub></th><th>R<sub>k</sub> &Omega;</th><th>V<sub>G2</sub></th><th>V<sub>k</sub></th><th>V<sub>a</sub></th><th>I<sub>a</sub> mA</th><th>gain</th></tr>{dc_rows}</table>
<p><b>Bias as a knob.</b> To compare valves fairly I define bias by where the quiescent plate sits, V<sub>a0</sub>/B+, from hot (0.40) to cold (0.90).
For each setting I solve for the V<sub>gk0</sub> and the cathode resistor that produce it, so every bias point is a physically realisable stage.
"Drive" is expressed in multiples of A<sub>10%</sub>, the input amplitude that gives 10% THD at the centre bias (V<sub>a0</sub> = 0.55&thinsp;B+).</p>
<table><tr><th>centre bias (V<sub>a0</sub> = 0.55 B+)</th><th>V<sub>gk0</sub> V</th><th>R<sub>k</sub> &Omega;</th><th>gain</th><th>A<sub>10%</sub> V pk</th></tr>{cen_rows}</table>
{img(F['f2_transfer_vs_bias'], 'transfer curves vs bias')}
<p>The curves show the mechanism directly. Positive input swings drive the plate down toward its knee and into grid conduction (soft, through R<sub>s</sub>). Negative
swings push toward cutoff. A cold bias brings cutoff close, a hot bias brings the knee close, and only in between are the two clip points roughly symmetric.</p>

<h2>4. Harmonics vs drive and bias</h2>
{img(F['f3_thd_vs_drive'], 'THD vs drive')}
<p>At small drive every single-ended stage is H2-dominant, the familiar "valve warmth". As drive rises, H3 grows faster (roughly 3&times; the slope of H1
in dB versus 2&times; for H2) and overtakes H2 somewhere between 1&times; and 2&times;A<sub>10%</sub> at centre bias.</p>
{img(F['f4_harmonic_bars'], 'harmonic bars')}
<table><tr><th rowspan="2">drive 2&times;A<sub>10%</sub></th><th colspan="3">hot (V<sub>a0</sub>=0.45 B+)</th><th colspan="3">centre (0.55)</th><th colspan="3">cold (0.80)</th></tr>
<tr><th>THD</th><th>H2 / H3 dB</th><th>even&minus;odd dB</th><th>THD</th><th>H2 / H3 dB</th><th>even&minus;odd dB</th><th>THD</th><th>H2 / H3 dB</th><th>even&minus;odd dB</th></tr>
{''.join(f'<tr><td>{short[n]}</td>{bars_row(n)}</tr>' for n in bars)}</table>
{img(F['f5_bias_even_odd'], 'even minus odd vs bias')}
<table><tr><th>valve</th><th>bias of max odd dominance (V<sub>a0</sub>/B+), 2&times; drive</th><th>even&minus;odd there, dB</th><th>even&minus;odd at cold 0.85, 2&times;</th><th>even&minus;odd at cold 0.85, 1&times;</th><th>median even&minus;odd, 0.3&times; drive</th></tr>{bias_rows}</table>
<div class="key"><b>What the bias knob does (in this model).</b> At light drive the stage stays even-dominant at any bias. Once drive
reaches both clip points, bias decides the balance. There is a fairly narrow "symmetric" bias where H2 cancels and the sound turns odd and
harder. Toward cold bias the spectrum swings strongly back to even. Toward hot bias the triodes return to mildly even, but the pentode
stays odd-dominant across the range tested. The pentode's symmetric point sits at a hotter bias than the triodes', and at 1&times; drive it
is the most odd-dominant ({p1:.0f}&nbsp;dB vs {t1_lo:.0f} to {t1_hi:.0f}&nbsp;dB). Its plate current barely depends on plate voltage, so its
transfer curve stays straighter and then clips more abruptly at both ends. This gives a plausible mechanism for a bias control that steers the character from subtle to harsh. Whether the real unit's bias control works
this way is an open question that only measurements of a real unit can answer.</div>

<h2>5. Antialiasing on this nonlinearity</h2>
<p>Setup: the EF86 stand-in stage at centre bias and 2&times;A<sub>10%</sub> drive ({aa['amplitude_V']:.2f}&nbsp;V pk, about 21% THD), sampled at 48&nbsp;kHz.
The stage is tabulated once ({aa['table_points']} points, max error {aa['table_max_abs_err_V']*1e3:.2f}&nbsp;mV against the Newton solve, on a swing of about 200&nbsp;V).
Naive, oversampled and ADAA versions all use the same table. For first-order ADAA {cite('parker')}{cite('bilbao')}, the piecewise-linear table has an exact piecewise-quadratic antiderivative F:</p>
<div class="eq">y[n] = (F(x[n]) &minus; F(x[n&minus;1])) / (x[n] &minus; x[n&minus;1]), &nbsp;&nbsp; falling back to f((x[n] + x[n&minus;1])/2) when |x[n] &minus; x[n&minus;1]| &lt; &epsilon;</div>
<p>Metric: input frequencies sit on odd FFT bins (coherent sampling), with a rectangular window. Every true harmonic and every alias then lands on its own
bin, so the measurement floor is about {min(aa['measurement_floor_dB'].values()):.0f}&nbsp;dB. I report the energy in non-harmonic bins between 20&nbsp;Hz and 20&nbsp;kHz relative to the harmonic energy (lower is better).
Oversampling uses scipy's polyphase FIR (Kaiser &beta;=5, 20L+1 taps), and "long FIR" uses Kaiser &beta;=10 with 64L+1 taps.</p>
<table><tr><th>method</th>{''.join(f'<th>{float(f):.0f} Hz</th>' for f in f0s)}<th>H1 droop at 10 kHz, dB</th><th>Python ns/sample</th></tr>{aa_rows}</table>
{img(F['f7_alias_summary'], 'alias summary')}
{img(F['f6_alias_spectra'], 'alias spectra')}
<p><b>Scalar C cost</b> of the same table, ADAA and polyphase FIR. The C output was cross-checked against Python: alias figures match to within 1&nbsp;dB, and the naive lookup matches to under 1&nbsp;mV.
These numbers are one core, no SIMD, {html.escape(cb.get('cpu', '').replace('Model name:', '').strip())}:</p>
<pre>{cbench}</pre>
<p>Solving the stage directly with Newton for every sample cost {aa['python_ns_per_sample']['direct Newton solve (no table)']/1e3:.1f}&nbsp;&micro;s per sample in vectorised Python
(up to {aa['newton_iterations_vectorised']} iterations across a block). That is why a memoryless stage should become a table, or a fitted closed form, in the plugin.</p>
<div class="key"><b>Antialiasing takeaways.</b>
<ul>
<li>ADAA1 on its own is cheap (about {c_ad:.0f}&nbsp;ns per sample in C), but it only buys {adg_lo:.0f}&ndash;{adg_hi:.0f}&nbsp;dB on this curve. It also has a gentle built-in low-pass: H1 drops {abs(A[f10]['ADAA1']['fund_rel_ref_dB']):.1f}&nbsp;dB at 10&nbsp;kHz, plus a half-sample delay.</li>
<li>2&times; oversampling with ADAA1 ({f1(ad2)}&nbsp;dB at 5&nbsp;kHz) beats plain 2&times; ({f1(o2)}&nbsp;dB) by {o2-ad2:.0f}&nbsp;dB, at roughly the cost of 2&times; alone.</li>
<li>4&times; and 8&times; with scipy's default filter both stop near {f1(a4)} to {f1(o8)}&nbsp;dB at 5&nbsp;kHz, because the filter's stopband is the limit. The long filter takes 8&times; to {f1(o8l)}&nbsp;dB.
<b>So the oversampling filter design matters more than the factor</b>, which matches Kahles et al. {cite('kahles')}.</li>
<li>At 1&nbsp;kHz, naive is already at {n1k:.0f}&nbsp;dB and ADAA1 at {a1k:.0f}&nbsp;dB, and every oversampled option is below {os1k:.0f}&nbsp;dB. Aliasing is a high-frequency, high-drive problem.</li>
</ul></div>

<h2>6. Try it: drive and bias</h2>
<div id="demo">
<p><small>Precomputed transfer curves from the Python solver (21 bias points). The page interpolates between them and runs a DFT in your browser.</small></p>
<label>Valve: <select id="valve"></select></label>
<label>Drive (&times; A<sub>10%</sub>): <input id="drive" type="range" min="-1.3" max="0.6" step="0.01" value="0"> <span id="dv"></span></label>
<label>Bias (V<sub>a0</sub>/B+): <input id="bias" type="range" min="0" max="20" step="1" value="6"> <span id="bv"></span></label>
<p id="stats"></p>
<canvas id="tc" width="440" height="300"></canvas> <canvas id="hb" width="440" height="300"></canvas>
</div>

<h2>7. What this means for the plugin, and next steps</h2>
<ol>
<li><b>Bias belongs in the model, not as an EQ trick.</b> The even/odd balance comes from where the bias sits relative to the two clip points.
A plugin "bias" control should move the operating point of a solved stage, or index a family of tables as the demo does.</li>
<li><b>Core architecture to start from:</b> per stage, a 2-D table f(x, bias) with exact antiderivative, ADAA1 on top of 2&times; oversampling, and a properly designed
half-band or polyphase filter. That is about {c_ad2:.0f}&nbsp;ns per sample per stage in scalar C before SIMD, so a three-stage stereo chain uses about {chain:.1f}% of one core at 48&nbsp;kHz before any optimisation.
Interpolating bias between tables needs smoothing on the bias parameter. ADAA with a time-varying table is a known gotcha: changing F between n&minus;1 and n breaks the difference quotient.</li>
<li><b>C/C++ details we now know we need:</b> compute F and the difference quotient in double, or rescale, because float32 cancellation hurts when x[n] &asymp; x[n&minus;1]. Flush denormals in the
FIR states. No allocation on the audio thread, and the tables are built off-thread on parameter change. ADAA's half-sample delay matters if a dry/wet blend is offered.</li>
<li><b>What the memoryless model misses</b> (the next modelling step): blocking distortion from grid current charging the coupling capacitor, which is audible "sag" and
bias shift under heavy drive, the cathode bypass (frequency-dependent gain), Miller capacitance, and inter-stage filtering. Those need a stateful
model, either a DK-method state-space or a WDF stage with the valve as the nonlinear root, and stateful ADAA {cite('holters')} or oversampling.</li>
<li><b>Data we need:</b> 6AS6 plate curves (including g3 control) to fit a real model in place of the EF86 stand-in, and, most of all, measurements
of a real unit: sine sweeps and THD at several drive and bias settings, plus each mode. Without them, "sounds like" claims are off the table.</li>
<li><b>Verification plan:</b> once a circuit hypothesis exists, run it in ngspice with these same Koren subcircuits and null-test the Python/C++ model against it
(frequency response, harmonic profile, transient).</li>
</ol>

<h2>Sources</h2>
<ol>{src_list}</ol>
<p><small>Code: a NumPy package (models, stage solver, analysis, antialiasing), a scalar C benchmark and 23 pytest checks. Unpublished; available on request.
No code, presets or measurements from any commercial product were used.</small></p>

<script>
const D = {demo};
const sel = document.getElementById('valve'), dr = document.getElementById('drive'), bi = document.getElementById('bias');
Object.keys(D).forEach(k => {{ const o = document.createElement('option'); o.value = o.textContent = k; sel.appendChild(o); }});
function curve(v, qi) {{ return D[v].curves[qi]; }}
function interp(u, U, Y) {{
  if (u <= U[0]) return Y[0]; if (u >= U[U.length-1]) return Y[Y.length-1];
  const du = U[1]-U[0], t = (u-U[0])/du, i = Math.floor(t), f = t-i; return Y[i] + f*(Y[i+1]-Y[i]);
}}
function harmonics(v, qi, d) {{
  const N = 512, U = D[v].u, Y = curve(v, qi), y = new Float64Array(N); let m = 0;
  for (let n = 0; n < N; n++) {{ y[n] = interp(d*Math.sin(2*Math.PI*n/N), U, Y); m += y[n]; }}
  m /= N; const H = [];
  for (let k = 1; k <= 7; k++) {{ let re = 0, im = 0;
    for (let n = 0; n < N; n++) {{ const p = 2*Math.PI*k*n/N; re += (y[n]-m)*Math.cos(p); im -= (y[n]-m)*Math.sin(p); }}
    H.push(2*Math.hypot(re, im)/N); }}
  return H;
}}
function draw() {{
  const v = sel.value, qi = +bi.value, d = Math.pow(10, +dr.value), q = D[v].q[qi];
  document.getElementById('dv').textContent = d.toFixed(2) + '× (' + (d*D[v].A10).toFixed(2) + ' V pk)';
  document.getElementById('bv').textContent = q.toFixed(3);
  const U = D[v].u, Y = curve(v, qi), c = document.getElementById('tc').getContext('2d');
  c.clearRect(0,0,440,300); c.font = '11px sans-serif';
  const ymax = Math.max(...D[v].curves.map(r => Math.max(...r.map(Math.abs))));
  const X = u => 20 + (u+4)/8*410, Yp = y => 150 - y/ymax*135;
  c.strokeStyle = '#ccc'; c.beginPath(); c.moveTo(20,150); c.lineTo(430,150); c.moveTo(X(0),10); c.lineTo(X(0),290); c.stroke();
  c.fillStyle = 'rgba(80,120,255,0.12)'; c.fillRect(X(-d), 10, X(d)-X(-d), 280);
  c.strokeStyle = '#c33'; c.lineWidth = 2; c.beginPath(); U.forEach((u,i) => i ? c.lineTo(X(u),Yp(Y[i])) : c.moveTo(X(u),Yp(Y[i]))); c.stroke(); c.lineWidth = 1;
  c.fillStyle = '#333'; c.fillText('transfer curve (shaded = input swing), input in ×A10', 24, 22);
  const H = harmonics(v, qi, d), b = document.getElementById('hb').getContext('2d');
  b.clearRect(0,0,440,300); b.font = '11px sans-serif';
  const thd = Math.sqrt(H.slice(1).reduce((s,h) => s + (h/H[0])**2, 0));
  let ev = 0, od = 0; H.forEach((h,i) => {{ if (i>0) {{ if ((i+1)%2==0) ev += (h/H[0])**2; else od += (h/H[0])**2; }} }});
  for (let k = 2; k <= 7; k++) {{ const db = Math.max(20*Math.log10(H[k-1]/H[0]+1e-12), -100), h = (db+100)/100*250;
    b.fillStyle = k%2 ? '#d33' : '#36c'; b.fillRect(40+(k-2)*65, 280-h, 45, h); b.fillStyle = '#333';
    b.fillText('H'+k, 55+(k-2)*65, 294); b.fillText(db.toFixed(0)+' dB', 44+(k-2)*65, 274-h); }}
  b.fillText('harmonics re H1 (blue even, red odd), floor -100 dB', 24, 16);
  document.getElementById('stats').textContent = 'THD ' + (100*thd).toFixed(1) + '%   even−odd ' + (10*Math.log10((ev+1e-30)/(od+1e-30))).toFixed(1) + ' dB (H2..H7)';
}}
[sel, dr, bi].forEach(e => e.addEventListener('input', draw)); draw();
</script>
</body></html>
"""
    with open(os.path.join(root, "index.html"), "w") as fh:
        fh.write(page)
    return os.path.join(root, "index.html")
