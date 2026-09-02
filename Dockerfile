# syntax=docker/dockerfile:1.7

# The guest is an i386 Linux 2.6.32 system. Keep its helper i386 even when
# Docker builds the surrounding image on an ARM or x86-64 host.
ARG ANALYTICS_HELPER_PLATFORM=linux/386

FROM debian:13-slim AS ruckus-squashfs-tools

ARG DEBIAN_FRONTEND=noninteractive
ARG RUCKUS_AP_TOOLS_REV=3d9e4add414228eac4091f301e813d14130c3d61

# The R600 rootfs is historical LZMA SquashFS. Build the exact compatible GPL
# tools from a pinned public source revision; no vendor firmware is involved.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        git \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/* \
    && git init /src \
    && git -C /src remote add origin https://github.com/ms264556/ruckus_ap_firmware_mod.git \
    && git -C /src fetch --depth 1 origin "$RUCKUS_AP_TOOLS_REV" \
    && git -C /src checkout --detach FETCH_HEAD \
    && make -C /src/src/squashfs4.0-ruckus-lzma -j"$(nproc)"

FROM --platform=$ANALYTICS_HELPER_PLATFORM debian:13-slim AS ping-monitor-helper

ARG DEBIAN_FRONTEND=noninteractive
# Use a SQLite release contemporary with the ZD1200's Linux 2.6.32 guest.
# Modern SQLite builds returned SQLITE_IOERR when opening its live database.
ARG SQLITE_YEAR=2013
ARG SQLITE_AMALGAMATION=3071700
ARG SQLITE_AMALGAMATION_SHA256=022ef41bd83a1333faf40dc8f1f8469205f4a18c30dc5e137889ba7ea924ef30

RUN --security=insecure apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        build-essential \
        musl-tools \
        unzip \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://www.sqlite.org/${SQLITE_YEAR}/sqlite-amalgamation-${SQLITE_AMALGAMATION}.zip" \
        -o /tmp/sqlite-amalgamation.zip \
    && echo "${SQLITE_AMALGAMATION_SHA256}  /tmp/sqlite-amalgamation.zip" | sha256sum -c - \
    && unzip -q /tmp/sqlite-amalgamation.zip -d /src

COPY analytics/zd1200-ping-monitor.c \
     analytics/zd1200-local-getstat.c \
     /src/

RUN mkdir -p /out \
    && musl-gcc -std=c99 -Os -static -s \
        -DSQLITE_OMIT_LOAD_EXTENSION \
        -I"/src/sqlite-amalgamation-${SQLITE_AMALGAMATION}" \
        /src/zd1200-ping-monitor.c \
        "/src/sqlite-amalgamation-${SQLITE_AMALGAMATION}/sqlite3.c" \
        -o /out/zd1200-ping-monitor \
    && musl-gcc -std=c99 -Os -static -s \
        /src/zd1200-local-getstat.c \
        -o /out/zd1200-local-getstat

FROM debian:13-slim

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        binutils \
        binutils-i686-linux-gnu \
        cpio \
        gcc-i686-linux-gnu \
        curl \
        e2fsprogs \
        gzip \
        iproute2 \
        procps \
        python3 \
        qemu-system-x86 \
        qemu-utils \
        ripgrep \
        openssl \
        tar \
        unzip \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/zd1200
# The build context deliberately excludes vendor/, runtime/, firmware files,
# archives, and private material.  This generic image contains only project
# source plus the local preparation/runtime toolchain.
COPY . /opt/zd1200/
COPY --from=ruckus-squashfs-tools \
     /src/src/squashfs4.0-ruckus-lzma/mksquashfs \
     /src/src/squashfs4.0-ruckus-lzma/unsquashfs \
     /usr/local/lib/zd1200/ruckus-squashfs/
COPY --from=ping-monitor-helper \
     /out/zd1200-ping-monitor \
     /out/zd1200-local-getstat \
     /opt/zd1200/

RUN chmod +x /opt/zd1200/boot-initrd-handoff \
        /opt/zd1200/*.sh /opt/zd1200/*.py \
    && mkdir -p /opt/zd1200/image /var/lib/zd1200

ENV STATE_DIR=/var/lib/zd1200 \
    NETWORK_MODE=tap \
    TAP_IF=tap-zd \
    WEB_PROBE=auto \
    MEMORY_MB=2048 \
    WEB_WAIT_SECONDS=600

VOLUME ["/var/lib/zd1200"]

HEALTHCHECK --interval=30s --timeout=8s --start-period=10m --retries=3 \
    CMD ["/opt/zd1200/zd-healthcheck.sh"]

CMD ["./run-zd1200-web.sh"]
