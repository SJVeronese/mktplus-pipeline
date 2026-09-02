# MeerKAT+ Data Reduction Pipeline

A modular pipeline for data reduction and imaging of MeerKAT+ observations,
driven entirely by a single YAML configuration file.

---

## Repository layout

```
.
├── pipeline.sh          # Main pipeline runner (Bash)
├── pipeline.sub         # HTCondor submission file
├── config_files/
	├── default_config.yml # Fully-documented reference configuration
	└── minimal_config.yml  # Minimal config file for a data reduction and imaging run
├── scripts/
│   ├── crosscal.py      # 1GC cross-calibration worker
│   ├── eval_image_params.py  # Automatic cell/image-size estimator
│   ├── flagdata.py      # Flagging worker
│   ├── listobs.py       # CASA listobs wrapper
│   └── mstransform.py   # MS splitting / averaging worker
├── LICENSE
└── README.md
```

---

## Requirements

### Bare-metal cluster (no containers)

| Component | Purpose |
|-----------|---------|
| Python ≥ 3.8 | Worker script runtime |
| casatasks / casatools | CASA Python bindings |
| WSClean | Continuum imaging |
| shadems | Diagnostic visibility plots |
| numpy, scipy, matplotlib | Used by `crosscal.py` and `eval_image_params.py` |

No HTCondor or Singularity installation is required in this mode.

### HTCondor cluster (container mode)

| Component | Purpose |
|-----------|---------|
| HTCondor ≥ 8.9 | Job scheduling |
| Singularity / Apptainer | Container runtime |
| CASA container | `listobs`, `flagdata`, `mstransform`, `gaincal`, `bandpass`, `applycal`, WSClean |
| shadems container | Diagnostic visibility plots |

All Python dependencies (`casatasks`, `casatools`, `numpy`, `scipy`, `matplotlib`)
are satisfied inside the CASA Singularity image. No local Python environment is required.

---

## Quick start

### 1. Clone the repository

```bash
git clone https://github.com/SJVeronese/mktplus-pipeline.git
cd mktplus-pipeline
```

### 2. Create your working directory

The pipeline writes all products (split MSs, calibration tables, images, plots)
under a single work directory you specify in the config.

```bash
mkdir -p /path/to/workdir/{data,output,plots,scripts}
cp scripts/*.py /path/to/workdir/scripts/
```

### 3. Write your pipeline config

Copy the reference configuration and fill in your paths and science parameters:

```bash
cp default_config.yml pipeline_config.yml
```

The minimum required fields are shown below for each execution mode.

#### Bare-metal cluster (no containers)

```yaml
general:
  msdir:  '/path/to/raw/ms'
  outdir: '/path/to/workdir'
  prefix: 'my_project'
  use_containers:
    enable: false     # contdir and containers are ignored
getdata:
  ms: 'my_observation'
```

#### HTCondor + containers

```yaml
general:
  msdir:  '/path/to/raw/ms'
  outdir: '/path/to/workdir'
  prefix: 'my_project'
  use_containers:
    enable:  true
    contdir: '/path/to/containers'
    containers:
      casa:    'casa_latest.sif'
      shadems: 'shadems_latest.sif'
getdata:
  ms: 'my_observation'
```

Enable workers and set their parameters following the comments in `default_config.yml`.

### 4a. Run directly on a bare-metal cluster

```bash
bash pipeline.sh /path/to/pipeline_config.yml
```

Or submit through your cluster's native scheduler (SLURM, PBS, etc.) as a
single-node job, passing the config path as the only argument.

### 4b. Submit to HTCondor

Edit `pipeline.sub` so that the `arguments` line points to your config:

```
arguments = /path/to/pipeline_config.yml
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

See [`default_config.yml`](config_files/default_config.yml) for a fully-documented
description of every available parameter.

---

## Logging

The pipeline writes structured, timestamped logs to standard output.
On HTCondor clusters this is captured in `condor_logs/pipeline_<Cluster>_<Process>.out`.
On bare-metal clusters redirect it yourself if you need a persistent log:

```bash
bash pipeline.sh pipeline_config.yml 2>&1 | tee pipeline.log
```

Lines from CASA that contain `SEVERE`, `ERROR`, or `Traceback` are promoted to
`ERROR` level; lines containing `WARN` become `WARNING`.

---

## License

MIT — see `LICENSE`.