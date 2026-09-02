import casatools
import numpy as np
import sys

# casa logging
casalog = casatools.logsink()
casalog.showconsole(True)   # echo CASA log to stdout

# store the input arguments
ms_path = sys.argv[1]

# central frequency in Hz
# optional: falls back to reading it from the MS
freq_hz = float(sys.argv[2])*1e9

# constants
c        = 299792458.0  # m/s
D_dish   = 13.5         # smallest MeerKAT dish diameter, metres
beam_pix = 3            # pixels per synthesized beam FWHM

# initialise the table handler
tb = casatools.table()

#open the antenna table
tb.open(ms_path + '/ANTENNA')

#get the antenna positions
positions = tb.getcol('POSITION')  # shape (3, N_ant), ITRF metres

#get the antenna names
names     = tb.getcol('NAME')
tb.close()

# total number of antennas
N = positions.shape[1]

# initialise the maximum baseline
max_bl, max_pair = 0.0, ('', '')

# for each antenna
for i in range(N):
    # for each antenna to pair
    for j in range(i + 1, N):
        # compute the baseline from antenna i,j
        bl = np.sqrt(np.sum((positions[:, i] - positions[:, j])**2))
        # if the baseline is larger than current max
        if bl > max_bl:
            # update the max baseline
            max_bl, max_pair = bl, (names[i], names[j])

# if the central frequency is not supplied 
if freq_hz == 0 or ~np.isfinite(freq_hz):
    # open the spectral window table
    tb.open(ms_path + '/SPECTRAL_WINDOW')

    #determine the channel frequencies
    chan_freqs = tb.getcol('CHAN_FREQ')   # shape (nchan, nspw)
    tb.close()

    #assume the central frequency is the mean of the bandpass
    freq_hz = float(np.mean(chan_freqs))

# derived imaging quantities
lam        = c / freq_hz
theta_beam = 206265.0 * lam / max_bl           # synthesized beam FWHM, arcsec
cell       = round(theta_beam / beam_pix, 2)   # arcsec

theta_pb   = 206265.0 * 1.02 * lam / D_dish   # primary beam FWHM, arcsec

# helper to compute the smallest image size
def next_good_size(n):
    """Smallest integer >= n whose only prime factors are 2, 3, 5, 7."""
    n = int(np.ceil(n))
    while True:
        m = n
        for p in [2, 3, 5, 7]:
            while m % p == 0:
                m //= p
        if m == 1:
            return n
        n += 1

#compute the image size
imsize = next_good_size(theta_pb / cell)

# bash-parseable lines
print(f"Maximum_baseline_km={max_bl/1e3:.3f}")
print(f"Max_baseline_pair={max_pair[0]}-{max_pair[1]}")
print(f"Central_frequency_Hz={freq_hz/1e9:.4f}")
print(f"Wavelength_m={lam:.4f}")
print(f"Synthesized_beam_asec={theta_beam:.2f}")
print(f"Primary_beam_asec={theta_pb:.1f}")
print(f"Pixel_resolution_asec={cell}")
print(f"Image_size_pixel={imsize}")