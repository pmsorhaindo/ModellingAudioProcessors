"""Wave digital filter version of the 12AX7 stage (#12). It reuses the DK prototype's circuit values, triode, Newton
solver, resamplers and validation harness, imported from prototypes/fullstage (dk, spice, analysis)."""
import os
import sys

FULLSTAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "fullstage")
if FULLSTAGE not in sys.path:
    sys.path.insert(0, FULLSTAGE)
