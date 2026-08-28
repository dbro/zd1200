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
        util-linux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/zd1200
COPY boot-initrd-handoff \
     binary_patch_catalog.json \
     binary_patch_catalog.py \
     limit-process-cpu.py \
     make-runtime-initrd.sh \
     make-synthetic-cf.py \
     patch_binary_artifact.py \
     run-zd1200-qemu.sh \
     run-zd1200-web.sh \
     write-boarddata.py \
     zd_identity.py \
     zd_root_ssh.py \
     zd-controller-wrapper.sh \
     zd-healthcheck.sh \
     zd-memory-snapshot.sh \
     /opt/zd1200/

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
