FROM docker.io/artixlinux/artixlinux:base-dinit

RUN pacman -Syu --noconfirm \
    linux \
    mkinitcpio \
    grub \
    ostree \
    dinit

RUN mkdir -p /usr/etc && \
    cp -a /etc/. /usr/etc/ && \
    rm -rf /usr/etc/machine-id /usr/etc/.*- 2>/dev/null || true

RUN rm -rf /etc
