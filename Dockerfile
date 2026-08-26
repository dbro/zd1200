FROM debian:13-slim

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        binutils \
        cpio \
        curl \
        e2fsprogs \
        gdb \
        gzip \
        procps \
        python3 \
        qemu-system-x86 \
        qemu-utils \
        ripgrep \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/zd1200
COPY boot-initrd-handoff \
     limit-process-cpu.py \
     make-runtime-initrd.sh \
     make-synthetic-cf.py \
     run-zd1200-qemu.sh \
     run-zd1200-web.sh \
     write-boarddata.py \
     zd1200-patch.gdb \
     /opt/zd1200/

RUN chmod +x /opt/zd1200/boot-initrd-handoff \
        /opt/zd1200/*.sh /opt/zd1200/*.py \
    && mkdir -p /opt/zd1200/image /var/lib/zd1200

ENV STATE_DIR=/var/lib/zd1200 \
    NETWORK_MODE=tap \
    TAP_IF=tap-zd \
    GUEST_IP=192.168.50.10 \
    MEMORY_MB=2048 \
    WEB_WAIT_SECONDS=600

VOLUME ["/var/lib/zd1200"]

HEALTHCHECK --interval=30s --timeout=8s --start-period=10m --retries=3 \
    CMD curl -kfsS --max-time 5 https://192.168.50.10/admin10/login.jsp >/dev/null || exit 1

CMD ["./run-zd1200-web.sh"]
