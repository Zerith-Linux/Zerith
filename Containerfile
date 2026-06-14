FROM docker.io/artixlinux/artixlinux:base-dinit

RUN pacman -Syu --noconfirm \
    linux \
    mkinitcpio \
    limine \
    systemd-ukify \
    composefs-tools \
    util-linux \
    dinit

RUN KVER="$(ls /usr/lib/modules | head -n1)"; \
    depmod -a "$KVER"

RUN KVER="$(ls /usr/lib/modules | head -n1)"; \
    mkinitcpio -k "$KVER" -g /work/initramfs/initramfs.img

RUN mkdir /out

RUN KVER="$(ls /usr/lib/modules | head -n1)"; \
    ukify build \
      --linux="/usr/lib/modules/$KVER/vmlinuz" \
      --initrd="/work/initramfs/initramfs.img" \
      --cmdline="slot=a root=composefs quiet" \
      --output="/out/zerith.efi"

RUN mkdir -p /usr/etc && \
    cp -a /etc/. /usr/etc/ && \
    rm /usr/etc/machine-id || true
