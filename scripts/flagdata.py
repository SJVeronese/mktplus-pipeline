#!/usr/bin/env python3
"""
flagdata.py – Flagging worker for the MeerKAT+ pipeline.

Applies a sequence of flagging operations to a CASA measurement set
using casatasks.flagdata:

  1. Manual flags  – autocorrelations, user-specified antennas, scans,
                     spectral windows, and time ranges.
  2. Shadow flags  – antennas physically blocked by a neighbouring dish.
  3. Clip flags    – visibilities whose amplitude is exactly zero
                     (correlator dropouts).
  4. RFI flags     – automated detection in the time-frequency plane
                     via the CASA tfcrop algorithm.

A named flag backup ('preliminary') is saved at the end with
casatasks.flagmanager so the pipeline can verify completion on restart
without re-running the full flagging sequence.

Usage
-----
    python3 flagdata.py --ms <path> [options]

Arguments
---------
--ms : str
    Absolute path to the input measurement set (including .ms extension).
--antenna : str
    CASA antenna selection string for manual flagging (e.g. 'm010,m045').
    Leave empty to skip antenna-based manual flags.
--autocorr : bool
    Flag autocorrelations (default: True).
--clip : bool
    Flag zero-amplitude visibilities (default: True).
--rfi : bool
    Run CASA tfcrop automated RFI detection (default: True).
--shadow : bool
    Flag shadowed antennas (default: True).
--scan : str
    Comma-separated scan IDs or ranges for manual flagging (e.g. '1,3,5~8').
    Leave empty to skip.
--spw : str
    CASA spw syntax for channel/spw manual flagging (e.g. '0:0~50').
    Leave empty to skip.
--time : str
    CASA timerange syntax for time-range manual flagging.
    Leave empty to skip.
"""

import argparse
import casatasks
import casatools
import numpy as np

# casa logging
casalog = casatools.logsink()
casalog.showconsole(True)   # echo CASA log to stdout

# store the input arguments
parser = argparse.ArgumentParser(description="Process MSTRANSFORM arguments.")
parser.add_argument("--ms", default="")
parser.add_argument("--antenna", default="")
parser.add_argument("--autocorr", type=lambda v: v.lower() == 'true', default=True)
parser.add_argument("--clip",     type=lambda v: v.lower() == 'true', default=True)
parser.add_argument("--rfi",      type=lambda v: v.lower() == 'true', default=True)
parser.add_argument("--shadow",   type=lambda v: v.lower() == 'true', default=True)
parser.add_argument("--scan", default="")
parser.add_argument("--spw", default="")
parser.add_argument("--time", default="")

# parse arguments
args = parser.parse_args()

# flag autocorrelations
if args.autocorr: casatasks.flagdata(vis=args.ms, mode='manual', autocorr=True)

# flag shadowed antennas
if args.shadow: casatasks.flagdata(vis=args.ms, mode='shadow')

# flag zero-amplitude data
if args.clip: casatasks.flagdata(vis=args.ms, mode='clip', clipzeros=True)

# flag user-supplied values
if any(x != '' for x in [args.spw, args.antenna, args.time, args.scan]):
    casatasks.flagdata(vis=args.ms, mode='manual', autocorr=False,
                    spw=args.spw, antenna=args.antenna,
                    timerange=args.time, scan=args.scan)

# automated RFI flagging
if args.rfi: casatasks.flagdata(vis=args.ms, mode='tfcrop', datacolumn='data')

# save a named backup so the pipeline can check completion
casatasks.flagmanager(vis=args.ms, mode='save', versionname='preliminary')