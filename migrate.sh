#!/bin/bash
# =============================================================================
#  migrate.sh -- one-time migration of an Argon install built by the September
#  2026 setup.sh (commits 3657d43 .. 1052cdd, "setup_nlp_study.sh" in the
#  transcripts) to the layout the rewritten setup.sh expects. Run it from a
#  login node, after pulling the new version and before ./setup.sh:
#
#     cd /nfsscratch/Users/apkane/nlp-study/repo
#     git pull --ff-only                 # or let this script switch branches
#     ./migrate.sh                       # prints the plan, asks for "yes"
#     ./setup.sh                         # rebuilds what the migration removed
#
#  It runs no Ollama process, no apptainer exec and no inference, so it is safe
#  on a login node. Nothing that could be PHI is ever deleted: notes.csv, the
#  SQLite database, output/ and any untracked file it does not recognise are
#  moved to <project>/preserved/migrate-<timestamp>/ and listed at the end.
#
#  What it does, by area:
#     repo      untracked build junk (__pycache__, *.pyc, ollama-*.log, probes)
#               is deleted; everything else untracked is moved aside (above);
#               then the checkout is fast-forwarded to its upstream, or switched
#               to --branch, and must carry the rewritten setup.sh
#     venv      left alone (its base interpreter is checked, nothing more);
#               setup.sh runs pip once to write the dependency stamp it expects
#     model     manifests for models other than LOCAL_MODEL (read from the
#               commit being deployed) are removed and the blobs no manifest
#               references any more are deleted (the September store held
#               gemma4:e2b, which the code no longer asks for)
#     image     left alone; setup.sh replaces it with the image it now pins,
#               because the September image carries no provenance stamp
#     caches    apptainer-cache/ and apptainer-tmp/ are emptied
#     $HOME     September generated scripts (no NLP_ENV_VERSION), their .bak
#               copies and the old setup transcripts are removed or moved into
#               ~/nlp-study-logs/
#     processes an orphaned `ollama serve` left on this login node is killed
#
#  Usage:
#     ./migrate.sh                 plan, confirm with "yes", migrate
#     ./migrate.sh --dry-run       plan only, change nothing (still fetches)
#     ./migrate.sh -y              skip the confirmation (required without a TTY)
#     ./migrate.sh --branch NAME   switch the checkout to origin/NAME first
#     ./migrate.sh --reset         discard local changes and local commits in the checkout
#     ./migrate.sh --keep-data     leave notes.csv, data.db and output/ in the repo
#     ./migrate.sh --keep-models   do not touch the model store
#
#  Honours NLP_FAST_ROOT, NLP_BULK_ROOT and MODEL like setup.sh. Re-running is
#  harmless: a migrated install produces an empty plan.
#  Bash 4.2 and git 1.8.3 compatible (CentOS 7): no `git -C`, no mapfile -d.
# =============================================================================
set -Eeuo pipefail

# ----------------------------------------------------------------------------- config (mirrors setup.sh)
readonly HAWKID="${USER:-$(id -un)}"
readonly JOB_SCRIPT="${HOME}/nlp-study.job"
readonly ENV_SCRIPT="${HOME}/nlp-study-env.sh"
readonly LOG_DIR="${HOME}/nlp-study-logs"
readonly MODEL_OVERRIDE="${MODEL:-}"
STAMP="$(date +%Y%m%d-%H%M%S)"; readonly STAMP
readonly MIGRATE_LOG="${LOG_DIR}/migrate-${STAMP}.log"
export GIT_TERMINAL_PROMPT=0

DRY_RUN="" ASSUME_YES="" BRANCH="" RESET="" KEEP_DATA="" KEEP_MODELS=""
PROJECT_ROOT="" ASSET_ROOT="" REPO_DIR="" VENV_DIR="" PREFLIGHT_PY="" PRESERVE_DIR=""
SIF_PATH="" SIF_PARTIAL="" SIF_IMAGE_STAMP="" MODELS_DIR="" APPT_CACHE="" APPT_TMP=""
LOCK_DIR="" HAVE_NET=0 MODEL="" MODEL_SOURCE=""
MODELS=()
GIT_ACTION="none" GIT_TARGET="" GIT_REMOTE_REF="" GIT_TARGET_SHA="" target_setup=""
KILL_PIDS="" MANIFEST_WANTED_PRESENT=""

# Plan: everything is recorded here first and applied only after confirmation.
DEL_PATHS=()      # delete outright
DEL_BYTES=0
PRESERVE_REL=()   # repo-relative paths moved to PRESERVE_DIR
PRESERVE_BYTES=0
LOGMV_SRC=()      # old setup transcripts moved into LOG_DIR
LOGMV_DST=()
WARNINGS=()

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
warn()    { WARNINGS+=("$*"); printf '%s[%s]  WARN  %s%s\n' "$C_YELLOW" "$(_ts)" "$*" "$C_RESET"; }
err()     { printf '%s[%s] ERROR  %s%s\n' "$C_RED" "$(_ts)" "$*" "$C_RESET" >&2; }
die()     { err "$*"; exit 1; }
run()     { printf '%s         $ %s%s\n' "$C_DIM" "$*" "$C_RESET"; "$@"; }
item()    { printf '         %-9s %s\n' "$1" "$2"; }           # plan line: verb + description
git_in()  { local d="$1"; shift; ( cd "$d" && git "$@" ); }   # `git -C` needs git >= 1.8.5
usage()   { sed -n '/^#  Usage:/,/^# ====/p' "$0" | sed '$d; s/^# \{0,2\}//'; }
is_login_node() { [[ "$(hostname)" == *login* ]]; }
path_bytes()    { du -sb "$1" 2>/dev/null | cut -f1 || echo 0; }
hsize() {   # bytes -> human readable; numfmt is coreutils 8.21+ (CentOS 7 has 8.22)
    if command -v numfmt >/dev/null 2>&1; then numfmt --to=iec --suffix=B "$1" 2>/dev/null || printf '%sB' "$1"
    else printf '%sB' "$1"; fi
}

# ----------------------------------------------------------------------------- arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)     DRY_RUN=1 ;;
        -y|--yes)      ASSUME_YES=1 ;;
        --branch)      [[ $# -ge 2 ]] || die "--branch needs a name"; BRANCH="$2"; shift ;;
        --branch=*)    BRANCH="${1#--branch=}" ;;
        --reset)       RESET=1 ;;
        --keep-data)   KEEP_DATA=1 ;;
        --keep-models) KEEP_MODELS=1 ;;
        -h|--help)     usage; exit 0 ;;
        *)             die "Unknown option: $1 (try --help)" ;;
    esac
    shift
done

# ----------------------------------------------------------------------------- lock (same directory and rules as setup.sh)
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
            die "A setup run is active on ${host} (pid ${pid}, since $(date -d "@${since}" 2>/dev/null || echo '?')). Wait for it, or if it is dead:  rm -rf ${dir}"
        fi
    fi
    printf '%s %s %s\n' "$(hostname)" "$$" "$(date +%s)" > "${dir}/owner"
    LOCK_DIR="$dir"
}
release_lock() { [[ -z "$LOCK_DIR" ]] || rm -rf "$LOCK_DIR"; LOCK_DIR=""; }

# ----------------------------------------------------------------------------- plan helpers
plan_delete() {   # plan_delete PATH DESCRIPTION [NOTE]
    local p="$1" what="$2" note="${3:-}" b
    [[ -e "$p" || -L "$p" ]] || return 0
    b="$(path_bytes "$p")"
    DEL_PATHS+=("$p"); DEL_BYTES=$((DEL_BYTES + b))
    item delete "${what}  (${C_DIM}$(hsize "$b")${note:+; $note}${C_RESET})"
}
plan_preserve() {   # plan_preserve REPO_RELATIVE_PATH LABEL
    local rel="${1%/}" label="$2" b
    b="$(path_bytes "${REPO_DIR}/${rel}")"
    PRESERVE_REL+=("$rel"); PRESERVE_BYTES=$((PRESERVE_BYTES + b))
    item preserve "${rel}  (${C_DIM}${label}, $(hsize "$b")${C_RESET})"
}

# What an untracked path in the repo is. PHI patterns are those setup.sh's
# preserve_user_data knows, plus SQLite side files. Junk is only what the
# pipeline or an interpreter is known to generate; anything else is preserved.
classify_untracked() {   # prints phi | junk | self | other
    local p="${1%/}" b
    b="${p##*/}"
    case "$p" in
        migrate.sh) echo self; return 0 ;;
        __pycache__|*/__pycache__|.pytest_cache|.mypy_cache|.ruff_cache|.venv|*.egg-info) echo junk; return 0 ;;
    esac
    # git cannot track an empty directory, so one is a leftover by definition.
    if [[ -d "${REPO_DIR}/${p}" && -z "$(ls -A "${REPO_DIR}/${p}" 2>/dev/null)" ]]; then echo junk; return 0; fi
    case "$b" in
        *.csv|*.db|*.sqlite|*.sqlite3|*.db-journal|*.db-wal|*.db-shm|*.db.backup|*.db.backup.partial|*.csv.partial|output) echo phi; return 0 ;;
        *.pyc|*.pyo|ollama-*.log|.preflight-probe|.probe.*|.write-probe.*|*.tmp.*|nohup.out|.DS_Store|*~|*.swp|core|core.[0-9]*) echo junk; return 0 ;;
    esac
    echo other
}

# The models the program will call: LOCAL_MODEL in llm_analysis.py plus the other names in
# its MODELS block (same seds as setup.sh), read from the commit the checkout will be at
# after the git step. MODELS holds them all; MODEL is the one model_manifest_rel acts on.
detect_model() {
    local src="" found="" others=""
    if [[ -n "$GIT_REMOTE_REF" ]] && src="$(git_in "$REPO_DIR" show "${GIT_REMOTE_REF}:llm_analysis.py" 2>/dev/null)"; then :
    else src="$(cat "${REPO_DIR}/llm_analysis.py" 2>/dev/null || true)"; fi
    found="$(printf '%s\n' "$src" | sed -n "s/^LOCAL_MODEL *= *[\"']\([^\"']*\)[\"'].*/\1/p" | head -1 || true)"
    others="$(printf '%s\n' "$src" | sed -n '/^MODELS = (/,/^)/p' | sed -n 's/^ *ModelSpec("\([^"]*\)".*/\1/p' | tr '\n' ' ' || true)"
    MODELS=()
    if [[ -n "$MODEL_OVERRIDE" ]]; then
        read -r -a MODELS <<< "$MODEL_OVERRIDE"; MODEL_SOURCE="MODEL env override"
        [[ -z "$found" || "${found} ${others}" == "${MODELS[*]} " ]] \
            || warn "MODEL='${MODELS[*]}' given, but llm_analysis.py asks for ${found} ${others}; the program will call what the repo says."
    elif [[ -n "$found" ]]; then
        read -r -a MODELS <<< "${found} ${others}"; MODEL_SOURCE="llm_analysis.py"
    else
        MODEL_SOURCE="unknown"
    fi
    MODEL="${MODELS[0]:-}"
}
# MODEL is [host/][ns/]name[:tag], parsed exactly as setup.sh's model_parts does:
# three or more path components mean the first is a registry host (hf.co/...).
model_manifest_rel() {   # prints the manifest path for MODEL relative to MODELS_DIR/manifests
    local ref="${MODEL%%:*}" tag="${MODEL#*:}" host="registry.ollama.ai" ns="library" name
    [[ "$tag" != "$MODEL" ]] || tag="latest"
    name="$ref"
    case "$ref" in
        */*/*) host="${ref%%/*}"; ref="${ref#*/}"; ns="${ref%/*}"; name="${ref##*/}" ;;
        */*)   ns="${ref%/*}"; name="${ref##*/}" ;;
    esac
    printf '%s/%s/%s/%s\n' "$host" "$ns" "$name" "$tag"
}
manifest_display_name() {   # host/ns/name/tag -> the name Ollama shows for it
    local rel="$1" host tag name rest ns
    host="${rel%%/*}"; rest="${rel#*/}"
    tag="${rest##*/}"; rest="${rest%/*}"
    name="${rest##*/}"; ns="${rest%/*}"
    [[ "$ns" != "$name" ]] || ns=""
    if [[ "$host" != "registry.ollama.ai" ]]; then printf '%s/%s/%s:%s' "$host" "$ns" "$name" "$tag"
    elif [[ -z "$ns" || "$ns" == "library" ]]; then printf '%s:%s' "$name" "$tag"
    else printf '%s/%s:%s' "$ns" "$name" "$tag"; fi
}
wanted_rels=()           # the manifest paths (relative to manifests/) of every model the code asks for
wanted_manifest() {      # wanted_manifest REL: whether REL is one of them
    local w
    for w in ${wanted_rels[@]+"${wanted_rels[@]}"}; do [[ "$1" != "$w" ]] || return 0; done
    return 1
}

# ============================================================================= main
mkdir -p "$LOG_DIR"
exec > >(tee -a "$MIGRATE_LOG") 2>&1
cleanup() { release_lock; }
on_error() {
    local rc=$1 line=$2
    err "Failed at line ${line} (exit ${rc}). Transcript: ${MIGRATE_LOG}"
    err "Nothing here is half-done in a way that matters: re-run migrate.sh (an empty plan means it is finished), then setup.sh."
    exit "$rc"
}
trap cleanup EXIT
trap 'on_error $? $LINENO' ERR
trap 'printf "\n"; err "Interrupted."; exit 130' INT TERM

section "Environment"
log "$(date)  host=$(hostname)  user=${HAWKID}  sge_job=${JOB_ID:-none}${DRY_RUN:+  DRY RUN}"
for cmd in git curl du; do command -v "$cmd" >/dev/null 2>&1 || die "'${cmd}' not on PATH."; done
log "git $(git --version | awk '{print $3}'), bash ${BASH_VERSION}"
if curl -sfI --max-time 15 https://github.com >/dev/null 2>&1; then
    HAVE_NET=1; ok "Outbound HTTPS works: the checkout can be fetched and fast-forwarded here."
else
    HAVE_NET=0; warn "No outbound HTTPS from this node: the checkout is migrated as it is, without fetching."
fi
if command -v qstat >/dev/null 2>&1 && qstat -u "$HAWKID" 2>/dev/null | grep 'nlp-study' >/dev/null; then
    die "An nlp-study job is queued or running for ${HAWKID} (qstat -u ${HAWKID}). It uses the files this script moves; wait for it or qdel it."
fi

section "Scratch layout"
probe_writable() { mkdir -p "$1" 2>/dev/null && touch "$1/.probe.$$" 2>/dev/null && rm -f "$1/.probe.$$"; }
resolve_scratch_roots() {   # identical to setup.sh, so both scripts agree on the paths
    local c
    local -a fast=("/scratch/Users/${HAWKID}" "/nfsscratch/Users/${HAWKID}")
    local -a bulk=("/nfsscratch/Users/${HAWKID}" "/scratch/Users/${HAWKID}")
    [[ -z "${NLP_FAST_ROOT:-}" ]] || fast=("$NLP_FAST_ROOT")
    [[ -z "${NLP_BULK_ROOT:-}" ]] || bulk=("$NLP_BULK_ROOT")
    for c in "${fast[@]}"; do if probe_writable "$c"; then PROJECT_ROOT="${c}/nlp-study"; break; fi; done
    for c in "${bulk[@]}"; do if probe_writable "$c"; then ASSET_ROOT="${c}/nlp-study-assets"; break; fi; done
    [[ -n "$PROJECT_ROOT" && -n "$ASSET_ROOT" ]] \
        || die "No writable scratch dir for ${HAWKID} under /scratch/Users or /nfsscratch/Users. Set NLP_FAST_ROOT and NLP_BULK_ROOT."
}
resolve_scratch_roots
REPO_DIR="${PROJECT_ROOT}/repo"; VENV_DIR="${PROJECT_ROOT}/venv"; PREFLIGHT_PY="${PROJECT_ROOT}/nlp-preflight.py"
PRESERVE_DIR="${PROJECT_ROOT}/preserved/migrate-${STAMP}"
SIF_PATH="${ASSET_ROOT}/ollama.sif"; SIF_PARTIAL="${ASSET_ROOT}/ollama.partial.sif"; SIF_IMAGE_STAMP="${ASSET_ROOT}/ollama.sif.image"
MODELS_DIR="${ASSET_ROOT}/ollama-models"; APPT_CACHE="${ASSET_ROOT}/apptainer-cache"; APPT_TMP="${ASSET_ROOT}/apptainer-tmp"
[[ -d "${REPO_DIR}/.git" ]] || die "No checkout at ${REPO_DIR}: nothing to migrate here. (Different roots? NLP_FAST_ROOT=... NLP_BULK_ROOT=... $0)"
ok "repo + venv   -> ${PROJECT_ROOT}"
ok "image + model -> ${ASSET_ROOT}"
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
[[ "$SELF_DIR" == "$REPO_DIR" ]] || warn "Running from ${SELF_DIR}, but the managed checkout is ${REPO_DIR}; that checkout is what gets migrated."
if [[ -z "$DRY_RUN" ]]; then acquire_lock; else
    [[ ! -f "${PROJECT_ROOT}/.setup.lock/owner" ]] || warn "a setup run holds the lock (host pid epoch): $(cat "${PROJECT_ROOT}/.setup.lock/owner")"
fi

# ============================================================================= plan
section "Plan"

# --- repo --------------------------------------------------------------------
printf '  %srepo%s     %s\n' "$C_BOLD" "$C_RESET" "$REPO_DIR"
git_in "$REPO_DIR" rev-parse --verify -q HEAD >/dev/null 2>&1 || die "[repo] ${REPO_DIR} has no valid HEAD (interrupted clone?). Let setup.sh rebuild it: ./setup.sh --force repo"
log "HEAD $(git_in "$REPO_DIR" log -1 --format='%h %ad %s' --date=short)"
CUR_BRANCH="$(git_in "$REPO_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)"
GIT_TARGET="${BRANCH:-$CUR_BRANCH}"
[[ "$GIT_TARGET" != "HEAD" ]] || die "[repo] detached HEAD; say which branch to deploy: --branch main (or --branch nlp-study-2.0)"

dirty="$(git_in "$REPO_DIR" status --porcelain -uno 2>/dev/null || true)"
if [[ -n "$dirty" ]]; then
    if [[ -n "$RESET" ]]; then
        item reset "local modifications to tracked files are discarded (--reset):"
        printf '%s\n' "$dirty" | sed 's/^/                   /'
    else
        printf '%s\n' "$dirty" | sed 's/^/         /'
        die "[repo] tracked files are modified in the Argon checkout (above). Keep them with 'git stash', or pass --reset to discard them."
    fi
fi

if [[ "$HAVE_NET" -eq 1 ]]; then
    log "fetching origin (updates remote-tracking refs only)"
    git_in "$REPO_DIR" fetch --prune --quiet origin 2>&1 | sed 's/^/         /' || warn "[repo] git fetch failed; planning against the refs already known locally"
fi
GIT_REMOTE_REF="refs/remotes/origin/${GIT_TARGET}"
if ! git_in "$REPO_DIR" rev-parse --verify -q "$GIT_REMOTE_REF" >/dev/null 2>&1; then
    if [[ "$HAVE_NET" -eq 1 ]]; then die "[repo] origin has no branch '${GIT_TARGET}'. Push it first, or pass --branch <existing branch>."; fi
    warn "[repo] origin/${GIT_TARGET} is not known here and this node is offline; the checkout stays at HEAD"
    GIT_REMOTE_REF=""
fi
if [[ -n "$GIT_REMOTE_REF" ]]; then
    GIT_TARGET_SHA="$(git_in "$REPO_DIR" rev-parse --short "$GIT_REMOTE_REF")"
    behind="$(git_in "$REPO_DIR" rev-list --count "HEAD..${GIT_REMOTE_REF}")"
    ahead="$(git_in "$REPO_DIR" rev-list --count "${GIT_REMOTE_REF}..HEAD")"
    if [[ "$GIT_TARGET" != "$CUR_BRANCH" ]]; then
        GIT_ACTION="switch"
        stay=""; [[ "$ahead" -eq 0 ]] || stay="; ${ahead} local commit(s) stay behind on ${CUR_BRANCH}"
        item checkout "switch from ${CUR_BRANCH} to ${GIT_TARGET} at origin/${GIT_TARGET} (${GIT_TARGET_SHA})${stay}"
    elif [[ "$ahead" -gt 0 && -n "$RESET" ]]; then
        GIT_ACTION="reset"
        item reset "${CUR_BRANCH} has ${ahead} commit(s) origin does not; reset --hard to origin/${GIT_TARGET} (${GIT_TARGET_SHA}) (--reset)"
    elif [[ "$ahead" -gt 0 ]]; then
        die "[repo] ${CUR_BRANCH} has ${ahead} local commit(s) that origin/${GIT_TARGET} lacks. Push them, or pass --reset to drop them."
    elif [[ "$behind" -gt 0 ]]; then
        GIT_ACTION="ff"
        item pull "fast-forward ${CUR_BRANCH} by ${behind} commit(s) to origin/${GIT_TARGET} (${GIT_TARGET_SHA})"
    else
        item keep "${CUR_BRANCH} is already at origin/${GIT_TARGET} (${GIT_TARGET_SHA})"
    fi
    # The whole point: the checkout must end up with the rewritten setup.sh, not the September one.
    # (No `git show | grep -q`: under pipefail grep's early exit makes git fail with SIGPIPE.)
    target_setup="$(git_in "$REPO_DIR" show "${GIT_REMOTE_REF}:setup.sh" 2>/dev/null || true)"
    if ! grep -q '^readonly ENV_VERSION=' <<< "$target_setup"; then
        die "[repo] origin/${GIT_TARGET} still carries the September setup.sh (no ENV_VERSION line). The rewrite lives on another branch: pass --branch <that branch> (for example nlp-study-2.0), or merge it into ${GIT_TARGET} and push first."
    fi
elif ! grep -q '^readonly ENV_VERSION=' "${REPO_DIR}/setup.sh" 2>/dev/null; then
    die "[repo] the checkout has the September setup.sh and this node cannot fetch. Run migrate.sh on a login node."
fi

# Untracked files, ignored ones included (`ls-files -o` without --exclude-standard), directories collapsed.
n_untracked=0
while IFS= read -r -d '' p; do
    [[ -n "$p" ]] || continue
    n_untracked=$((n_untracked + 1))
    case "$(classify_untracked "$p")" in
        self)  item keep "${p}  (${C_DIM}this script; git replaces it${C_RESET})" ;;
        junk)  plan_delete "${REPO_DIR}/${p}" "${p}" ;;
        phi)   if [[ -n "$KEEP_DATA" ]]; then item keep "${p%/}  (${C_DIM}PHI, --keep-data${C_RESET})"; else plan_preserve "$p" "PHI"; fi ;;
        other) plan_preserve "$p" "untracked, not recognised" ;;
    esac
done < <(git_in "$REPO_DIR" ls-files --others --directory -z 2>/dev/null)
[[ "$n_untracked" -gt 0 ]] || item keep "no untracked files"

# --- venv --------------------------------------------------------------------
printf '  %svenv%s     %s\n' "$C_BOLD" "$C_RESET" "$VENV_DIR"
if [[ ! -d "$VENV_DIR" ]]; then
    item absent "setup.sh will create it (needs NLP_PYTHON=/Users/${HAWKID}/temp/build_python/python3.12/bin/python3.12 or any Python 3.10+ on PATH)"
elif v="$("${VENV_DIR}/bin/python" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null)"; then
    base="$(sed -n 's/^executable *= *//p; s/^home *= *\(.*\)/\1\/python3/p' "${VENV_DIR}/pyvenv.cfg" 2>/dev/null | head -1 || true)"
    item keep "Python ${v} venv runs (base interpreter ${base:-unknown}); left alone"
    [[ -f "${VENV_DIR}/.requirements.sha256" ]] || item note "no dependency stamp yet: setup.sh runs pip once against the pinned requirements.txt (no downloads if the pins are already installed) and writes it"
else
    item broken "bin/python does not run: setup.sh will rebuild the venv. If /Users/${HAWKID}/temp/build_python/python3.12 moved, pass NLP_PYTHON=<its bin/python3.12> to setup.sh"
fi

# --- model store -------------------------------------------------------------
detect_model
printf '  %smodel%s    %s\n' "$C_BOLD" "$C_RESET" "$MODELS_DIR"
if [[ -n "$KEEP_MODELS" ]]; then
    item keep "model store untouched (--keep-models)"
elif [[ ! -d "$MODELS_DIR" ]]; then
    item absent "no model store yet; setup.sh downloads ${MODELS[*]:-the models}"
elif [[ -z "$MODEL" ]]; then
    warn "[model] LOCAL_MODEL not found in llm_analysis.py and MODEL not set; the model store is left alone"
else
    for MODEL in "${MODELS[@]}"; do
        wanted_rels+=("$(model_manifest_rel)")
        item wanted "${MODEL} (${MODEL_SOURCE}) -> manifests/$(model_manifest_rel)"
    done
    MODEL="${MODELS[0]}"
    kept_manifests=(); present_n=0
    while IFS= read -r -d '' m; do
        rel="${m#"${MODELS_DIR}"/manifests/}"
        if wanted_manifest "$rel"; then
            kept_manifests+=("$m"); present_n=$((present_n + 1))
            item keep "manifest $(manifest_display_name "$rel")"
        elif [[ "$rel" == *.partial ]] && wanted_manifest "${rel%.partial}"; then
            plan_delete "$m" "manifest $(manifest_display_name "${rel%.partial}") (partial)" "interrupted fetch; setup.sh refetches it"
        else
            plan_delete "$m" "manifest $(manifest_display_name "$rel")" "not a model the code asks for"
        fi
    done < <(find "${MODELS_DIR}/manifests" -type f -print0 2>/dev/null)
    if [[ "$present_n" -eq ${#wanted_rels[@]} ]]; then
        MANIFEST_WANTED_PRESENT=1
    else
        item absent "$((${#wanted_rels[@]} - present_n)) of ${#wanted_rels[@]} model(s) not in the store: setup.sh downloads them on the next online run (several GB, resumable)"
    fi
    referenced=""
    [[ ${#kept_manifests[@]} -eq 0 ]] || referenced="$(cat "${kept_manifests[@]}" | grep -oE 'sha256:[0-9a-f]{64}' | tr ':' '-' | sort -u || true)"
    if [[ ${#kept_manifests[@]} -gt 0 && -z "$referenced" ]]; then
        warn "[model] the kept manifest(s) list no blobs (unreadable?); blobs are left alone, setup.sh will sort it out"
    else
        # Without a kept manifest every blob is an orphan, except in-progress .partial downloads
        # of the wanted model, which setup.sh resumes. Ollama's own "-partial" leftovers go.
        orphan_n=0 orphan_bytes=0 partial_kept=0
        while IFS= read -r -d '' f; do
            if [[ "$f" == *.partial && -z "$MANIFEST_WANTED_PRESENT" ]]; then partial_kept=$((partial_kept + 1)); continue; fi
            if [[ -z "$referenced" ]] || ! grep -qx "$(basename "$f")" <<< "$referenced"; then
                DEL_PATHS+=("$f"); b="$(path_bytes "$f")"; DEL_BYTES=$((DEL_BYTES + b))
                orphan_n=$((orphan_n + 1)); orphan_bytes=$((orphan_bytes + b))
            fi
        done < <(find "${MODELS_DIR}/blobs" -mindepth 1 -maxdepth 1 -print0 2>/dev/null)
        [[ "$orphan_n" -eq 0 ]] || item delete "${orphan_n} blob(s) no remaining manifest references  (${C_DIM}$(hsize "$orphan_bytes")${C_RESET})"
        [[ "$partial_kept" -eq 0 ]] || item keep "${partial_kept} resumable .partial blob(s) of the model(s) still to fetch"
    fi
fi

# --- container image ---------------------------------------------------------
printf '  %simage%s    %s\n' "$C_BOLD" "$C_RESET" "$SIF_PATH"
if [[ -f "$SIF_PATH" ]]; then
    if [[ -f "$SIF_IMAGE_STAMP" ]]; then
        item keep "$(hsize "$(path_bytes "$SIF_PATH")") image, pulled from $(cat "$SIF_IMAGE_STAMP"); setup.sh decides whether it matches NLP_OLLAMA_IMAGE"
    else
        item keep "$(hsize "$(path_bytes "$SIF_PATH")") September image (ollama:latest of 2026-09-14, no provenance stamp); setup.sh replaces it with ${NLP_OLLAMA_IMAGE:-the image it pins (NLP_OLLAMA_IMAGE or its default)} on the next online run"
    fi
else
    item absent "setup.sh pulls it"
fi
plan_delete "$SIF_PARTIAL" "ollama.partial.sif" "interrupted pull"
plan_delete "${ASSET_ROOT}/ollama-setup.log" "ollama-setup.log" "September setup-time server log"

# --- caches ------------------------------------------------------------------
printf '  %scaches%s   %s\n' "$C_BOLD" "$C_RESET" "$ASSET_ROOT"
for d in "$APPT_CACHE" "$APPT_TMP"; do
    [[ -d "$d" ]] || continue
    n="$(find "$d" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l)"
    if [[ "$n" -gt 0 ]]; then
        b="$(path_bytes "$d")"
        while IFS= read -r -d '' e; do DEL_PATHS+=("$e"); done < <(find "$d" -mindepth 1 -maxdepth 1 -print0 2>/dev/null)
        DEL_BYTES=$((DEL_BYTES + b))
        item delete "$(basename "$d")/* (${n} entries)  (${C_DIM}$(hsize "$b"); setup.sh re-pulls the image without it${C_RESET})"
    else
        item keep "$(basename "$d")/ is empty"
    fi
done
# Probe files a killed run could have left in the scratch roots.
probe_roots="$(dirname "$PROJECT_ROOT")"
[[ "$(dirname "$ASSET_ROOT")" == "$probe_roots" ]] || probe_roots="${probe_roots}"$'\n'"$(dirname "$ASSET_ROOT")"
while IFS= read -r r; do
    while IFS= read -r -d '' f; do plan_delete "$f" "$f" "stale write probe"; done \
        < <(find "$r" -maxdepth 1 \( -name '.write-probe.*' -o -name '.probe.*' \) -mmin +60 -print0 2>/dev/null)
done <<< "$probe_roots"

# --- generated files in $HOME and on scratch ---------------------------------
printf '  %shome%s     %s\n' "$C_BOLD" "$C_RESET" "$HOME"
plan_delete "$PREFLIGHT_PY" "$(basename "$PREFLIGHT_PY")" "generated; setup.sh rewrites it"
for f in "$ENV_SCRIPT" "$JOB_SCRIPT"; do
    [[ -f "$f" ]] || continue
    if grep -q '^export NLP_ENV_VERSION=' "$f" 2>/dev/null; then
        item keep "$(basename "$f") is already from the rewritten setup.sh"
    else
        plan_delete "$f" "~/$(basename "$f")" "September version; setup.sh regenerates it"
    fi
done
while IFS= read -r -d '' f; do plan_delete "$f" "~/$(basename "$f")" "backup kept by the September script"; done \
    < <(find "$HOME" -maxdepth 1 \( -name 'nlp-study.job.bak.*' -o -name 'nlp-study-env.sh.bak.*' \) -print0 2>/dev/null)
while IFS= read -r -d '' f; do
    dst="${LOG_DIR}/setup-${f##*/nlp-study-setup-}"
    LOGMV_SRC+=("$f"); LOGMV_DST+=("$dst")
    item move "~/$(basename "$f") -> ~/nlp-study-logs/$(basename "$dst")  (${C_DIM}setup.sh deletes transcripts there after 30 days${C_RESET})"
done < <(find "$HOME" -maxdepth 1 -name 'nlp-study-setup-*.log' -print0 2>/dev/null)
for d in "${HOME}/.cache/pip" "${HOME}/.apptainer"; do
    [[ -d "$d" ]] && item note "${d} holds $(hsize "$(path_bytes "$d")"); not touched (shared with other projects)" || true
done
if [[ -d "${PROJECT_ROOT}/preserved" ]] && [[ -n "$(ls -A "${PROJECT_ROOT}/preserved" 2>/dev/null)" ]]; then
    item note "earlier stashes under ${PROJECT_ROOT}/preserved/ ($(hsize "$(path_bytes "${PROJECT_ROOT}/preserved")")) are left for you to review"
fi

# --- processes ---------------------------------------------------------------
if is_login_node; then
    KILL_PIDS="$(pgrep -u "$HAWKID" -f "${SIF_PATH}.*ollama serve" 2>/dev/null || true)"
    [[ -z "$KILL_PIDS" ]] || { printf '  %sprocs%s\n' "$C_BOLD" "$C_RESET"; item kill "orphaned ollama serve on this login node: ${KILL_PIDS//$'\n'/ }"; }
fi

# --- totals ------------------------------------------------------------------
printf '\n'
log "to delete: ${#DEL_PATHS[@]} path(s), $(hsize "$DEL_BYTES")"
if [[ ${#PRESERVE_REL[@]} -gt 0 ]]; then
    log "moved aside, never deleted: ${#PRESERVE_REL[@]} path(s), $(hsize "$PRESERVE_BYTES") -> ${PRESERVE_DIR}"
fi
if [[ ${#DEL_PATHS[@]} -eq 0 && ${#PRESERVE_REL[@]} -eq 0 && ${#LOGMV_SRC[@]} -eq 0 && "$GIT_ACTION" == "none" && -z "$KILL_PIDS" && -z "$dirty" ]]; then
    ok "Nothing to do: this install is already migrated. Next: ./setup.sh"
    exit 0
fi
if [[ -n "$DRY_RUN" ]]; then
    log "Dry run: nothing was changed. Transcript: ${MIGRATE_LOG}"
    exit 0
fi
if [[ -z "$ASSUME_YES" ]]; then
    [[ -t 0 ]] || die "Non-interactive; pass --yes to confirm the plan above."
    read -r -p "Type 'yes' to apply this plan: " reply
    [[ "$reply" == "yes" ]] || die "Aborted; nothing changed."
fi

# ============================================================================= apply
section "Migrating"

if [[ -n "$KILL_PIDS" ]]; then
    # shellcheck disable=SC2086
    kill $KILL_PIDS 2>/dev/null || true
    ok "orphaned Ollama server(s) killed"
fi

if [[ ${#PRESERVE_REL[@]} -gt 0 ]]; then
    for rel in "${PRESERVE_REL[@]}"; do
        mkdir -p "${PRESERVE_DIR}/$(dirname "$rel")"
        mv "${REPO_DIR}/${rel}" "${PRESERVE_DIR}/${rel}"
    done
    ok "moved ${#PRESERVE_REL[@]} path(s) to ${PRESERVE_DIR}"
fi

if [[ ${#DEL_PATHS[@]} -gt 0 ]]; then
    for p in "${DEL_PATHS[@]}"; do rm -rf "$p"; done
    ok "deleted ${#DEL_PATHS[@]} path(s), $(hsize "$DEL_BYTES")"
    find "${MODELS_DIR}/manifests" -mindepth 1 -type d -empty -delete 2>/dev/null || true
fi
# git cannot track empty directories, so these are leftovers by definition.
find "$REPO_DIR" -mindepth 1 -type d -empty -not -path "${REPO_DIR}/.git/*" -delete 2>/dev/null || true

if [[ ${#LOGMV_SRC[@]} -gt 0 ]]; then
    i=0
    while [[ $i -lt ${#LOGMV_SRC[@]} ]]; do mv -f "${LOGMV_SRC[$i]}" "${LOGMV_DST[$i]}"; i=$((i + 1)); done
    ok "moved ${#LOGMV_SRC[@]} September transcript(s) into ${LOG_DIR}"
fi

if [[ -n "$RESET" && -n "$dirty" ]]; then
    run git_in "$REPO_DIR" reset -q --hard HEAD
fi
if [[ "$GIT_ACTION" != "none" ]]; then
    # A copied-in migrate.sh is the one untracked file git may need to overwrite. Unlinking a
    # running script is safe: bash keeps reading the old inode, and git writes a new one.
    if git_in "$REPO_DIR" ls-files --others 2>/dev/null | grep -x migrate.sh >/dev/null \
       && git_in "$REPO_DIR" cat-file -e "${GIT_REMOTE_REF}:migrate.sh" 2>/dev/null; then
        log "[repo] removing the copied-in migrate.sh so git can write the tracked one"
        rm -f "${REPO_DIR}/migrate.sh"
    fi
fi
case "$GIT_ACTION" in
    switch) run git_in "$REPO_DIR" checkout -q -B "$GIT_TARGET" "$GIT_REMOTE_REF" ;;
    reset)  run git_in "$REPO_DIR" reset -q --hard "$GIT_REMOTE_REF" ;;
    ff)     run git_in "$REPO_DIR" merge -q --ff-only "$GIT_REMOTE_REF" ;;
esac
[[ "$GIT_ACTION" == "none" ]] || ok "checkout at $(git_in "$REPO_DIR" log -1 --format='%h %ad %s' --date=short) on $(git_in "$REPO_DIR" rev-parse --abbrev-ref HEAD)"
[[ -x "${REPO_DIR}/setup.sh" ]] || chmod +x "${REPO_DIR}/setup.sh" 2>/dev/null || true

# ============================================================================= verify
section "Verification"
left="$(git_in "$REPO_DIR" ls-files --others --directory 2>/dev/null || true)"
if [[ -z "$left" ]]; then ok "[repo] nothing untracked, nothing modified"
else printf '%s\n' "$left" | sed 's/^/         untracked: /'; ok "[repo] the entries above were kept on purpose (--keep-data, or this script itself)"; fi
[[ -z "$(git_in "$REPO_DIR" status --porcelain -uno 2>/dev/null || true)" ]] || warn "[repo] tracked files are still modified; setup.sh will not pull over them"
if grep -q '^readonly ENV_VERSION=' "${REPO_DIR}/setup.sh" 2>/dev/null; then ok "[repo] setup.sh is the rewrite"
else warn "[repo] setup.sh is still the September version; do not run it. Re-run migrate.sh with --branch <branch carrying the rewrite>."; fi
for f in "$ENV_SCRIPT" "$JOB_SCRIPT"; do
    [[ ! -f "$f" ]] || grep -q '^export NLP_ENV_VERSION=' "$f" 2>/dev/null || warn "[home] $(basename "$f") is still a September version"
done
[[ ! -f "$PREFLIGHT_PY" ]] || warn "[home] ${PREFLIGHT_PY} still exists"
if [[ -d "$MODELS_DIR" && -z "$KEEP_MODELS" && -n "$MODEL" ]]; then
    n_manifests="$(find "${MODELS_DIR}/manifests" -type f 2>/dev/null | wc -l)"
    ok "[model] store holds ${n_manifests} manifest(s), $(hsize "$(path_bytes "$MODELS_DIR")")"
fi

section "Done"
printf '  Transcript   %s\n' "$MIGRATE_LOG"
[[ ${#PRESERVE_REL[@]} -eq 0 ]] || printf '  Moved aside  %s   (review; copy to durable storage or rm -rf when sure)\n' "$PRESERVE_DIR"
cat <<EOF

  Next, on a login node:
    1. cd ${REPO_DIR} && ./setup.sh
       It will: verify the venv and write its dependency stamp (pip against the pinned requirements),
       replace the September image with the one setup.sh pins (apptainer pull, mksquashfs capped),
       download ${MODELS[*]:-the models} if they are not in the store, regenerate ~/nlp-study.job and
       ~/nlp-study-env.sh, and run the static (tier 0) preflight.
EOF
if [[ ${#PRESERVE_REL[@]} -gt 0 && -z "$KEEP_DATA" ]]; then
    for rel in "${PRESERVE_REL[@]}"; do
        [[ "$rel" == "notes.csv" ]] || continue
        printf '    2. mv %s/notes.csv %s/notes.csv     # stage the input again (it was moved aside)\n' "$PRESERVE_DIR" "$REPO_DIR"
    done
fi
cat <<EOF
    3. qlogin -q UI -pe smp 8   then   ./setup.sh      # live LLM check on a compute node
    4. qsub ~/nlp-study.job --analyze --csv ./notes.csv
EOF
[[ ${#WARNINGS[@]} -eq 0 ]] || { printf '\n'; warn "${#WARNINGS[@]} warning(s) above; read them before running setup.sh."; }
