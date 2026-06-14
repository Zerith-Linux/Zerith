FROM docker.io/archlinux:base AS uki-builder

RUN pacman -Syu --noconfirm \
    linux \
    mkinitcpio \
    binutils \
    util-linux

RUN mkdir -p /work/initramfs /out

COPY init /init
RUN chmod +x /init

RUN printf '%s\n' \
    'MODULES=()' \
    'BINARIES=(mount switch_root)' \
    'FILES=(/init)' \
    'HOOKS=()' \
    > /etc/mkinitcpio.conf

RUN KVER="$(ls /usr/lib/modules | head -n1)" && \
    mkinitcpio -k "$KVER" -g /work/initramfs/initramfs.img

RUN KVER="$(ls /usr/lib/modules | head -n1)" && \
    objcopy \
      --add-section .linux="/usr/lib/modules/$KVER/vmlinuz" \
      --add-section .initrd="/work/initramfs/initramfs.img" \
      /usr/lib/systemd/boot/efi/linuxx64.efi.stub \
      /out/zerith.efi

FROM docker.io/artixlinux/artixlinux:base-dinit
COPY --from=uki-builder /out/zerith.efi /out/zerith.efi

RUN pacman -Syu --noconfirm \
    limine \
    util-linux \
    dinit \
    composefs

RUN mkdir -p /usr/etc && \
    cp -a /etc/. /usr/etc/ && \
    rm /usr/etc/machine-id || true
