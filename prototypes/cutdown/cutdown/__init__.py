"""Cut-down 12AX7 stage prototype (#23): 3 states, 2-D lookup table, 2x IIR half-band resampling."""
import os
import sys

FULLSTAGE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "fullstage"))
if FULLSTAGE_DIR not in sys.path:
    sys.path.append(FULLSTAGE_DIR)
