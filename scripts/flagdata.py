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