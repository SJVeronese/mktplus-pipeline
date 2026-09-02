# MeerKAT+ Data Reduction Pipeline

A modular, HTCondor-based pipeline for the first-generation cross-calibration
and imaging of MeerKAT+ observations, driven entirely by a single YAML
configuration file.

---

## Repository layout

```
.
├── pipeline.sh          # Main pipeline runner (Bash)
├── pipeline.sub         # HTCondor submission file
├── default_config.yml   # Fully-documented reference configuration
├── scripts/
│   ├── listobs.py       # CASA listobs wrapper
│   ├── flagdata.py      # Flagging worker
│   ├── mstransform.py   # MS splitting / averaging worker
│   ├── crosscal.py      # 1GC cross-calibration worker
│   └── eval_image_params.py  # Automatic cell/image-size estimator
└── README.md
```

---

## Requirements

| Component | Purpose |
|-----------|---------|
| HTCondor ≥ 8.9 | Job scheduling on the cluster |
| Singularity / Apptainer | Container runtime |
| CASA container | `listobs`, `flagdata`, `mstransform`, `gaincal`, `bandpass`, `applycal`, WSClean |
| shadems container | Diagnostic visibility plots |

All Python dependencies (`casatasks`, `casatools`, `numpy`, `scipy`,
`matplotlib`) are satisfied inside the CASA Singularity image.
No local Python environment is required.

---

## Quick start

### 1. Clone the repository on the cluster

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>
```

### 2. Create your working directory

The pipeline writes all products (split MSs, calibration tables, images,
plots) under a single work directory that you specify in the config.

```bash
mkdir -p /path/to/workdir/{data,output,plots,scripts}
cp scripts/*.py /path/to/workdir/scripts/
```

### 3. Write your pipeline config

Copy the reference configuration and fill in your paths and science
parameters:

```bash
cp default_config.yml pipeline_config.yml
```

The minimum required fields are:

```yaml
general:
  msdir:   '/path/to/raw/ms'        # directory containing the raw .ms
  contdir: '/path/to/containers'    # directory containing .sif images
  outdir:  '/path/to/workdir'       # work directory from step 2
  containers:
    casa:    'casa_latest.sif'
    shadems: 'shadems_latest.sif'
  prefix: 'my_project'

getdata:
  ms: 'my_observation'              # filename without .ms extension
```

Enable workers and set their parameters following the comments in
`default_config.yml`.

### 4. Submit to HTCondor

Edit `pipeline.sub` so that the `arguments` line points to your config:

```
arguments = /path/to/workdir/pipeline_config.yml
```

Then submit:

```bash
condor_submit pipeline.sub
```

Monitor progress:

```bash
condor_q
tail -f condor_logs/pipeline_<Cluster>_0.log
```

---

## Workers

Workers are listed in the YAML **in the order they will be executed**.
Each worker is skipped automatically if its output already exists,
making restarts safe.

| Worker | Key purpose |
|--------|------------|
| `listobs` | Write a human-readable observation summary |
| `mstransform` | Split a field, select channels/scans, average |
| `flagdata` | Flag autocorrelations, shadows, RFI (tfcrop) |
| `crosscal` | Delay (K), gain (G), bandpass (B) calibration |
| `wsclean` | Continuum imaging with WSClean |

Workers whose names contain `__` (e.g. `mstransform__bpcal`,
`mstransform__target`) can be duplicated to process multiple targets
independently with different parameters.

---

## Configuration reference

See [`default_config.yml`](default_config.yml) for a fully-documented
description of every available parameter.

---

## Logging

The pipeline writes structured, timestamped logs to standard output, which
HTCondor captures in `condor_logs/pipeline_<Cluster>_<Process>.out`.
Lines from CASA that contain `SEVERE`, `ERROR`, or `Traceback` are
promoted to `ERROR` level; lines containing `WARN` become `WARNING`.

---

## License

MIT — see `LICENSE`.
```