ARG DEPLOY_ID

FROM docker.io/archlinux:base AS uki-builder

ARG DEPLOY_ID
ENV INITRAMFS=/work/initramfs
ENV APPLETS="sh mount cat mkdir ls echo sleep switch_root insmod cp findfs"
ENV ESSENTIAL="erofs overlay loop ext4 btrfs"
ENV HW="virtio_pci virtio_blk vmd nvme ahci sd_mod usb_storage uas xhci_pci ehci_pci sdhci_pci mmc_block"

COPY init /init
RUN chmod +x /init

RUN pacman -Syu --noconfirm \
        linux binutils util-linux busybox cpio systemd systemd-ukify composefs kmod zstd

RUN ls /usr/lib/modules | grep -v '^extramodules' | head -n1 > /kver

RUN mkdir -p "$INITRAMFS"/{bin,sbin,dev,proc,sys,mnt,sysroot,run} "$INITRAMFS/usr/lib" /out && \
    ln -s usr/lib "$INITRAMFS/lib" && \
    ln -s usr/lib "$INITRAMFS/lib64"

RUN cp /usr/bin/busybox "$INITRAMFS/bin/" && \
    for a in $APPLETS; do ln -sf busybox "$INITRAMFS/bin/$a"; done

RUN cp "$(command -v modprobe)" "$INITRAMFS/sbin/modprobe" && \
    cp "$(command -v mount.composefs || echo /usr/sbin/mount.composefs)" "$INITRAMFS/sbin/mount.composefs"

RUN for b in "$INITRAMFS"/bin/busybox "$INITRAMFS"/sbin/modprobe "$INITRAMFS"/sbin/mount.composefs; do \
        ldd "$b" 2>/dev/null | awk '/=>/{print $3} /ld-linux/{print $1}'; \
    done | sort -u | while read -r lib; do \
        [ -f "$lib" ] || continue; \
        mkdir -p "$INITRAMFS$(dirname "$lib")" && cp -Lu "$lib" "$INITRAMFS$lib"; \
    done

RUN KVER="$(cat /kver)"; MODDIR="$INITRAMFS/usr/lib/modules/$KVER"; \
    mkdir -p "$MODDIR" && \
    for f in modules.builtin modules.builtin.modinfo modules.order; do \
        cp "/usr/lib/modules/$KVER/$f" "$MODDIR/"; \
    done && \
    for m in $ESSENTIAL $HW; do modprobe -S "$KVER" -D "$m" 2>/dev/null; done \
        | awk '/^insmod/{print $2}' | sort -u | while read -r ko; do \
            dst="$MODDIR/${ko##*/modules/$KVER/}"; \
            mkdir -p "$(dirname "$dst")" && cp "$ko" "$dst"; \
        done && \
    find "$MODDIR" -name '*.ko.zst' -exec zstd -d --rm {} \; && \
    depmod -b "$INITRAMFS" "$KVER"

RUN KVER="$(cat /kver)"; MODDIR="$INITRAMFS/usr/lib/modules/$KVER"; \
    BUILTIN="/usr/lib/modules/$KVER/modules.builtin"; \
    present() { grep -qE "(^|/)$1\.ko" "$BUILTIN" || find "$MODDIR" -name "$1.ko*" | grep -q .; }; \
    for m in $ESSENTIAL; do \
        present "$m" && echo "ok essential: $m" || { echo "FATAL: essential '$m' missing" >&2; exit 1; }; \
    done; \
    for m in $HW; do \
        present "$m" && echo "ok hw:        $m" || echo "note: hw '$m' unavailable (skipped)"; \
    done

RUN cp /init "$INITRAMFS/init" && chmod +x "$INITRAMFS/init" && \
    cd "$INITRAMFS" && \
    find . -print0 | cpio --null -ov --format=newc | gzip -9 > /out/initramfs.img

RUN KVER="$(cat /kver)"; \
    ukify build \
        --linux="/usr/lib/modules/$KVER/vmlinuz" \
        --initrd=/out/initramfs.img \
        --cmdline "deploy=$DEPLOY_ID" \
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

RUN echo 'root:root' | chpasswd #for debugging purposes

RUN mkdir -p /usr/etc/dinit.d && \
    printf 'type = internal\noptions = starts-rwfs\n' > /usr/etc/dinit.d/early-root-rw.target

RUN mkdir -p /usr/lib/tmpfiles.d && \
    printf 'd /var/log/dinit 0755 root root -\n' > /usr/lib/tmpfiles.d/zerith-dinit.conf
