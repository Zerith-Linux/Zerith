FROM docker.io/artixlinux/artixlinux:base-dinit

RUN pacman -Syu --noconfirm \
    linux \
    mkinitcpio \
    grub \
    ostree \
    dinit

RUN mkdir -p /usr/etc && \
    cp -a /etc/. /usr/etc/ && \
    rm /usr/etc/machine-id || true

RUN echo 'HOOKS=(base udev modconf block filesystems keyboard fsck)' > /etc/mkinitcpio.conf

RUN mkdir -p /boot

RUN mkinitcpio -P
