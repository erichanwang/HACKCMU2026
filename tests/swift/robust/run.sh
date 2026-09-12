#!/usr/bin/env bash
# Runs the pure-logic tests extracted from Spike/ScanView.swift on Linux.
set -euo pipefail
cd "$(dirname "$0")"

export PATH="$HOME/.local/share/swiftly/bin:$PATH"
export LD_LIBRARY_PATH="$HOME/.local/swift-compat/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

swift test
