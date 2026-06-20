ARG DEPLOY_ID

FROM docker.io/archlinux:base AS uki-builder

ARG DEPLOY_ID
ENV INITRAMFS=/work/initramfs
ENV APPLETS="sh mount cat mkdir ls echo sleep switch_root insmod cp findfs"
ENV ESSENTIAL="erofs overlay loop fat vfat ext4 btrfs"
ENV HW="virtio_pci virtio_blk vmd nvme ahci sd_mod usb_storage uas xhci_pci ehci_pci sdhci_pci mmc_block"

COPY init /init
RUN chmod +x /init

RUN pacman -Syu --noconfirm \
        linux-zen binutils util-linux busybox cpio systemd systemd-ukify composefs kmod zstd

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

RUN cp -a "/usr/lib/modules" /out/modules

FROM docker.io/artixlinux/artixlinux:base-dinit

COPY --from=uki-builder /out/initramfs.img /usr/lib/zerith/initramfs.img
COPY --from=uki-builder /out/modules /usr/lib/modules

# Host tooling: the zerith package lands on the import path at
# /usr/lib/zerith, and the zerithctl shim (which adds that dir to
# sys.path) goes on PATH. See docs/host-tooling.md.
COPY zerith /usr/lib/zerith/zerith
COPY zerithctl /usr/local/bin/zerithctl
RUN chmod +x /usr/local/bin/zerithctl
COPY system_files /

# Base Packages
RUN pacman -Syu --noconfirm \
    base \
    base-devel \
    ca-certificates \
    util-linux \
    which \
    dinit \
    dbus \
    dbus-dinit \
    networkmanager-dinit \
    shadow \
    sudo \
    curl \
    dhcpcd \
    iproute2 \
    iputils \
    networkmanager \
    wget \
    polkit \
    btrfs-progs \
    composefs \
    fsverity-utils \
    dosfstools \
    e2fsprogs \
    fuse-overlayfs \
    efibootmgr \
    podman \
    kmod \
    pciutils \
    usbutils \
    findutils \
    gawk \
    grep \
    procps-ng \
    sed \
    gzip \
    rsync \
    tar \
    unzip \
    xz \
    zstd \
    git \
    less \
    nvim \
    emacs \
    btop \
    awww

RUN set -eux; \
    VERSION="$(curl -fsSL https://api.github.com/repos/oras-project/oras/releases/latest | \
      sed -n 's/.*"tag_name":[[:space:]]*"v\([^"]*\)".*/\1/p')"; \
    curl -fsSLo oras.tar.gz \
      "https://github.com/oras-project/oras/releases/download/v${VERSION}/oras_${VERSION}_linux_amd64.tar.gz"; \
    tar -xzf oras.tar.gz; \
    install -Dm755 oras /usr/local/bin/oras; \
    rm -f oras.tar.gz oras

RUN set -eux; \
    VERSION="$(curl -fsSL https://api.github.com/repos/sigstore/cosign/releases/latest | \
      sed -n 's/.*"tag_name":[[:space:]]*"v\([^"]*\)".*/\1/p')"; \
    curl -fsSLo /usr/local/bin/cosign \
      "https://github.com/sigstore/cosign/releases/download/v${VERSION}/cosign-linux-amd64"; \
    chmod 755 /usr/local/bin/cosign

RUN useradd -m -G wheel aur && \
    echo "aur ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers && \
    su aur -c "cd /home/aur && git clone https://aur.archlinux.org/yay-bin.git && cd yay-bin && makepkg -si --noconfirm" && \
    su aur -c "yay -S --noconfirm mangowm vibepanel-bin veila-bin" && \
    pacman -Rs --noconfirm base-devel && \
    pacman -Scc --noconfirm && \
    userdel -r aur && \
    sed -i '/aur ALL=(ALL) NOPASSWD: ALL/d' /etc/sudoers

RUN echo 'root:root' | chpasswd #for debugging purposes

RUN dinitctl -o enable NetworkManager && \
    dinitctl -o enable dbus

RUN mkdir -p /usr/etc/dinit.d && \
    printf 'type = internal\noptions = starts-rwfs\n' > /usr/etc/dinit.d/early-root-rw.target

RUN mkdir -p /usr/lib/tmpfiles.d && \
    printf 'd /var/log/dinit 0755 root root -\n' > /usr/lib/tmpfiles.d/zerith-dinit.conf

RUN echo "LIBSEAT_BACKEND=logind" >> /etc/environment
RUN echo 'ZERITH_COSIGN_IDENTITY=^https://github.com/Zerith-Linux/Zerith/\.github/workflows/build\.yml@refs/heads/.+$' >> /etc/environment

RUN rm -rf /usr/lib/systemd /etc/systemd /var/lib/systemd
