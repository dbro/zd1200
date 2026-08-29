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

FROM debian:13-slim

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        binutils \
        binutils-i686-linux-gnu \
        cpio \
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

RUN chmod +x /opt/zd1200/boot-initrd-handoff \
        /opt/zd1200/*.sh /opt/zd1200/*.py \
    && mkdir -p /opt/zd1200/image /var/lib/zd1200

ENV STATE_DIR=/var/lib/zd1200 \
    NETWORK_MODE=tap \
    TAP_IF=tap-zd \
    GUEST_IP=192.168.50.10 \
    WEB_PROBE=auto \
    MEMORY_MB=2048 \
    WEB_WAIT_SECONDS=600

VOLUME ["/var/lib/zd1200"]

HEALTHCHECK --interval=30s --timeout=8s --start-period=10m --retries=3 \
    CMD ["/opt/zd1200/zd-healthcheck.sh"]

CMD ["./run-zd1200-web.sh"]
