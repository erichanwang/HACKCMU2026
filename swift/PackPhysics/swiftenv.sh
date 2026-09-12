# Source this before `swift build` / `swift test` on this Linux box:
#   source swift/PackPhysics/swiftenv.sh
# Toolchain: swiftly (user-space). Ubuntu 26.04 lacks libxml2.so.2, which the
# 24.04-built toolchain links, so a private compat lib dir is prepended.
export PATH="$HOME/.local/share/swiftly/bin:$PATH"
export LD_LIBRARY_PATH="$HOME/.local/swift-compat/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
