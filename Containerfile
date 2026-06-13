FROM docker.io/artixlinux/artixlinux:base-dinit

RUN pacman -Syu --noconfirm \
    linux \
    mkinitcpio \
    grub \
    ostree \
    dinit
