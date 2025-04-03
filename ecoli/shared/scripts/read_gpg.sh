#!/usr/bin/env bash

set -e

passphrase_filepath="$1"
dest="$2"
module_name="$3"

module_fp="$dest/$module_name.py"

gpg --batch --yes \
    --passphrase-file "$passphrase_filepath" \
    --output "$module_fp" \
    --decrypt "$module_fp.gpg"
