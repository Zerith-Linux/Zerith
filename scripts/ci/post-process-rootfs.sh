#!/usr/bin/env bash
#
# Post-process the freshly built rootfs into Zerith's immutable layout and
# commit it as the image "post-processed". Run under `buildah unshare` so the
# mount + chroot edits happen in a user namespace:
#
#     buildah unshare scripts/ci/post-process-rootfs.sh
#
# Requires: IMAGE_NAME. See docs/build-process.md.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/common.sh
. "$DIR/../lib/common.sh"
require_env IMAGE_NAME

ctr="$(buildah from "containers-storage:localhost/${IMAGE_NAME}:localbuild")"
mnt="$(buildah mount "$ctr")"

log "cleaning runtime/cache data"
rm -rf \
    "$mnt"/var/lib/pacman/sync/* \
    "$mnt"/var/tmp/* \
    "$mnt"/var/lib/dbus/machine-id
find "$mnt/var/cache" "$mnt/var/log" -type f -delete 2>/dev/null || true

log "relocating roothome/srv under factory /var"
rm -rf "$mnt/root" "$mnt/srv"
mkdir -p "$mnt/var/roothome" "$mnt/var/srv"
ln -sfn var/roothome "$mnt/root"
ln -sfn var/srv      "$mnt/srv"

log "capturing factory /var and relocating /etc -> /usr/etc"
mkdir -p "$mnt/usr/share/factory"
cp -a "$mnt/var" "$mnt/usr/share/factory/var"
mkdir -p "$mnt/usr/etc"
cp -a "$mnt/etc/." "$mnt/usr/etc/"
rm -f "$mnt/usr/etc/machine-id"

log "blanking mutable dirs back to empty mountpoints"
for d in etc var home dev proc sys run tmp mnt efi deploy; do
    rm -rf "${mnt:?}/$d"
    mkdir -p "$mnt/$d"
done
chmod 1777 "$mnt/tmp"

buildah unmount "$ctr"
buildah commit "$ctr" post-processed
buildah rm "$ctr"
log "committed image 'post-processed'"
