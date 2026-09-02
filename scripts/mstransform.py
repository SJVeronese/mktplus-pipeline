#!/usr/bin/env python3
"""
mstransform.py – Measurement set splitting worker for the MeerKAT+ pipeline.

Extracts a single field from an input measurement set into a new MS
using casatasks.mstransform, with optional selection on spectral window,
channels, correlations, antennas, and scans, and optional frequency
and/or time averaging.

An on-the-fly calibration step can also be applied immediately after
the split (casatasks.applycal) by setting --otfcal true.  In that case
the script scans a fixed calibration-table directory for the most recent
K, G, and B tables whose names match the supplied label and applies them
in order, writing corrected visibilities to the CORRECTED_DATA column of
the output MS.

Usage
-----
    python3 mstransform.py --ms <path> --output <path> [options]

Arguments
---------
--ms : str
    Absolute path to the input measurement set (including .ms extension).
--datacolumn : str
    Data column to read from the input MS ('data', 'corrected', 'model').
--output : str
    Absolute path for the output measurement set.
--antenna : str
    CASA antenna selection string (e.g. 'm000&m001'). Leave empty to
    select all antennas.
--correlation : str
    Correlations to keep (e.g. 'XX,YY'). Leave empty for all.
--field : str
    Field ID or name to extract.
--scan : str
    Comma-separated scan IDs or ranges. Leave empty for all.
--spw : str
    CASA spw selection string. Leave empty for all channels.
--chanavg : bool
    Enable channel averaging (default: False).
--chanbin : int
    Number of channels to average per output channel (default: 1).
--timeavg : bool
    Enable time averaging (default: False).
--timebin : str
    Time averaging window with units (e.g. '8s'). Required when
    --timeavg is True.
--otfcal : bool
    Apply on-the-fly calibration after the split (default: False).
--label_cal : str
    Calibration label used to locate the correct tables in the
    caltables directory. Required when --otfcal is True.
"""

import argparse
import casatasks
import casatools
import glob
import os
import re

# casa logging
casalog = casatools.logsink()
casalog.showconsole(True)   # echo CASA log to stdout

# store the input arguments
parser = argparse.ArgumentParser(description="Process MSTRANSFORM arguments.")
parser.add_argument("--ms", default="")
parser.add_argument("--datacolumn", default="data")
parser.add_argument("--output", default="")
parser.add_argument("--antenna", default="")
parser.add_argument("--correlation", default="")
parser.add_argument("--field", default="")
parser.add_argument("--scan", default="")
parser.add_argument("--spw", default="")
parser.add_argument("--chanavg", type=lambda v: v.lower() == 'true', default=False)
parser.add_argument("--chanbin", type=int, default=1)
parser.add_argument("--timeavg", type=lambda v: v.lower() == 'true', default=False)
parser.add_argument("--timebin", default="")
parser.add_argument("--otfcal", type=lambda v: v.lower() == 'true', default=False)
parser.add_argument("--label_cal", default="")


def find_latest_caltables(caltable_dir, label_cal):
    """
    Locate the most recent K, G, and B calibration tables for a given label.

    Scans caltable_dir for files matching the pattern
    ``*<label_cal>_<TYPE><N>.cal`` where TYPE is one of K, G, B and N is
    an integer counter.  For each calibration type the file with the
    highest counter is selected, mimicking the accumulation logic of the
    crosscal worker.

    Parameters
    ----------
    caltable_dir : str
        Absolute path to the directory containing the calibration tables.
    label_cal : str
        Calibration label used to construct the search pattern
        (e.g. 'pipeline_1gc1').

    Returns
    -------
    list of str
        Ordered list of absolute paths [K_table, G_table, B_table],
        containing only the types for which at least one matching file
        was found.

    Raises
    ------
    RuntimeError
        Raised by the calling code (not this function) if the returned
        list is empty, i.e. no tables were found for the given label.
    """
    
    gaintable = []
    for step in ('K', 'G', 'B'):
        pattern = os.path.join(caltable_dir, f'*{label_cal}_{step}*')
        matches = glob.glob(pattern)
        if not matches:
            continue

        def get_counter(path, label_cal=label_cal, step=step):
            m = re.search(
                rf'{re.escape(label_cal)}_{step}(\d+)\.cal$',
                os.path.basename(path)
            )
            return int(m.group(1)) if m else 0

        gaintable.append(max(matches, key=get_counter))

    return gaintable

# parse arguments
args = parser.parse_args()

# run mstransform
casatasks.mstransform(vis=args.ms,
                      datacolumn=args.datacolumn,
                      outputvis=args.output,
                      antenna=args.antenna,
                      correlation=args.correlation,
                      field=args.field,
                      scan=args.scan,
                      spw=args.spw,
                      chanaverage=args.chanavg,
                      chanbin=args.chanbin,
                      timeaverage=args.timeavg,
                      timebin=args.timebin)

# apply on-the-fly calibration
if args.otfcal:
    # create the calibration table list
    caltable_dir = '/output/caltables'
    gaintable = find_latest_caltables(caltable_dir, args.label_cal)

    if not gaintable:
        raise RuntimeError(
            f'No calibration tables found for label "{args.label_cal}" '
            f'in {caltable_dir}'
        )

    print(f'Applying calibration tables: {[os.path.basename(t) for t in gaintable]}')
    casatasks.applycal(
        vis       = args.output,
        gaintable = gaintable,
    )
