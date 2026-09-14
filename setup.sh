#!/bin/bash
# =============================================================================
#  setup_nlp_study.sh
#
#  One-shot, re-runnable setup for benpodium8/nlp-study on the UIowa Argon
#  cluster. RUN THIS FROM A LOGIN NODE (it needs outbound internet; compute
#  nodes frequently do not have it).
#
#  ---------------------------------------------------------------------------
#  RESOURCE HANDLING
#  ---------------------------------------------------------------------------
#  Every managed resource (repo, venv, container image, model) is health
#  checked before anything is done to it, and lands in one of three states:
#
#     healthy  -> left alone and skipped
#     broken   -> removed and rebuilt (e.g. a half-finished git clone)
#     absent   -> created
#
#  So a failed run is always safe to resume by simply running the script again.
#
#  ---------------------------------------------------------------------------
#  WHY THERE IS A PREFLIGHT
#  ---------------------------------------------------------------------------
#  data_worker.py catches every LLM exception per note, records llm_failed=1
#  and continues to the next note. A wrong endpoint, a renamed model or a
#  model that will not emit schema-clean JSON therefore does NOT crash the
#  job. It runs for its full walltime and hands back a table in which every
#  LLM column is null. There is no loud failure to react to.
#
#  So this script ships a preflight that exercises the repo's real code path
#  -- llm_analysis.llm_analysis() on a synthetic, PHI-free note -- and refuses
#  to bless the setup unless it comes back schema-valid.
#
#  The preflight is tiered, because inference is not permitted on a login
#  node (per-user cap is 100% of ONE logical CPU and 16G of memory, and the
#  limiter kills the largest process when you exceed it -- which is what hung
#  the previous version of this script):
#
#     tier 0   static checks: imports, model-name agreement, endpoint
#              reachability, client wiring, writability. No inference.
#              Runs anywhere, including a login node. Seconds.
#
#     tier 1   tier 0 plus real llm_analysis() calls, schema validation,
#              extraction spot-checks and a runtime projection.
#              Compute nodes only.
#
#  Setup on a login node automatically stops at tier 0 and tells you how to
#  finish. The generated job script runs tier 1 before app.py, so a batch job
#  dies in the first minute instead of wasting 24 hours.
#
#  Usage:
#     ./setup_nlp_study.sh                  resume: skip healthy, repair broken
#     ./setup_nlp_study.sh --status         report state of everything, change nothing
#     ./setup_nlp_study.sh --preflight      check an EXISTING install, build nothing
#     ./setup_nlp_study.sh --force repo     rebuild one resource unconditionally
#     ./setup_nlp_study.sh --force all      rebuild everything
#     ./setup_nlp_study.sh --clean          remove all managed scratch resources
#
#  Resource names for --force: repo, venv, deps, sif, model, all
#
#  The intended sequence is:
#     [login]   ./setup_nlp_study.sh            builds everything, tier 0
#     [qlogin]  ./setup_nlp_study.sh --preflight  tier 1, proves the LLM works
#     [login]   qsub ~/nlp-study.job --analyze --csv ./notes.csv
#
#  Optional environment overrides:
#     NLP_PYTHON=/path/to/python3.12    Skip Python auto-detection
#     NLP_FAST_ROOT / NLP_BULK_ROOT     Force scratch locations
#     PIP_INSTALL_ARGS="..."            Default: --only-binary=:all:
#     MODEL=name:tag                    Override the model (normally read
#                                       straight out of llm_analysis.py)
#     SKIP_SMOKE_TEST=1                 Skip the preflight entirely
#     FORCE_SMOKE_TEST=1                Run tier 1 even on a login node (do not)
#     NLP_PREFLIGHT_REPS=3              Inference reps during tier 1
#     NLP_PREFLIGHT_BUDGET=600          Per-call timeout, seconds
#     NLP_NOTE_ESTIMATE=0               Row count in notes.csv, for projections
#     NLP_JOB_QUEUE=UI                  Queue baked into the generated job script
#     NLP_JOB_GPU=1                     Bake GPU directives into the job script
# =============================================================================

set -euo pipefail

# -----------------------------------------------------------------------------
# Static configuration
# -----------------------------------------------------------------------------
readonly HAWKID="${USER}"
readonly REPO_URL="https://github.com/benpodium8/nlp-study.git"
readonly CONTAINER_URI="docker://ollama/ollama:latest"

# Fallback only. The authoritative model name is LOCAL_MODEL in
# llm_analysis.py, and detect_repo_model() adopts it once the repo exists.
# The repo's own history ("change model from gemma4:e4b to gemma4:e2b")
# is exactly why this is not hardcoded any more: pulling the wrong weights
# costs 7.2 GB and a silent all-null run.
readonly MODEL_FALLBACK="gemma4:e2b"
readonly MODEL_OVERRIDE="${MODEL:-}"   # explicit MODEL=... wins over the repo
MODEL=""
MODEL_SOURCE="unresolved"

readonly JOB_SCRIPT="${HOME}/nlp-study.job"
readonly ENV_SCRIPT="${HOME}/nlp-study-env.sh"
readonly LOG_DIR="${HOME}/nlp-study-logs"
readonly SETUP_LOG="${HOME}/nlp-study-setup-$(date +%Y%m%d-%H%M%S).log"

readonly JOB_QUEUE="${NLP_JOB_QUEUE:-UI}"

# Extra flags for `pip install -r requirements.txt`.
#
# Default is --only-binary=:all: because this cluster runs CentOS 7 (glibc 2.17,
# GCC 4.8). When pip cannot find a wheel it falls back to building from an
# sdist, and for spacy/thinc/blis that either burns ten minutes before dying or
# produces a broken build. Wheels-only turns that into an immediate, legible
# "no matching distribution found" naming the exact package.
#
# Override:  PIP_INSTALL_ARGS="--only-binary=:all: --no-cache-dir" ./setup_nlp_study.sh
# Disable:   PIP_INSTALL_ARGS="" ./setup_nlp_study.sh
readonly PIP_ARGS_DEFAULT="--only-binary=:all:"

# Resolved at runtime by resolve_scratch_roots(); Argon does not mount the same
# scratch filesystems on every host.
PROJECT_ROOT=""   # many small files: git repo + virtualenv
ASSET_ROOT=""     # few large files: .sif image + model blobs
REPO_DIR=""
VENV_DIR=""
SIF_PATH=""
MODELS_DIR=""
APPT_CACHE=""
APPT_TMP=""
PREFLIGHT_PY=""

PYTHON_BIN=""
PYTHON_VER=""
OLLAMA_PID=""
NV_FLAG=""
MODE="setup"
FORCE_LIST=""
ASSUME_YES=""

# -----------------------------------------------------------------------------
# Output helpers
# -----------------------------------------------------------------------------
if [[ -t 1 ]]; then
    C_RESET=$'\033[0m'; C_DIM=$'\033[2m';     C_CYAN=$'\033[36m'
    C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'
    C_BOLD=$'\033[1m'
else
    C_RESET=''; C_DIM=''; C_CYAN=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_BOLD=''
fi

_ts() { date '+%H:%M:%S'; }

section() {
    printf '\n%s%s══════════════════════════════════════════════════════════════%s\n' \
        "$C_BOLD" "$C_CYAN" "$C_RESET"
    printf '%s%s  %s%s\n' "$C_BOLD" "$C_CYAN" "$*" "$C_RESET"
    printf '%s%s══════════════════════════════════════════════════════════════%s\n' \
        "$C_BOLD" "$C_CYAN" "$C_RESET"
}
log()  { printf '%s[%s]%s %s\n' "$C_DIM" "$(_ts)" "$C_RESET" "$*"; }
ok()   { printf '%s[%s]   OK   %s%s\n' "$C_GREEN" "$(_ts)" "$*" "$C_RESET"; }
warn() { printf '%s[%s]  WARN  %s%s\n' "$C_YELLOW" "$(_ts)" "$*" "$C_RESET"; }
err()  { printf '%s[%s] ERROR  %s%s\n' "$C_RED" "$(_ts)" "$*" "$C_RESET" >&2; }
die()  { err "$*"; exit 1; }

run() {
    printf '%s         $ %s%s\n' "$C_DIM" "$*" "$C_RESET"
    "$@"
}

# `git -C <path>` requires git >= 1.8.5. CentOS 7 ships 1.8.3.1, where -C is an
# unknown option. Use a subshell cd instead, which works on every git ever.
git_in() {
    local dir="$1"; shift
    ( cd "$dir" && git "$@" )
}

usage() {
    sed -n '2,80p' "$0" | sed 's/^#//; s/^ //'
}

# Where are we? Decides whether inference is allowed.
#   login    -> login node. 1 logical CPU, 16G, killer. Tier 0 only.
#   job      -> inside an SGE allocation (qlogin or batch). Tier 1 fine.
#   compute  -> not a login node and no SGE vars. Treat as tier 1 capable,
#               but say so, because it usually means something is unusual.
node_kind() {
    if [[ -n "${JOB_ID:-}" || -n "${PE_HOSTFILE:-}" || -n "${SGE_TASK_ID:-}" ]]; then
        printf 'job'; return
    fi
    if [[ "$(hostname)" == *login* ]]; then
        printf 'login'; return
    fi
    printf 'compute'
}

# -----------------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --status)      MODE="status";    shift ;;
        --clean)       MODE="clean";     shift ;;
        --preflight)   MODE="preflight"; shift ;;
        --yes|-y)      ASSUME_YES="1";   shift ;;
        --force)
            [[ $# -ge 2 ]] || die "--force needs a resource name (repo|venv|deps|sif|model|all)"
            FORCE_LIST="${FORCE_LIST},${2}"; shift 2 ;;
        --force=*)     FORCE_LIST="${FORCE_LIST},${1#--force=}"; shift ;;
        -h|--help)     usage; exit 0 ;;
        *)             die "Unknown option: $1  (try --help)" ;;
    esac
done

# Backwards compatibility with the earlier env-var flags.
[[ -n "${FORCE_VENV:-}" ]] && FORCE_LIST="${FORCE_LIST},venv"
[[ -n "${FORCE_SIF:-}"  ]] && FORCE_LIST="${FORCE_LIST},sif"

force_requested() {
    [[ ",${FORCE_LIST}," == *",all,"* ]] && return 0
    [[ ",${FORCE_LIST}," == *",${1},"* ]] && return 0
    return 1
}

# -----------------------------------------------------------------------------
# Traps
# -----------------------------------------------------------------------------
stop_ollama() {
    if [[ -n "$OLLAMA_PID" ]] && kill -0 "$OLLAMA_PID" 2>/dev/null; then
        log "Stopping setup-time Ollama server (pid ${OLLAMA_PID})"
        kill "$OLLAMA_PID" 2>/dev/null || true
        wait "$OLLAMA_PID" 2>/dev/null || true
    fi
    OLLAMA_PID=""
}
trap stop_ollama EXIT

on_error() {
    local exit_code=$1 line=$2
    printf '\n'
    err "Setup failed at line ${line} (exit code ${exit_code})."
    err "Full transcript: ${SETUP_LOG}"
    printf '\n%sState at failure:%s\n' "$C_BOLD" "$C_RESET"
    printf '  Host          : %s  (%s)\n' "$(hostname)" "$(node_kind)"
    printf '  Python in use : %s\n' "${PYTHON_BIN:-<not yet detected>}"
    printf '  Repo dir      : %s\n' "${REPO_DIR:-<not yet resolved>}"
    printf '  Venv dir      : %s\n' "${VENV_DIR:-<not yet resolved>}"
    printf '  SIF path      : %s\n' "${SIF_PATH:-<not yet resolved>}"
    printf '  Models dir    : %s\n' "${MODELS_DIR:-<not yet resolved>}"
    printf '  Model         : %s (%s)\n' "${MODEL:-<not yet resolved>}" "$MODEL_SOURCE"
    if [[ -n "${ASSET_ROOT:-}" && -f "${ASSET_ROOT}/ollama-setup.log" ]]; then
        printf '\n%sLast 30 lines of the Ollama server log:%s\n' "$C_BOLD" "$C_RESET"
        tail -n 30 "${ASSET_ROOT}/ollama-setup.log" || true
    fi
    printf '\n%sNothing here is fatal. Re-run the script: anything left half-built%s\n' "$C_BOLD" "$C_RESET"
    printf '%sis detected and rebuilt automatically. Use --status to inspect first.%s\n\n' "$C_BOLD" "$C_RESET"
    exit "$exit_code"
}
trap 'on_error $? $LINENO' ERR

exec > >(tee -a "$SETUP_LOG") 2>&1

# =============================================================================
#  Resource state machine
# =============================================================================
#  Each verify_* function sets V_STATE to healthy|broken|absent and, when
#  broken, a human-readable V_REASON. ensure_resource() then decides.
# -----------------------------------------------------------------------------
V_STATE="absent"
V_REASON=""

ensure_resource() {
    local name="$1" verify="$2" destroy="$3" create="$4"

    V_STATE="absent"; V_REASON=""
    "$verify"

    if force_requested "$name"; then
        if [[ "$V_STATE" != "absent" ]]; then
            warn "[${name}] --force given: removing the existing copy."
            "$destroy"
        fi
        log "[${name}] building ..."
        "$create"
        ok "[${name}] rebuilt."
        return
    fi

    case "$V_STATE" in
        healthy)
            ok "[${name}] already present and healthy — skipping."
            ;;
        broken)
            warn "[${name}] present but unusable: ${V_REASON}"
            warn "[${name}] removing the damaged copy and rebuilding."
            "$destroy"
            "$create"
            ok "[${name}] rebuilt."
            ;;
        absent)
            log "[${name}] not present — creating."
            "$create"
            ok "[${name}] created."
            ;;
    esac
}

# Move anything that looks like user data out of a directory before deleting it.
# The pipeline keeps notes.csv, the SQLite DB and output/ inside the repo, and
# all three are PHI. Never silently rm -rf them.
preserve_user_data() {
    local dir="$1"
    [[ -d "$dir" ]] || return 0

    local stash="${PROJECT_ROOT}/preserved/$(date +%Y%m%d-%H%M%S)"
    local candidates=() existing=() p

    shopt -s nullglob
    candidates=( "$dir"/*.csv "$dir"/*.db "$dir"/*.sqlite "$dir"/*.sqlite3 )
    shopt -u nullglob
    # 'output' has no wildcard, so nullglob would not strip it when absent.
    # Filter on real existence rather than trusting the glob results.
    candidates+=( "$dir/output" )
    for p in ${candidates[@]+"${candidates[@]}"}; do
        [[ -e "$p" ]] && existing+=("$p")
    done

    [[ ${#existing[@]} -gt 0 ]] || return 0

    warn "Found user data in ${dir} — moving it aside rather than deleting:"
    mkdir -p "$stash"
    for p in "${existing[@]}"; do
        mv "$p" "${stash}/" && warn "    preserved: $(basename "$p")"
    done
    warn "Saved to: ${stash}"
    warn "That path is still on scratch and subject to cleanup. Copy anything"
    warn "you care about to durable storage."
}

# The model the program will actually ask for. Read it out of the repo rather
# than trusting the constant at the top of this file, so a `git pull` that
# swaps models cannot leave us serving weights nobody requests.
detect_repo_model() {
    local f="${REPO_DIR}/llm_analysis.py" found=""
    if [[ -r "$f" ]]; then
        found="$(sed -n 's/^[[:space:]]*LOCAL_MODEL[[:space:]]*=[[:space:]]*["'"'"']\([^"'"'"']*\)["'"'"'].*/\1/p' "$f" | head -1)"
    fi

    if [[ -n "$MODEL_OVERRIDE" ]]; then
        MODEL="$MODEL_OVERRIDE"
        MODEL_SOURCE="MODEL env override"
        if [[ -n "$found" && "$found" != "$MODEL" ]]; then
            warn "MODEL=${MODEL} was given, but llm_analysis.py asks for ${found}."
            warn "The program will call ${found}, so the preflight will fail on this"
            warn "unless you also edit the repo. Drop the override to follow the repo."
        fi
        return 0
    fi

    if [[ -n "$found" ]]; then
        MODEL="$found"
        MODEL_SOURCE="llm_analysis.py"
        if [[ "$found" != "$MODEL_FALLBACK" ]]; then
            warn "The repo has moved off ${MODEL_FALLBACK}: llm_analysis.py now"
            warn "requests ${found}. Following the repo and pulling that instead."
        fi
    else
        MODEL="$MODEL_FALLBACK"
        MODEL_SOURCE="fallback (could not parse llm_analysis.py)"
        warn "Could not read LOCAL_MODEL from llm_analysis.py; assuming ${MODEL}."
        warn "If the repo was restructured this is a guess. Verify by hand."
    fi
}

# =============================================================================
# Resource definitions
# =============================================================================

# --- repo --------------------------------------------------------------------
verify_repo() {
    if [[ ! -e "$REPO_DIR" ]]; then V_STATE="absent"; return; fi
    if [[ ! -d "$REPO_DIR" ]]; then
        V_STATE="broken"; V_REASON="path exists but is not a directory"; return
    fi
    if [[ -z "$(ls -A "$REPO_DIR" 2>/dev/null)" ]]; then
        rmdir "$REPO_DIR" 2>/dev/null || true
        V_STATE="absent"; return
    fi
    if ! git_in "$REPO_DIR" rev-parse --git-dir >/dev/null 2>&1; then
        V_STATE="broken"
        V_REASON="directory has contents but is not a git repo (interrupted clone)"
        return
    fi
    local missing=() f
    for f in app.py cli.py llm_analysis.py nlp_analysis.py data_worker.py \
             database.py requirements.txt; do
        [[ -f "${REPO_DIR}/${f}" ]] || missing+=("$f")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        V_STATE="broken"; V_REASON="incomplete checkout, missing: ${missing[*]}"; return
    fi
    V_STATE="healthy"
}

destroy_repo() {
    preserve_user_data "$REPO_DIR"
    run rm -rf "$REPO_DIR"
}

create_repo() {
    if ! run git clone "$REPO_URL" "$REPO_DIR"; then
        err "git clone failed."
        err "On CentOS 7, an old git/curl/NSS stack sometimes cannot negotiate"
        err "TLS with GitHub. If the error mentions SSL, TLS or 'gnutls', try:"
        err "    git config --global http.sslVersion tlsv1.2"
        err "and re-run. If it mentions authentication, check that the repo is"
        err "still public."
        return 1
    fi
}

update_repo() {
    log "Fetching latest commits ..."
    if ! git_in "$REPO_DIR" diff --quiet 2>/dev/null; then
        warn "Working tree has local modifications; skipping pull to preserve them."
        warn "Review with: cd ${REPO_DIR} && git status"
        return 0
    fi
    run git_in "$REPO_DIR" fetch --all --prune
    git_in "$REPO_DIR" pull --ff-only || warn "Fast-forward pull declined; leaving checkout as-is."
}

# --- venv --------------------------------------------------------------------
verify_venv() {
    if [[ ! -e "$VENV_DIR" ]]; then V_STATE="absent"; return; fi
    if [[ -z "$(ls -A "$VENV_DIR" 2>/dev/null)" ]]; then
        rmdir "$VENV_DIR" 2>/dev/null || true
        V_STATE="absent"; return
    fi
    if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
        V_STATE="broken"; V_REASON="no usable bin/python (interrupted creation)"; return
    fi
    if ! "${VENV_DIR}/bin/python" -c 'import sys' >/dev/null 2>&1; then
        V_STATE="broken"
        V_REASON="bin/python will not execute (base interpreter moved, or module unloaded)"
        return
    fi
    local have
    have="$("${VENV_DIR}/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    if [[ -n "$PYTHON_VER" && "$have" != "$PYTHON_VER" ]]; then
        V_STATE="broken"
        V_REASON="built against Python ${have} but ${PYTHON_VER} is selected now"
        return
    fi
    V_STATE="healthy"
}

destroy_venv() { run rm -rf "$VENV_DIR"; }
create_venv()  { run "$PYTHON_BIN" -m venv "$VENV_DIR"; }

# --- container image ---------------------------------------------------------
verify_sif() {
    if [[ ! -e "$SIF_PATH" ]]; then V_STATE="absent"; return; fi
    if [[ ! -s "$SIF_PATH" ]]; then
        V_STATE="broken"; V_REASON="file is zero bytes"; return
    fi
    if ! apptainer exec "$SIF_PATH" ollama --version >/dev/null 2>&1; then
        V_STATE="broken"
        V_REASON="image will not execute (truncated or corrupt download)"
        return
    fi
    V_STATE="healthy"
}

destroy_sif() {
    run rm -f "$SIF_PATH"
    # A corrupt cached layer will otherwise be reused and reproduce the problem.
    log "Clearing the Apptainer layer cache so the pull genuinely re-downloads."
    apptainer cache clean --force >/dev/null 2>&1 || true
}

create_sif() {
    log "Pulling ${CONTAINER_URI} (~2 GB, then SquashFS conversion). A few minutes."
    run apptainer pull "$SIF_PATH" "$CONTAINER_URI"
}

# =============================================================================
#  Ollama helpers, shared by setup and --preflight
# =============================================================================

detect_gpu() {
    NV_FLAG=""
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
        NV_FLAG="--nv"
        log "GPU visible on this host; Apptainer gets --nv:"
        nvidia-smi -L 2>/dev/null | sed 's/^/         /' || true
    else
        log "No GPU on this host. Inference will be CPU-only."
    fi
}

# OLLAMA_MODELS must be set explicitly: the upstream image defaults to
# /root/.ollama/models, but under Apptainer you are unprivileged, so that path
# is not writable and Ollama fails with a confusing error.
apptainer_ollama() {
    apptainer exec ${NV_FLAG:+--nv} \
        --bind "${MODELS_DIR}:/models" \
        --env OLLAMA_MODELS=/models \
        --env OLLAMA_HOST="$OLLAMA_HOST" \
        --env OLLAMA_KEEP_ALIVE=-1 \
        "$SIF_PATH" "$@"
}

pick_free_port() {
    local py
    py="$(command -v python || command -v python3)"
    "$py" -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'
}

start_ollama_server() {
    local port
    port="$(pick_free_port)"
    export OLLAMA_HOST="127.0.0.1:${port}"
    log "Ollama will listen on ${OLLAMA_HOST} for this session."

    OLLAMA_LOG="${ASSET_ROOT}/ollama-setup.log"
    : > "$OLLAMA_LOG"

    log "Starting Ollama server ..."
    apptainer_ollama ollama serve >> "$OLLAMA_LOG" 2>&1 &
    OLLAMA_PID=$!
    log "pid ${OLLAMA_PID}, log ${OLLAMA_LOG}"

    local i
    for i in $(seq 1 60); do
        if ! kill -0 "$OLLAMA_PID" 2>/dev/null; then
            err "Ollama server died on startup. Log:"; cat "$OLLAMA_LOG"; return 1
        fi
        if curl -sf --max-time 3 "http://${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then
            printf '\n'; ok "API responding after ~$((i * 2))s."; return 0
        fi
        printf '%s         ... waiting (%d/60)%s\r' "$C_DIM" "$i" "$C_RESET"
        sleep 2
    done
    printf '\n'
    err "Server never became ready. Log:"; cat "$OLLAMA_LOG"
    return 1
}

# Report where the model actually got loaded. If this says CPU when you asked
# for a GPU, the run will be an order of magnitude slower than you planned.
report_model_placement() {
    log "Model placement (PROCESSOR column is the one that matters):"
    apptainer_ollama ollama ps 2>/dev/null | sed 's/^/         /' || \
        warn "ollama ps unavailable in this image version."
}

# =============================================================================
#  Preflight harness
# =============================================================================

write_preflight_script() {
    PREFLIGHT_PY="${PROJECT_ROOT}/nlp-preflight.py"
    cat > "$PREFLIGHT_PY" <<'PREFLIGHT_PY_EOF'
#!/usr/bin/env python
"""
nlp-preflight.py  --  generated by setup_nlp_study.sh, do not hand-edit.

Fails fast on anything that would make `python app.py --analyze` quietly
produce a table full of llm_failed=1 rows several hours from now.

data_worker.py catches every LLM exception per note, records llm_failed=1
and moves on. A misconfigured endpoint, a renamed model or a model that
will not emit schema-clean JSON therefore does NOT stop the run -- it
just makes the run worthless. This script front-loads that discovery.

It imports the repo exactly as shipped. It changes nothing.

Tiers:
  --tier 0   static checks only. No inference. Safe on a login node.
  --tier 1   tier 0 plus real llm_analysis() calls on a synthetic note.
             Needs a compute node.
"""

import argparse
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Synthetic note. Entirely fabricated, contains no PHI, and is written to
# exercise every field in llm_analysis.EXPECTED_FIELDS.
# ---------------------------------------------------------------------------
SYNTHETIC_NOTE = """PROCEDURE NOTE

Indication: Dyspepsia with suspected celiac disease.

An upper endoscopy (EGD) was performed using an Olympus H190 gastroscope.
The esophagus was intubated under direct visualization without difficulty.
The stomach and duodenum were examined in full.

Four biopsies were obtained from the duodenal bulb and second portion of
the duodenum for histologic evaluation.

No colonoscopy was performed during this encounter.

The attending physician was assisted by a gastroenterology fellow
throughout the procedure.
"""

# What a correct extraction looks like. 'hard' entries abort the run;
# 'soft' entries only warn, because phrasing sensitivity is expected.
HARD_EXPECT = {
    "Endoscopy": 1,
    "Colonoscopy": 0,
}
SOFT_EXPECT = {
    "DuodenalBiopsiesTaken": 1,
    "NumberOfDuodenalBiopsies": 4,
    "FellowPresent": 1,
}

C = {
    "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
    "green": "\033[32m", "yellow": "\033[33m", "red": "\033[31m",
    "cyan": "\033[36m",
}
if not sys.stdout.isatty():
    C = {k: "" for k in C}

FAILURES = []
WARNINGS = []


def ok(msg):
    print(f"{C['green']}  PASS  {msg}{C['reset']}")


def warn(msg):
    WARNINGS.append(msg)
    print(f"{C['yellow']}  WARN  {msg}{C['reset']}")


def fail(msg, hint=None):
    FAILURES.append(msg)
    print(f"{C['red']}  FAIL  {msg}{C['reset']}")
    if hint:
        for line in hint.strip().splitlines():
            print(f"{C['dim']}        {line}{C['reset']}")


def head(msg):
    print(f"\n{C['bold']}{C['cyan']}-- {msg}{C['reset']}")


class PreflightTimeout(Exception):
    pass


def _alarm(signum, frame):
    raise PreflightTimeout("preflight call exceeded its time budget")


# ===========================================================================
# Tier 0 -- static checks. No inference. Cheap enough for a login node.
# ===========================================================================
def tier0(repo: Path, expected_model: str):
    head("Repo imports")

    sys.path.insert(0, str(repo))
    mods = {}
    for name in ("llm_analysis", "nlp_analysis", "data_worker", "database", "cli"):
        try:
            mods[name] = __import__(name)
            ok(f"import {name}")
        except Exception as e:
            fail(f"import {name}: {type(e).__name__}: {e}",
                 "A dependency is missing from the venv, or the repo moved.\n"
                 "Re-run the setup script without --preflight to repair it.")
            return None

    for name in ("ollama", "spacy", "rich", "click"):
        try:
            __import__(name)
            try:
                from importlib.metadata import version as _ver
                v = _ver(name)
            except Exception:
                v = "?"
            ok(f"import {name} ({v})")
        except Exception as e:
            fail(f"import {name}: {e}")

    head("Model name agreement")

    local_model = getattr(mods["llm_analysis"], "LOCAL_MODEL", None)
    if local_model is None:
        fail("llm_analysis.LOCAL_MODEL is gone",
             "The repo changed shape. The setup script can no longer tell\n"
             "which model to pull. Inspect llm_analysis.py by hand.")
    elif local_model != expected_model:
        fail(f"model drift: llm_analysis.LOCAL_MODEL is {local_model!r}, "
             f"setup pulled {expected_model!r}",
             f"The repo was updated to a different model. Re-run:\n"
             f"    MODEL={local_model} ./setup_nlp_study.sh --force model\n"
             f"and update MODEL at the top of the setup script.")
    else:
        ok(f"llm_analysis.LOCAL_MODEL == {local_model!r}")

    head("Ollama endpoint")

    host = os.environ.get("OLLAMA_HOST")
    if not host:
        fail("OLLAMA_HOST is not set",
             "llm_analysis.py calls the module-level ollama client, which reads\n"
             "OLLAMA_HOST when `import ollama` runs. Export it before python starts.")
        return None
    ok(f"OLLAMA_HOST = {host}")

    base = host if host.startswith("http") else f"http://{host}"
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=10) as r:
            tags = json.loads(r.read().decode())
    except Exception as e:
        fail(f"cannot reach {base}/api/tags: {e}",
             "The server is not running, or is on a different port.\n"
             "In a job this means every note records llm_failed=1 and the\n"
             "run completes 'successfully' with no LLM data at all.")
        return None
    ok(f"{base}/api/tags responded")

    names = [m.get("name", "") for m in tags.get("models", [])]
    target = local_model or expected_model
    if target in names:
        ok(f"model {target!r} is registered with this server")
    else:
        fail(f"model {target!r} not served here (has: {names or 'nothing'})",
             "OLLAMA_MODELS is probably pointing somewhere other than the\n"
             "directory the model was pulled into.")

    head("Client wiring")

    # llm_analysis uses the module-level default client, not an explicit one.
    # Confirm that client actually resolved to our host.
    try:
        import ollama
        client_host = getattr(getattr(ollama, "_client", None), "_client", None)
        client_host = getattr(client_host, "base_url", None)
        if client_host is None:
            warn("could not introspect the default ollama client host "
                 "(client internals differ in this version); relying on /api/tags above")
        else:
            ch = str(client_host).rstrip("/")
            if ch.endswith(base.rstrip("/").split("//")[-1]) or base.rstrip("/") in ch:
                ok(f"default ollama client points at {ch}")
            else:
                fail(f"default ollama client points at {ch}, not {base}",
                     "OLLAMA_HOST was changed after `import ollama`. Export it first.")
    except Exception as e:
        warn(f"client introspection skipped: {e}")

    head("Filesystem")

    for target_dir, label in ((repo, "repo (sqlite db lands here)"),
                              (repo / "output", "output/ (csv exports land here)")):
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            probe = target_dir / ".preflight-probe"
            probe.write_text("x")
            probe.unlink()
            ok(f"writable: {target_dir}  {C['dim']}{label}{C['reset']}")
        except Exception as e:
            fail(f"not writable: {target_dir} ({e})",
                 "app.py creates data.db in the cwd and exports into output/.")

    return mods


# ===========================================================================
# Tier 1 -- real inference through the repo's own code path.
# ===========================================================================
def tier1(mods, reps: int, budget: int, note_estimate: int):
    head(f"Live extraction ({reps} rep(s), {budget}s budget each)")

    llm_analysis = mods["llm_analysis"]
    fn = llm_analysis.llm_analysis
    LLMAnalysisError = llm_analysis.LLMAnalysisError

    timings = []
    parsed = None
    raw = None
    clean_json_first_try = 0

    for i in range(1, reps + 1):
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(budget)
        t0 = time.time()
        try:
            parsed, raw = fn(-1, SYNTHETIC_NOTE)
            dt = time.time() - t0
            timings.append(dt)
            ok(f"rep {i}/{reps}: schema-valid JSON in {dt:.1f}s")
            if raw.strip().startswith("{") and raw.strip().endswith("}"):
                clean_json_first_try += 1
        except PreflightTimeout:
            fail(f"rep {i}/{reps}: no response within {budget}s",
                 "This is the hang you hit before. Either the model is running\n"
                 "on CPU when you expected a GPU, or it is emitting a very long\n"
                 "reasoning trace. Check the PROCESSOR column in `ollama ps`.")
            return
        except LLMAnalysisError as e:
            msg = str(e)
            dt = time.time() - t0
            if "exceeded its time budget" in msg:
                fail(f"rep {i}/{reps}: no response within {budget}s",
                     "Same as above: check `ollama ps` for CPU vs GPU placement.")
                return
            fail(f"rep {i}/{reps}: {msg}  ({dt:.1f}s)",
                 "This exact exception is what data_worker.py swallows per note.\n"
                 "In a real run it becomes llm_failed=1 and the job keeps going.")
            if raw is not None:
                print(f"{C['dim']}        raw head: {raw[:300]!r}{C['reset']}")
            return
        except Exception as e:
            fail(f"rep {i}/{reps}: unexpected {type(e).__name__}: {e}")
            return
        finally:
            signal.alarm(0)

    head("Extraction quality on the synthetic note")

    for field, want in HARD_EXPECT.items():
        got = parsed.get(field)
        if got == want:
            ok(f"{field} == {want}")
        else:
            fail(f"{field} == {got!r}, expected {want}",
                 "This case is unambiguous in the note text. A model that gets it\n"
                 "wrong here will misclassify the real corpus. Do not submit.")

    for field, want in SOFT_EXPECT.items():
        got = parsed.get(field)
        if got == want:
            ok(f"{field} == {want}")
        else:
            warn(f"{field} == {got!r}, expected {want} "
                 f"(phrasing-sensitive; review before trusting the run)")

    scope = parsed.get("ScopeType")
    if scope and "H190" in str(scope):
        ok(f"ScopeType == {scope!r}")
    else:
        warn(f"ScopeType == {scope!r}, expected something like 'Olympus H190'")

    head("Cost projection")

    if raw.strip().startswith("{"):
        ok("raw output is bare JSON; the repair layer is not being exercised")
    else:
        warn("raw output is not bare JSON, so parse_json_with_repair is doing work")
        warn("data_worker retries up to 5x per note on JSON errors; each retry is "
             "a full generation, so this can multiply runtime by 5")
        print(f"{C['dim']}        raw head: {raw.strip()[:200]!r}{C['reset']}")

    if clean_json_first_try < reps:
        warn(f"only {clean_json_first_try}/{reps} reps returned bare JSON "
             f"(inconsistent formatting across calls)")

    avg = sum(timings) / len(timings)
    spread = max(timings) - min(timings)
    print(f"\n        per-note latency : {avg:.1f}s avg, "
          f"{min(timings):.1f}-{max(timings):.1f}s range")
    if spread > avg:
        warn("latency spread exceeds the mean; the node is likely contended")

    for n in (100, 1000, note_estimate):
        if n <= 0:
            continue
        hrs = avg * n / 3600.0
        print(f"        {n:>6} notes    ~{hrs:6.1f} h  "
              f"{C['dim']}(single-threaded, no retries){C['reset']}")
    print()

    if avg > 60:
        warn("over a minute per note. Request a GPU queue, or expect a very "
             "long h_rt. See the notes in the generated job script.")


def main():
    p = argparse.ArgumentParser(description="Fail-fast preflight for nlp-study.")
    p.add_argument("--repo", required=True, type=Path)
    p.add_argument("--model", required=True)
    p.add_argument("--tier", type=int, default=1, choices=(0, 1))
    p.add_argument("--reps", type=int,
                   default=int(os.environ.get("NLP_PREFLIGHT_REPS", "3")))
    p.add_argument("--budget", type=int,
                   default=int(os.environ.get("NLP_PREFLIGHT_BUDGET", "600")))
    p.add_argument("--notes", type=int,
                   default=int(os.environ.get("NLP_NOTE_ESTIMATE", "0")),
                   help="row count in notes.csv, for the runtime projection")
    args = p.parse_args()

    print(f"{C['bold']}nlp-study preflight  (tier {args.tier}){C['reset']}")
    print(f"{C['dim']}repo={args.repo}  model={args.model}{C['reset']}")

    mods = tier0(args.repo, args.model)

    if FAILURES:
        summarise(args.tier)
        return 1

    if args.tier >= 1 and mods is not None:
        tier1(mods, args.reps, args.budget, args.notes)

    return summarise(args.tier)


def summarise(tier):
    print()
    if FAILURES:
        print(f"{C['red']}{C['bold']}PREFLIGHT FAILED "
              f"({len(FAILURES)} problem(s)){C['reset']}")
        for f in FAILURES:
            print(f"{C['red']}  - {f}{C['reset']}")
        print(f"\n{C['bold']}Do not submit the job. Every one of these becomes a "
              f"silent llm_failed=1{C['reset']}")
        print(f"{C['bold']}row rather than a crash, so the run would waste its "
              f"full walltime.{C['reset']}\n")
        return 1

    if WARNINGS:
        print(f"{C['yellow']}{C['bold']}PREFLIGHT PASSED with "
              f"{len(WARNINGS)} warning(s){C['reset']}")
    else:
        print(f"{C['green']}{C['bold']}PREFLIGHT PASSED{C['reset']}")

    if tier == 0:
        print(f"{C['dim']}Static checks only. The model has not actually been "
              f"run. Re-check on a compute node.{C['reset']}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
PREFLIGHT_PY_EOF
    chmod +x "$PREFLIGHT_PY"
    ok "Wrote ${PREFLIGHT_PY}"
}

# Project real runtime rather than a round number, when notes.csv is staged.
# Only the row count is read; no note content is touched.
estimate_note_count() {
    if [[ -z "${NLP_NOTE_ESTIMATE:-}" && -f "${REPO_DIR}/notes.csv" ]]; then
        local lines
        lines="$(wc -l < "${REPO_DIR}/notes.csv" 2>/dev/null || echo 1)"
        NLP_NOTE_ESTIMATE="$(( lines > 1 ? lines - 1 : 0 ))"
        log "notes.csv is staged: ${NLP_NOTE_ESTIMATE} rows, used for the projection."
    fi
    export NLP_NOTE_ESTIMATE="${NLP_NOTE_ESTIMATE:-0}"
}

run_preflight() {
    local tier="$1"
    estimate_note_count
    python "$PREFLIGHT_PY" \
        --repo "$REPO_DIR" \
        --model "$MODEL" \
        --tier "$tier"
}

# =============================================================================
# 0. Environment report
# =============================================================================
section "0. Environment report"

NODE_KIND="$(node_kind)"

log "Started $(date)   mode=${MODE}   force='${FORCE_LIST#,}'"
log "Transcript: ${SETUP_LOG}"
printf '\n'
printf '  HawkID            : %s\n' "$HAWKID"
printf '  Hostname          : %s\n' "$(hostname)"
printf '  Node kind         : %s\n' "$NODE_KIND"
printf '  SGE JOB_ID        : %s\n' "${JOB_ID:-<none>}"
printf '  OS                : %s\n' "$(cat /etc/redhat-release 2>/dev/null || echo unknown)"
printf '  Kernel            : %s\n' "$(uname -r)"
printf '  Bash              : %s\n' "$BASH_VERSION"
printf '  System glibc      : %s\n' "$(ldd --version 2>/dev/null | head -1)"
printf '  HOME              : %s\n' "$HOME"
printf '\n'

case "$NODE_KIND" in
    login)
        if [[ "$MODE" == "setup" ]]; then
            ok "Running on a login node — correct for building (needs internet)."
        else
            warn "Running on a login node. Inference is capped at one logical CPU"
            warn "and 16G here, so tier 1 of the preflight will be refused."
        fi ;;
    job)
        ok "Inside an SGE allocation (JOB_ID=${JOB_ID:-?}) — inference is allowed."
        if [[ "$MODE" == "setup" ]]; then
            warn "Building from inside a job: compute nodes often lack outbound"
            warn "internet, so the clone/pull steps may fail. Build on a login node."
        fi ;;
    compute)
        warn "Not a login node and no SGE variables set. Proceeding, but if this"
        warn "is an unscheduled host you may be taking resources you were not given."
        if [[ "$MODE" == "setup" ]]; then
            warn "Press Ctrl-C within 10 seconds to abort."
            sleep 10
        fi ;;
esac

if [[ "$MODE" == "setup" ]]; then
    log "The glibc above is why we do not use Ollama's install.sh: the upstream"
    log "binary needs GLIBC_2.27+ and will not start on CentOS 7 (glibc 2.17)."
fi

# =============================================================================
# 1. Prerequisite checks
# =============================================================================
section "1. Prerequisite checks"

for cmd in git curl apptainer; do
    if command -v "$cmd" >/dev/null 2>&1; then
        ok "$(printf '%-10s' "$cmd") -> $(command -v "$cmd")"
    else
        die "Required command '$cmd' not found on PATH."
    fi
done
log "Apptainer version: $(apptainer --version 2>&1)"
log "git version: $(git --version 2>&1)"

# Informational only: the script never uses `git -C`, but knowing which side of
# the 1.8.5 boundary you are on explains a lot of unrelated git advice online.
if git -C / --version >/dev/null 2>&1; then
    ok "git supports 'git -C' (>= 1.8.5)."
else
    warn "git predates 1.8.5, so 'git -C <path>' is unsupported on this host."
    warn "This script uses subshell 'cd' instead and is unaffected."
fi

if [[ "$MODE" == "setup" ]]; then
    log "Checking outbound network access ..."
    if curl -sfI --max-time 20 https://github.com >/dev/null 2>&1; then
        ok "Outbound HTTPS works."
    else
        die "Cannot reach https://github.com. Run this from a login node."
    fi
fi

# =============================================================================
# 2. Resolve scratch locations
# =============================================================================
section "2. Scratch filesystem layout"

log "Scratch filesystems visible on $(hostname):"
for fs in /scratch /nfsscratch /localscratch; do
    if [[ -d "$fs" ]]; then
        printf '  %-14s present   %s\n' "$fs" \
            "$(df -h "$fs" 2>/dev/null | awk 'NR==2 {printf "%s avail of %s", $4, $2}')"
    else
        printf '  %-14s %sNOT MOUNTED on this host%s\n' "$fs" "$C_YELLOW" "$C_RESET"
    fi
done
printf '\n'
log "Mount table entries mentioning scratch:"
grep -i scratch /proc/mounts | sed 's/^/         /' || printf '         (none)\n'
printf '\n'

# Existence is not usability: /scratch/Users is often root-owned with per-user
# directories provisioned separately.
probe_writable() {
    local dir="$1"
    [[ -d "$(dirname "$dir")" ]] || return 1
    mkdir -p "$dir" 2>/dev/null || return 1
    local probe="${dir}/.write-probe.$$"
    touch "$probe" 2>/dev/null || return 1
    rm -f "$probe" 2>/dev/null
    return 0
}

resolve_scratch_roots() {
    local fast_candidates=("/scratch/Users/${HAWKID}" "/nfsscratch/Users/${HAWKID}")
    local bulk_candidates=("/nfsscratch/Users/${HAWKID}" "/scratch/Users/${HAWKID}")
    local c

    if [[ -n "${NLP_FAST_ROOT:-}" ]]; then
        fast_candidates=("$NLP_FAST_ROOT"); log "NLP_FAST_ROOT override: ${NLP_FAST_ROOT}"
    fi
    if [[ -n "${NLP_BULK_ROOT:-}" ]]; then
        bulk_candidates=("$NLP_BULK_ROOT"); log "NLP_BULK_ROOT override: ${NLP_BULK_ROOT}"
    fi

    for c in "${fast_candidates[@]}"; do
        if probe_writable "$c"; then PROJECT_ROOT="${c}/nlp-study"; break; fi
        log "  not usable for repo/venv: ${c}"
    done
    for c in "${bulk_candidates[@]}"; do
        if probe_writable "$c"; then ASSET_ROOT="${c}/nlp-study-assets"; break; fi
        log "  not usable for image/models: ${c}"
    done
}

log "Resolving usable scratch roots for HawkID '${HAWKID}' ..."
resolve_scratch_roots

if [[ -z "$PROJECT_ROOT" || -z "$ASSET_ROOT" ]]; then
    err "No writable scratch directory found for '${HAWKID}'."
    printf '\n'
    err "You cannot mount these yourself; they are system mounts owned by ITS."
    err "  1. Are you on a login node?   hostname"
    err "  2. Does your directory exist?  ls -ld /nfsscratch/Users/${HAWKID}"
    err "  3. If not, email research-computing@uiowa.edu."
    err "  4. To use somewhere else entirely:"
    err "       NLP_FAST_ROOT=/path NLP_BULK_ROOT=/path $0"
    exit 1
fi

REPO_DIR="${PROJECT_ROOT}/repo"
VENV_DIR="${PROJECT_ROOT}/venv"
SIF_PATH="${ASSET_ROOT}/ollama.sif"
MODELS_DIR="${ASSET_ROOT}/ollama-models"
APPT_CACHE="${ASSET_ROOT}/apptainer-cache"
APPT_TMP="${ASSET_ROOT}/apptainer-tmp"
PREFLIGHT_PY="${PROJECT_ROOT}/nlp-preflight.py"

ok "Repo + venv    -> ${PROJECT_ROOT}"
ok "Image + models -> ${ASSET_ROOT}"

mkdir -p "$PROJECT_ROOT" "$ASSET_ROOT" "$MODELS_DIR" "$APPT_CACHE" "$APPT_TMP" "$LOG_DIR"
# NOTE: deliberately NOT creating REPO_DIR or VENV_DIR here. Pre-creating them
# is what made the previous version fail on re-run: git clone refuses a
# non-empty target. Their create_* functions own them.

export APPTAINER_CACHEDIR="$APPT_CACHE"
export APPTAINER_TMPDIR="$APPT_TMP"
export SINGULARITY_CACHEDIR="$APPT_CACHE"
export SINGULARITY_TMPDIR="$APPT_TMP"

# Sweep leftover build scratch from an interrupted `apptainer pull`; these can
# be several GB. Only touch entries older than an hour so a concurrent run of
# this script is never disturbed.
if [[ -d "$APPT_TMP" ]]; then
    stale="$(find "$APPT_TMP" -mindepth 1 -maxdepth 1 -mmin +60 2>/dev/null | wc -l)"
    if [[ "$stale" -gt 0 ]]; then
        warn "Clearing ${stale} stale Apptainer build dir(s) from ${APPT_TMP}"
        find "$APPT_TMP" -mindepth 1 -maxdepth 1 -mmin +60 -exec rm -rf {} + 2>/dev/null || true
    fi
fi

# Kill orphaned servers from an earlier run that died without its EXIT trap.
if [[ -n "$SIF_PATH" ]]; then
    orphans="$(pgrep -u "$HAWKID" -f "$SIF_PATH" 2>/dev/null || true)"
    if [[ -n "$orphans" ]]; then
        warn "Found orphaned Ollama process(es) from a previous run: ${orphans//$'\n'/ }"
        # shellcheck disable=SC2086
        kill $orphans 2>/dev/null || true
        sleep 2
        ok "Orphans cleared."
    fi
fi

# The model name is a property of the repo, so resolve it as soon as the repo
# path is known and a checkout might already exist.
detect_repo_model
log "Model: ${MODEL}   (source: ${MODEL_SOURCE})"

# =============================================================================
#  --status
# =============================================================================
if [[ "$MODE" == "status" ]]; then
    section "Resource status"
    report() {
        local name="$1" verify="$2" path="$3"
        V_STATE="absent"; V_REASON=""
        "$verify"
        local colour="$C_YELLOW"
        [[ "$V_STATE" == "healthy" ]] && colour="$C_GREEN"
        [[ "$V_STATE" == "broken"  ]] && colour="$C_RED"
        printf '  %-10s %s%-8s%s %s\n' "$name" "$colour" "$V_STATE" "$C_RESET" "$path"
        [[ -n "$V_REASON" ]] && printf '             %sreason: %s%s\n' "$C_DIM" "$V_REASON" "$C_RESET"
        return 0
    }
    report repo verify_repo "$REPO_DIR"
    report venv verify_venv "$VENV_DIR"
    report sif  verify_sif  "$SIF_PATH"
    if [[ -d "$MODELS_DIR" ]] && [[ -n "$(ls -A "$MODELS_DIR" 2>/dev/null)" ]]; then
        printf '  %-10s %s%-8s%s %s (%s)\n' model "$C_GREEN" present "$C_RESET" \
            "$MODELS_DIR" "$(du -sh "$MODELS_DIR" 2>/dev/null | cut -f1)"
    else
        printf '  %-10s %s%-8s%s %s\n' model "$C_YELLOW" absent "$C_RESET" "$MODELS_DIR"
    fi
    if [[ -f "$PREFLIGHT_PY" ]]; then
        printf '  %-10s %s%-8s%s %s\n' preflight "$C_GREEN" present "$C_RESET" "$PREFLIGHT_PY"
    else
        printf '  %-10s %s%-8s%s %s\n' preflight "$C_YELLOW" absent "$C_RESET" "$PREFLIGHT_PY"
    fi
    printf '\n'
    printf '  model wanted by llm_analysis.py : %s (%s)\n' "$MODEL" "$MODEL_SOURCE"
    printf '\n'
    for f in "$JOB_SCRIPT" "$ENV_SCRIPT"; do
        [[ -f "$f" ]] && ok "generated: $f" || warn "missing: $f"
    done
    printf '\n'
    log "Re-run without --status to build or repair anything not healthy."
    log "Run --preflight inside a qlogin session to prove the LLM actually works."
    exit 0
fi

# =============================================================================
#  --clean
# =============================================================================
if [[ "$MODE" == "clean" ]]; then
    section "Removing all managed scratch resources"
    printf '\nAbout to delete:\n  %s\n  %s\n\n' "$PROJECT_ROOT" "$ASSET_ROOT"
    warn "User data (csv / db / output) will be moved to ${PROJECT_ROOT}/preserved first."
    if [[ -z "$ASSUME_YES" ]]; then
        if [[ -t 0 ]]; then
            read -r -p "Type 'yes' to continue: " reply
            [[ "$reply" == "yes" ]] || die "Aborted."
        else
            die "Refusing to clean non-interactively without --yes."
        fi
    fi
    preserve_user_data "$REPO_DIR"
    run rm -rf "$REPO_DIR" "$VENV_DIR" "$SIF_PATH" "$MODELS_DIR" "$APPT_CACHE" "$APPT_TMP"
    rm -f "$PREFLIGHT_PY"
    apptainer cache clean --force >/dev/null 2>&1 || true
    ok "Scratch resources removed."
    log "Left alone: ${JOB_SCRIPT}, ${ENV_SCRIPT}, ${LOG_DIR}/, and anything under"
    log "            ${PROJECT_ROOT}/preserved/"
    exit 0
fi

# =============================================================================
#  --preflight
# =============================================================================
#  Checks an existing install and builds nothing. This is what you run inside
#  a qlogin session after building on a login node.
# -----------------------------------------------------------------------------
if [[ "$MODE" == "preflight" ]]; then
    section "Preflight on the existing install"

    check_healthy() {
        local name="$1" verify="$2"
        V_STATE="absent"; V_REASON=""
        "$verify"
        if [[ "$V_STATE" != "healthy" ]]; then
            err "[${name}] is ${V_STATE}${V_REASON:+ (${V_REASON})}."
            err "Preflight builds nothing. Repair it from a login node first:"
            err "    ./setup_nlp_study.sh"
            exit 1
        fi
        ok "[${name}] healthy."
    }

    check_healthy repo verify_repo
    check_healthy venv verify_venv
    check_healthy sif  verify_sif

    if [[ ! -d "$MODELS_DIR" || -z "$(ls -A "$MODELS_DIR" 2>/dev/null)" ]]; then
        err "No model blobs in ${MODELS_DIR}."
        err "Pull them from a login node first: ./setup_nlp_study.sh"
        exit 1
    fi
    ok "[model] blobs present ($(du -sh "$MODELS_DIR" 2>/dev/null | cut -f1))."

    # shellcheck disable=SC1091
    source "${VENV_DIR}/bin/activate"
    log "Interpreter: $(command -v python) ($(python --version 2>&1))"

    write_preflight_script
    detect_gpu

    PREFLIGHT_TIER=1
    if [[ "$NODE_KIND" == "login" && -z "${FORCE_SMOKE_TEST:-}" ]]; then
        PREFLIGHT_TIER=0
        warn "Login node: running tier 0 only. Tier 1 needs a compute node."
    fi

    start_ollama_server || exit 1

    PREFLIGHT_RC=0
    run_preflight "$PREFLIGHT_TIER" || PREFLIGHT_RC=$?

    if [[ "$PREFLIGHT_TIER" -ge 1 ]]; then
        report_model_placement
    fi

    stop_ollama

    if [[ "$PREFLIGHT_RC" -ne 0 ]]; then
        err "Preflight failed. Do not submit the job."
        exit "$PREFLIGHT_RC"
    fi

    if [[ "$PREFLIGHT_TIER" -ge 1 ]]; then
        printf '\n'
        ok "Tier 1 passed on $(hostname). The LLM path works end to end."
        log "Submit from a login node:"
        log "    qsub ~/nlp-study.job --analyze --csv ./notes.csv"
    else
        printf '\n'
        log "Tier 0 passed. Get onto a compute node and repeat:"
        log "    qlogin -q ${JOB_QUEUE} -pe smp 8"
        log "    $0 --preflight"
    fi
    exit 0
fi

# =============================================================================
# 3. Repository
# =============================================================================
section "3. Repository: ${REPO_URL}"

ensure_resource repo verify_repo destroy_repo create_repo

# If we skipped because it was already healthy, still try to bring it current.
if ! force_requested repo && [[ "$V_STATE" == "healthy" ]]; then
    update_repo
fi

log "HEAD: $(git_in "$REPO_DIR" log -1 --format='%h %ad %s' --date=short)"
log "Contents:"
ls -la "$REPO_DIR" | sed 's/^/         /'

# The checkout just changed under us (fresh clone, or a pull that could have
# moved LOCAL_MODEL). Re-read it before anything downloads several GB.
detect_repo_model
ok "Model requested by the repo: ${MODEL}  (source: ${MODEL_SOURCE})"

# =============================================================================
# 4. Python interpreter
# =============================================================================
section "4. Locating a suitable Python interpreter"

WANTED="3.12"
if [[ -f "${REPO_DIR}/.python-version" ]]; then
    raw="$(tr -d '[:space:]' < "${REPO_DIR}/.python-version")"
    log ".python-version pins: ${raw}"
    WANTED="$(printf '%s' "$raw" | cut -d. -f1,2)"
else
    warn "No .python-version file; defaulting to ${WANTED}"
fi
log "Target Python: ${WANTED}"

if ! command -v module >/dev/null 2>&1; then
    for init in /etc/profile.d/modules.sh /etc/profile.d/lmod.sh \
                /usr/share/Modules/init/bash /usr/share/lmod/lmod/init/bash; do
        if [[ -r "$init" ]]; then
            set +u; . "$init" 2>/dev/null || true; set -u
            break
        fi
    done
fi

if command -v module >/dev/null 2>&1; then
    ok "Environment Modules available."
    log "Python modules Argon offers:"
    set +u
    module avail python 2>&1 | sed 's/^/         /' | head -40 || true
    for candidate in "py-${WANTED}" "python/${WANTED}" "python${WANTED}" \
                     "python/3.12" "python/3.11" "python3"; do
        if module load "$candidate" >/dev/null 2>&1; then
            ok "module load ${candidate} -> succeeded"
            break
        fi
    done
    set -u
else
    warn "No 'module' command found; relying on PATH only."
fi

check_python() {
    local cand="$1" v major minor
    command -v "$cand" >/dev/null 2>&1 || return 1
    v="$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" || return 1
    major="${v%%.*}"; minor="${v##*.}"
    [[ "$major" -eq 3 && "$minor" -ge 10 ]] || return 1
    PYTHON_BIN="$(command -v "$cand")"
    PYTHON_VER="$v"
    return 0
}

log "Probing candidate interpreters ..."
for cand in ${NLP_PYTHON:-} "python${WANTED}" python3.13 python3.12 python3.11 python3.10 python3; do
    [[ -z "$cand" ]] && continue
    if check_python "$cand"; then
        ok "Selected ${PYTHON_BIN}  (Python ${PYTHON_VER})"
        break
    fi
    log "  rejected: ${cand} ($(command -v "$cand" 2>/dev/null || echo 'not on PATH'))"
done

if [[ -z "$PYTHON_BIN" ]]; then
    err "No Python >= 3.10 found. Load a module by hand and pass it explicitly:"
    err "    module load python/3.12.5"
    err "    NLP_PYTHON=\$(command -v python3.12) $0"
    exit 1
fi
[[ "$PYTHON_VER" == "$WANTED" ]] || \
    warn "Repo pins ${WANTED} but ${PYTHON_VER} selected; usually fine."

# =============================================================================
# 5. Virtual environment
# =============================================================================
section "5. Virtual environment"

ensure_resource venv verify_venv destroy_venv create_venv

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
log "Interpreter : $(command -v python)  ($(python --version 2>&1))"

# =============================================================================
# 5b. Dependencies
# =============================================================================
section "5b. Dependencies"

deps_satisfied() {
    python - <<'PY' >/dev/null 2>&1
import sys
try:
    from importlib.metadata import distribution
    from packaging.requirements import Requirement
except Exception:
    sys.exit(1)
import pathlib, os
req_file = os.environ["REQ_FILE"]
for line in pathlib.Path(req_file).read_text().splitlines():
    line = line.split("#")[0].strip()
    if not line or line.startswith("-"):
        continue
    try:
        r = Requirement(line)
        d = distribution(r.name)
        if r.specifier and d.version not in r.specifier:
            sys.exit(1)
    except Exception:
        sys.exit(1)
sys.exit(0)
PY
}

export REQ_FILE="${REPO_DIR}/requirements.txt"

if ! force_requested deps && ! force_requested venv && deps_satisfied; then
    ok "[deps] every requirement already satisfied — skipping pip."
else
    log "Upgrading pip toolchain ..."
    run python -m pip install --upgrade pip setuptools wheel
    log "pip version: $(python -m pip --version)"

    log "Wheel platform tags pip accepts on this host:"
    python -m pip debug --verbose 2>/dev/null \
        | sed -n '/Compatible tags/,$p' \
        | grep -E 'manylinux|linux_x86_64|Compatible tags' \
        | head -15 | sed 's/^/         /' \
        || warn "pip debug unavailable; continuing."

    printf '%s         --- requirements.txt ---%s\n' "$C_DIM" "$C_RESET"
    sed 's/^/         /' "$REQ_FILE"
    printf '%s         ------------------------%s\n' "$C_DIM" "$C_RESET"

    # ${VAR-default}, not ${VAR:-default}: PIP_INSTALL_ARGS="" means no flags.
    IFS=' ' read -r -a PIP_ARGS <<< "${PIP_INSTALL_ARGS-$PIP_ARGS_DEFAULT}"

    # Bash 4.2 (CentOS 7) errors on "${arr[@]}" for an empty array under set -u.
    pip_install_requirements() {
        if [[ ${#PIP_ARGS[@]} -gt 0 ]]; then
            log "Extra pip flags: ${PIP_ARGS[*]}"
            run python -m pip install "${PIP_ARGS[@]}" -r "$REQ_FILE"
        else
            warn "No extra pip flags set; source builds are permitted."
            run python -m pip install -r "$REQ_FILE"
        fi
    }

    if ! pip_install_requirements; then
        err "pip install failed."
        printf '\n'
        if [[ " ${PIP_ARGS[*]:-} " == *" --only-binary=:all: "* ]]; then
            err "Wheels-only mode is on, so this is almost certainly"
            err "'Could not find a version that satisfies...' — no wheel exists"
            err "for this platform. Identify the package:"
            err "    source ${VENV_DIR}/bin/activate"
            err "    pip download --only-binary=:all: --no-deps -d /tmp/wt <package>"
            err ""
            err "Then pick one:"
            err "  - Pin an older version with a manylinux2014 wheel."
            err "    spaCy is the usual culprit; try spacy==3.7.5."
            err "  - Allow a source build for that one package:"
            err "        pip install --no-binary <package> <package>"
            err "    then re-run this script; finished work is skipped."
            err "  - Move the dependency stack into a container, as Ollama is."
        else
            err "A source build likely failed. On CentOS 7 (GCC 4.8, glibc 2.17)"
            err "most modern C-extension packages will not compile. Retry with:"
            err "    PIP_INSTALL_ARGS='--only-binary=:all:' $0"
        fi
        exit 1
    fi
    ok "Dependencies installed."
fi

log "Installed packages:"
python -m pip list --format=columns | sed 's/^/         /'

# spaCy trained pipelines, if the repo loads any by name.
#
# As of this writing nlp_analysis.py uses spacy.blank("en") plus a sentencizer,
# so nothing needs downloading. This block exists so that if the repo later
# switches to spacy.load("en_core_web_sm"), setup notices on a login node
# (where there is internet) rather than the job failing on a compute node
# (where there usually is not).
if python -c 'import spacy' 2>/dev/null; then
    ok "spaCy imports cleanly ($(python -c 'import spacy; print(spacy.__version__)'))"
    SPACY_MODELS="$(grep -rhoE "spacy\.load\(\s*[\"'][A-Za-z0-9_]+[\"']" "$REPO_DIR" \
                      --include='*.py' 2>/dev/null \
                    | grep -oE "[\"'][A-Za-z0-9_]+[\"']" | tr -d "\"'" | sort -u || true)"
    if [[ -n "$SPACY_MODELS" ]]; then
        for m in $SPACY_MODELS; do
            if python -c "import ${m}" 2>/dev/null; then
                ok "spaCy pipeline '${m}' already installed — skipping."
            else
                log "Repo calls spacy.load('${m}') -> downloading ..."
                run python -m spacy download "$m"
            fi
        done
    else
        ok "No spacy.load('<name>') calls found; no trained pipeline needed."
    fi
else
    warn "spaCy not importable. If nlp_analysis.py needs it, runs will fail."
fi

# =============================================================================
# 6. Ollama container image
# =============================================================================
section "6. Ollama container image"

ensure_resource sif verify_sif destroy_sif create_sif

log "Image size: $(du -h "$SIF_PATH" | cut -f1)"
log "Ollama version inside the container:"
run apptainer exec "$SIF_PATH" ollama --version

# =============================================================================
# 7. Model
# =============================================================================
section "7. Model: ${MODEL}"

detect_gpu

if force_requested model; then
    warn "[model] --force given: clearing ${MODELS_DIR}"
    run rm -rf "${MODELS_DIR:?}"/*
fi

start_ollama_server || exit 1

if apptainer_ollama ollama list 2>/dev/null | grep -q "^${MODEL}[[:space:]]"; then
    ok "[model] ${MODEL} already present — skipping download."
else
    # Sizes for reference: gemma4:e2b is 5.12B parameters at Q4_K_M, about
    # 7.2 GB on disk. The "e2b" is an on-device/effective label, NOT a
    # 2-billion-parameter model, and it is a reasoning model that emits a
    # thinking trace by default. Both facts matter for how long a note takes.
    log "[model] pulling ${MODEL} into ${MODELS_DIR} ..."
    log "        Expect several GB. An interrupted pull resumes where it stopped."
    run apptainer_ollama ollama pull "$MODEL"
    ok "[model] downloaded."
fi

log "Models available:"
apptainer_ollama ollama list | sed 's/^/         /'
log "Model store size: $(du -sh "$MODELS_DIR" | cut -f1)"

# =============================================================================
# 8. Preflight
# =============================================================================
section "8. Preflight: will app.py actually produce LLM results?"

write_preflight_script

PREFLIGHT_TIER=0
PREFLIGHT_WHY=""

if [[ -n "${SKIP_SMOKE_TEST:-}" ]]; then
    PREFLIGHT_TIER=-1; PREFLIGHT_WHY="SKIP_SMOKE_TEST is set"
elif [[ -n "${FORCE_SMOKE_TEST:-}" ]]; then
    PREFLIGHT_TIER=1;  PREFLIGHT_WHY="FORCE_SMOKE_TEST is set"
elif [[ "$NODE_KIND" == "login" ]]; then
    PREFLIGHT_TIER=0;  PREFLIGHT_WHY="login node"
else
    PREFLIGHT_TIER=1;  PREFLIGHT_WHY="${NODE_KIND} node"
fi

log "Tier ${PREFLIGHT_TIER} selected (${PREFLIGHT_WHY})."

if [[ "$PREFLIGHT_TIER" -lt 0 ]]; then
    warn "Preflight skipped entirely. Nothing has verified that the LLM works."
    warn "Run this before submitting anything:"
    warn "    qlogin -q ${JOB_QUEUE} -pe smp 8"
    warn "    $0 --preflight"
else
    if [[ "$PREFLIGHT_TIER" -eq 0 ]]; then
        printf '\n'
        warn "Tier 1 (real inference) is refused here on purpose."
        warn ""
        warn "Argon caps each user on a login node at 100% of ONE logical CPU"
        warn "and 16G of memory, and kills the largest process past that limit."
        warn "${MODEL} is a multi-billion-parameter reasoning model whose weights"
        warn "alone are several GB. Asking it for one sentence under that cap is"
        warn "what wedged the previous version of this script."
        warn ""
        warn "Everything that can be checked without generating a token will be."
        printf '\n'
    fi

    PREFLIGHT_RC=0
    run_preflight "$PREFLIGHT_TIER" || PREFLIGHT_RC=$?

    if [[ "$PREFLIGHT_TIER" -ge 1 ]]; then
        report_model_placement
    fi

    if [[ "$PREFLIGHT_RC" -ne 0 ]]; then
        stop_ollama
        printf '\n'
        err "Preflight failed. The build is fine, but the pipeline would not"
        err "produce usable LLM output, and data_worker.py would not tell you:"
        err "it records llm_failed=1 per note and keeps going to the end."
        err ""
        err "Fix the failures above, then re-run. Nothing needs rebuilding;"
        err "healthy resources are skipped."
        exit 1
    fi
fi

stop_ollama

# =============================================================================
# 9. Runtime scripts
# =============================================================================
section "9. Generating runtime scripts in \$HOME"

write_generated() {
    local target="$1" tmp="$1.tmp.$$"
    cat > "$tmp"
    if [[ -f "$target" ]] && ! cmp -s "$tmp" "$target"; then
        local backup="${target}.bak.$(date +%Y%m%d-%H%M%S)"
        cp "$target" "$backup"
        warn "$(basename "$target") differed from the generated version."
        warn "Your copy was backed up to $(basename "$backup")"
    fi
    mv "$tmp" "$target"
    chmod +x "$target"
    ok "Wrote ${target}"
}

write_generated "$ENV_SCRIPT" <<EOF
#!/bin/bash
# Generated by setup_nlp_study.sh on $(date)
# Source in a qlogin session:  source ~/nlp-study-env.sh

export NLP_REPO_DIR="${REPO_DIR}"
export NLP_VENV_DIR="${VENV_DIR}"
export NLP_SIF="${SIF_PATH}"
export NLP_MODELS_DIR="${MODELS_DIR}"
export NLP_MODEL="${MODEL}"
export NLP_PREFLIGHT="${PREFLIGHT_PY}"
export APPTAINER_CACHEDIR="${APPT_CACHE}"
export APPTAINER_TMPDIR="${APPT_TMP}"

export OLLAMA_HOST="127.0.0.1:\$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()' 2>/dev/null || echo 11434)"

nlp_gpu_flag() {
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
        echo "--nv"
    fi
}

nlp_start_ollama() {
    apptainer exec \$(nlp_gpu_flag) \\
        --bind "\${NLP_MODELS_DIR}:/models" \\
        --env OLLAMA_MODELS=/models \\
        --env OLLAMA_HOST="\$OLLAMA_HOST" \\
        --env OLLAMA_KEEP_ALIVE=-1 \\
        "\$NLP_SIF" ollama serve > "\${TMPDIR:-/tmp}/ollama-\$\$.log" 2>&1 &
    export NLP_OLLAMA_PID=\$!
    echo "Ollama starting on \$OLLAMA_HOST (pid \$NLP_OLLAMA_PID)"
    for i in \$(seq 1 60); do
        curl -sf --max-time 3 "http://\$OLLAMA_HOST/api/tags" >/dev/null 2>&1 && {
            echo "Ollama ready."; return 0; }
        sleep 2
    done
    echo "Ollama did not start. See \${TMPDIR:-/tmp}/ollama-\$\$.log" >&2
    return 1
}

nlp_stop_ollama() {
    [ -n "\${NLP_OLLAMA_PID:-}" ] && kill "\$NLP_OLLAMA_PID" 2>/dev/null
    unset NLP_OLLAMA_PID
}

# Prove the LLM path works before burning a real run on it.
nlp_preflight() {
    python "\$NLP_PREFLIGHT" --repo "\$NLP_REPO_DIR" --model "\$NLP_MODEL" --tier "\${1:-1}"
}

cd "\$NLP_REPO_DIR"
source "\${NLP_VENV_DIR}/bin/activate"
echo "Ready. Repo: \$NLP_REPO_DIR   Model: \$NLP_MODEL"
echo "Run 'nlp_start_ollama', then 'nlp_preflight', then"
echo "'python app.py --analyze --csv ./notes.csv'"
EOF

# GPU directives are opt-in because UI-GPU has its own queue wait. Requesting a
# *-GPU queue sets ngpus=1 automatically, but pinning a card type matters here:
# the weights need roughly 8-10 GB of VRAM once the KV cache is allocated, so a
# K80 would silently fall back to CPU.
JOB_GPU_DIRECTIVES=""
if [[ -n "${NLP_JOB_GPU:-}" ]]; then
    JOB_GPU_DIRECTIVES="#\$ -l ngpus=1
#\$ -l gpu_p100"
    log "Baking GPU directives into the job script (queue ${JOB_QUEUE})."
    if [[ "$JOB_QUEUE" != *GPU* && "$JOB_QUEUE" != "all.q" ]]; then
        warn "NLP_JOB_GPU=1 but the queue is '${JOB_QUEUE}', which has no GPU nodes."
        warn "This job will sit pending forever. Regenerate with:"
        warn "    NLP_JOB_QUEUE=UI-GPU NLP_JOB_GPU=1 $0"
    fi
else
    JOB_GPU_DIRECTIVES="# No GPU requested. To use one, regenerate with:
#     NLP_JOB_QUEUE=UI-GPU NLP_JOB_GPU=1 $0
# or add by hand:   #\$ -l ngpus=1    and    #\$ -l gpu_p100"
fi

write_generated "$JOB_SCRIPT" <<EOF
#!/bin/bash
# Generated by setup_nlp_study.sh on $(date)
# Submit from anywhere; arguments are forwarded to app.py:
#     qsub ~/nlp-study.job --analyze --csv ./notes.csv
#
# This job runs the preflight BEFORE app.py and exits immediately if it
# fails. That matters because data_worker.py swallows LLM errors per note:
# without the gate, a broken endpoint or model produces a full-length run
# whose LLM columns are all null, with a zero exit status.
#
# Set NLP_SKIP_PREFLIGHT=1 to bypass the gate (not recommended).
#
#\$ -N nlp-study
#\$ -q ${JOB_QUEUE}
#\$ -pe smp 8
#\$ -l h_rt=24:00:00
#\$ -j y
#\$ -o ${LOG_DIR}/
#\$ -m ea
${JOB_GPU_DIRECTIVES}

set -euo pipefail

REPO_DIR="${REPO_DIR}"
VENV_DIR="${VENV_DIR}"
SIF="${SIF_PATH}"
MODELS_DIR="${MODELS_DIR}"
MODEL="${MODEL}"
PREFLIGHT="${PREFLIGHT_PY}"

echo "=============================================================="
echo " nlp-study job \${JOB_ID:-interactive}"
echo " Node    : \$(hostname)"
echo " Started : \$(date)"
echo " Slots   : \${NSLOTS:-unknown}"
echo " Model   : \$MODEL"
echo " Args    : \$*"
echo "=============================================================="

cd "\$REPO_DIR"
source "\${VENV_DIR}/bin/activate"
echo "Python: \$(command -v python) (\$(python --version 2>&1))"

PORT=\$(python -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
export OLLAMA_HOST="127.0.0.1:\${PORT}"
echo "Ollama endpoint: \$OLLAMA_HOST"

GPU_FLAG=""
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    GPU_FLAG="--nv"; echo "GPU detected:"; nvidia-smi -L
else
    echo "No GPU on this node; running \$MODEL on CPU."
fi

apptainer exec \$GPU_FLAG \\
    --bind "\${MODELS_DIR}:/models" \\
    --env OLLAMA_MODELS=/models \\
    --env OLLAMA_HOST="\$OLLAMA_HOST" \\
    --env OLLAMA_KEEP_ALIVE=-1 \\
    "\$SIF" ollama serve > "ollama-\${JOB_ID:-local}.log" 2>&1 &
OLLAMA_PID=\$!
trap 'kill \$OLLAMA_PID 2>/dev/null || true' EXIT

echo "Waiting for Ollama (pid \$OLLAMA_PID) ..."
READY=0
for i in \$(seq 1 90); do
    if ! kill -0 \$OLLAMA_PID 2>/dev/null; then
        echo "Ollama died on startup:" >&2
        cat "ollama-\${JOB_ID:-local}.log" >&2
        exit 1
    fi
    if curl -sf --max-time 3 "http://\$OLLAMA_HOST/api/tags" >/dev/null 2>&1; then
        READY=1; break
    fi
    sleep 2
done
if [ "\$READY" -ne 1 ]; then
    echo "Ollama never became ready:" >&2
    cat "ollama-\${JOB_ID:-local}.log" >&2
    exit 1
fi
echo "Ollama ready."

# ---------------------------------------------------------------------------
# Fail-fast gate. One synthetic, PHI-free note through llm_analysis().
# ---------------------------------------------------------------------------
if [ -z "\${NLP_SKIP_PREFLIGHT:-}" ]; then
    echo "--------------------------------------------------------------"
    echo "Preflight (set NLP_SKIP_PREFLIGHT=1 to bypass)"
    if [ -f "\$PREFLIGHT" ]; then
        set +e
        NLP_PREFLIGHT_REPS="\${NLP_PREFLIGHT_REPS:-1}" \\
        NLP_PREFLIGHT_BUDGET="\${NLP_PREFLIGHT_BUDGET:-900}" \\
        python "\$PREFLIGHT" --repo "\$REPO_DIR" --model "\$MODEL" --tier 1
        PF_RC=\$?
        set -e
        if [ \$PF_RC -ne 0 ]; then
            echo "Preflight failed; aborting before app.py." >&2
            echo "Without this gate the run would finish normally with every" >&2
            echo "LLM column null, because data_worker.py records llm_failed=1" >&2
            echo "per note instead of raising." >&2
            exit \$PF_RC
        fi
    else
        echo "WARNING: \$PREFLIGHT missing; re-run setup_nlp_study.sh." >&2
    fi
    echo "Model placement:"
    apptainer exec \$GPU_FLAG \\
        --bind "\${MODELS_DIR}:/models" \\
        --env OLLAMA_MODELS=/models \\
        --env OLLAMA_HOST="\$OLLAMA_HOST" \\
        "\$SIF" ollama ps 2>/dev/null || true
fi

echo "--------------------------------------------------------------"
set +e
python app.py "\$@"
RC=\$?
set -e
echo "--------------------------------------------------------------"
echo "app.py exited with \$RC at \$(date)"
echo "Reminder: exit 143 outside all.q usually means the memory limit, not a bug."
exit \$RC
EOF

# =============================================================================
# 10. Summary
# =============================================================================
section "10. Setup complete"

cat <<EOF

  Repo          ${REPO_DIR}
  Virtualenv    ${VENV_DIR}
  Container     ${SIF_PATH}
  Model         ${MODEL}  (from ${MODEL_SOURCE})
  Model store   ${MODELS_DIR}  ($(du -sh "$MODELS_DIR" 2>/dev/null | cut -f1))
  Preflight     ${PREFLIGHT_PY}
  Job script    ${JOB_SCRIPT}
  Job logs      ${LOG_DIR}/
  This log      ${SETUP_LOG}

  ${C_BOLD}${C_YELLOW}Put your notes.csv here first:${C_RESET}
      ${REPO_DIR}/notes.csv

  ${C_BOLD}${C_GREEN}1. Prove the LLM works, on a compute node:${C_RESET}

      qlogin -q ${JOB_QUEUE} -pe smp 8
      $0 --preflight
      exit

  ${C_BOLD}${C_GREEN}2. Then submit, from \$HOME on a login node:${C_RESET}

      qsub ~/nlp-study.job --analyze --csv ./notes.csv

  Monitor:
      qstat -u ${HAWKID}
      tail -f ${LOG_DIR}/nlp-study.o\<JOB_ID\>

  Interactive instead:
      qlogin -q ${JOB_QUEUE} -pe smp 8
      source ~/nlp-study-env.sh
      nlp_start_ollama
      nlp_preflight
      python app.py --analyze --csv ./notes.csv

  ${C_DIM}Step 1 is not optional busywork. data_worker.py catches LLM errors per
  note, writes llm_failed=1 and continues, so a misconfigured model does not
  crash the job -- it returns a complete-looking table with no LLM data in it.
  The preflight is the only thing that turns that into a loud failure.

  Re-running this script is always safe: healthy resources are skipped,
  damaged ones rebuilt. Use --status to inspect, --force <name> to rebuild
  one thing, --clean to start over. Scratch is purged on a schedule, so copy
  results out of ${REPO_DIR}/output to durable storage.${C_RESET}

EOF
ok "Done."