#!/bin/sh
# SPDX-License-Identifier: MIT
#
# Build the .deb in target/debian/ with dpkg-buildpackage.
#
#   packaging/debian/build.sh
#
# Packages tracked files as the next commit would have them (changes to
# tracked files included, untracked files not). Needs control's
# Build-Depends installed.
set -eu

cd "$(dirname "$0")/../.."
name=chimp
ver=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n1)
deb=$(sed -n '1s/^chimp (\([^-]*\)-.*/\1/p' packaging/debian/changelog)
if [ "$deb" != "$ver" ]; then
    echo "packaging/debian/changelog is at $deb, pyproject.toml at $ver: run 'just bump'" >&2
    exit 1
fi

top=$PWD/target/debian
rm -rf "$top"
mkdir -p "$top"
tree=$(git stash create)
git archive --format=tar --prefix="$name-$ver/" "${tree:-HEAD}" | tar -x -C "$top"

src=$top/$name-$ver
mv "$src/packaging/debian" "$src/debian"
rm "$src/debian/build.sh"
(cd "$src" && dpkg-buildpackage -b -us -uc)
rm -rf "$src"

find "$top" -name '*.deb'
