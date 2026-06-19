#!/usr/bin/env bash
#
# Inner build script — runs INSIDE archlinux:base (see render-uki.sh). Renders
# the composefs image + object store, pins the fs-verity digest into the UKI
# cmdline, and Secure Boot-signs the UKI and Limine loader when a key is given.
# Self-contained: the container has none of the repo's helpers.
#
# Inputs (env): DEPLOY_ID (required), SB_KEY/SB_CERT (optional PEM),
#   LIMINE_VERSION / LIMINE_ZIP_SHA256 (optional; pinned defaults below).
# Reads /rootfs (ro), writes /out.
set -euo pipefail

# Limine loader, pinned. Fetched and signed here (rather than shipped in the OS
# image) so it is Secure Boot-signed in the same step as the UKI. See docs.
LIMINE_VERSION="${LIMINE_VERSION:-12.3.3}"
LIMINE_ZIP_SHA256="${LIMINE_ZIP_SHA256:-7142601b68640b2980d0f42f9be2c1878cdd49ca8c65caae2663c3d79d832abd}"

pacman -Sy --noconfirm composefs fsverity-utils systemd-ukify sbsigntools \
    curl unzip >/dev/null

# 1. Render the read-only image + content-addressed object store.
mkdir -p /out/objects
mkcomposefs --digest-store=/out/objects /rootfs /out/root.cfs

# 2. Compute the image fs-verity digest offline (no kernel verity here).
#    `fsverity digest` prints "sha256:<hex> <file>"; keep the bare hex.
DIGEST="$(fsverity digest /out/root.cfs | awk '{print $1}' | sed 's/^sha256://')"
[ -n "$DIGEST" ] || { echo "FATAL: empty composefs digest" >&2; exit 1; }
echo "$DIGEST" > /out/composefs.digest
echo "composefs.digest=$DIGEST"

# 3. Build the UKI with the digest baked into the signed cmdline.
KVER=""
for moddir in /rootfs/usr/lib/modules/*/; do
    name="$(basename "$moddir")"
    case "$name" in extramodules*) continue ;; esac
    KVER="$name"; break
done
[ -n "$KVER" ] || { echo "FATAL: no kernel modules dir in rootfs" >&2; exit 1; }
ukify build \
    --linux="/rootfs/usr/lib/modules/$KVER/vmlinuz" \
    --initrd="/rootfs/usr/lib/zerith/initramfs.img" \
    --cmdline "deploy=$DEPLOY_ID composefs.digest=$DIGEST" \
    --stub=/usr/lib/systemd/boot/efi/linuxx64.efi.stub \
    --output=/out/zerith.efi

# 4. Fetch the pinned Limine loader (verified by sha256) for the same chain
#    (firmware -> Limine -> UKI) and Secure Boot sign both, gated on a key.
curl -fsSLo /tmp/limine-binary.zip \
    "https://github.com/Limine-Bootloader/Limine/releases/download/v${LIMINE_VERSION}/limine-binary.zip"
echo "${LIMINE_ZIP_SHA256}  /tmp/limine-binary.zip" | sha256sum -c -
unzip -j /tmp/limine-binary.zip limine-binary/BOOTX64.EFI -d /out
rm -f /tmp/limine-binary.zip

if [ -n "${SB_KEY:-}" ] && [ -n "${SB_CERT:-}" ]; then
    printf '%s' "$SB_KEY"  > /tmp/sb.key
    printf '%s' "$SB_CERT" > /tmp/sb.crt
    for img in /out/zerith.efi /out/BOOTX64.EFI; do
        sbsign --key /tmp/sb.key --cert /tmp/sb.crt --output "$img" "$img"
        sbverify --cert /tmp/sb.crt "$img"
    done
    rm -f /tmp/sb.key /tmp/sb.crt
    echo "UKI + Limine signed for Secure Boot"
else
    echo "::warning::SB_KEY/SB_CERT not set — UKI and Limine are UNSIGNED. Secure Boot will reject them."
fi
