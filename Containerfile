FROM docker.io/archlinux:base AS uki-builder
COPY init /init
RUN chmod +x /init

RUN pacman -Syu --noconfirm \
    linux binutils util-linux busybox cpio systemd composefs kmod zstd

RUN mkdir -p /work/initramfs/{bin,sbin,dev,proc,sys,mnt,sysroot,run,out} && \
    ln -s usr/lib /work/initramfs/lib && \
    ln -s usr/lib /work/initramfs/lib64 && \
    mkdir -p /work/initramfs/usr/lib

# busybox + applets
RUN cp /usr/bin/busybox /work/initramfs/bin/ && \
    for a in sh mount cat mkdir ls echo sleep switch_root insmod; do \
        ln -sf busybox /work/initramfs/bin/$a; \
    done

# real modprobe (handles zstd-compressed modules + deps) and mount.composefs
RUN cp "$(command -v modprobe)" /work/initramfs/sbin/modprobe && \
    MC="$(command -v mount.composefs || echo /usr/sbin/mount.composefs)" && \
    cp "$MC" /work/initramfs/sbin/mount.composefs

# copy shared libs for every dynamic binary we added
RUN for b in /work/initramfs/bin/busybox \
             /work/initramfs/sbin/modprobe \
             /work/initramfs/sbin/mount.composefs; do \
        for l in $(ldd "$b" 2>/dev/null | awk '/=>/{print $3} /ld-linux/{print $1}'); do \
            [ -f "$l" ] || continue; \
            mkdir -p "/work/initramfs$(dirname "$l")"; \
            cp -Lu "$l" "/work/initramfs$l"; \
        done; \
    done

# copy only the modules we need, with their dependency closure
RUN KVER="$(ls /usr/lib/modules | grep -v '^extramodules' | head -n1)" && \
    for m in virtio_pci virtio_blk ext4 btrfs loop erofs overlay; do \
        modprobe -S "$KVER" -D "$m" 2>/dev/null; \
    done | awk '/^insmod/{print $2}' | sort -u | while read ko; do \
        dst="/work/initramfs/usr/lib/modules/$KVER/${ko#/usr/lib/modules/$KVER/}"; \
        mkdir -p "$(dirname "$dst")"; cp "$ko" "$dst"; \
    done && \
    depmod -b /work/initramfs "$KVER"

RUN cp /init /work/initramfs/init && chmod +x /work/initramfs/init && \
    cd /work/initramfs && \
    find . -print0 | cpio --null -ov --format=newc | gzip -9 > /out/initramfs.img

RUN KVER="$(ls /usr/lib/modules | grep -v '^extramodules' | head -n1)" && \
    objcopy \
      --add-section .linux="/usr/lib/modules/$KVER/vmlinuz" \
      --change-section-vma .linux=0x2000000 \
      --add-section .initrd="/out/initramfs.img" \
      --change-section-vma .initrd=0x3000000 \
      /usr/lib/systemd/boot/efi/linuxx64.efi.stub \
      /out/zerith.efi

FROM docker.io/artixlinux/artixlinux:base-dinit
COPY --from=uki-builder /out/zerith.efi /usr/lib/uki/zerith.efi

RUN pacman -Syu --noconfirm \
    limine \
    util-linux \
    dinit \
    composefs

RUN mkdir -p /usr/etc && \
    cp -a /etc/. /usr/etc/ && \
    rm /usr/etc/machine-id || true
