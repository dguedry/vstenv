#!/usr/bin/env bash
# Build and install the Flatpak for the current user from the WORKING TREE.
# The committed manifest points Flathub at a git tag; dev-manifest.py swaps
# the vstenv module's source for the local directory.
set -euo pipefail
cd "$(dirname "$0")"
ID=io.github.dguedry.vstenv
python3 dev-manifest.py
flatpak-builder --user --install --force-clean --ccache --repo=repo build-dir "$ID.dev.yml" "$@"
echo "Run with: flatpak run $ID"
