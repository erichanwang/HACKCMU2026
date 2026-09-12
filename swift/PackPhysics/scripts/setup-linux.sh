#!/usr/bin/env bash
# Idempotent, user-space Swift toolchain setup for Linux. No sudo.
#
# 1. Installs swiftly + the latest stable Swift toolchain into
#    ~/.local/share/swiftly, if `swift` isn't already usable.
# 2. Only if that toolchain's swift-build is missing libxml2.so.2 /
#    libicuuc.so.74 (true on Ubuntu 26.04, which dropped both from the
#    base image; not true on 22.04/24.04), fetches the matching Ubuntu
#    noble (24.04) .debs and extracts them into ~/.local/swift-compat.
#
# Safe to re-run: exits quickly once both are already in place.
set -euo pipefail

SWIFTLY_HOME="${SWIFTLY_HOME_DIR:-$HOME/.local/share/swiftly}"
COMPAT_DIR="$HOME/.local/swift-compat"
COMPAT_LIB="$COMPAT_DIR/usr/lib/x86_64-linux-gnu"

log() { echo "==> $*"; }

# --- 1. swiftly + toolchain -------------------------------------------------

if [ -x "$SWIFTLY_HOME/bin/swift" ]; then
    export PATH="$SWIFTLY_HOME/bin:$PATH"
fi

if ! command -v swift >/dev/null 2>&1; then
    if [ ! -x "$SWIFTLY_HOME/bin/swiftly" ]; then
        log "swiftly not found; installing into $SWIFTLY_HOME (user-space, no sudo)"
        arch="$(uname -m)"
        tmp="$(mktemp -d)"
        trap 'rm -rf "$tmp"' EXIT
        curl -fsSL "https://download.swift.org/swiftly/linux/swiftly-${arch}.tar.gz" -o "$tmp/swiftly.tar.gz"
        tar -xzf "$tmp/swiftly.tar.gz" -C "$tmp"
        "$tmp/swiftly" init --assume-yes --no-modify-profile --quiet-shell-followup
    else
        log "swiftly present but no toolchain installed; installing latest stable"
        "$SWIFTLY_HOME/bin/swiftly" install latest --use --assume-yes
    fi
    export PATH="$SWIFTLY_HOME/bin:$PATH"
fi

if ! command -v swift >/dev/null 2>&1; then
    echo "error: swift still not on PATH after setup" >&2
    exit 1
fi

log "swift toolchain ready: $(command -v swift)"

# --- 2. libxml2 / libicu compat, only if this box actually needs it --------

swift_build_bin="$(find "$SWIFTLY_HOME/toolchains" -name swift-build 2>/dev/null | head -1)"

missing=""
if [ -n "$swift_build_bin" ]; then
    # Clear LD_LIBRARY_PATH for the check so it reflects the real system,
    # not a compat dir this same script (or swiftenv.sh) already exported.
    missing="$(LD_LIBRARY_PATH="" ldd "$swift_build_bin" 2>/dev/null | grep 'not found' || true)"
fi

if [ -z "$missing" ]; then
    log "system already has the shared libraries swift-build needs; no compat layer required"
else
    log "swift-build is missing shared libraries on this system:"
    echo "$missing" | sed 's/^/    /'

    if [ -f "$COMPAT_LIB/libxml2.so.2" ] && ls "$COMPAT_LIB"/libicuuc.so.74* >/dev/null 2>&1; then
        log "compat libs already extracted at $COMPAT_LIB"
    else
        log "fetching Ubuntu noble (24.04) libxml2/libicu74 .debs into $COMPAT_DIR"
        tmp="$(mktemp -d)"
        trap 'rm -rf "$tmp"' EXIT

        fetch_deb() {
            local pool_url="$1" name_regex="$2" listing file
            listing="$(curl -fsSL "$pool_url")"
            file="$(echo "$listing" | grep -oE "$name_regex" | sort -V | tail -1 || true)"
            if [ -z "$file" ]; then
                echo "error: no package matching '$name_regex' found at $pool_url" >&2
                exit 1
            fi
            curl -fsSL "${pool_url}${file}" -o "$tmp/$file"
            echo "$tmp/$file"
        }

        # noble ships libxml2 2.9.14 and libicu74 74.2 (SONAMEs libxml2.so.2
        # and libicuuc.so.74) -- the exact ubuntuN patch suffix changes with
        # security updates, so it's discovered here rather than pinned.
        xml2_deb="$(fetch_deb "http://archive.ubuntu.com/ubuntu/pool/main/libx/libxml2/" 'libxml2_2\.9\.14[^"]*_amd64\.deb')"
        icu_deb="$(fetch_deb "http://archive.ubuntu.com/ubuntu/pool/main/i/icu/" 'libicu74_74\.2[^"]*_amd64\.deb')"

        mkdir -p "$COMPAT_DIR"
        dpkg-deb -x "$xml2_deb" "$COMPAT_DIR"
        dpkg-deb -x "$icu_deb" "$COMPAT_DIR"
        log "extracted compat libs to $COMPAT_LIB"
    fi

    export LD_LIBRARY_PATH="$COMPAT_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

log "add these to your shell (or just: source swift/PackPhysics/swiftenv.sh):"
echo "    export PATH=\"$SWIFTLY_HOME/bin:\$PATH\""
echo "    export LD_LIBRARY_PATH=\"$COMPAT_LIB\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}\""

swift --version
