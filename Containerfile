FROM docker.io/archlinux:base AS uki-builder
COPY init /init
RUN chmod +x /init

RUN pacman -Syu --noconfirm \
    linux binutils util-linux busybox cpio systemd systemd-ukify composefs kmod zstd

RUN mkdir -p /work/initramfs/{bin,sbin,dev,proc,sys,mnt,sysroot,run} /out && \
    ln -s usr/lib /work/initramfs/lib && \
    ln -s usr/lib /work/initramfs/lib64 && \
    mkdir -p /work/initramfs/usr/lib

# busybox + applets
RUN cp /usr/bin/busybox /work/initramfs/bin/ && \
    for a in sh mount cat mkdir ls echo sleep switch_root insmod cp; do \
        ln -sf busybox /work/initramfs/bin/$a; \
    done

# modprobe + mount.composefs
RUN cp "$(command -v modprobe)" /work/initramfs/sbin/modprobe && \
    MC="$(command -v mount.composefs || echo /usr/sbin/mount.composefs)" && \
    cp "$MC" /work/initramfs/sbin/mount.composefs

# shared libs for every dynamic binary we added
RUN for b in /work/initramfs/bin/busybox \
             /work/initramfs/sbin/modprobe \
             /work/initramfs/sbin/mount.composefs; do \
        for l in $(ldd "$b" 2>/dev/null | awk '/=>/{print $3} /ld-linux/{print $1}'); do \
            [ -f "$l" ] || continue; \
            mkdir -p "/work/initramfs$(dirname "$l")"; \
            cp -Lu "$l" "/work/initramfs$l"; \
        done; \
    done

# only the modules we need, with their dependency closure (shipped UNCOMPRESSED)
RUN KVER="$(ls /usr/lib/modules | grep -v '^extramodules' | head -n1)" && \
    mkdir -p "/work/initramfs/usr/lib/modules/$KVER" && \
    for f in modules.builtin modules.builtin.modinfo modules.order; do \
        cp "/usr/lib/modules/$KVER/$f" "/work/initramfs/usr/lib/modules/$KVER/"; \
    done && \
    for m in virtio_pci virtio_blk ext4 btrfs loop erofs overlay; do \
        modprobe -S "$KVER" -D "$m" 2>/dev/null; \
    done | awk '/^insmod/{print $2}' | sort -u | while read ko; do \
        rel="${ko##*/modules/$KVER/}"; \
        dst="/work/initramfs/usr/lib/modules/$KVER/$rel"; \
        mkdir -p "$(dirname "$dst")"; cp "$ko" "$dst"; \
    done && \
    find "/work/initramfs/usr/lib/modules/$KVER" -name '*.ko.zst' -exec zstd -d --rm {} \; && \
    depmod -b /work/initramfs "$KVER"

# sanity check: every required module must be builtin or present in the initramfs
RUN KVER="$(ls /usr/lib/modules | grep -v '^extramodules' | head -n1)" && \
    for m in virtio_pci virtio_blk ext4 btrfs loop erofs overlay; do \
        if grep -qE "(^|/)${m}\.ko" "/usr/lib/modules/$KVER/modules.builtin"; then \
            echo "ok (builtin): $m"; \
        elif find "/work/initramfs/usr/lib/modules/$KVER" -name "${m}.ko*" | grep -q .; then \
            echo "ok (module):  $m"; \
        else \
            echo "FATAL: '$m' is neither builtin nor copied into the initramfs" >&2; \
            exit 1; \
        fi; \
    done

RUN cp /init /work/initramfs/init && chmod +x /work/initramfs/init && \
    cd /work/initramfs && \
    find . -print0 | cpio --null -ov --format=newc | gzip -9 > /out/initramfs.img

RUN KVER="$(ls /usr/lib/modules | grep -v '^extramodules' | head -n1)" && \
    ukify build \
      --linux="/usr/lib/modules/$KVER/vmlinuz" \
      --initrd=/out/initramfs.img \
      --stub=/usr/lib/systemd/boot/efi/linuxx64.efi.stub \
      --output=/out/zerith.efi

FROM docker.io/artixlinux/artixlinux:base-dinit
COPY --from=uki-builder /out/zerith.efi /usr/lib/uki/zerith.efi

RUN pacman -Syu --noconfirm \
    limine \
    util-linux \
    dinit \
    composefs \
    fuse-overlayfs \
    podman

RUN mkdir -p /usr/etc/dinit.d && \
    printf 'type = internal\noptions = starts-rwfs\n' > /usr/etc/dinit.d/early-root-rw.target

RUN mkdir -p /usr/lib/tmpfiles.d && \
    printf 'd /var/log/dinit 0755 root root -\n' > /usr/lib/tmpfiles.d/zerith-dinit.conf

RUN rm -rf /var/lib/pacman/sync/* /var/tmp/* /var/lib/dbus/machine-id && \
    find /var/cache /var/log -type f -delete && \
    mkdir -p /usr/share/factory && cp -a /var /usr/share/factory/var

RUN mkdir -p /usr/etc && \
    cp -a /etc/. /usr/etc/ && \
    rm /usr/etc/machine-id || true
