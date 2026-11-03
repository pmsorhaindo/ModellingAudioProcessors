"""Published valve model parameter sets, each with its source.

Rule for this file: every number comes from a citable source, copied
verbatim. Nothing is fitted or tweaked here. If a valve has no published
fit, it is not in this file; we use the nearest documented valve and label it
as a stand-in in the analysis code (see STAND_INS).

Local copies of the source libraries live in ../sources/ (with SHA256SUMS),
downloaded 2026-10-18.
"""

KOREN_ARTICLE = {
    "title": "Improved vacuum tube models for SPICE simulations (Glass Audio 8(5), 1996; web version updated 2003)",
    "author": "Norman L. Koren",
    "url": "https://www.normankoren.com/Audio/Tubemodspice_article.html",
}
KOREN_LIB = {
    "title": "Tube.lib in Tubemods.zip (Koren's own PSpice tube library)",
    "author": "Norman L. Koren",
    "url": "https://www.normankoren.com/Audio/Tubemods.zip",
    "local": "sources/koren_Tube.lib",
}
LTWIKI_KOREN = {
    "title": "nk_pentode.sub / Tubes_Koren.lib, LTspice wiki library (Koren equations, parameters fitted to manufacturer data sheets by library contributors, 2008)",
    "author": "LTspice wiki contributors (models in Norman Koren's format)",
    "url": "https://ltwiki.org/files/LTspiceIV/lib/sub/nk_pentode.sub",
    "local": "sources/ltwiki_nk_pentode.sub",
}
DEMPWOLF = {
    "title": "A Physically-Motivated Triode Model for Circuit Simulations, Proc. DAFx-11, Paris, 2011, Table 1",
    "author": "Kristjan Dempwolf, Udo Zoelzer",
    "url": "https://www.dafx.de/paper-archive/2011/Papers/76_e.pdf",
}

# ---- Koren triode: MU, EX, KG1, KP, KVB (KG1 in ohm-ish units, V and A) ----
KOREN_TRIODE = {
    "12AX7": dict(MU=100.0, EX=1.4, KG1=1060.0, KP=600.0, KVB=300.0,
                  source=KOREN_ARTICLE, note="Koren Table 1 and Tube.lib 'NEW MODEL'"),
    "12AU7": dict(MU=21.5, EX=1.3, KG1=1180.0, KP=84.0, KVB=300.0,
                  source=KOREN_ARTICLE, note="Koren Table 1 and Tube.lib 'NEW MODEL'"),
}

# Koren grid-current network from Tube.lib triode subcircuits:
#   R1 grid->node5 RGI=2000 ohm ; D3 node5->cathode, .MODEL DX D(IS=1N RS=1 ...)
KOREN_GRID = dict(RGI=2000.0, IS=1e-9, RS=1.0, N=1.0, source=KOREN_LIB)

# ---- Koren pentode: MU, EX, KG1, KG2, KP, KVB ----
KOREN_PENTODE = {
    # EF86 is the Culture Vulture input valve per sources (see references/).
    # NOTE: KG2=4500 appears as a library-wide default in this library, not a
    # per-valve fit, so screen current from it is not trustworthy. We hold the
    # screen at a fixed (bypassed, stiff) voltage so KG2 only affects the DC
    # cathode current, and we say so in the report.
    "EF86": dict(MU=34.90, EX=1.350, KG1=2648.1, KG2=4500.0, KP=222.06, KVB=4.7,
                 VCT=0.0, source=LTWIKI_KOREN,
                 note="'Philips data sheet AKA 6CF8 and 6267 Z729 EF806', library format LTSpice 02-Jun-2008"),
    # Koren's own small-signal pentode fit (pentode section of 6AN8), Tube.lib.
    "6AN8P": dict(MU=45.0, EX=1.35, KG1=520.0, KG2=120.0, KP=120.0, KVB=18.0,
                  VCT=0.0, source=KOREN_LIB, note="Tube.lib SUBCKT 6AN8P (pentode section)"),
}

# ---- Dempwolf & Zoelzer triode (12AX7 only), Table 1, tube 'RSD-1' ----
DEMPWOLF_TRIODE = {
    "12AX7_RSD1": dict(G=2.242e-3, mu=103.2, gamma=1.26, C=3.40,
                       Gg=6.177e-4, xi=1.314, Cg=9.901, Ig0=8.025e-8,
                       source=DEMPWOLF, note="Table 1, column RSD-1 (individual measured 12AX7)"),
}

# Valves in the original unit (per references/schematics-and-manuals.md) for
# which NO published model parameters were found (searched Koren's site and
# library, the LTspice wiki Koren libraries, web search, 2026-10-18).
STAND_INS = {
    "6AS6": dict(stand_in="EF86 (Koren-format fit); Koren's own 6AN8P pentode fit is kept in the code as a second reference",
                 why="No published Koren/other fit found. 6AS6 is a sharp-cutoff dual-control "
                     "pentode (Tung-Sol data: Va=Vg2=120 V, Vg1=-2 V, Vg3=-3 V, Ia=3.6 mA, "
                     "gm(g1)=1850 umho, gm(g3)=810 umho). Its suppressor-grid control is NOT "
                     "modelled by any Koren pentode equation.",
                 datasheet="https://frank.pocnet.net/sheets/127/6/6AS6.pdf"),
    "5963": dict(stand_in="12AU7 (Koren)",
                 why="No published fit found. RCA data: medium-mu twin triode, mu=21, "
                     "rp~6600 ohm, gm=3200 umho at Va=67.5 V, Vg=0, Ia=8.5 mA; widely "
                     "described as close to 12AU7 near typical bias.",
                 datasheet="https://frank.pocnet.net/sheets/049/5/5963.pdf"),
}
