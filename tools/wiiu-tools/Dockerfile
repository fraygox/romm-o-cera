# Builds wuatool (Wii U .wud/.wux -> .wua for Cemu) for Batocera x86_64.
# Debian 12 (glibc 2.36) so it runs on Batocera 43.1 (glibc 2.40). zstd is
# built from source; OpenSSL's libcrypto, libstdc++ and libgcc are linked in
# statically: the program depends only on glibc. Boost, fmt and glm are used
# as headers only (X11 too: Cemu's platform.h includes it; nothing links it). python3-cryptography runs the self-test (synthetic discs
# with random keys).
FROM debian:bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates file binutils xz-utils python3 python3-cryptography \
        libssl-dev libfmt-dev libboost-dev libglm-dev libx11-dev libxrender-dev \
    && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["/recipe/build.sh"]
