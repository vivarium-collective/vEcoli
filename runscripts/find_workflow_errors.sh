#!/usr/bin/env bash
# Find errors in the most recent nextflow workflow run.
#
# Usage:
#   ./runscripts/find_workflow_errors.sh                  # newest dir under out/
#   ./runscripts/find_workflow_errors.sh out/my_workflow  # specific dir
#
# Walks .command.err / .command.log under nextflow_workdirs/, prints any
# tasks that exited non-zero (or whose .command.err is non-empty), with
# the tail of their err output so the actual exception is visible.

set -u

ROOT="${1:-}"
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

found=0
# Sort by mtime so the most recent failure appears last (and is easiest to read)
while IFS= read -r err; do
    workdir=$(dirname "$err")
    exitcode_file="$workdir/.exitcode"
    err_size=$(stat -c %s "$err" 2>/dev/null || echo 0)
    exitcode="?"
    [[ -f "$exitcode_file" ]] && exitcode=$(cat "$exitcode_file")

    # Skip clean tasks (exit 0 AND empty err)
    if [[ "$exitcode" == "0" && "$err_size" == "0" ]]; then
        continue
    fi

    # Sometimes err is empty but exitcode != 0; print .command.log tail too.
    found=$((found+1))
    echo "=========================================================="
    echo "FAILED: $workdir"
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
