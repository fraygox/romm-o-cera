# Builds dolphin-tool (Dolphin's CLI: convert/verify/header) for Batocera x86_64.
# Debian 12 (glibc 2.36) so the binary runs on Batocera 43.1 (glibc 2.40);
# Dolphin's bundled externals + static libstdc++/libgcc: depends only on glibc.
FROM debian:bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake ninja-build git pkg-config ca-certificates python3 \
        libegl-dev libgl-dev libudev-dev \
    && rm -rf /var/lib/apt/lists/*
COPY build.sh /build.sh
ENTRYPOINT ["/build.sh"]
