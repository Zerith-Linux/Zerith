FROM docker.io/artixlinux/artixlinux:base-dinit

RUN pacman -Syu --noconfirm \
    linux \
    dracut \
    grub \
    ostree \
    dinit

RUN mkdir -p /usr/etc && \
    cp -a /etc/. /usr/etc/ && \
    rm /usr/etc/machine-id || true

RUN echo 'HOOKS=(base udev modconf block filesystems keyboard fsck)' > /etc/mkinitcpio.conf

RUN rm -rf /boot/*

RUN set -eux; \
    KVER="$(ls /usr/lib/modules | head -n1)"; \
    depmod -a "$KVER"; \
    export DRACUT_NO_XATTR=1; \
    DRACUT_NO_XATTR=1 dracut \
      --no-hostonly \
      --kver "$KVER" \
      --reproducible \
      --zstd -v \
      -f "/usr/lib/modules/$KVER/initramfs.img"; \
    chmod 0600 "/usr/lib/modules/$KVER/initramfs.img"
