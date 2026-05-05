#!/usr/bin/env bash
# Find errors in a nextflow workflow run, local or remote (atlantis/S3).
#
# Usage:
#   ./runscripts/find_workflow_errors.sh                       # newest local dir under out/
#   ./runscripts/find_workflow_errors.sh out/my_workflow       # local workflow dir
#   ./runscripts/find_workflow_errors.sh 81                    # atlantis simulation id
#   ./runscripts/find_workflow_errors.sh s3://bucket/path/...  # atlantis-style S3 URI
#
# For S3 sources the URI may point at the workflow dir, the nextflow/
# subdir, or the nextflow_workdirs/ subdir — autodetected. Only the
# log/err/sh/exitcode files are mirrored locally (not staged data).
#
# Walks each task workdir; prints any task that exited non-zero (or
# has non-empty .command.err) along with the tail of its err output and
# the head of its .command.sh so you can see WHICH task failed and why.

set -u

ROOT="${1:-}"

# --- Atlantis simulation id: ask CLI for the S3 workdir ---------------------

if [[ "$ROOT" =~ ^[0-9]+$ ]]; then
    sim_id="$ROOT"
    if ! command -v atlantis >/dev/null 2>&1 && ! command -v uv >/dev/null 2>&1; then
        echo "neither 'atlantis' nor 'uv' found — needed to resolve sim $sim_id" >&2
        exit 1
    fi
    # The atlantis CLI lives in the sibling sms-api project; invoke via uv from there.
    if command -v atlantis >/dev/null 2>&1; then
        atlantis_cmd=(atlantis)
    else
        sms_api_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../sms-api" 2>/dev/null && pwd)"
        if [[ -z "$sms_api_dir" ]]; then
            echo "could not find ../sms-api (where atlantis lives) relative to this script" >&2
            exit 1
        fi
        atlantis_cmd=(uv --project "$sms_api_dir" run atlantis)
    fi

    # atlantis output is often a Python repr like ['s3://.../nextflow_workdirs']
    # so we have to strip surrounding quote/bracket/paren punctuation after grepping.
    trim_punct() {
        local s="$1"
        while true; do
            case "$s" in
                *\'|*\"|*\)|*\]|*,|*\>) s="${s%?}";;
                *) break;;
            esac
        done
        printf '%s' "$s"
    }

    echo "Resolving atlantis simulation $sim_id ..." >&2
    # Try `simulation status` first (more structured), then `simulation log`.
    atlantis_out=$("${atlantis_cmd[@]}" simulation status "$sim_id" 2>&1 || true)
    s3_uri=$(echo "$atlantis_out" \
        | grep -oE 's3://[^[:space:]]*nextflow_workdirs[^[:space:]]*' \
        | head -1)
    if [[ -z "$s3_uri" ]]; then
        atlantis_out=$("${atlantis_cmd[@]}" simulation log "$sim_id" 2>&1 || true)
        s3_uri=$(echo "$atlantis_out" \
            | grep -oE 's3://[^[:space:]]*nextflow_workdirs[^[:space:]]*' \
            | head -1)
    fi
    if [[ -z "$s3_uri" ]]; then
        # Fall back to any s3 URI containing /nextflow/ — older log formats.
        s3_uri=$(echo "$atlantis_out" \
            | grep -oE 's3://[^[:space:]]*/nextflow/[^[:space:]]*' \
            | head -1)
    fi
    if [[ -z "$s3_uri" ]]; then
        # In-progress runs sometimes only surface the outer bucket prefix.
        # Take any s3:// URI from the output; the walker below will descend
        # into <prefix>/[<sim_id>/]nextflow/nextflow_workdirs.
        s3_uri=$(echo "$atlantis_out" \
            | grep -oE 's3://[^[:space:]]+' \
            | head -1)
    fi
    s3_uri=$(trim_punct "$s3_uri")
    if [[ -z "$s3_uri" ]]; then
        echo "could not find any S3 URI in atlantis output for sim $sim_id" >&2
        echo "atlantis output (last 30 lines):" >&2
        echo "$atlantis_out" | tail -30 >&2
        exit 1
    fi
    echo "Found: $s3_uri" >&2
    ROOT="$s3_uri"
fi

# --- Remote (S3) source: sync error/log files to a local mirror -------------

if [[ "$ROOT" == s3://* ]]; then
    if ! command -v aws >/dev/null 2>&1; then
        echo "aws CLI not found — required to sync S3 workdirs" >&2
        exit 1
    fi

    # Normalize the URI to point at the nextflow_workdirs/ root.
    # Atlantis writes workdirs under a doubled segment
    # (sim<N>-<id>-<hash>/sim<N>-<id>-<hash>/nextflow/nextflow_workdirs/),
    # but users often pass just the outer prefix — so probe candidates and
    # walk one level down if the obvious path is empty.
    s3_has_keys() {
        # Returns 0 if the prefix has at least one key under it.
        aws s3 ls "${1%/}/" 2>/dev/null | head -1 | grep -q .
    }

    case "$ROOT" in
        */nextflow_workdirs|*/nextflow_workdirs/)
            WORKDIRS_S3="${ROOT%/}";;
        */nextflow|*/nextflow/)
            WORKDIRS_S3="${ROOT%/}/nextflow_workdirs";;
        *)
            WORKDIRS_S3=""
            # Try the obvious candidate first
            cand="${ROOT%/}/nextflow/nextflow_workdirs"
            if s3_has_keys "$cand"; then
                WORKDIRS_S3="$cand"
            else
                # Walk one level down — Atlantis's doubled-segment layout
                # puts everything under <prefix>/<sim_id_repeated>/nextflow/...
                while IFS= read -r sub; do
                    sub="${sub%/}"
                    [[ -z "$sub" ]] && continue
                    cand="${ROOT%/}/${sub}/nextflow/nextflow_workdirs"
                    if s3_has_keys "$cand"; then
                        WORKDIRS_S3="$cand"
                        echo "Found workdirs at $cand" >&2
                        break
                    fi
                done < <(aws s3 ls "${ROOT%/}/" 2>/dev/null | awk '/PRE/ {print $2}')
            fi
            if [[ -z "$WORKDIRS_S3" ]]; then
                echo "no nextflow_workdirs/ found under $ROOT" >&2
                echo "(checked $ROOT/nextflow/nextflow_workdirs and one level down)" >&2
                exit 1
            fi
            ;;
    esac

    # Local mirror path keyed off the workflow segment so repeated runs
    # against the same simulation reuse the cache.
    bn_trim="${WORKDIRS_S3%/}"
    bn_trim="${bn_trim%%/nextflow*}"
    base_name="${bn_trim##*/}"
    [[ -z "$base_name" ]] && base_name="atlantis_run"
    LOCAL_MIRROR="/tmp/wf_errors_${base_name}"

    mkdir -p "$LOCAL_MIRROR/nextflow/nextflow_workdirs"
    echo "Syncing $WORKDIRS_S3 -> $LOCAL_MIRROR (logs/errs only)"
    aws s3 sync "$WORKDIRS_S3" "$LOCAL_MIRROR/nextflow/nextflow_workdirs" \
        --exclude '*' \
        --include '*/.command.err' \
        --include '*/.command.log' \
        --include '*/.command.sh' \
        --include '*/.exitcode' \
        --no-progress 2>&1 | grep -E "download|warning|error" | tail -10
    ROOT="$LOCAL_MIRROR"
    echo
fi

# --- Local source: pick newest if not specified ------------------------------

if [[ -z "$ROOT" ]]; then
    # newest output dir that contains a nextflow/ subdir
    ROOT=$(ls -dt out/*/nextflow 2>/dev/null | head -1 | xargs -r dirname)
    if [[ -z "$ROOT" ]]; then
        echo "no workflow output dir found under out/" >&2
        exit 1
    fi
fi

echo "Scanning: $ROOT"
echo

WORKDIRS="$ROOT/nextflow/nextflow_workdirs"
if [[ ! -d "$WORKDIRS" ]]; then
    echo "no $WORKDIRS — is this a workflow output dir?" >&2
    exit 1
fi

describe_task() {
    # Pull a one-liner identifier out of .command.sh: the script being
    # invoked + the most discriminating CLI args (seed, generation,
    # agent_id, analysis name).
    local sh="$1"
    [[ -f "$sh" ]] || { echo "(no .command.sh)"; return; }
    local script_name args
    script_name=$(grep -oE 'python [^ ]+\.py' "$sh" | head -1 | awk '{print $2}' | xargs -r basename)
    args=$(grep -oE -- '--(seed|lineage_seed|generation|agent_id|analysis|variant) [^\\]+' "$sh" \
           | tr '\n' ' ' | sed 's/  */ /g; s/ $//')
    if [[ -z "$script_name$args" ]]; then
        # Fallback: just the first non-comment, non-empty line of the script.
        script_name=$(grep -v '^#\|^$' "$sh" | head -1 | cut -c1-80)
    fi
    echo "${script_name:-?} ${args}"
}

found=0
# Sort by mtime so the most recent failure appears last (and is easiest to read)
while IFS= read -r err; do
    workdir=$(dirname "$err")
    exitcode_file="$workdir/.exitcode"
    sh_file="$workdir/.command.sh"
    err_size=$(stat -c %s "$err" 2>/dev/null || echo 0)
    exitcode="?"
    [[ -f "$exitcode_file" ]] && exitcode=$(cat "$exitcode_file")

    # Skip clean tasks (exit 0 AND empty err)
    if [[ "$exitcode" == "0" && "$err_size" == "0" ]]; then
        continue
    fi

    # Sometimes err is empty but exitcode != 0; print .command.log tail too.
    found=$((found+1))
    task_id=$(describe_task "$sh_file")
    echo "=========================================================="
    echo "FAILED: $workdir"
    echo "  task: $task_id"
    echo "  exitcode: $exitcode    .command.err size: $err_size bytes"
    echo "----------------------------------------------------------"
    if [[ "$err_size" -gt 0 ]]; then
        tail -40 "$err"
    else
        log="$workdir/.command.log"
        if [[ -f "$log" ]]; then
            echo "(.command.err empty — last 40 lines of .command.log)"
            tail -40 "$log"
        else
            echo "(no .command.err and no .command.log)"
        fi
    fi
    echo
done < <(find "$WORKDIRS" -name ".command.err" -printf '%T@ %p\n' \
         | sort -n | awk '{print $2}')

if [[ "$found" == 0 ]]; then
    echo "No failed tasks found."
fi
