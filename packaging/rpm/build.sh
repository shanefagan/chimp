#!/bin/sh
# SPDX-License-Identifier: MIT
#
# Make the two tarballs chimp.spec builds from, then run rpmbuild.
#
#   packaging/rpm/build.sh          # .src.rpm and .rpm, in target/rpm/
#   packaging/rpm/build.sh --srpm   # .src.rpm only, for COPR or mock
#
# Packages tracked files as the next commit would have them (changes to
# tracked files included, untracked files not). python-rtmidi's sdist
# comes from PyPI (pip download), so this needs the network once.
set -eu

mode=-ba
case "${1:-}" in
    --srpm) mode=-bs ;;
    "") ;;
    *) echo "usage: $0 [--srpm]" >&2; exit 2 ;;
esac

cd "$(dirname "$0")/../.."
name=chimp
ver=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n1)
spec=packaging/rpm/$name.spec
if ! grep -Eq "^Version:[[:space:]]+$ver\$" "$spec"; then
    echo "$spec: Version does not match pyproject.toml ($ver)" >&2
    exit 1
fi
rtmidi=$(sed -n 's/^%global rtmidi_version //p' "$spec")

top=$PWD/target/rpm
rm -rf "$top"
mkdir -p "$top/SOURCES"

tree=$(git stash create)
git archive --format=tar.gz --prefix="$name-$ver/" \
    -o "$top/SOURCES/$name-$ver.tar.gz" "${tree:-HEAD}"

cache=$PWD/target/cache
sdist=python_rtmidi-$rtmidi.tar.gz
if [ ! -f "$cache/$sdist" ]; then
    mkdir -p "$cache"
    python3 -m pip download --quiet --no-deps --no-binary :all: --no-build-isolation \
        -d "$cache" "python-rtmidi==$rtmidi"
fi
cp "$cache/$sdist" "$top/SOURCES/"

rpmbuild "$mode" \
    --define "_topdir $top" \
    --define "_sourcedir $top/SOURCES" \
    "$spec"

find "$top/SRPMS" "$top/RPMS" -name '*.rpm' 2>/dev/null
