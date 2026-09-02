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