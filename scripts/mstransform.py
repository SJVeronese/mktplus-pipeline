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
    For each calibration type K, G, B, find the table under caltable_dir
    whose name matches <label_cal>_<TYPE><N>.cal with the highest counter N.
    Returns an ordered list [K_table, G_table, B_table], omitting any type
    for which no table is found.
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
