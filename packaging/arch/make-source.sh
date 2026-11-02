#!/usr/bin/env bash
# Pack the source tree into the tarball the PKGBUILD builds from (no tools beyond tar).
set -euo pipefail
here="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
root="$(cd "$here/../.." && pwd)"
ver=$(sed -n 's/^version = "\(.*\)"/\1/p' "$root/pyproject.toml" | head -1)
pkgver=$(sed -n 's/^pkgver=//p' "$here/PKGBUILD")
if [[ "$ver" != "$pkgver" ]]; then
    echo "version mismatch: pyproject.toml says $ver, PKGBUILD says $pkgver" >&2
    exit 1
fi
out="$here/chimp-$ver.tar.gz"
tar -C "$root" -czf "$out" \
    --exclude='__pycache__' --exclude='*.pyc' \
    --transform "s,^,chimp-$ver/," \
    pyproject.toml README.md LICENSE chimp tests \
    packaging/chimp.desktop packaging/chimp.service
echo "$out"
