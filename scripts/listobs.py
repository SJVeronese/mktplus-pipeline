#!/usr/bin/env python3
"""
listobs.py – Observation metadata extractor for the MeerKAT+ pipeline.

Runs CASA listobs on a measurement set and writes a human-readable
summary of the observation (fields, scans, spectral windows, antennas,
and exposure times) to a plain-text file.

Usage
-----
    python3 listobs.py <ms> <output>

Arguments
---------
ms : str
    Absolute path to the input measurement set (including .ms extension).
output : str
    Absolute path to the output text file where the listing will be written.
    An existing file at this path is silently overwritten.

Output
------
A plain-text file containing the full CASA listobs report for the
given measurement set.
"""

import casatasks
import casatools
import sys

# casa logging
casalog = casatools.logsink()
casalog.showconsole(True)   # echo CASA log to stdout

# store the input arguments
ms = sys.argv[1]
output = sys.argv[2]

# run listobs
casatasks.listobs(vis=ms, verbose=True, listfile=output, overwrite=True)