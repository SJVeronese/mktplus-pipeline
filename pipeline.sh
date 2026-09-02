#!/usr/bin/env bash
# ============================================================
#  MeerKAT+ Data Reduction Pipeline   –   pipeline.sh
#  Version     : 0.1.0
#
#  Description : JSON-driven pipeline runner for HTCondor.
#                Reads a JSON configuration file and executes
#                each enabled worker in sequence.  All science
#                parameters live in the JSON; this script is
#                never modified to change reduction settings.
#
#  Usage       : ./pipeline.sh [/path/to/pipeline.yml]
#                Defaults to 'pipeline.yml' in the CWD.
#
#  Workers     : listobs | mstransform | flagdata |
#                inspect | crosscal | wsclean
#
# ============================================================

set -euo pipefail

readonly PIPELINE_VERSION="0.1.0"

# ────────────────────────────────────────────────────────────
# CENTRALISED DEFAULT VALUES FOR WORKERS
# ────────────────────────────────────────────────────────────

# centralised defaults for every known config key.
# Add a new line here whenever a new key is introduced in the pipeline.
declare -A YML_DEFAULTS=(
    # general
	["general.use_containers.enable"]=false
	["general.use_containers.contdir"]=''
	["general.use_containers.containers.casa"]=''
	["general.use_containers.containers.shadems"]=''
    ["general.prefix"]='pipeline'

    # crosscal
    ["crosscal.enable"]=false
    ["crosscal.label_in"]=''
    ["crosscal.label_out"]='1gc1'
    ["crosscal.refant"]='m000'
    ["crosscal.band"]='L'
    ["crosscal.primary.name"]='J0408-6545'
    ["crosscal.primary.order"]='KGB'
    ["crosscal.primary.fillgaps"]=70
    ["crosscal.primary.smooth_window"]=1
    ["crosscal.primary.plotgains"]=false

    # flagdata
    ["flagdata.enable"]=false
    ["flagdata.label_in"]=''
    ["flagdata.flag_antenna"]=''
    ["flagdata.flag_autocorr"]=true
    ["flagdata.flag_clip"]=true
    ["flagdata.flag_rfi"]=true
    ["flagdata.flag_shadow"]=true
    ["flagdata.flag_scan"]=''
    ["flagdata.flag_spw"]=''
    ["flagdata.flag_time"]=''

    # inspect
    ["inspect.enable"]=false
    ["inspect.label_in"]=''
    ["inspect.dirname"]=''
    ["inspect.col"]='DATA'
    ["inspect.plots"]=''

    # listobs
    ["listobs.enable"]=false
    ["listobs.label_out"]=''

    # mstransform
    ["mstransform.enable"]=false
    ["mstransform.label_in"]=''
    ["mstransform.label_out"]='corr'
    ["mstransform.col"]='data'
    ["mstransform.field"]='0'
    ["mstransform.spw"]=''
    ["mstransform.correlation"]=''
    ["mstransform.scan"]=''
    ["mstransform.antenna"]=''
    ["mstransform.chanavg.enable"]=false
    ["mstransform.chanavg.chanbin"]=1
    ["mstransform.timeavg.enable"]=false
    ["mstransform.timeavg.timebin"]='1s'
    ["mstransform.otfcal.enable"]=false
    ["mstransform.otfcal.label_cal"]='1gc1'

    # wsclean
    ["wsclean.enable"]=false
    ["wsclean.label_in"]=''
    ["wsclean.dirname"]=''
    ["wsclean.estimate_params.enable"]=true
    ["wsclean.estimate_params.restfreq"]=1.420405752
    ["wsclean.npix"]=1800
    ["wsclean.cell"]=2.0
    ["wsclean.padding"]=1.3
    ["wsclean.weight"]='briggs'
    ["wsclean.robust"]=0.0
    ["wsclean.taper"]=0.0
    ["wsclean.gain"]=0.1
    ["wsclean.mgain"]=0.9
    ["wsclean.auto_mask"]=10
    ["wsclean.auto_thr"]=0.5
    ["wsclean.niter"]=1000000
    ["wsclean.nmiter"]=0
    ["wsclean.nchans"]=1
    ["wsclean.joinchans"]=false
)

# ────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ────────────────────────────────────────────────────────────

# define the logging function
# Format:  TIMESTAMP  LEVEL  [CONTEXT]  MESSAGE
log() {
    local level="$1" context="$2" msg="$3"
    printf '%s  %-5s  %-15s  %s\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" "${level}" "[${context}]" "${msg}"
}
log_info()  { log "INFO   " "$1" "$2"; }
log_warn()  { log "WARNING" "$1" "$2"; }
log_error() { log "ERROR  " "$1" "$2" >&2; }

# classify a captured output line and route it to the right log function.
# Matches are case-insensitive and require word boundaries to suppress
# false positives (e.g. "no errors", "directory").
route_log() {
    local context="$1"
    local line="$2"

    # strip log prefix: "YYYY-MM-DD HH:MM:SS\tLEVEL\torigin\tmessage"
    # if the line matches, extract just the message (4th tab-field onward)
    # !!! THIS DOES NOT SEEM TO WORK !!! #
    if printf '%s' "${line}" | grep -qE \
        '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\t'; then
        line=$(printf '%s' "${line}" | cut -f4-)
    fi

    if printf '%s' "${line}" | grep -qiE '\b(SEVERE|CRITICAL|ERROR|Exception|Traceback)\b'; then
        log_error "${context}" "${line}"
    elif printf '%s' "${line}" | grep -qiE '\b(WARN|WARNING)\b'; then
        log_warn  "${context}" "${line}"
    else
        log_info  "${context}" "${line}"
    fi
}

# helper function to open a YAML file using Python
open_yaml() {
    python3 -c "
import yaml
        
yaml.safe_load(open('${CONFIG}'))";
}

# helper function to read YAML values using Python
get_value() {
    local key_path="$1"

    # Strip any __suffix from the worker name (first path component) so that
    # e.g. "crosscal__bpcal.enable" resolves against "crosscal.enable" in
    # YML_DEFAULTS.  Workers without a suffix are unaffected.
    local first="${key_path%%.*}"          # e.g. "crosscal__bpcal"
    local rest="${key_path#*.}"            # e.g. "enable" or "primary.order"
    local base="${first%%__*}"             # e.g. "crosscal"

    local base_key_path
    if [[ "${key_path}" == *"."* ]]; then
        base_key_path="${base}.${rest}"    # e.g. "crosscal.enable"
    else
        base_key_path="${base}"            # top-level key with no dot
    fi

    # look up the per-key default; fall back to empty string if the key
    # is not listed in YML_DEFAULTS at all
    local default="${YML_DEFAULTS[${base_key_path}]:-}"

    python3 -c "
import yaml, sys

with open('$CONFIG', 'r') as f:
    data = yaml.safe_load(f)

val = data
try:
    for key in '$key_path'.split('.'):
        val = val[key]
except (KeyError, TypeError):
    print('${default}') # fallback to its default if not set
    sys.exit(0) 

# print boolean/numbers/strings cleanly.
# Fallback to default if a value is not set
if val is None:
    print('${default}')
elif isinstance(val, bool):
    print(str(val).lower())
else:
    print(val)
"
}

# helper function to read YAML lists using Python
get_list() {
    local key_path="$1"
    python3 -c "
import yaml, sys

with open('$CONFIG', 'r') as f:
    data = yaml.safe_load(f)

val = data
try:
    for key in '$key_path'.split('.'):
        val = val[key]
except (KeyError, TypeError):
    sys.exit(0)

if isinstance(val, list):
    for item in val:
        print(item)
elif val is not None:
    print(val)
"
}

# helper function to read the workers in a YAML file using Python
get_worker() {
    python3 -c "
import yaml
with open('${CONFIG}', 'r') as f:
    data = yaml.safe_load(f)
    for key in data:
        print(key)
"
}

# wraps a command with "singularity exec" in container mode,
# or runs it directly on bare-metal.
# Usage: run_cmd <container_path> <command> [args...]
run_cmd() {
    local container="$1"
    shift
    if [[ "${USE_CONTAINERS}" == "true" ]]; then
        run_cmd "${container}" "$@"
    else
        "$@"
    fi
}

# ────────────────────────────────────────────────────────────
# WORKER FUNCTIONS
# ────────────────────────────────────────────────────────────

run_crosscal() {
    # name of the worker
    local worker="$1"
    local worker_base_name="${worker%__*}"

    # read parameters from config
    local label_in=$(   get_value "${worker}.label_in")
    local label_out=$(  get_value "${worker}.label_out")
    local band=$(       get_value "${worker}.band")
    local refant=$(     get_value "${worker}.refant")
    local primary=$(    get_value "${worker}.primary.name")
    local order=$(      get_value "${worker}.primary.order")
    local fillgaps=$(   get_value "${worker}.primary.fillgaps")
    local smooth_win=$( get_value "${worker}.primary.smooth_window")
    local plotgains=$(  get_value "${worker}.primary.plotgains")

    # per-step lists (one entry per character in 'order')
    mapfile -t calmode_list < <(get_list "${worker}.primary.calmode")
    mapfile -t solint_list  < <(get_list "${worker}.primary.solint")
    mapfile -t combine_list < <(get_list "${worker}.primary.combine")

    # build filenames
    local inname
    if [[ -n "${label_in}" ]]; then
        inname="${OUT_PREFIX}_${label_in}.ms"
    else
        inname="${OUT_PREFIX}.ms"
    fi
    local input="${DATADIR}/${inname}"

    # cal tables live in their own subdirectory under data/
    local outname="${OUT_PREFIX}_${label_out}"
    local outdir="${OUTDIR}/caltables/"   # path inside container
    local locdir="${OUT_PATH}/caltables/"  # path on host

    # plots live in their own subdirectory under plots/
    local plotdir="${PLOTDIR}/${label_out}/"  # path inside container

    # skip if already done
    if [[ -d "${locdir}*.cal" ]]; then
        log_info "${worker_base_name}" "Calibration tables already exist at ${locdir}. Skipping."
        return 0
    fi

    # build the python command
    local cmd=(
        python3 "${SCRIPTDIR}/crosscal.py"
        --ms            "${input}"
        --outname       "${outname}"
        --outdir        "${outdir}"
        --plotdir       "${plotdir}"
        --refant        "${refant}"
        --band          "${band}"
        --calibrator    "${primary}"
        --fillgaps      "${fillgaps}"
        --smooth-window "${smooth_win}"
        --plotgains     "${plotgains}"
        --order         "${order}"
    )

    # append per-step values as repeated flags;
    # empty strings are passed through so Python can apply its own defaults
    for v in "${calmode_list[@]}"; do cmd+=(--calmode "$v"); done
    for v in "${solint_list[@]}";  do cmd+=(--solint  "$v"); done
    for v in "${combine_list[@]}"; do cmd+=(--combine "$v"); done

    # log the run parameters
    log_info "${worker_base_name}" "Cross-calibrating  ${input}"
    log_info "${worker_base_name}" "with parameters:"
    log_info "${worker_base_name}" "    refant=${refant}"
    log_info "${worker_base_name}" "    band=${band}"
    log_info "${worker_base_name}" "    calibrator=${primary}"
    log_info "${worker_base_name}" "    order=${order}"
    log_info "${worker_base_name}" "    calmode=${calmode_list[*]}"
    log_info "${worker_base_name}" "    solint=${solint_list[*]}"
    log_info "${worker_base_name}" "    combine=${combine_list[*]}"
    log_info "${worker_base_name}" "    fillgaps=${fillgaps}"
    log_info "${worker_base_name}" "    smooth_window=${smooth_win}"
    log_info "${worker_base_name}" "    plotgains=${plotgains}"
    log_info "${worker_base_name}" "    cal tables -> ${locdir}"

    # run and forward every print line to log_info
    # 2>&1 merges stderr (CASA warnings) into the same stream.
    # The process substitution < <(...) preserves the exit code under set -e.
    while IFS= read -r line; do
        route_log "${worker_base_name}" "${line}"
    done < <(run_cmd "${CASA_IMG}" "${cmd[@]}" 2>&1)

}

run_flagdata() {
    # name of the worker
    local worker="$1"
    local worker_base_name="${worker%__*}"

    # store the parameters
    local label_in=$( get_value "${worker}.label_in" )
    local antenna=$(  get_value "${worker}.flag_antenna")
    local autocorr=$( get_value "${worker}.flag_autocorr")
    local clip=$(     get_value "${worker}.flag_clip")
    local rfi=$(      get_value "${worker}.flag_rfi")
    local shadow=$(   get_value "${worker}.flag_shadow")
    local scan=$(     get_value "${worker}.flag_scan")
    local spw=$(      get_value "${worker}.flag_spw")
    local time=$(     get_value "${worker}.flag_time")

    # build the input filename
    local inname
    if [[ -n "${label_in}" ]]; then
        inname="${OUT_PREFIX}_${label_in}.ms"
    else
        inname="${OUT_PREFIX}.ms"
    fi
    local input="${DATADIR}/${inname}"

    # build the python command
    local cmd=(
        python3 "${SCRIPTDIR}/flagdata.py"
        --ms       "${input}"
        --antenna  "${antenna}"
        --autocorr "${autocorr}"
        --clip     "${clip}"
        --rfi      "${rfi}"
        --shadow   "${shadow}"
        --scan     "${scan}"
        --spw      "${spw}"
        --time     "${time}"
    )

    log_info "${worker_base_name}" "Flagging  ${input}"
    log_info "${worker_base_name}" "with parameters:"
    log_info "${worker_base_name}" "    antenna=${antenna}"
    log_info "${worker_base_name}" "    autocorr=${autocorr}"
    log_info "${worker_base_name}" "    clip=${clip}"
    log_info "${worker_base_name}" "    rfi=${rfi}"
    log_info "${worker_base_name}" "    shadow=${shadow}"
    log_info "${worker_base_name}" "    scan=${scan}"
    log_info "${worker_base_name}" "    spw=${spw}"
    log_info "${worker_base_name}" "    time=${time}"

    # run and forward every print line to log_info
    # 2>&1 merges stderr (CASA warnings) into the same stream.
    # The process substitution < <(...) preserves the exit code under set -e.
    while IFS= read -r line; do
        route_log "${worker_base_name}" "${line}"
    done < <(run_cmd "${CASA_IMG}" "${cmd[@]}" 2>&1)

    log_info "${worker_base_name}" "Flag backup written to ${DATA_PATH}/${inname}.flagversions."
}

run_inspect() {
    # name of the worker
    local worker="$1"
    local worker_base_name="${worker%__*}"
    local plotter="shadems"

    # store the parameters
    local label_in=$(  get_value "${worker}.label_in" )
    local dirname=$(   get_value "${worker}.dirname")
    local col=$(       get_value "${worker}.col")
    mapfile -t plot_list < <( get_list "${worker}.plots" ) # mapfile converts the yaml list into a bash array

    # build the input filename
    local inname
    if [[ -n "${label_in}" ]]; then
        inname="${OUT_PREFIX}_${label_in}.ms"
    else
        inname="${OUT_PREFIX}.ms"
    fi
    local input="${DATADIR}/${inname}"

    # build the output directory
    if [[ -n "${dirname}" ]]; then
        local outdir="${PLOTDIR}/${dirname}/"
    else
        local outdir="${PLOTDIR}/"
    fi
    
    # for each plot
    for plot in "${plot_list[@]}"; do
        # build the shadems command
        local cmd=( ${plot} --col "${col}" --dir "${outdir}" )

        log_info "${plotter}" "Generating diagnostic plot ${cmd[*]} for ${input}"
        
        # run shadems captruing its messages
        while IFS= read -r line; do
            route_log "${plotter}" "${line}"
        done < <(run_cmd "${SHADEMS_IMG}" shadems "${input}" "${cmd[@]}" 2>&1)
    done

    log_info "${plotter}" "All plots written to ${PLOTDIR}/."
}

run_listobs() {
    # name of the worker
    local worker="$1"

    # store the parameters
    local label_out=$( get_value "listobs.label_out")

    # build the output filename
    local outname
    if [[ -n "${label_out}" ]]; then
        outname="${OUT_PREFIX}_${label_out}_listobs.txt"
    else
        outname="${OUT_PREFIX}_listobs.txt"
    fi

    # full output in the container environment
    local output="${OUTDIR}/${outname}"

    # full output in the local environment
    local locdir="${OUT_PATH}/${outname}"

    # skip if output already exists
    if [[ -f "${locdir}" ]]; then
        log_info "${worker}" "Output already exists at ${locdir}. Skipping."
        return 0
    fi

    log_info "${worker}" "Extracting observation metadata from ${MS}"

    run_cmd "${CASA_IMG}" \
        python3 "${SCRIPTDIR}/listobs.py" "${MS}" "${output}"

    log_info "${worker}" "Observation info written to ${output}."
}

run_mstransform() {
    # name of the worker
    local worker="$1"
    local worker_base_name="${worker%__*}"

    # store the parameters
    local label_in=$(    get_value "${worker}.label_in" )
    local label_out=$(   get_value "${worker}.label_out" )
    local datacolumn=$(  get_value "${worker}.col")
    local antenna=$(     get_value "${worker}.antenna")
    local correlation=$( get_value "${worker}.correlation")
    local field=$(       get_value "${worker}.field")
    local scan=$(        get_value "${worker}.scan")
    local spw=$(         get_value "${worker}.spw")
    local chanavg=$(     get_value "${worker}.chanavg.enable")
    local chanbin=$(     get_value "${worker}.chanavg.chanbin")
    local timeavg=$(     get_value "${worker}.timeavg.enable")
    local timebin=$(     get_value "${worker}.timeavg.timebin")
    local otfcal=$(      get_value "${worker}.otfcal.enable")
    local label_cal=$(   get_value "${worker}.otfcal.label_cal")

    # build the input filename
    local input
    if [[ -n "${label_in}" ]]; then
        input="${MSDIR}/${label_in}.ms"
    else
        input="${MS}"
    fi

    # build the output filename
    local outname
    if [[ -n "${label_out}" ]]; then
        outname="${OUT_PREFIX}_${label_out}.ms"
    else
        outname="${OUT_PREFIX}.ms"
    fi

    local output="${DATADIR}/${outname}"
    local locdir="${DATA_PATH}/${outname}"

    # skip if output already exists
    if [[ -d "${locdir}" ]]; then
        log_info "${worker_base_name}" "Output MS already exists at ${locdir}. Skipping."
        return 0
    fi

    # build the python command
    local cmd=(
        python3 "${SCRIPTDIR}/mstransform.py"
        --ms          "${input}"
        --datacolumn  "${datacolumn}"
        --output      "${output}"
        --antenna      "${antenna}"
        --correlation "${correlation}"
        --field       "${field}"
        --scan        "${scan}"
        --spw         "${spw}"
        --chanavg     "${chanavg}"
        --chanbin     "${chanbin}"
        --timeavg     "${timeavg}"
        --timebin     "${timebin}"
        --otfcal      "${otfcal}"
    )

    log_info "${worker_base_name}" "Splitting  ${input}  →  ${output}"
    log_info "${worker_base_name}" "with parameters:"
    log_info "${worker_base_name}" "    datacolumn=${datacolumn}"
    log_info "${worker_base_name}" "    antenna=${antenna}"
    log_info "${worker_base_name}" "    correlation=${correlation}"
    log_info "${worker_base_name}" "    field=${field}"
    log_info "${worker_base_name}" "    scan=${scan}"
    log_info "${worker_base_name}" "    spw=${spw}"
    log_info "${worker_base_name}" "    chanavg=${chanavg}"
    log_info "${worker_base_name}" "    chanbin=${chanbin}"
    log_info "${worker_base_name}" "    timeavg=${timeavg}"
    log_info "${worker_base_name}" "    timebin=${timebin}"
    log_info "${worker_base_name}" "    otfcal=${otfcal}"

    # add the calibration table label
    if [[ "${otfcal,,}" == "true" ]]; then
        cmd+=(--label_cal "${label_cal}")
        log_info "${worker_base_name}" "    label_cal=${label_cal[*]}"
    fi

    # run and forward every print line to log_info
    # 2>&1 merges stderr (CASA warnings) into the same stream.
    # The process substitution < <(...) preserves the exit code under set -e.
    while IFS= read -r line; do
        route_log "${worker_base_name}" "${line}"
    done < <(run_cmd "${CASA_IMG}" "${cmd[@]}" 2>&1)

    log_info "${worker_base_name}" "Split MS written to ${output}."
}

run_wsclean() {
    # name of the worker
    local worker="$1"
    local worker_base_name="${worker%__*}"

    # store the parameters
    local label_in=$(  get_value "${worker}.label_in" )
    local dirname=$( get_value "${worker}.dirname" )
    local estimate=$(  get_value "${worker}.estimate_params.enable")
    local restfreq=$(  get_value "${worker}.estimate_params.restfreq")
    local npix=$(      get_value "${worker}.npix")
    local padding=$(   get_value "${worker}.padding")
    local gain=$(      get_value "${worker}.gain")
    local mgain=$(     get_value "${worker}.mgain")
    local cell=$(      get_value "${worker}.cell")
    local weight=$(    get_value "${worker}.weight")
    local robust=$(    get_value "${worker}.robust")
    local taper=$(     get_value "${worker}.taper")
    local niter=$(     get_value "${worker}.niter")
    local nmiter=$(    get_value "${worker}.nmiter")
    local nchans=$(    get_value "${worker}.nchans")
    local joinchans=$( get_value "${worker}.joinchans")
    local automask=$(  get_value "${worker}.auto_mask")
    local autothr=$(   get_value "${worker}.auto_thr")

    # build the input filename
    local inname
    if [[ -n "${label_in}" ]]; then
        inname="${OUT_PREFIX}_${label_in}.ms"
    else
        inname="${OUT_PREFIX}.ms"
    fi
    local input="${DATADIR}/${inname}"

    # build the output filename
    local outname="${inname%%.*}"
    if [[ -n "${dirname}" ]]; then
        # create the output directory
        mkdir -p "${IMG_PATH}/${dirname}/"
        
        local output="${IMGDIR}/${dirname}/${outname}"
        local locdir="${IMG_PATH}/${dirname}/${outname}-image.fits"
    else
        local output="${IMGDIR}/${outname}"
        local locdir="${IMG_PATH}/${outname}-image.fits"
    fi

    # skip if output already exists
    if [[ -f "${locdir}" ]]; then
        log_info "${worker_base_name}" "Image already exists at ${locdir}. Skipping."
        return 0
    fi
    
    # estimate the optimal imaging parameters
    if [[ "${estimate,,}" == "true" ]]; then
        log_info "${worker_base_name}" "Computing optimal cell size and image size"

        local evaluate_cmd=(python3 "${SCRIPTDIR}/eval_image_params.py" "${input}" "${restfreq}")

        # run the parameters estimation script
        PARAMS=$(run_cmd "${CASA_IMG}" "${evaluate_cmd[@]}")

        # parse every KEY=VALUE line: extract cell/npix and log everything else
        while IFS='=' read -r key val; do
            [[ -z "${key}" ]] && continue
            case "${key}" in
                Pixel_resolution_asec)   cell="${val}" ;;
                Image_size_pixel) npix="${val}"  ;;
                *)      route_log "${worker_base_name}" "    ${key//_/ }=${val}" ;;
            esac
        done <<< "${PARAMS}"

        if [[ -z "${cell}" || -z "${npix}" ]]; then
            log_error "${worker_base_name}" "Failed to parse pixel resolution or image size from eval_image_params.py output. Aborting."
            exit 1
        fi
    fi

    # build the wsclean command (MS goes at the end)
    local cmd=(
        wsclean
        -size "${npix}" "${npix}"
        -scale "${cell}asec"
        -padding "${padding}"
        -gridder "wstacking"
        -niter "${niter}"
        -nmiter "${nmiter}"
        -auto-threshold "${autothr}"
        -auto-mask "${automask}"
        -gain "${gain}"
        -mgain "${mgain}"
        -channels-out "${nchans}"
        -name "${output}"
    )

    # gaussian taper: only add if > 0
    if awk "BEGIN{exit !( ${taper} > 0 )}"; then
        cmd+=( -taper-gaussian "${taper}asec" )
    fi

    # join-channels: boolean switch, no argument
    if [[ "${joinchans,,}" == "true" ]]; then
        cmd+=( -join-channels )
    fi

    # weighting
    if [[ "${weight}" == "briggs" ]]; then
        cmd+=( -weight "${weight}" "${robust}" )
    else
        cmd+=( -weight "${weight}" )
    fi

    # MS at the end
    cmd+=( "${input}" )

    log_info "${worker_base_name}" "Imaging  ${input}  →  ${output}-image.fits"
    log_info "${worker_base_name}" "with parameters:"
    log_info "${worker_base_name}" "    size=${npix} x ${npix} pixels"
    log_info "${worker_base_name}" "    scale=${cell} arcsec"
    log_info "${worker_base_name}" "    padding=${padding}"
    log_info "${worker_base_name}" "    weight=${weight}"
    log_info "${worker_base_name}" "    robust=${robust}"
    log_info "${worker_base_name}" "    taper=${taper} arcsec"
    log_info "${worker_base_name}" "    gain=${gain}"
    log_info "${worker_base_name}" "    mgain=${mgain}"
    log_info "${worker_base_name}" "    auto-threshold=${autothr}"
    log_info "${worker_base_name}" "    auto-mask=${automask}"
    log_info "${worker_base_name}" "    niter=${niter}"
    log_info "${worker_base_name}" "    nmiter=${nmiter}"
    log_info "${worker_base_name}" "    channels-out=${nchans}"
    log_info "${worker_base_name}" "    join-channels=${joinchans}"

    # run wsclean capturing its messages
    while IFS= read -r line; do
        route_log "${worker_base_name}" "${line}"
    done < <(run_cmd "${CASA_IMG}" "${cmd[@]}" 2>&1)

    log_info "${worker_base_name}" "Cleaned fits written to ${output}-image.fits."
}

# ────────────────────────────────────────────────────────────
# WORKER DISPATCHER
# ────────────────────────────────────────────────────────────

# reads worker type and enable flag from JSON, routes to the appropriate function, and records wall-clock time
dispatch_worker() {
    local worker="$1"
    local worker_base_name="${worker%__*}"

    local enable=$( get_value "${worker}.enable")

    if [[ "${enable}" != "true" ]]; then
        log_info "pipeline" "${worker} is disabled. Skipping."
        return 0
    fi
    
    # nicely format the log message
    formatted_msg=$(printf '%-30.30s' "[${worker}]")

    log_info "pipeline" "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log_info "pipeline" "━━━  START  ${formatted_msg}  ━━━"
    log_info "pipeline" "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    t_start=$(date +%s)

    case "${worker_base_name}" in
        crosscal)     run_crosscal     "${worker}" ;;
        flagdata)     run_flagdata     "${worker}" ;;
        inspect)      run_inspect      "${worker}" ;;
        listobs)      run_listobs      "${worker}" ;;
        mstransform)  run_mstransform  "${worker}" ;;
        wsclean)      run_wsclean      "${worker}" ;;
        *)
            log_error "pipeline" "Unknown worker type: '${worker_base_name}'."
            log_error "pipeline" "Valid types: listobs  mstransform  flagdata  wsclean  crosscal"
            exit 1
            ;;
    esac

    # time when the worker finished
    local t_end=$(date +%s)

    # total exectution time
    local elapsed=$(( t_end - t_start ))

    # nicely format the log message
    formatted_msg=$(printf '%-30.30s' "[${worker}] (${elapsed}s)")

    log_info "pipeline" "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log_info "pipeline" "━━━  DONE   ${formatted_msg}  ━━━"
    log_info "pipeline" "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# ────────────────────────────────────────────────────────────
# ERROR TRAP
# ────────────────────────────────────────────────────────────

# catches unexpected failures from set -e and reports the line number before the shell exits.
trap 'log_error "pipeline" "Unexpected failure at line ${LINENO} (exit code $?). Pipeline aborted."' ERR

# ────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────
log_info "pipeline" "═══════════════════════════════════════════════"
log_info "pipeline" " MeerKAT+ Reduction Pipeline  v${PIPELINE_VERSION}"
log_info "pipeline" "═══════════════════════════════════════════════"

# ────────────────────────────────────────────────────────────
# 1: INITIALISATION
# ────────────────────────────────────────────────────────────

readonly PIPELINE_START_TS=$(date +%s)

# store the configuration file pointer
readonly CONFIG="${1:-pipeline.yml}"

# ensure config file exists
if [[ ! -f "${CONFIG}" ]]; then
    log_error "startup" "Configuration file not found: '${CONFIG}'. Aborting."
    exit 1
fi

# validate the YAML so errors are caught before any work starts
if ! open_yaml 2>/dev/null; then
    log_error "startup" "Configuration file is not valid YAML: '${CONFIG}'. Aborting."
    exit 1
fi

# ────────────────────────────────────────────────────────────
# 2: GENERAL SETTINGS
# ────────────────────────────────────────────────────────────

# directory that contains pipeline.sh
# Works whether called by absolute path, relative path, or HTCondor's remote execution
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# read general settings
readonly MS_PATH=$(   get_value "general.msdir")
readonly WORK_PATH=$( get_value "general.outdir")

# define the containers
readonly USE_CONTAINERS=$(    get_value "general.use_containers.enable")
readonly CONT_PATH=$(         get_value "general.use_containers.contdir")
readonly CASA_CONTAINER=$(    get_value "general.use_containers.containers.casa")
readonly SHADEMS_CONTAINER=$( get_value "general.use_containers.containers.shadems")



readonly CASA_IMG="${CONT_PATH}/${CASA_CONTAINER}"
readonly SHADEMS_IMG="${CONT_PATH}/${SHADEMS_CONTAINER}"

if [[ "${USE_CONTAINERS}" == "true" ]]; then
    [[ -z "${CONT_PATH}" ]] && {
        log_error "startup" "use_containers.enable is true but use_containers.contdir is empty. Aborting."
        exit 1
    }
    [[ -z "${CASA_IMG}" ]] && {
        log_error "startup" "use_containers.enable is true but use_containers.containers.casa is empty. Aborting."
        exit 1
    }
    [[ -z "${SHADEMS_IMG}" ]] && {
        log_error "startup" "use_containers.enable is true but use_containers.containers.shadems is empty. Aborting."
        exit 1
    }
	
	# container-internal mount points (what the scripts see inside Singularity)
	readonly MSDIR="/input_ms"
    readonly DATADIR="/data"
    readonly OUTDIR="/output"
    readonly PLOTDIR="/plots"
    readonly SCRIPTDIR="/scripts"
    readonly IMGDIR="/output/continuum"
    readonly CUBEDIR="/output/cubes"

	# define local paths
    readonly DATA_PATH="${WORK_PATH}/data"
    readonly OUT_PATH="${WORK_PATH}/output"
    readonly PLOT_PATH="${WORK_PATH}/plots"
    readonly SCRIPT_PATH="${WORK_PATH}/scripts"
    readonly IMG_PATH="${OUT_PATH}/continuum"
    readonly CUBE_PATH="${OUT_PATH}/cubes"
	
	# maps host directories to the fixed container mount points defined early
	BINDS=(
		--bind "${MS_PATH}:${MSDIR}"
		--bind "${WORK_PATH}:/homes"
		--bind "${DATA_PATH}:${DATADIR}"
		--bind "${OUT_PATH}:${OUTDIR}"
		--bind "${PLOT_PATH}:${PLOTDIR}"
		--bind "${IMG_PATH}:${IMGDIR}"
		--bind "${CUBE_PATH}:${CUBEDIR}"
		--bind "${SCRIPT_PATH}:${SCRIPTDIR}"
	)
else
    # bare-metal: internal paths == host paths
    readonly DATA_PATH="${WORK_PATH}/data"
    readonly OUT_PATH="${WORK_PATH}/output"
    readonly PLOT_PATH="${WORK_PATH}/plots"
    readonly SCRIPT_PATH="${WORK_PATH}/scripts"
    readonly IMG_PATH="${OUT_PATH}/continuum"
    readonly CUBE_PATH="${OUT_PATH}/cubes"

    readonly MSDIR="${MS_PATH}"
    readonly DATADIR="${DATA_PATH}"
    readonly OUTDIR="${OUT_PATH}"
    readonly PLOTDIR="${PLOT_PATH}"
    readonly SCRIPTDIR="${SCRIPT_PATH}"
    readonly IMGDIR="${IMG_PATH}"
    readonly CUBEDIR="${CUBE_PATH}"
	
	BINDS=()
fi

# create the output directories
mkdir -p "${DATA_PATH}" "${OUT_PATH}" "${PLOT_PATH}" "${IMG_PATH}" "${CUBE_PATH}"

# ────────────────────────────────────────────────────────────
# 3: INPUT MS AND OUTPUT PREFIX HANDLER
# ────────────────────────────────────────────────────────────

# read the ms to process
readonly MS_FILE=$( get_value "getdata.ms")

# set the output prefix when non-empty, else fallback to the input ms file
OUT_PREFIX=$( get_value "general.prefix")
[[ -z "${OUT_PREFIX}" ]] && OUT_PREFIX="${MS_FILE}"
readonly OUT_PREFIX

# container-internal path to the raw input MS
readonly MS="${MSDIR}/${MS_FILE}.ms"

log_info "pipeline" " Config      : ${CONFIG}"
log_info "pipeline" " Work dir    : ${WORK_PATH}"
log_info "pipeline" " MS file     : ${MS_FILE}.ms"
log_info "pipeline" " Prefix      : ${OUT_PREFIX:-<none>}"
log_info "pipeline" "═══════════════════════════════════════════════"

# ────────────────────────────────────────────────────────────
# 4: NON-MANDATORY STEPS
# ────────────────────────────────────────────────────────────

# mandatory workers
readonly MANDATORY_KEYS=("general" "getdata")

#process the non-mandatory workers
while IFS= read -r worker; do
    #if a mandatory worker skip
    [[ "${MANDATORY_KEYS[*]}" == *"${worker}"* ]] && continue

    #else process
    dispatch_worker "${worker}"
done < <( get_worker )

# ────────────────────────────────────────────────────────────
# 5: FINALISING
# ────────────────────────────────────────────────────────────

TOTAL=$(( $(date +%s) - PIPELINE_START_TS ))
log_info "pipeline" "════════════════════════════════════════════"
log_info "pipeline" " Pipeline complete. Total runtime: ${TOTAL}s ($(( TOTAL / 60 ))m $(( TOTAL % 60 ))s)"
log_info "pipeline" "════════════════════════════════════════════"