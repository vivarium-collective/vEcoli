#!/usr/bin/env bash

# Start an interactive singularity container from an image
# built with runscripts/container/build-image.sh.
# Supports optional bind mounts and Cloud Storage bucket mounting

set -eu # Exit on any error or unset variable

# Keep track of resources to clean up
TMP_OVERLAY_DIR=""

cleanup() {
  # Unmount bucket if mounted
  if [ -d "$(pwd)/bucket_mnt" ]; then
    fusermount -u $(pwd)/bucket_mnt &>/dev/null || true
    rm -rf "$(pwd)/bucket_mnt" &>/dev/null || true
    echo "Unmounted Cloud Storage bucket"
  fi

  # Remove the temporary overlay directory if it exists
  if [ -n "$TMP_OVERLAY_DIR" ] && [ -d "$TMP_OVERLAY_DIR" ]; then
    rm -rf "$TMP_OVERLAY_DIR" &>/dev/null || true
    echo "Cleaned up temporary overlay directory"
  fi
}

# Ensure resources are cleaned up on exit
trap cleanup EXIT INT TERM

# Default configuration variables
IMAGE_NAME="${USER}-image"
USE_SINGULARITY=0
RUN_LOCAL=0
DEV_MODE=0
BIND_MOUNTS=()
BIND_STR=""
BUCKET=""
OVERLAY_SIZE=1024
COMMAND="" # Default is empty, will start interactive shell if not specified

# Help message string
usage_str="Usage: interactive.sh [-i IMAGE_NAME] [-d] [-a] [-s OVERLAY_SIZE] [-l] [-b BUCKET] [-p PATH] [-c \"COMMAND\"]\n\
Options:\n\
    -i: Path to image to run if -a or -l are passed, otherwise name of Docker \
image inside vecoli Artifact Registry; defaults to \"$IMAGE_NAME\".\n\
    -d: Create editable install of current directory in container virtual environment; \
useful for making and testing code changes that, unlike changes to
the code in the container at /vEcoli, are persistent and work with git.\n\
    -a: Load SINGULARITY image (cannot use with -l).\n\
    -s: Size of sparse temporary SINGULARITY overlay image in MB; \
defaults to \"$OVERLAY_SIZE\" (only used if -a is passed).\n
    -l: Load local Docker image (cannot use with -a).\n\
    -b: Name of Cloud Storage bucket to mount inside container; first mounts
bucket at $(pwd)/bucket_mnt using gcsfuse (does not work with -a).\n\
    -p: Path(s) to mount inside container; can specify multiple with \
\"-p path1 -p path2\"\n\
    -c: Command to run inside container (non-interactive mode); if not provided, \
an interactive bash shell will be started.\n"

# Function to print usage instructions
function print_usage {
  printf "$usage_str"
}

# Parse command-line options
while getopts 'i:das:lb:p:c:' flag; do
  case "${flag}" in
  i) IMAGE_NAME="${OPTARG}" ;; # Set custom image name
  d) DEV_MODE=1 ;;             # Enable development mode
  a)
    # Make sure -a and -l are not both specified
    if [ "$RUN_LOCAL" -eq 1 ]; then
      echo "ERROR: Options -a (Singularity) and -l (local Docker) cannot be used together."
      print_usage
      exit 1
    fi
    USE_SINGULARITY=1
    ;;                           # Enable singularity mode
  s) OVERLAY_SIZE="${OPTARG}" ;; # Set the size of the sparse overlay
  l)
    # Make sure -l and -a are not both specified
    if [ "$USE_SINGULARITY" -eq 1 ]; then
      echo "ERROR: Options -l (local Docker) and -a (Singularity) cannot be used together."
      print_usage
      exit 1
    fi
    RUN_LOCAL=1
    ;;                                         # Enable local Docker mode
  b) BUCKET="${OPTARG}" ;;                     # Set Cloud Storage bucket to mount
  # p) BIND_MOUNTS+=($(realpath "${OPTARG}")) ;; # Collect absolute mount path(s)
  p)
    # If user passed host:container
    if [[ "$OPTARG" == *:* ]]; then
      BIND_MOUNTS+=("$OPTARG")
    else
      # Same path inside container
      # BIND_MOUNTS+=("$(realpath "$OPTARG")")
      echo "YOU must pass colonized bind paths"
    fi
    ;;
  c) COMMAND="${OPTARG}" ;;                    # Set the command to run (non-interactive mode)
  *)
    print_usage # Print usage for unknown flags
    exit 1
    ;;
  esac
done

# Validate that bucket mounting is not used with SINGULARITY
if [ -n "$BUCKET" ] && [ "$USE_SINGULARITY" -eq 1 ]; then
  echo "ERROR: Bucket mounting (-b) is not supported with Singularity mode (-a)."
  print_usage
  exit 1
fi

# ============= SINGULARITY-specific logic ============= #
if (($USE_SINGULARITY)); then
  # If there are bind mounts, format them for Singularity
  if [ ${#BIND_MOUNTS[@]} -ne 0 ]; then
    # BIND_STR=$(printf " -B %s" "${BIND_MOUNTS[@]}")
    BIND_STR=$(printf " -B %s" "${BIND_MOUNTS[@]}")
  fi

  echo "=== Launching SINGULARITY container from ${IMAGE_NAME} ==="

  # Create a temporary overlay directory
  TMP_OVERLAY_DIR=$(mktemp -d)
  echo "Creating ${OVERLAY_SIZE}MB sparse temporary overlay at ${TMP_OVERLAY_DIR}/overlay.img"
  # Create a sparse file (only allocates blocks as needed)
  dd if=/dev/zero of=${TMP_OVERLAY_DIR}/overlay.img bs=1M count=0 seek=${OVERLAY_SIZE}
  # Format the file as ext3 filesystem
  mkfs.ext3 -F ${TMP_OVERLAY_DIR}/overlay.img
  if (($DEV_MODE)); then
    echo "Starting container in development mode..."
    # Fakeroot is necessary for overlay to work
    #
    # UV_PROJECT_ENVIRONMENT is set to the virtual environment inside
    # the container with all dependencies installed. This way uv does
    # not try to create a new one and waste time installing dependencies.
    #
    # UV_COMPILE_BYTECODE=0 skips byte code compilation which would
    # otherwise add dozens of seconds to the start time for development
    # mode. This is because we are doing an editable install of the
    # repository on the host machine to the container .venv.

    # Non-interactive mode with custom command
    echo "Running command: $COMMAND"
    singularity exec -e --overlay ${TMP_OVERLAY_DIR}/overlay.img \
      --fakeroot ${BIND_STR} ${IMAGE_NAME} \
      bash -c "export UV_PROJECT_ENVIRONMENT=/vEcoli/.venv \
      && export UV_COMPILE_BYTECODE=0 \
      && export JAVA_HOME=$HOME/.local/bin/java-22 \
      && export PATH=$JAVA_HOME/bin:$HOME/.local/bin:$PATH \
      && uv sync --frozen && $COMMAND"
  else
    # Non-interactive mode with custom command
    echo "Running command: $COMMAND"
    singularity exec -e --overlay ${TMP_OVERLAY_DIR}/overlay.img \
      --fakeroot ${BIND_STR} ${IMAGE_NAME} bash -c "$COMMAND"
  fi
fi
