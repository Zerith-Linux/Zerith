FROM docker.io/archlinux:base AS uki-builder
COPY init /init
RUN chmod +x /init

RUN pacman -Syu --noconfirm \
    linux \
    binutils \
    util-linux \
    busybox \
    systemd

RUN mkdir -p /work/initramfs/{bin,dev,proc,sys,mnt,sysroot,out}

RUN cp /usr/bin/busybox /work/initramfs/bin/ && \
    ln -s busybox /work/initramfs/bin/sh && \
    ln -s busybox /work/initramfs/bin/mount

RUN cp /init /work/initramfs/init

RUN cd /work/initramfs && \
    find . -print0 | cpio --null -ov --format=newc | gzip -9 > /out/initramfs.img

RUN KVER="$(ls /usr/lib/modules | head -n1)" && \
    objcopy \
      --add-section .linux="/usr/lib/modules/$KVER/vmlinuz" \
      --add-section .initrd="/out/initramfs.img" \
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
