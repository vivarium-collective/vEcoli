#!/usr/bin/env bash

set -e

passphrase_filepath="$1"
module_filepath="$2"

gpg --batch --yes \
    --passphrase-file "$passphrase_filepath" \
    --symmetric --cipher-algo AES256 "$module_filepath"

