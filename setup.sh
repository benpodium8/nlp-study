#!/bin/bash
# =============================================================================
#  setup.sh -- re-runnable setup for nlp-study on the UIowa Argon cluster
#  (SGE + Apptainer). Run it from any node; it does what that node allows:
#
#     login node     clone, venv, pip, container image, model download, then
#                    static checks only (no Ollama process here, see below)
#     qlogin / qsub  the same build steps for anything missing, if the node
#                    has internet (most compute nodes do not), then a live
#                    inference check through the repo's own llm_analysis()
#
#  Argon login nodes cap each user at one logical CPU and 16 GB and kill the
#  largest process over the cap. Earlier versions of this script started
#  `ollama serve` there to pull and smoke-test the model, and hung or died.
#  This version never runs an Ollama process, or `apptainer exec` at all,
#  outside an SGE allocation: the image is pulled with mksquashfs pinned to
#  one thread and the model blobs are downloaded with curl straight into
#  Ollama's on-disk store.
#
#  Each resource (repo, venv, deps, sif, model) is health-checked and is either
#  healthy (skipped), broken (removed and rebuilt) or absent (created), so
#  re-running after a failure is always safe, and a node without internet
#  simply uses what is already built.
#
#  Usage:
#     ./setup.sh                 build or repair, then preflight as far as this node allows
#     ./setup.sh --preflight     check an existing install, build nothing
#     ./setup.sh --status        report state, change nothing
#     ./setup.sh --force NAME    rebuild repo|venv|deps|sif|model|all
#     ./setup.sh --clean [-y]    remove everything on scratch (PHI is moved aside)
#
#  Sequence:  [login] ./setup.sh  ->  [qlogin] ./setup.sh  ->  [login] qsub ~/nlp-study.job --analyze --csv ./notes.csv
#
#  Optional environment overrides:
#     MODEL=name:tag                 model to fetch (default: LOCAL_MODEL in llm_analysis.py)
#     NLP_REPO_URL=...               clone a fork or branch mirror instead of the GitHub repo
#     NLP_PYTHON=/path/to/python     skip interpreter detection
#     NLP_FAST_ROOT, NLP_BULK_ROOT   scratch dirs for repo+venv and image+model
#     NLP_OLLAMA_IMAGE=docker://ollama/ollama:0.12.5   pin the container image
#     PIP_INSTALL_ARGS="..."         default: --only-binary=:all:
#     NLP_JOB_QUEUE=UI NLP_JOB_SLOTS=8 NLP_JOB_HRT=24:00:00 NLP_JOB_GPU=1
#     NLP_CONTEXT_LENGTH=8192        Ollama context window used by the job
#     NLP_PREFLIGHT_REPS=3 NLP_PREFLIGHT_BUDGET=600   live calls / seconds each
#     NLP_ALLOW_INFERENCE=1          allow inference outside SGE (never on a login node)
#     NLP_SKIP_PREFLIGHT=1           (job only) skip the fail-fast gate before app.py
# =============================================================================
# shellcheck disable=SC1090,SC1091   # sourced files (module init, venv activate, env script) are runtime paths
set -Eeuo pipefail

# ----------------------------------------------------------------------------- config
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"; readonly SELF
readonly REPO_URL="${NLP_REPO_URL:-https://github.com/benpodium8/nlp-study.git}"
readonly OLLAMA_IMAGE="${NLP_OLLAMA_IMAGE:-docker://ollama/ollama:latest}"
readonly REGISTRY="https://registry.ollama.ai"
readonly JOB_SCRIPT="${HOME}/nlp-study.job"
readonly ENV_SCRIPT="${HOME}/nlp-study-env.sh"
readonly LOG_DIR="${HOME}/nlp-study-logs"
SETUP_LOG="${LOG_DIR}/setup-$(date +%Y%m%d-%H%M%S).log"; readonly SETUP_LOG
readonly PIP_ARGS_DEFAULT="--only-binary=:all:"   # never build sdists: they fail slowly on old toolchains
readonly JOB_QUEUE="${NLP_JOB_QUEUE:-UI}" JOB_SLOTS="${NLP_JOB_SLOTS:-8}" JOB_HRT="${NLP_JOB_HRT:-24:00:00}"
readonly MODEL_OVERRIDE="${MODEL:-}"
readonly ENV_VERSION=2                             # bump when the generated scripts change shape
readonly HAWKID="${USER:-$(id -un)}"               # SGE sets USER; fall back for stripped environments

MODEL="" MODEL_SOURCE=""
PROJECT_ROOT="" ASSET_ROOT="" REPO_DIR="" VENV_DIR="" SIF_PATH="" SIF_PARTIAL="" MODELS_DIR=""
APPT_CACHE="" APPT_TMP="" PREFLIGHT_PY="" PYTHON_BIN="" PYTHON_VER=""
MODE="setup" FORCE_LIST="" ASSUME_YES=""
V_STATE="absent" V_REASON=""
M_NS="" M_NAME="" M_TAG="" MANIFEST=""
CURL_PID="" LOCK_DIR="" PRESERVED_DIR=""

# ----------------------------------------------------------------------------- output
IS_TTY=""; [[ ! -t 1 ]] || IS_TTY=1
if [[ -n "$IS_TTY" ]]; then
    C_RESET=$'\033[0m' C_DIM=$'\033[2m' C_BOLD=$'\033[1m' C_CYAN=$'\033[36m'
    C_GREEN=$'\033[32m' C_YELLOW=$'\033[33m' C_RED=$'\033[31m'
else
    C_RESET='' C_DIM='' C_BOLD='' C_CYAN='' C_GREEN='' C_YELLOW='' C_RED=''
fi
_ts()     { date '+%H:%M:%S'; }
section() { printf '\n%s%s== %s ==%s\n' "$C_BOLD" "$C_CYAN" "$*" "$C_RESET"; }
log()     { printf '%s[%s]%s %s\n' "$C_DIM" "$(_ts)" "$C_RESET" "$*"; }
ok()      { printf '%s[%s]   OK   %s%s\n' "$C_GREEN" "$(_ts)" "$*" "$C_RESET"; }
warn()    { printf '%s[%s]  WARN  %s%s\n' "$C_YELLOW" "$(_ts)" "$*" "$C_RESET"; }
err()     { printf '%s[%s] ERROR  %s%s\n' "$C_RED" "$(_ts)" "$*" "$C_RESET" >&2; }
die()     { err "$*"; exit 1; }
run()     { printf '%s         $ %s%s\n' "$C_DIM" "$*" "$C_RESET"; "$@"; }
git_in()  { local d="$1"; shift; ( cd "$d" && git "$@" ); }   # `git -C` needs git >= 1.8.5
usage()   { sed -n '/^#  Usage:/,/^# ====/p' "$0" | sed '$d; s/^# \{0,2\}//'; }
in_sge_job()    { [[ -n "${JOB_ID:-}${PE_HOSTFILE:-}" ]]; }
is_login_node() { [[ "$(hostname)" == *login* ]]; }

# ----------------------------------------------------------------------------- arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --status)    MODE="status" ;;
        --clean)     MODE="clean" ;;
        --preflight) MODE="preflight" ;;
        -y|--yes)    ASSUME_YES=1 ;;
        --force)     [[ $# -ge 2 ]] || die "--force needs repo|venv|deps|sif|model|all"; FORCE_LIST="${FORCE_LIST},$2"; shift ;;
        --force=*)   FORCE_LIST="${FORCE_LIST},${1#--force=}" ;;
        -h|--help)   usage; exit 0 ;;
        *)           die "Unknown option: $1 (try --help)" ;;
    esac
    shift
done
force_requested() { [[ ",${FORCE_LIST}," == *",all,"* || ",${FORCE_LIST}," == *",${1},"* ]]; }

# Set by the environment check in setup mode. Nothing that is already healthy
# needs the network, so a compute node without internet can run every mode.
HAVE_NET=1
require_net() {
    [[ "$HAVE_NET" -eq 1 ]] || die "[$1] has to be built or updated, but this node has no outbound internet. Run ${SELF} on a login node first."
}

# One builder at a time per scratch tree: two concurrent runs would append to the
# same partial downloads. mkdir is atomic, NFS included. A lock whose owner is
# dead on this host, or older than six hours, is stale and taken over.
pid_alive() { [[ "$1" =~ ^[1-9][0-9]*$ ]] && kill -0 "$1" 2>/dev/null; }
acquire_lock() {
    local dir="${PROJECT_ROOT}/.setup.lock" host="?" pid=0 since=0
    if ! mkdir "$dir" 2>/dev/null; then
        read -r host pid since < "${dir}/owner" 2>/dev/null || true
        [[ "$since" =~ ^[0-9]+$ ]] || since=0
        if { [[ "$host" == "$(hostname)" ]] && ! pid_alive "$pid"; } || [[ $(( $(date +%s) - since )) -gt 21600 ]]; then
            warn "Taking over a stale setup lock (host ${host}, pid ${pid})."
            rm -rf "$dir"; mkdir "$dir" || die "Could not create ${dir}"
        else
            die "Another setup run is active on ${host} (pid ${pid}, since $(date -d "@${since}" 2>/dev/null || echo '?')). Wait for it, or if it is dead:  rm -rf ${dir}"
        fi
    fi
    printf '%s %s %s\n' "$(hostname)" "$$" "$(date +%s)" > "${dir}/owner"
    LOCK_DIR="$dir"
}
release_lock() { [[ -z "$LOCK_DIR" ]] || rm -rf "$LOCK_DIR"; LOCK_DIR=""; }

# atomic_write FILE (content on stdin): a job sourcing the file never sees it half-written.
atomic_write() {
    local tmp="$1.tmp.$$"
    cat > "$tmp" && chmod +x "$tmp" && mv -f "$tmp" "$1"
}

# ----------------------------------------------------------------------------- resource state machine
# Each verify_NAME sets V_STATE=healthy|broken|absent (and V_REASON); ensure_resource acts on it.
ensure_resource() {
    local name="$1" why
    V_STATE="absent"; V_REASON=""; "verify_$name"
    if force_requested "$name"; then
        why="--force given"
    else
        case "$V_STATE" in
            healthy) ok "[${name}] healthy, skipping"; return 0 ;;
            broken)  why="unusable: ${V_REASON}" ;;
            absent)  why="absent" ;;
        esac
    fi
    case "$name" in repo|sif|model) require_net "$name" ;; esac   # before anything is destroyed
    if [[ "$V_STATE" == "absent" ]]; then
        log "[${name}] ${why}; creating"
    else
        warn "[${name}] ${why}; removing the existing copy and rebuilding"
        "destroy_$name"
    fi
    "create_$name"
    ok "[${name}] ready"
}

# notes.csv, data.db and output/ live inside the repo and are PHI: never rm -rf them.
# They are moved aside before a rebuild and moved back into the fresh checkout.
preserve_user_data() {
    local dir="$1" p
    local -a keep=()
    [[ -d "$dir" ]] || return 0
    shopt -s nullglob
    for p in "$dir"/*.csv "$dir"/*.db "$dir"/*.sqlite "$dir"/*.sqlite3 "$dir"/output; do
        if [[ -e "$p" ]]; then keep+=("$p"); fi
    done
    shopt -u nullglob
    [[ ${#keep[@]} -gt 0 ]] || return 0
    PRESERVED_DIR="${PROJECT_ROOT}/preserved/$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$PRESERVED_DIR"
    mv "${keep[@]}" "$PRESERVED_DIR"/
    warn "User data in ${dir} moved to ${PRESERVED_DIR}"
}
restore_user_data() {   # after a successful re-clone: put this run's stash back
    local dir="$1" p
    [[ -n "$PRESERVED_DIR" && -d "$PRESERVED_DIR" ]] || return 0
    shopt -s nullglob
    for p in "$PRESERVED_DIR"/*; do
        if [[ -e "${dir}/$(basename "$p")" ]]; then
            warn "[repo] not restoring $(basename "$p"): the fresh checkout has one; yours stays in ${PRESERVED_DIR}"
        else
            mv "$p" "$dir"/
        fi
    done
    shopt -u nullglob
    if rmdir "$PRESERVED_DIR" 2>/dev/null; then ok "[repo] user data (csv/db/output) moved back into the new checkout"
    else warn "[repo] some files were left in ${PRESERVED_DIR}"; fi
    PRESERVED_DIR=""
}

# The model the program will actually call is LOCAL_MODEL in llm_analysis.py.
detect_repo_model() {
    local found=""
    if [[ -r "${REPO_DIR}/llm_analysis.py" ]]; then
        found="$(sed -n "s/^LOCAL_MODEL *= *[\"']\([^\"']*\)[\"'].*/\1/p" "${REPO_DIR}/llm_analysis.py" | head -1)"
    fi
    if [[ -n "$MODEL_OVERRIDE" ]]; then
        MODEL="$MODEL_OVERRIDE"; MODEL_SOURCE="MODEL env override"
        [[ -z "$found" || "$found" == "$MODEL" ]] || warn "MODEL=${MODEL} given, but llm_analysis.py asks for ${found}; the program will call ${found}."
    elif [[ -n "$found" ]]; then
        MODEL="$found"; MODEL_SOURCE="llm_analysis.py"
    else
        MODEL=""; MODEL_SOURCE="unknown"
    fi
}

# --- repo --------------------------------------------------------------------
verify_repo() {
    [[ -e "$REPO_DIR" ]] || { V_STATE="absent"; return 0; }
    if [[ -z "$(ls -A "$REPO_DIR" 2>/dev/null)" ]]; then rmdir "$REPO_DIR" 2>/dev/null || true; V_STATE="absent"; return 0; fi
    if ! git_in "$REPO_DIR" rev-parse --verify -q HEAD >/dev/null 2>&1; then
        V_STATE="broken"; V_REASON="not a complete git checkout (interrupted clone)"; return 0
    fi
    # An interrupted checkout leaves tracked files missing; an upstream rename does not, so no rebuild loop.
    if git_in "$REPO_DIR" status --porcelain 2>/dev/null | grep -q '^.D'; then
        V_STATE="broken"; V_REASON="tracked files are missing from the checkout"; return 0
    fi
    V_STATE="healthy"
}
destroy_repo() { preserve_user_data "$REPO_DIR"; run rm -rf "$REPO_DIR"; }
create_repo() {
    run git clone "$REPO_URL" "$REPO_DIR" || die "git clone failed. On an old TLS stack try: git config --global http.sslVersion tlsv1.2"
    restore_user_data "$REPO_DIR"
}
update_repo() {
    if ! git_in "$REPO_DIR" diff --quiet 2>/dev/null; then warn "[repo] local modifications present; not pulling"; return 0; fi
    git_in "$REPO_DIR" pull --ff-only --quiet 2>&1 | sed 's/^/         /' \
        || warn "[repo] pull failed (offline or diverged); keeping the current checkout"
}

# --- venv --------------------------------------------------------------------
verify_venv() {
    [[ -e "$VENV_DIR" ]] || { V_STATE="absent"; return 0; }
    local have
    if ! have="$("${VENV_DIR}/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)"; then
        V_STATE="broken"; V_REASON="bin/python does not run (interrupted creation, or base interpreter gone)"; return 0
    fi
    if [[ -n "$PYTHON_VER" && "$have" != "$PYTHON_VER" ]]; then
        V_STATE="broken"; V_REASON="built on Python ${have}, but ${PYTHON_VER} is selected now"; return 0
    fi
    [[ -r "${VENV_DIR}/bin/activate" ]] || { V_STATE="broken"; V_REASON="incomplete (no bin/activate)"; return 0; }
    V_STATE="healthy"
}
destroy_venv() { run rm -rf "$VENV_DIR"; }
create_venv()  { run "$PYTHON_BIN" -m venv "$VENV_DIR"; }

# --- container image ---------------------------------------------------------
# Pulled to a .partial name and renamed on success, so an existing file is a
# complete one. `apptainer sif list` parses the header without mounting or
# executing anything, which keeps this check safe on a login node.
verify_sif() {
    [[ -e "$SIF_PATH" ]] || { V_STATE="absent"; return 0; }
    if [[ "$(stat -c %s "$SIF_PATH")" -lt 104857600 ]] || ! apptainer sif list "$SIF_PATH" >/dev/null 2>&1; then
        V_STATE="broken"; V_REASON="not a valid SIF image"; return 0
    fi
    V_STATE="healthy"
}
destroy_sif() { run rm -f "$SIF_PATH" "$SIF_PARTIAL"; apptainer cache clean --force >/dev/null 2>&1 || true; }
create_sif() {
    rm -f "$SIF_PARTIAL"
    log "[sif] pulling ${OLLAMA_IMAGE} (a few GB; mksquashfs pinned to one thread, so allow several minutes)"
    run apptainer pull "$SIF_PARTIAL" "$OLLAMA_IMAGE"
    mv "$SIF_PARTIAL" "$SIF_PATH"
}

# --- model -------------------------------------------------------------------
# Fetched with curl straight from the registry into Ollama's own on-disk layout
# (manifests/registry.ollama.ai/<ns>/<name>/<tag> plus blobs/sha256-<hex>), so
# no Ollama process ever runs on the login node. The manifest is written last:
# its presence means every blob is complete. Downloads resume after interruption.
model_parts() {
    local ref="${MODEL%%:*}"
    M_TAG="${MODEL#*:}"; [[ "$M_TAG" != "$MODEL" ]] || M_TAG="latest"
    M_NS="library"; M_NAME="$ref"
    [[ "$ref" != */* ]] || { M_NS="${ref%/*}"; M_NAME="${ref##*/}"; }
    MANIFEST="${MODELS_DIR}/manifests/registry.ollama.ai/${M_NS}/${M_NAME}/${M_TAG}"
}
manifest_blobs() {   # prints "<digest> <size>" per blob (config first); prints nothing for a bad manifest
    local py
    py="$(command -v "${VENV_DIR}/bin/python" || command -v python3)" || return 0   # not `python`: 2.7 on CentOS 7
    "$py" -I - "$1" <<'PY' 2>/dev/null || true
import json, sys
m = json.load(open(sys.argv[1]))
for b in [m["config"]] + m["layers"]:
    print(b["digest"], b["size"])
PY
}
verify_model() {
    [[ -n "$MODEL" ]] || { V_STATE="absent"; V_REASON="model name unknown until the repo exists"; return 0; }
    model_parts
    [[ -f "$MANIFEST" ]] || { V_STATE="absent"; return 0; }
    local d s f n=0
    while read -r d s; do
        f="${MODELS_DIR}/blobs/${d/:/-}"
        if [[ ! -f "$f" || "$(stat -c %s "$f")" != "$s" ]]; then
            V_STATE="broken"; V_REASON="blob ${d:7:12} missing or truncated"; return 0
        fi
        n=$((n + 1))
    done < <(manifest_blobs "$MANIFEST")
    if [[ "$n" -eq 0 ]]; then V_STATE="broken"; V_REASON="manifest unreadable (or no python to parse it)"; else V_STATE="healthy"; fi
}
destroy_model() {
    model_parts
    rm -f "$MANIFEST" "${MANIFEST}.partial"
    # Blobs are kept unless forced: create_model only fetches what is missing or short.
    if force_requested model; then run rm -rf "${MODELS_DIR:?}/blobs"; fi
}
create_model() {
    [[ -n "$MODEL" ]] || die "Cannot tell which model to fetch: LOCAL_MODEL not found in llm_analysis.py. Set MODEL=name:tag."
    model_parts
    local base="${REGISTRY}/v2/${M_NS}/${M_NAME}" tmp="${MANIFEST}.partial" d s total=0 count=0
    mkdir -p "${MANIFEST%/*}" "${MODELS_DIR}/blobs"
    curl -fsSL --retry 3 -H 'Accept: application/vnd.docker.distribution.manifest.v2+json' \
         -o "$tmp" "${base}/manifests/${M_TAG}" \
        || die "registry.ollama.ai has no '${MODEL}'. Check LOCAL_MODEL in llm_analysis.py (or MODEL=...)."
    while read -r d s; do total=$((total + s)); count=$((count + 1)); done < <(manifest_blobs "$tmp")
    [[ "$count" -gt 0 ]] || die "Unreadable manifest for ${MODEL} (left at ${tmp})."
    log "[model] ${MODEL}: ${count} blobs, $((total / 1048576)) MB, into ${MODELS_DIR}"
    while read -r d s; do
        fetch_blob "${base}/blobs/${d}" "${MODELS_DIR}/blobs/${d/:/-}" "$s" "${d#sha256:}"
    done < <(manifest_blobs "$tmp")
    mv "$tmp" "$MANIFEST"
}
fetch_blob() {   # fetch_blob URL DEST SIZE SHA256: single-threaded curl, resumable, checksummed, retried
    local url="$1" dest="$2" size="$3" sha="$4" part="$2.partial" attempt have t
    if [[ -f "$dest" && "$(stat -c %s "$dest")" -eq "$size" ]]; then return 0; fi
    log "[model] blob ${sha:0:12} ($((size / 1048576)) MB)"
    for attempt in 1 2 3 4 5; do
        have="$(stat -c %s "$part" 2>/dev/null || echo 0)"
        if [[ "$have" -gt "$size" ]]; then rm -f "$part"; have=0; fi
        if [[ "$have" -lt "$size" ]]; then
            curl -fsSL -C - -o "$part" "$url" </dev/null & CURL_PID=$!
            t=0
            while kill -0 "$CURL_PID" 2>/dev/null; do
                sleep 2; t=$((t + 2))
                if (( t % 30 == 0 )); then
                    printf '         %6d / %d MB\n' "$(( $(stat -c %s "$part" 2>/dev/null || echo 0) / 1048576 ))" "$((size / 1048576))"
                fi
            done
            if ! wait "$CURL_PID"; then
                CURL_PID=""; warn "[model] download interrupted (attempt ${attempt}/5); resuming in 10 s"; sleep 10; continue
            fi
            CURL_PID=""
        fi
        [[ "$(stat -c %s "$part" 2>/dev/null || echo 0)" -eq "$size" ]] || continue
        log "[model] verifying sha256 of ${sha:0:12}"
        if [[ "$(sha256sum "$part" | cut -d' ' -f1)" == "$sha" ]]; then mv "$part" "$dest"; return 0; fi
        warn "[model] checksum mismatch on ${sha:0:12} (attempt ${attempt}/5); downloading it again"
        rm -f "$part"
    done
    die "[model] blob ${sha:0:12} could not be downloaded intact after 5 attempts. Re-run to try again."
}
prune_model_store() {   # blobs no manifest references any more, left behind by a model change
    local referenced f n=0
    referenced="$(find "${MODELS_DIR}/manifests" -type f -exec cat {} + 2>/dev/null | grep -oE 'sha256:[0-9a-f]{64}' | tr ':' '-' | sort -u || true)"
    [[ -n "$referenced" ]] || return 0
    shopt -s nullglob
    for f in "${MODELS_DIR}"/blobs/sha256-*; do
        if [[ "$f" != *.partial ]] && ! grep -qx "$(basename "$f")" <<< "$referenced"; then rm -f "$f"; n=$((n + 1)); fi
    done
    shopt -u nullglob
    [[ "$n" -eq 0 ]] || log "[model] pruned ${n} blob(s) that no manifest references"
}

# ----------------------------------------------------------------------------- generated: preflight
write_preflight_script() {
    atomic_write "$PREFLIGHT_PY" <<'PREFLIGHT_EOF'
#!/usr/bin/env python
"""nlp-preflight.py -- generated by setup.sh, do not hand-edit.

data_worker.py catches every LLM exception per note, records llm_failed=1 and
keeps going, so a dead endpoint or a wrong model never crashes a run; it just
makes the run worthless. This script fails loudly up front instead.

  --tier 0   static checks only: imports, model name, model blobs on disk,
             writable dirs. No inference, no Ollama process. Safe anywhere.
  --tier 1   tier 0 plus live calls through llm_analysis() against the server
             at OLLAMA_HOST, with schema and extraction checks. Compute nodes.
"""
import argparse
import csv
import json
import os
import shutil
import signal
import sys
import time
import urllib.request
from pathlib import Path

# Fabricated, PHI-free note that exercises every field in llm_analysis.EXPECTED_FIELDS.
NOTE = """PROCEDURE NOTE

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
HARD = {"Endoscopy": 1, "Colonoscopy": 0}      # unambiguous in the note: a miss is a failure
SOFT = {"DuodenalBiopsiesTaken": 1, "NumberOfDuodenalBiopsies": 4, "FellowPresent": 1}

COLOR = sys.stdout.isatty() or os.environ.get("NLP_COLOR") == "1"
FAILS, WARNS = [], []


def paint(code, text):
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def head(msg):
    print(paint("1;36", f"\n-- {msg}"))


def ok(msg):
    print(paint("32", f"  PASS  {msg}"))


def warn(msg):
    WARNS.append(msg)
    print(paint("33", f"  WARN  {msg}"))


def fail(msg, hint=""):
    FAILS.append(msg)
    print(paint("31", f"  FAIL  {msg}"))
    for line in hint.splitlines():
        print(paint("2", f"        {line}"))


class Timeout(BaseException):
    """BaseException so llm_analysis()'s `except Exception` cannot swallow it."""


def split_model(model):
    ref, _, tag = model.partition(":")
    ns, _, name = ref.rpartition("/")
    return ns or "library", name, tag or "latest"


def http_json(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def count_notes(repo):
    path = repo / "notes.csv"
    if not path.is_file():
        return 0
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        return max(sum(1 for _ in csv.reader(fh)) - 1, 0)


def tier0(repo, model, models_dir):
    head("Imports")
    sys.path.insert(0, str(repo))
    mods = {}
    for name in ("llm_analysis", "nlp_analysis", "data_worker", "database", "cli"):
        try:
            mods[name] = __import__(name)
            ok(f"import {name}")
        except Exception as exc:
            fail(f"import {name}: {type(exc).__name__}: {exc}",
                 "A dependency is missing or the repo changed. Re-run setup.sh (without --preflight).")
            return None
    for name in ("ollama", "spacy", "rich", "click"):
        try:
            __import__(name)
            ok(f"import {name}")
        except Exception as exc:
            fail(f"import {name}: {exc}")

    head("Model")
    local = getattr(mods["llm_analysis"], "LOCAL_MODEL", None)
    if local == model:
        ok(f"llm_analysis.LOCAL_MODEL == {model!r}")
    else:
        fail(f"llm_analysis.LOCAL_MODEL is {local!r} but setup fetched {model!r}",
             "Re-run setup.sh: it reads LOCAL_MODEL and fetches whatever the repo asks for.")
    ns, name, tag = split_model(model)
    manifest = models_dir / "manifests" / "registry.ollama.ai" / ns / name / tag
    try:
        data = json.loads(manifest.read_text())
        blobs = [data["config"]] + data["layers"]
        bad = []
        for blob in blobs:
            path = models_dir / "blobs" / blob["digest"].replace(":", "-")
            if not path.is_file() or path.stat().st_size != blob["size"]:
                bad.append(blob["digest"][7:19])
        if bad:
            fail(f"model blobs missing or truncated: {bad}", "Re-run setup.sh; it resumes the download.")
        else:
            ok(f"model store complete: {len(blobs)} blobs under {models_dir}")
    except Exception as exc:
        fail(f"no usable manifest at {manifest}: {exc}", "Re-run setup.sh to fetch the model.")

    head("Filesystem")
    for path, what in ((repo, "data.db"), (repo / "output", "csv exports")):
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".preflight-probe"
            probe.write_text("x")
            probe.unlink()
            ok(f"writable: {path} ({what})")
        except Exception as exc:
            fail(f"not writable: {path} ({exc})")
    return mods


def placement(base):
    head("Model placement")
    try:
        loaded = http_json(f"{base}/api/ps").get("models", [])
    except Exception as exc:
        warn(f"/api/ps unavailable: {exc}")
        return
    if not loaded:
        warn("no model is loaded; the call never reached the model")
    for entry in loaded:
        size, vram = entry.get("size", 0), entry.get("size_vram", 0)
        pct = 100 * vram // size if size else 0
        desc = f"{entry.get('name')}: {pct}% on GPU ({vram / 1e9:.1f} of {size / 1e9:.1f} GB)"
        if pct == 100:
            ok(desc)
        elif vram == 0 and not shutil.which("nvidia-smi"):
            ok(desc + ", CPU-only node")
        else:
            warn(desc + "; partly or fully on CPU, expect it to be slow")


def tier1(mods, model, reps, budget, notes):
    head("Ollama endpoint")
    host = os.environ.get("OLLAMA_HOST", "")
    if not host:
        fail("OLLAMA_HOST is not set", "Start the server (nlp_start_ollama) before running python.")
        return
    base = host if host.startswith("http") else f"http://{host}"
    try:
        served = [m.get("name") for m in http_json(f"{base}/api/tags").get("models", [])]
    except Exception as exc:
        fail(f"cannot reach {base}/api/tags: {exc}", "In a job this means every note records llm_failed=1.")
        return
    ok(f"{base}/api/tags responded")
    if model in served:
        ok(f"{model!r} is served")
    else:
        fail(f"{model!r} is not served (server has: {served or 'nothing'})",
             "OLLAMA_MODELS inside the container does not point at the fetched blobs.")
        return
    try:
        import ollama
        url = str(ollama._client._client.base_url).rstrip("/")
        if host.split("//")[-1].rstrip("/") in url:
            ok(f"ollama default client -> {url}")
        else:
            fail(f"ollama default client -> {url}, not {base}", "OLLAMA_HOST was set after `import ollama`.")
            return
    except Exception as exc:
        warn(f"could not introspect the ollama client ({exc}); relying on /api/tags")

    head(f"Live extraction: {reps} call(s) through llm_analysis(), {budget}s budget each")
    llm = mods["llm_analysis"]

    def on_alarm(*_):
        raise Timeout()

    signal.signal(signal.SIGALRM, on_alarm)
    times, parsed, raw = [], None, None
    for i in range(1, reps + 1):
        start = time.time()
        signal.alarm(budget)
        try:
            parsed, raw = llm.llm_analysis(-1, NOTE)
        except Timeout:
            fail(f"call {i}: no response within {budget}s",
                 "Model on CPU instead of GPU, or a very long output. See placement below.")
            break
        except llm.LLMAnalysisError as exc:
            fail(f"call {i}: {exc}", "Exactly what data_worker.py swallows per note as llm_failed=1.")
            break
        except Exception as exc:
            fail(f"call {i}: unexpected {type(exc).__name__}: {exc}")
            break
        finally:
            signal.alarm(0)
        times.append(time.time() - start)
        ok(f"call {i}: schema-valid JSON in {times[-1]:.1f}s")
    placement(base)
    if len(times) < reps:
        return

    head("Extraction quality on the synthetic note")
    for field, want in HARD.items():
        if parsed.get(field) == want:
            ok(f"{field} == {want}")
        else:
            fail(f"{field} == {parsed.get(field)!r}, expected {want}",
                 "Unambiguous in the note text; a model that misses it will misclassify the corpus.")
    for field, want in SOFT.items():
        if parsed.get(field) == want:
            ok(f"{field} == {want}")
        else:
            warn(f"{field} == {parsed.get(field)!r}, expected {want} (phrasing-sensitive; review before trusting the run)")
    scope = parsed.get("ScopeType")
    report = ok if scope and "H190" in str(scope) else warn
    report(f"ScopeType == {scope!r} (expected something like 'Olympus H190')")

    head("Throughput")
    if not raw.strip().startswith("{"):
        warn("output is not bare JSON, so the repair layer is doing work; data_worker retries up to 5x per note on JSON errors")
    avg = sum(times) / len(times)
    print(f"        {avg:.1f}s per note (range {min(times):.1f}-{max(times):.1f}s)")
    for n in (100, 1000, notes):
        if n > 0:
            print(f"        {n:>6} notes  ~{avg * n / 3600:6.1f} h  (single-threaded, no retries)")
    if avg > 60:
        warn("over a minute per note: use a GPU queue (NLP_JOB_QUEUE=UI-GPU NLP_JOB_GPU=1) or a longer NLP_JOB_HRT")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--model", required=True)
    ap.add_argument("--models-dir", required=True, type=Path)
    ap.add_argument("--tier", type=int, default=1, choices=(0, 1))
    ap.add_argument("--reps", type=int, default=int(os.environ.get("NLP_PREFLIGHT_REPS", "3")))
    ap.add_argument("--budget", type=int, default=int(os.environ.get("NLP_PREFLIGHT_BUDGET", "600")))
    ap.add_argument("--notes", type=int, default=0, help="rows in notes.csv, for the projection (default: counted)")
    args = ap.parse_args()
    print(paint("1", f"nlp-study preflight, tier {args.tier}: repo={args.repo} model={args.model}"))

    mods = tier0(args.repo, args.model, args.models_dir)
    if args.tier >= 1 and mods and not FAILS:
        tier1(mods, args.model, args.reps, args.budget, args.notes or count_notes(args.repo))

    print()
    if FAILS:
        print(paint("1;31", f"PREFLIGHT FAILED ({len(FAILS)} problem(s))"))
        for msg in FAILS:
            print(paint("31", f"  - {msg}"))
        print("Do not submit the job: each of these becomes a silent llm_failed=1 row, not a crash.")
        return 1
    print(paint("1;32", "PREFLIGHT PASSED") + (paint("33", f" with {len(WARNS)} warning(s)") if WARNS else ""))
    if args.tier == 0:
        print(paint("2", "Static checks only; the model has not been run. Repeat inside qlogin: setup.sh (or setup.sh --preflight)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
PREFLIGHT_EOF
    ok "Wrote ${PREFLIGHT_PY}"
}

# ----------------------------------------------------------------------------- generated: env + job scripts
# The env script is the single definition of how Ollama is started and stopped;
# the job script, --preflight and interactive qlogin sessions all source it.
write_runtime_scripts() {
    local gpu_directives
    if [[ -n "${NLP_JOB_GPU:-}" ]]; then
        gpu_directives='#$ -l ngpus=1'$'\n''# To pin a card type add "#$ -l <complex>" (see qconf -sc); the preflight reports whether the model landed on the GPU.'
        [[ "$JOB_QUEUE" == *GPU* || "$JOB_QUEUE" == "all.q" ]] \
            || warn "NLP_JOB_GPU=1 but queue '${JOB_QUEUE}' has no GPUs; the job would never start. Use NLP_JOB_QUEUE=UI-GPU."
    else
        gpu_directives="# No GPU requested. For one, regenerate with: NLP_JOB_QUEUE=UI-GPU NLP_JOB_GPU=1 ${SELF}"
    fi

    {
        cat <<EOF
#!/bin/bash
# Generated by setup.sh on $(date); regenerated on every setup run, do not hand-edit.
# In a qlogin session:  source ${ENV_SCRIPT}
export NLP_ENV_VERSION=${ENV_VERSION}
export NLP_REPO_DIR="${REPO_DIR}"
export NLP_VENV_DIR="${VENV_DIR}"
export NLP_SIF="${SIF_PATH}"
export NLP_MODELS_DIR="${MODELS_DIR}"
export NLP_MODEL="${MODEL}"
export NLP_PREFLIGHT="${PREFLIGHT_PY}"
export NLP_LOG_DIR="${LOG_DIR}"
export APPTAINER_CACHEDIR="${APPT_CACHE}" APPTAINER_TMPDIR="${APPT_TMP}"
EOF
        cat <<'EOF'

nlp_has_gpu() { command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; }

# Start a private Ollama server for this shell or job on a random port.
# Refuses to run where the login-node limiter would kill it.
nlp_start_ollama() {
    case "$(hostname)" in
        *login*) echo "nlp_start_ollama: refusing on a login node (1 CPU / 16 GB cap). Use qlogin." >&2; return 1 ;;
    esac
    if [ -z "${JOB_ID:-}${PE_HOSTFILE:-}${NLP_ALLOW_INFERENCE:-}" ]; then
        echo "nlp_start_ollama: not inside an SGE job. Use qlogin/qsub, or NLP_ALLOW_INFERENCE=1 off-cluster." >&2
        return 1
    fi
    if [ -n "${NLP_OLLAMA_PID:-}" ] && kill -0 "$NLP_OLLAMA_PID" 2>/dev/null; then
        echo "Ollama already running on ${OLLAMA_HOST} (pid ${NLP_OLLAMA_PID})"; return 0
    fi
    local nv="" port
    nlp_has_gpu && nv="--nv"
    port="$(python -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')" || return 1
    export OLLAMA_HOST="127.0.0.1:${port}"
    NLP_OLLAMA_LOG="${NLP_LOG_DIR}/ollama-${JOB_ID:-$$}.log"
    mkdir -p "$NLP_LOG_DIR"
    apptainer exec ${nv:+--nv} --bind "${NLP_MODELS_DIR}:/models" \
        --env OLLAMA_MODELS=/models --env OLLAMA_HOST="$OLLAMA_HOST" \
        --env OLLAMA_KEEP_ALIVE=-1 --env OLLAMA_CONTEXT_LENGTH="${NLP_CONTEXT_LENGTH:-8192}" \
        "$NLP_SIF" ollama serve >"$NLP_OLLAMA_LOG" 2>&1 &
    NLP_OLLAMA_PID=$!
    echo "Ollama starting on ${OLLAMA_HOST} (pid ${NLP_OLLAMA_PID}, gpu: ${nv:-none}, context ${NLP_CONTEXT_LENGTH:-8192}, log ${NLP_OLLAMA_LOG})"
    for _ in $(seq 1 90); do
        if ! kill -0 "$NLP_OLLAMA_PID" 2>/dev/null; then
            echo "Ollama died on startup; log follows:" >&2; cat "$NLP_OLLAMA_LOG" >&2; unset NLP_OLLAMA_PID; return 1
        fi
        if curl -sf --max-time 3 "http://${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; then echo "Ollama ready."; return 0; fi
        sleep 2
    done
    echo "Ollama not answering after 180 s; log tail:" >&2; tail -n 30 "$NLP_OLLAMA_LOG" >&2
    nlp_stop_ollama; return 1
}

nlp_stop_ollama() {
    [ -n "${NLP_OLLAMA_PID:-}" ] || return 0
    kill "$NLP_OLLAMA_PID" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$NLP_OLLAMA_PID" 2>/dev/null || break; sleep 1; done
    kill -9 "$NLP_OLLAMA_PID" 2>/dev/null || true
    wait "$NLP_OLLAMA_PID" 2>/dev/null || true
    unset NLP_OLLAMA_PID
}

# nlp_preflight [0|1]: tier 1 (default) needs nlp_start_ollama first.
nlp_preflight() {
    python "$NLP_PREFLIGHT" --repo "$NLP_REPO_DIR" --model "$NLP_MODEL" --models-dir "$NLP_MODELS_DIR" --tier "${1:-1}"
}

cd "$NLP_REPO_DIR" && . "${NLP_VENV_DIR}/bin/activate"
if [ -z "${NLP_QUIET:-}" ]; then
    echo "nlp-study ready: repo ${NLP_REPO_DIR}, model ${NLP_MODEL}"
    echo "  nlp_start_ollama   then   nlp_preflight   then   python app.py --analyze --csv ./notes.csv"
fi
EOF
    } | atomic_write "$ENV_SCRIPT"
    ok "Wrote ${ENV_SCRIPT}"

    {
        cat <<EOF
#!/bin/bash
# Generated by setup.sh on $(date); regenerated on every setup run. Tune the directives
# through NLP_JOB_QUEUE / NLP_JOB_SLOTS / NLP_JOB_HRT / NLP_JOB_GPU and re-run setup.sh.
# Submit:  qsub ${JOB_SCRIPT} --analyze --csv ./notes.csv     (arguments go to app.py)
#\$ -N nlp-study
#\$ -q ${JOB_QUEUE}
#\$ -pe smp ${JOB_SLOTS}
#\$ -l h_rt=${JOB_HRT}
#\$ -j y
#\$ -o ${LOG_DIR}/
#\$ -m ea
${gpu_directives}
set -eo pipefail
export NLP_QUIET=1
. "${ENV_SCRIPT}"
EOF
        cat <<'EOF'
set -u
echo "nlp-study job ${JOB_ID:-?} on $(hostname), $(date); slots ${NSLOTS:-?}; model ${NLP_MODEL}; args: $*"
nlp_start_ollama
trap nlp_stop_ollama EXIT
if [ -z "${NLP_SKIP_PREFLIGHT:-}" ]; then
    # Gate: one synthetic, PHI-free note through llm_analysis(). Without it a dead
    # endpoint or wrong model produces a full-length run with every LLM column null.
    NLP_PREFLIGHT_REPS="${NLP_PREFLIGHT_REPS:-1}" nlp_preflight 1 \
        || { echo "Preflight failed; not starting app.py." >&2; exit 1; }
fi
python app.py "$@" || { rc=$?; echo "app.py exited ${rc} (143 outside all.q usually means the memory limit)" >&2; exit "$rc"; }
echo "app.py finished at $(date)"
EOF
    } | atomic_write "$JOB_SCRIPT"
    ok "Wrote ${JOB_SCRIPT}"
}

# ----------------------------------------------------------------------------- preflight runner
# Runs the generated preflight at the highest tier this node allows and sets
# PREFLIGHT_TIER. Ollama is started only inside an SGE allocation (or with
# NLP_ALLOW_INFERENCE=1 off-cluster), never on a login node.
PREFLIGHT_TIER=0
run_preflight_here() {
    local rc=0
    export NLP_QUIET=1
    set +u; . "$ENV_SCRIPT"; set -u
    PREFLIGHT_TIER=0
    if is_login_node; then
        log "Login node: static checks only; inference waits for a compute node."
    elif in_sge_job || [[ -n "${NLP_ALLOW_INFERENCE:-}" ]]; then
        nlp_start_ollama || { err "Ollama did not start; see the log above."; return 1; }
        PREFLIGHT_TIER=1
    else
        warn "Not inside an SGE job: static checks only (NLP_ALLOW_INFERENCE=1 overrides this off-cluster)."
    fi
    NLP_COLOR="${IS_TTY:+1}" nlp_preflight "$PREFLIGHT_TIER" || rc=$?
    nlp_stop_ollama
    return "$rc"
}
preflight_next_steps() {
    if [[ "$PREFLIGHT_TIER" -eq 1 ]]; then
        ok "LLM path works end to end on $(hostname). Submit from a login node:  qsub ${JOB_SCRIPT} --analyze --csv ./notes.csv"
    else
        log "Static checks passed. Prove inference on a compute node:  qlogin -q ${JOB_QUEUE} -pe smp ${JOB_SLOTS}   then   ${SELF}"
    fi
}

# Sourcing with NLP_SETUP_LIB_ONLY=1 loads the functions above and stops here (used by tests).
[[ -z "${NLP_SETUP_LIB_ONLY:-}" ]] || return 0 2>/dev/null || exit 0

# ============================================================================= main
mkdir -p "$LOG_DIR"
find "$LOG_DIR" -maxdepth 1 -name 'setup-*.log' -mtime +30 -delete 2>/dev/null || true
exec > >(tee -a "$SETUP_LOG") 2>&1
cleanup() {
    [[ -z "$CURL_PID" ]] || kill "$CURL_PID" 2>/dev/null || true
    if declare -F nlp_stop_ollama >/dev/null 2>&1; then nlp_stop_ollama; fi
    release_lock
}
on_error() {
    local rc=$1 line=$2
    err "Failed at line ${line} (exit ${rc}). Transcript: ${SETUP_LOG}"
    err "Re-running is safe: healthy resources are skipped and half-built ones rebuilt. --status shows the state."
    exit "$rc"
}
trap cleanup EXIT
trap 'on_error $? $LINENO' ERR
trap 'printf "\n"; err "Interrupted. Re-run to resume; partial downloads continue where they stopped."; exit 130' INT TERM

section "Environment"
log "$(date)  mode=${MODE}  force='${FORCE_LIST#,}'  host=$(hostname)  user=${HAWKID}  sge_job=${JOB_ID:-none}"
for cmd in git curl apptainer sha256sum; do command -v "$cmd" >/dev/null 2>&1 || die "'${cmd}' not on PATH."; done
log "git $(git --version | awk '{print $3}'), apptainer $(apptainer --version | awk '{print $NF}'), bash ${BASH_VERSION}"
if [[ "$MODE" == "setup" ]]; then
    if is_login_node; then ok "Login node: downloads happen here; inference waits for a compute node."
    elif in_sge_job; then ok "Inside SGE job ${JOB_ID:-?}: inference is allowed here."
    fi
    if curl -sfI --max-time 15 https://github.com >/dev/null 2>&1; then
        HAVE_NET=1; ok "Outbound HTTPS works: anything missing can be built on this node."
    else
        HAVE_NET=0; warn "No outbound HTTPS from this node: existing resources are used as they are; nothing can be downloaded."
    fi
fi

section "Scratch layout"
probe_writable() { mkdir -p "$1" 2>/dev/null && touch "$1/.probe.$$" 2>/dev/null && rm -f "$1/.probe.$$"; }
resolve_scratch_roots() {
    local c
    local -a fast=("/scratch/Users/${HAWKID}" "/nfsscratch/Users/${HAWKID}")
    local -a bulk=("/nfsscratch/Users/${HAWKID}" "/scratch/Users/${HAWKID}")
    [[ -z "${NLP_FAST_ROOT:-}" ]] || fast=("$NLP_FAST_ROOT")
    [[ -z "${NLP_BULK_ROOT:-}" ]] || bulk=("$NLP_BULK_ROOT")
    for c in "${fast[@]}"; do if probe_writable "$c"; then PROJECT_ROOT="${c}/nlp-study"; break; fi; done
    for c in "${bulk[@]}"; do if probe_writable "$c"; then ASSET_ROOT="${c}/nlp-study-assets"; break; fi; done
    [[ -n "$PROJECT_ROOT" && -n "$ASSET_ROOT" ]] \
        || die "No writable scratch dir for ${HAWKID} under /scratch/Users or /nfsscratch/Users. Ask research-computing@uiowa.edu, or set NLP_FAST_ROOT and NLP_BULK_ROOT."
}
resolve_scratch_roots
REPO_DIR="${PROJECT_ROOT}/repo"; VENV_DIR="${PROJECT_ROOT}/venv"; PREFLIGHT_PY="${PROJECT_ROOT}/nlp-preflight.py"
SIF_PATH="${ASSET_ROOT}/ollama.sif"; SIF_PARTIAL="${ASSET_ROOT}/ollama.partial.sif"; MODELS_DIR="${ASSET_ROOT}/ollama-models"
APPT_CACHE="${ASSET_ROOT}/apptainer-cache"; APPT_TMP="${ASSET_ROOT}/apptainer-tmp"
mkdir -p "$PROJECT_ROOT" "$ASSET_ROOT" "$MODELS_DIR" "$APPT_CACHE" "$APPT_TMP"   # not REPO_DIR/VENV_DIR: clone and venv own those
if [[ "$MODE" == "setup" || "$MODE" == "clean" ]]; then acquire_lock; fi
export APPTAINER_CACHEDIR="$APPT_CACHE" APPTAINER_TMPDIR="$APPT_TMP"
export APPTAINER_MKSQUASHFS_ARGS="-processors ${NSLOTS:-1} -mem 2G"   # one thread on a login node, the job's slots elsewhere
ok "repo + venv   -> ${PROJECT_ROOT}"
ok "image + model -> ${ASSET_ROOT}"
# Leftovers of an interrupted pull can be several GB; only touch entries older than an hour.
find "$APPT_TMP" -mindepth 1 -maxdepth 1 -mmin +60 -exec rm -rf {} + 2>/dev/null || true
# Servers left behind by older versions of this script eat the login-node memory budget.
if is_login_node; then
    orphans="$(pgrep -u "$HAWKID" -f "${SIF_PATH}.*ollama serve" || true)"
    if [[ -n "$orphans" ]]; then
        warn "Killing orphaned Ollama server(s) on this login node: ${orphans//$'\n'/ }"
        # shellcheck disable=SC2086
        kill $orphans 2>/dev/null || true
    fi
fi
detect_repo_model
log "Model: ${MODEL:-<unknown until the repo is cloned>} (${MODEL_SOURCE})"

# ----------------------------------------------------------------------------- --status
if [[ "$MODE" == "status" ]]; then
    section "Status"
    for r in repo venv sif model; do
        V_STATE="absent"; V_REASON=""; "verify_$r"
        case "$r" in repo) p="$REPO_DIR" ;; venv) p="$VENV_DIR" ;; sif) p="$SIF_PATH" ;; model) p="$MODELS_DIR" ;; esac
        case "$V_STATE" in healthy) col="$C_GREEN" ;; broken) col="$C_RED" ;; *) col="$C_YELLOW" ;; esac
        printf '  %-6s %s%-8s%s %s%s\n' "$r" "$col" "$V_STATE" "$C_RESET" "$p" "${V_REASON:+  ($V_REASON)}"
    done
    for f in "$PREFLIGHT_PY" "$ENV_SCRIPT" "$JOB_SCRIPT"; do
        if [[ -f "$f" ]]; then printf '  %-6s %spresent%s  %s\n' file "$C_GREEN" "$C_RESET" "$f"
        else printf '  %-6s %sabsent%s   %s\n' file "$C_YELLOW" "$C_RESET" "$f"; fi
    done
    [[ ! -f "${PROJECT_ROOT}/.setup.lock/owner" ]] || warn "a setup run holds the lock (host pid epoch): $(cat "${PROJECT_ROOT}/.setup.lock/owner")"
    [[ -z "$(ls -A "${PROJECT_ROOT}/preserved" 2>/dev/null)" ]] || warn "user data from earlier rebuilds sits in ${PROJECT_ROOT}/preserved/"
    exit 0
fi

# ----------------------------------------------------------------------------- --clean
if [[ "$MODE" == "clean" ]]; then
    section "Clean"
    warn "About to delete ${PROJECT_ROOT} and ${ASSET_ROOT} (csv/db/output are moved to ${PROJECT_ROOT}/preserved first)."
    if [[ -z "$ASSUME_YES" ]]; then
        [[ -t 0 ]] || die "Non-interactive; pass --yes to confirm."
        read -r -p "Type 'yes' to continue: " reply
        [[ "$reply" == "yes" ]] || die "Aborted."
    fi
    preserve_user_data "$REPO_DIR"
    run rm -rf "$REPO_DIR" "$VENV_DIR" "$PREFLIGHT_PY" "$SIF_PATH" "$SIF_PARTIAL" "$MODELS_DIR" "$APPT_CACHE" "$APPT_TMP"
    apptainer cache clean --force >/dev/null 2>&1 || true
    ok "Removed. Left alone: ${JOB_SCRIPT}, ${ENV_SCRIPT}, ${LOG_DIR}, ${PROJECT_ROOT}/preserved"
    exit 0
fi

# ----------------------------------------------------------------------------- --preflight
if [[ "$MODE" == "preflight" ]]; then
    section "Preflight on the existing install"
    for r in repo venv sif model; do
        V_STATE="absent"; V_REASON=""; "verify_$r"
        [[ "$V_STATE" == "healthy" ]] || die "[${r}] is ${V_STATE}${V_REASON:+ (${V_REASON})}. Repair it from a login node: ${SELF}"
        ok "[${r}] healthy"
    done
    grep -q "^export NLP_ENV_VERSION=${ENV_VERSION}\$" "$ENV_SCRIPT" 2>/dev/null \
        || die "${ENV_SCRIPT} is missing or from an older setup.sh. Re-run ${SELF} first (on a login node if anything needs downloading)."
    write_preflight_script
    run_preflight_here || die "Preflight failed. Do not submit the job."
    preflight_next_steps
    exit 0
fi

# ============================================================================= 1. repository
section "Repository"
ensure_resource repo
if [[ "$V_STATE" == "healthy" ]] && ! force_requested repo; then
    if [[ "$HAVE_NET" -eq 1 ]]; then update_repo; else log "[repo] offline: using the current checkout without pulling"; fi
fi
[[ -z "$(ls -A "${PROJECT_ROOT}/preserved" 2>/dev/null)" ]] \
    || warn "Earlier runs left user data under ${PROJECT_ROOT}/preserved/ (from --clean or an interrupted rebuild); review it."
log "HEAD: $(git_in "$REPO_DIR" log -1 --format='%h %ad %s' --date=short)"
detect_repo_model
[[ -n "$MODEL" ]] || die "LOCAL_MODEL not found in llm_analysis.py; set MODEL=name:tag and re-run."
ok "Model requested by the repo: ${MODEL} (${MODEL_SOURCE})"

# ============================================================================= 2. python
section "Python interpreter"
WANTED="$(tr -d '[:space:]' < "${REPO_DIR}/.python-version" 2>/dev/null | cut -d. -f1,2 || true)"
WANTED="${WANTED:-3.12}"
if ! command -v module >/dev/null 2>&1; then
    for init in /etc/profile.d/modules.sh /etc/profile.d/lmod.sh /usr/share/Modules/init/bash /usr/share/lmod/lmod/init/bash; do
        if [[ -r "$init" ]]; then set +u; . "$init" 2>/dev/null || true; set -u; break; fi
    done
fi
if command -v module >/dev/null 2>&1; then
    set +u
    for m in "python/${WANTED}" "py-${WANTED}" "python${WANTED}" python/3.12 python3; do
        if module load "$m" >/dev/null 2>&1; then log "module load ${m}"; break; fi
    done
    set -u
fi
check_python() {   # sets PYTHON_BIN/PYTHON_VER when $1 is a Python >= 3.10
    local v
    v="$("$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" || return 1
    [[ "${v%%.*}" -eq 3 && "${v##*.}" -ge 10 ]] || return 1
    PYTHON_BIN="$(command -v "$1")"; PYTHON_VER="$v"
}
venv_base_python() {   # the interpreter an existing venv was built from, so re-runs need no NLP_PYTHON
    local cfg="${VENV_DIR}/pyvenv.cfg"
    [[ -r "$cfg" ]] || return 0
    sed -n 's/^executable *= *//p; s/^home *= *\(.*\)/\1\/python3/p' "$cfg"
}
# shellcheck disable=SC2086,SC2046
for cand in ${NLP_PYTHON:-} $(venv_base_python) "python${WANTED}" python3.13 python3.12 python3.11 python3.10 python3; do
    if check_python "$cand"; then break; fi
done
[[ -n "$PYTHON_BIN" ]] || die "No Python >= 3.10 found (Argon's modules stop at 3.8). Point at a user-built one: NLP_PYTHON=/path/to/bin/python3.12 ${SELF}"
ok "Python ${PYTHON_VER} at ${PYTHON_BIN}"
[[ "$PYTHON_VER" == "$WANTED" ]] || warn "Repo pins ${WANTED}; ${PYTHON_VER} will probably work."

# ============================================================================= 3. venv + dependencies
section "Virtual environment"
ensure_resource venv
set +u; . "${VENV_DIR}/bin/activate"; set -u
REQ_FILE="${REPO_DIR}/requirements.txt"; REQ_STAMP="${VENV_DIR}/.requirements.sha256"
req_hash="$(sha256sum "$REQ_FILE" | cut -d' ' -f1)"
# The stamp holds the requirements.txt hash plus "check" or "nocheck": whether importing
# each requirement by name is a valid health test (it is for this repo; a package whose
# module name differs from its package name would otherwise force a rebuild every run).
deps_importable() {
    local names
    names="$(sed -E '/^[[:space:]]*(#|-|$)/d; s/[#;].*//; s/[][<>=!~ ].*//; s/-/_/g' "$REQ_FILE" \
             | tr '[:upper:]' '[:lower:]' | grep -E '^[a-z0-9_.]+$' | paste -sd, - || true)"
    [[ -z "$names" ]] || python -c "import ${names}" >/dev/null 2>&1
}
stamp_hash=""; stamp_mode=""
read -r stamp_hash stamp_mode < "$REQ_STAMP" 2>/dev/null || true
DEPS_OK=0
if [[ "$stamp_hash" == "$req_hash" ]] && ! force_requested deps; then
    if [[ "$stamp_mode" == "nocheck" ]] || deps_importable; then
        DEPS_OK=1
    else
        warn "[deps] were installed earlier but no longer import (scratch purge?); rebuilding the venv"
        destroy_venv; create_venv
        set +u; . "${VENV_DIR}/bin/activate"; set -u
    fi
fi
if [[ "$DEPS_OK" -eq 1 ]]; then
    ok "[deps] installed and importable, skipping pip"
else
    require_net deps
    read -r -a PIP_ARGS <<< "${PIP_INSTALL_ARGS-$PIP_ARGS_DEFAULT}"
    python -m pip install -q --upgrade pip || warn "[deps] pip self-upgrade failed; continuing with the current pip"
    if ! run python -m pip install ${PIP_ARGS[@]+"${PIP_ARGS[@]}"} -r "$REQ_FILE"; then
        err "pip failed. With --only-binary this means no wheel exists for this platform and Python;"
        err "pin an older release of the package named above, or allow a source build for it with"
        err "PIP_INSTALL_ARGS='' (slow, and it usually fails on old compilers)."
        exit 1
    fi
    if deps_importable; then
        printf '%s check\n' "$req_hash" > "$REQ_STAMP"
    else
        printf '%s nocheck\n' "$req_hash" > "$REQ_STAMP"
        warn "[deps] installed, but some module name differs from its package name; later runs trust pip instead of importing"
    fi
    ok "[deps] installed"
fi
# Trained spaCy pipelines must be fetched here, where there is internet (the repo uses spacy.blank today).
for m in $(grep -rhoE "spacy\.load\(\s*['\"][A-Za-z0-9_]+['\"]" "$REPO_DIR" --include='*.py' 2>/dev/null \
           | grep -oE "[A-Za-z0-9_]+['\"]$" | tr -d "'\"" | sort -u || true); do
    python -c "import ${m}" 2>/dev/null || { require_net "spaCy pipeline ${m}"; run python -m spacy download "$m"; }
done

# ============================================================================= 4. container image
section "Ollama container image"
ensure_resource sif
log "[sif] $(du -h "$SIF_PATH" | cut -f1) at ${SIF_PATH}"

# ============================================================================= 5. model
section "Model: ${MODEL}"
ensure_resource model
prune_model_store
log "[model] store size: $(du -sh "$MODELS_DIR" | cut -f1)"

# ============================================================================= 6. runtime scripts
section "Runtime scripts"
write_preflight_script
write_runtime_scripts

# ============================================================================= 7. preflight
section "Preflight"
run_preflight_here || die "Preflight failed; the build itself is intact. Fix the finding above and re-run."
preflight_next_steps

section "Done"
if [[ "$PREFLIGHT_TIER" -eq 1 ]]; then
    NEXT_STEP="(done on $(hostname): the live LLM check passed)"
else
    NEXT_STEP="qlogin -q ${JOB_QUEUE} -pe smp ${JOB_SLOTS}   then   ${SELF}      # live LLM check on a compute node; do not skip"
fi
cat <<EOF
  repo        ${REPO_DIR}
  venv        ${VENV_DIR}
  image       ${SIF_PATH}
  model       ${MODEL}  in ${MODELS_DIR}
  job script  ${JOB_SCRIPT}
  env script  ${ENV_SCRIPT}
  logs        ${LOG_DIR}/

  Put notes.csv at ${REPO_DIR}/notes.csv, then:
    1. ${NEXT_STEP}
    2. qsub ${JOB_SCRIPT} --analyze --csv ./notes.csv   # from a login node
       qstat -u ${HAWKID}    /    tail -f ${LOG_DIR}/nlp-study.o<JOB_ID>

  Interactive instead:  source ${ENV_SCRIPT}; nlp_start_ollama; nlp_preflight; python app.py --analyze --csv ./notes.csv
  Scratch is purged on a schedule: copy ${REPO_DIR}/output somewhere durable when done.
EOF
