#!/usr/bin/env python3
"""
crosscal.py – Order-driven 1GC cross-calibration worker for the MeerKAT+ pipeline.

Solves for instrumental delays (K), complex gains (G), and the bandpass
response (B) in a user-defined sequence using CASA gaincal and bandpass.
The calibration order is specified as a string of single-letter step
codes (e.g. 'KGB' or 'KGBKGB'); each step reuses all solutions derived
in previous steps as prior corrections.

After a B step the active solution set is reset to {B only}, because the
bandpass solution subsumes the preceding delay and gain corrections.  This
means that in a double-pass sequence 'KGBKGB' the second K and G steps
are solved against the first-pass bandpass, giving cleaner solutions.

Optionally, the bandpass amplitude and phase can be smoothed with a
running-mean kernel (--smooth-window) to suppress noise in low-S/N
solutions, and diagnostic PNG plots of all gain tables can be generated
(--plotgains).

The flux scale is set with casatasks.setjy before any solving: a custom
polynomial model is used for the standard MeerKAT primary calibrator
J0408-6545; all other calibrators fall back to the Perley-Butler 2017
standard.

Usage
-----
    python3 crosscal.py --ms <path> [options]

Arguments
---------
--ms : str
    Absolute path to the input measurement set (including .ms extension).
--outname : str
    Base name for the output calibration tables (e.g. 'pipeline_1gc1').
--outdir : str
    Directory where calibration tables (.cal) will be written.
--plotdir : str
    Directory where diagnostic PNG plots will be written.
--refant : str
    Reference antenna name or index (e.g. 'm000').
--band : str
    Observing band ('L' or 'S'). Used to set the frequency range for the
    J0408-6545 flux model.
--calibrator : str
    Source name of the primary calibrator exactly as printed by listobs.
--fillgaps : int
    Number of flagged channels over which to interpolate in the bandpass
    table.  0 disables gap-filling.
--smooth-window : int
    Width of the running-mean kernel (in channels) applied to the bandpass
    amplitude and phase after solving.  1 disables smoothing.
--plotgains : bool
    Generate diagnostic PNG plots for all solved tables (default: False).
--order : str
    Calibration sequence as a string of step codes, e.g. 'KGB' or 'KGBKGB'.
--calmode : str (repeatable)
    Per-step calmode ('p', 'a', or 'ap').  Pass once per step in order.
--solint : str (repeatable)
    Per-step solution interval (e.g. '120s', 'inf').  Pass once per step.
--combine : str (repeatable)
    Per-step data axes to combine before solving (e.g. '', 'scan').
    Pass once per step.
"""

import argparse
import casatasks
import casatools
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import os

# matplotlib style
plt.rc('font', size=14)
plt.rcParams['mathtext.default']      = 'regular'
plt.rcParams['xtick.direction']       = 'in'
plt.rcParams['xtick.major.size']      = 10
plt.rcParams['xtick.major.width']     = 1
plt.rcParams['xtick.minor.size']      = 5
plt.rcParams['xtick.minor.width']     = 1
plt.rcParams['xtick.top']             = True
plt.rcParams['ytick.direction']       = 'in'
plt.rcParams['ytick.major.size']      = 10
plt.rcParams['ytick.major.width']     = 1
plt.rcParams['ytick.minor.size']      = 5
plt.rcParams['ytick.minor.width']     = 1
plt.rcParams['ytick.right']           = True
plt.rcParams['axes.formatter.use_mathtext'] = True

# casa logging
casalog = casatools.logsink()
casalog.showconsole(True)   # echo CASA log to stdout

# argument parsing
parser = argparse.ArgumentParser(description="Order-driven cross-calibration worker.")
parser.add_argument("--ms",            default="")
parser.add_argument("--outname",       default="pipeline_1gc1")
parser.add_argument("--outdir",        default=os.getcwd())
parser.add_argument("--plotdir",       default=os.getcwd())
parser.add_argument("--refant",        default="m000")
parser.add_argument("--band",          default="L")
parser.add_argument("--calibrator",    default="J0408-6545")
parser.add_argument("--fillgaps",      default=0,     type=int)
parser.add_argument("--smooth-window", default=3,     type=int)
parser.add_argument("--plotgains",     default=False, type=lambda v: v.lower() == 'true')
parser.add_argument("--order",         default="KGB")
# one --calmode/--solint/--combine flag per step, repeated in order
parser.add_argument("--calmode",  action="append", default=[])
parser.add_argument("--solint",   action="append", default=[])
parser.add_argument("--combine",  action="append", default=[])

args = parser.parse_args()

ms            = args.ms
outname       = args.outname
outdir        = args.outdir
plotdir       = args.plotdir
refant        = args.refant
band          = args.band
calibrator    = args.calibrator
fillgaps      = args.fillgaps
smooth_window = args.smooth_window
plotgains     = args.plotgains

# ensure output directories exist
os.makedirs(outdir,  exist_ok=True)
os.makedirs(plotdir, exist_ok=True)

# resolve per-step parameters
steps = list(args.order.upper())
n     = len(steps)

# pad user-supplied lists with '' so they match the number of steps;
# truncate if the user supplied too many
calmode_in = (args.calmode + [''] * n)[:n]
solint_in  = (args.solint  + [''] * n)[:n]
combine_in = (args.combine + [''] * n)[:n]

# apply per-step defaults for any entry that is empty / not supplied:
#   calmode : 'a'    for K,  'ap'   for G and B
#   solint  : '120s' for K/G, 'inf' for B
#   combine : 'scan' for K/B, ''    for G
calmode_list = [v if v != '' else ('ap' if s in ('G', 'B') else 'a')   for s, v in zip(steps, calmode_in)]
solint_list  = [v if v != '' else ('inf' if s == 'B' else '120s')       for s, v in zip(steps, solint_in)]
combine_list = [v if v != '' else ('scan' if s in ('K', 'B') else '')   for s, v in zip(steps, combine_in)]

print(f'Calibration order : {" -> ".join(steps)}')
for i, (s, cm, si, co) in enumerate(zip(steps, calmode_list, solint_list, combine_list)):
    print(f'step {i+1:2d}  {s}  calmode={cm!r:4s}  solint={si!r:6s}  combine={co!r}')

# antenna names (used in plots)
_tb = casatools.table()
_tb.open(ms + '/ANTENNA')
ant_names = list(_tb.getcol('NAME'))
_tb.close()
n_ant = len(ant_names)

CORR_LABELS = ['XX', 'YY']
CORR_COLORS = ['blue', 'crimson']

# flux model helpers
def casa_flux_model(lnunu0, iref, *args):
    """
    Evaluate the CASA polynomial flux-density model.

    Computes S(nu) = iref * (nu/nu0)^(sum_k args[k] * log10(nu/nu0)^k),
    which is the functional form used by CASA setjy for spectral models.

    Parameters
    ----------
    lnunu0 : array-like
        log10(nu / nu0) evaluated at the frequencies of interest.
    iref : float
        Flux density at the reference frequency nu0, in Jy.
    *args : float
        Polynomial coefficients [alpha, beta, gamma, ...] of the
        log-log spectral model, starting from the first-order term.

    Returns
    -------
    numpy.ndarray
        Flux density in Jy at each input frequency.
    """
    
    exponent = np.sum([arg * (lnunu0 ** power) for power, arg in enumerate(args)], axis=0)
    return iref * (10 ** lnunu0) ** exponent
    

def fit_flux_model(nu, s, nu0, sigma, sref, order=5):
    """
    Fit a CASA polynomial flux-density model to a set of flux measurements.

    Iteratively attempts a least-squares fit of decreasing polynomial order
    until scipy.optimize.curve_fit converges, then pads the coefficient
    vector to the requested order.  Falls back to a weighted mean if all
    fits fail.

    Parameters
    ----------
    nu : array-like
        Observing frequencies in Hz.
    s : array-like
        Flux density measurements in Jy at each frequency.
    nu0 : float
        Reference frequency in Hz.
    sigma : array-like
        Uncertainty on each flux measurement in Jy.
    sref : float
        Initial guess for the flux density at nu0 in Jy.
    order : int, optional
        Maximum polynomial order of the spectral model (default: 5).

    Returns
    -------
    list
        [nu0, c0, c1, ..., c_order] where nu0 is the reference frequency
        in Hz and the remaining elements are the fitted polynomial
        coefficients.
    """
    
    from scipy.optimize import curve_fit
    init = [sref, -0.7] + [0] * (order - 1)
    lnunu0 = np.log10(nu / nu0)
    for fitorder in range(order, -1, -1):
        try:
            popt, _ = curve_fit(casa_flux_model, lnunu0, s,
                                p0=init[:fitorder + 1], sigma=sigma)
        except RuntimeError:
            continue
        else:
            coeffs = np.pad(popt, (0, order - fitorder), 'constant')
            return [nu0] + coeffs.tolist()
    coeffs = [np.average(s, weights=1. / sigma ** 2)] + [0] * order
    return [nu0] + coeffs.tolist()
    

def convert_flux_model(nu, a, b, c, d, Reffreq):
    """
    Convert a log-MHz polynomial flux model to the CASA setjy coefficient format.

    Evaluates the flux density S(nu) = 10^(a + b*log10(nu/MHz)
    + c*log10(nu/MHz)^2 + d*log10(nu/MHz)^3) across the supplied
    frequency grid and re-fits it as a CASA polynomial model centred
    on Reffreq using fit_flux_model.

    This is used to translate the published J0408-6545 model coefficients
    (defined in log-MHz space) into the format expected by casatasks.setjy.

    Parameters
    ----------
    nu : array-like
        Frequency grid in Hz over which to evaluate and re-fit the model.
    a, b, c, d : float
        Coefficients of the log-MHz polynomial flux model.
    Reffreq : float
        Reference frequency in Hz for the output CASA model.

    Returns
    -------
    list
        [nu0, fluxdensity, spix0, spix1, spix2] ready to be passed
        directly to casatasks.setjy.
    """
    
    MHz = 1e6
    S = 10 ** (a + b * np.log10(nu / MHz) + c * np.log10(nu / MHz) ** 2
                + d * np.log10(nu / MHz) ** 3)
    return fit_flux_model(nu, S, Reffreq, np.ones_like(nu), sref=1, order=3)


# bandpass smoother
def smooth_bandpass(btab, nchan):
    """
    Apply a running-mean smoothing kernel to a CASA bandpass calibration table.

    Replaces the CPARAM column of the table with values smoothed by a
    boxcar kernel of width nchan channels.  Amplitude and unwrapped phase
    are smoothed independently to avoid phase-wrap artefacts.  Flagged
    channels are excluded from the running mean via binary-weight
    convolution; unflagged channels adjacent to flagged gaps contribute
    their full weight so that the gap region is filled by extrapolation
    from neighbours.

    The table is modified in place.

    Parameters
    ----------
    btab : str
        Absolute path to the CASA bandpass calibration table to smooth.
    nchan : int
        Width of the boxcar smoothing kernel in channels.  Values <= 1
        are a no-op (the table is not opened).
    """

    tb = casatools.table()
    tb.open(btab, nomodify=False)
    cparam = tb.getcol('CPARAM')   # (n_corr, n_chan, n_row)
    flags  = tb.getcol('FLAG')
    n_corr, n_chan, n_row = cparam.shape
    kernel = np.ones(nchan)

    for row in range(n_row):
        for corr in range(n_corr):
            sol = cparam[corr, :, row]
            flg = flags[corr, :, row].astype(bool)
            wt  = (~flg).astype(float)
            amp = np.abs(sol)
            pha = np.unwrap(np.angle(sol))

            wt_sum  = np.convolve(wt,                     kernel, mode='same')
            amp_sum = np.convolve(np.where(flg, 0., amp), kernel, mode='same')
            pha_sum = np.convolve(np.where(flg, 0., pha), kernel, mode='same')

            safe_wt = np.where(wt_sum > 0, wt_sum, 1.0)
            good    = wt_sum > 0
            amp_s   = np.where(good, amp_sum / safe_wt, amp)
            pha_s   = np.where(good, pha_sum / safe_wt, pha)

            cparam[corr, :, row] = amp_s * np.exp(1j * pha_s)

    tb.putcol('CPARAM', cparam)
    tb.close()
    print(f'[crosscal] {nchan}-channel running mean applied -> {btab}')


# diagnostic plot functions
def plot_delays(ktab, label, outbase):
    """
    Plot delay solutions (ns) versus antenna for XX and YY correlations.

    Reads the FPARAM column of a CASA delay (K) calibration table and
    produces a single scatter plot with both correlations overlaid,
    coloured blue (XX) and crimson (YY).  Flagged solutions are excluded.

    Parameters
    ----------
    ktab : str
        Absolute path to the CASA delay calibration table.
    label : str
        Human-readable label used in the plot title (e.g. 'K1').
    outbase : str
        Output file path without extension.  The plot is saved as
        ``<outbase>.png`` at 300 dpi.
    """
    
    tb = casatools.table()
    tb.open(ktab)
    fparam = tb.getcol('FPARAM')   # (n_corr, 1, n_row)
    flags  = tb.getcol('FLAG')
    ant1   = tb.getcol('ANTENNA1')
    tb.close()

    fig, ax = plt.subplots(figsize=(10, 4))
    for corr in range(fparam.shape[0]):
        good = ~flags[corr, 0, :].astype(bool)
        ax.plot(ant1[good], fparam[corr, 0, good], 'o', ms=5,
                color=CORR_COLORS[corr], label=CORR_LABELS[corr], alpha=0.8)

    ax.set_xticks(range(n_ant))
    ax.set_xticklabels(ant_names, rotation=60, fontsize=9, ha='center')
    ax.set_xlabel('Antenna')
    ax.set_ylabel('Delay (ns)')
    ax.set_title(f'Delay solutions — {label}')
    ax.legend(prop={'size': 12}, frameon=False)
    plt.savefig(f'{outbase}.png', dpi=300, bbox_inches='tight')
    plt.close()
    

def plot_gains(gtab, label, outbase):
    """
    Plot gain solutions (amplitude and phase) versus time for XX and YY.

    Reads the CPARAM column of a CASA gain (G) calibration table and
    produces a two-panel figure (amplitude on top, phase in degrees below)
    with time on the x-axis in minutes from the start of the observation.
    Both correlations are overlaid using colour coding (blue XX, crimson YY).
    Flagged solutions are excluded.

    Parameters
    ----------
    gtab : str
        Absolute path to the CASA gain calibration table.
    label : str
        Human-readable label used in the plot title (e.g. 'G1').
    outbase : str
        Output file path without extension.  The plot is saved as
        ``<outbase>.png`` at 300 dpi.
    """
    
    tb = casatools.table()
    tb.open(gtab)
    cparam = tb.getcol('CPARAM')   # (n_corr, 1, n_row)
    flags  = tb.getcol('FLAG')
    times  = tb.getcol('TIME')
    tb.close()

    t_min  = (times - times.min()) / 60.0
    n_corr = cparam.shape[0]

    fig = plt.figure(figsize=(10, 8))
    gs  = fig.add_gridspec(2, 1, hspace=0.01)
    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1])

    for corr in range(n_corr):
        sol  = cparam[corr, 0, :]
        good = ~flags[corr, 0, :].astype(bool)
        ax1.plot(t_min[good], np.abs(sol[good]),               '.', ms=3, alpha=0.5,
                 color=CORR_COLORS[corr], label=CORR_LABELS[corr])
        ax2.plot(t_min[good], np.degrees(np.angle(sol[good])), '.', ms=3, alpha=0.5,
                 color=CORR_COLORS[corr], label=CORR_LABELS[corr])

    ax1.set_ylabel('Amplitude')
    ax1.set_title(f'Gain solutions — {label}')
    ax1.legend(prop={'size': 12}, frameon=False)
    ax2.set_ylabel('Phase (deg)')
    ax2.set_xlabel('Time (min from start)')
    ax2.legend(prop={'size': 12}, frameon=False)
    plt.savefig(f'{outbase}.png', dpi=300, bbox_inches='tight')
    plt.close()


def plot_bandpass(btab, label, outbase):
    """
    Plot bandpass solutions (amplitude and phase) versus channel for all antennas.

    Reads the CPARAM column of a CASA bandpass (B) calibration table and
    produces a two-panel figure (amplitude on top, phase in degrees below)
    for each correlation separately.  Each antenna is drawn as a separate
    line, coloured by antenna index from the 'coolwarm' colourmap.
    Flagged channels are excluded per antenna per row.

    Parameters
    ----------
    btab : str
        Absolute path to the CASA bandpass calibration table.
    label : str
        Human-readable label used in the plot title (e.g. 'B1 (smoothed)').
    outbase : str
        Output file path without extension.  One PNG is produced per
        correlation and saved as ``<outbase>_XX.png`` and
        ``<outbase>_YY.png`` at 300 dpi.
    """
    
    tb = casatools.table()
    tb.open(btab)
    cparam = tb.getcol('CPARAM')   # (n_corr, n_chan, n_row)
    flags  = tb.getcol('FLAG')
    ant1   = tb.getcol('ANTENNA1')
    tb.close()

    n_corr, n_chan, n_row = cparam.shape
    chans = np.arange(n_chan)
    cmap  = plt.get_cmap('coolwarm', n_ant)

    for corr in range(n_corr):
        fig = plt.figure(figsize=(10, 8))
        gs  = fig.add_gridspec(2, 1, hspace=0.01)
        ax1 = fig.add_subplot(gs[0])
        ax2 = fig.add_subplot(gs[1])

        for row in range(n_row):
            sol   = cparam[corr, :, row]
            good  = ~flags[corr, :, row].astype(bool)
            aidx  = ant1[row]
            aname = ant_names[aidx] if aidx < n_ant else f'ant{aidx}'
            c     = cmap(aidx % n_ant)

            ax1.plot(chans[good], np.abs(sol[good]),               lw=0.5, color=c, label=aname)
            ax2.plot(chans[good], np.degrees(np.angle(sol[good])), lw=0.5, color=c, label=aname)

        ax1.set_ylabel('Amplitude')
        ax1.set_title(f'Bandpass solutions — {label} — {CORR_LABELS[corr]}')
        ax1.legend(prop={'size': 9}, frameon=False, ncol=2,
                   loc='center left', bbox_to_anchor=(1., 0.))
        ax2.set_ylabel('Phase (deg)')
        ax2.set_xlabel('Channel')
        plt.savefig(f'{outbase}_{CORR_LABELS[corr]}.png', dpi=300, bbox_inches='tight')
        plt.close()

# flux model and setjy
if calibrator == 'J0408-6545':
    # custom model from https://skaafrica.atlassian.net/wiki/spaces/ESDKB/pages/1481408634
    a, b, c, d = -0.9790, 3.3662, -1.1216, 0.0861
    freq = np.linspace(1.75, 3.5, 200) * 1e9 if band == 'S' else np.linspace(0.9, 2, 200) * 1e9
    reffreq, fluxdensity, spix0, spix1, spix2 = convert_flux_model(freq, a, b, c, d)
    casatasks.setjy(vis=ms, field=calibrator,
                    spix=[spix0, spix1, spix2, 0],
                    fluxdensity=fluxdensity,
                    reffreq=f'{reffreq:.6f}Hz',
                    standard='manual')
else:
    casatasks.setjy(vis=ms, standard='Perley-Butler 2017', usescratch=True)

# main calibration loop
counters = {'K': 0, 'G': 0, 'B': 0}

# active[type] = path to the most recent table of that type.
# After each B step K and G are dropped because B subsumes them.
active = {}

# record of every solved table for the plotting step
solved_tables = []  # list of (step_type, label, table_path)

for step, cm, si, co in zip(steps, calmode_list, solint_list, combine_list):

    counters[step] += 1
    label = f'{step}{counters[step]}'
    table = os.path.join(outdir, f'{outname}_{label}.cal')

    # gaintable: most recent solution of every other type, ordered K → G → B
    gaintable = [active[t] for t in ('K', 'G', 'B') if t in active and t != step]

    step_name = {'K': 'delay', 'G': 'gain', 'B': 'bandpass'}[step]
    print(f'{label} : {step_name} calibration')
    print(f'calmode={cm!r}  solint={si!r}  combine={co!r}')
    print(f'gaintable={[os.path.basename(g) for g in gaintable]}')

    if step == 'K':
        casatasks.gaincal(
            vis       = ms,
            caltable  = table,
            gaintype  = 'K',
            calmode   = cm,
            solint    = si,
            combine   = co,
            refant    = refant,
            gaintable = gaintable,
        )

    elif step == 'G':
        casatasks.gaincal(
            vis       = ms,
            caltable  = table,
            gaintype  = 'G',
            calmode   = cm,
            solint    = si,
            combine   = co,
            refant    = refant,
            gaintable = gaintable,
        )

    elif step == 'B':
        casatasks.bandpass(
            vis       = ms,
            caltable  = table,
            bandtype  = 'B',
            solint    = si,
            combine   = co,
            refant    = refant,
            fillgaps  = fillgaps,
            gaintable = gaintable,
        )
        if smooth_window > 1:
            smooth_bandpass(table, smooth_window)

    else:
        print(f'=WARNING: unknown step type {step!r}. Skipping.')
        continue

    # update the active table tracker
    if step == 'B':
        active = {'B': table}   # B subsumes all previous K and G
    else:
        active[step] = table

    solved_tables.append((step, label, table))

# apply final solutions
final = {s: t for s, _, t in solved_tables}
final_tables = [final[t] for t in ('K', 'G', 'B') if t in final]
print(f'Applying solutions: {[os.path.basename(t) for t in final_tables]}')
casatasks.applycal(
    vis       = ms,
    gaintable = final_tables,
)

# diagnostic plots
if plotgains:
    print(f'Generating diagnostic plots')
    for step, label, table in solved_tables:
        outbase     = os.path.join(plotdir, os.path.basename(table).replace('.cal', ''))
        smooth_label = f'{label} (smoothed)' if (step == 'B' and smooth_window > 1) else label
        if   step == 'K': plot_delays  (table, label,        outbase)
        elif step == 'G': plot_gains   (table, label,        outbase)
        elif step == 'B': plot_bandpass(table, smooth_label, outbase)
    print(f'Plots written to {plotdir}')

print(f'Done.')