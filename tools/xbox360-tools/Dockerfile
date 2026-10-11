# Builds extract-xiso + zarchive (Xbox 360 ISO -> .zar for xenia-canary) for
# Batocera x86_64. Debian 12 (glibc 2.36) so the binaries run on Batocera 43.1
# (glibc 2.40); zstd is built from source and linked in statically, as are
# libstdc++/libgcc: both binaries depend only on glibc.
FROM debian:bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates file binutils \
    && rm -rf /var/lib/apt/lists/*
COPY build.sh /build.sh
ENTRYPOINT ["/build.sh"]
