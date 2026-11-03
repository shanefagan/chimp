#!/bin/sh
# SPDX-License-Identifier: MIT
#
# Set a new version everywhere it is recorded (`just bump` runs this):
#
#   packaging/bump-version.sh 0.6.0 "Debian and Fedora packages"
#   git commit -am "Release 0.6.0"
#   git tag -a v0.6.0                # its message becomes the release notes
#   git push origin main v0.6.0      # CI builds the packages and the release
#
# Updates pyproject.toml, the PKGBUILD, the RPM spec (Version, Release,
# %changelog), the Debian changelog and the man page headers. A unit test
# fails if any of them disagree, and CI refuses a tag that doesn't match.
set -eu

[ $# -ge 1 ] || { echo "usage: $0 NEW_VERSION [CHANGE]" >&2; exit 2; }
new=$1
note=${2:-New release.}
case $new in
    [0-9]*.[0-9]*.[0-9]*) ;;
    *) echo "version must look like 1.2.3" >&2; exit 2 ;;
esac
case $new in *[!0-9.]*) echo "version must look like 1.2.3" >&2; exit 2 ;; esac

cd "$(dirname "$0")/.."
maint=${MAINTAINER:-"Shane Fagan <mail@shanefagan.com>"}
old=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n1)
[ "$old" != "$new" ] || { echo "already at $new" >&2; exit 1; }

sed -i "0,/^version = \"$old\"/s//version = \"$new\"/" pyproject.toml

sed -i -e "s/^pkgver=.*/pkgver=$new/" -e "s/^pkgrel=.*/pkgrel=1/" packaging/arch/PKGBUILD

spec=packaging/rpm/chimp.spec
sed -i -e "s/^\(Version:[[:space:]]*\).*/\1$new/" \
       -e "s/^\(Release:[[:space:]]*\).*/\11%{?dist}/" "$spec"
entry="* $(LC_ALL=C date +'%a %b %d %Y') $maint - $new-1\\
- $note"
sed -i "/^%changelog/a\\
$entry\\
" "$spec"

deb=packaging/debian/changelog
# Debian changelog lines stay under 80 columns (lintian): wrap the note.
deb_note=$(printf '%s\n' "$note" | fold -s -w 72 | sed -e 's/[[:space:]]*$//' -e '2,$s/^/    /')
{
    printf 'chimp (%s-1) unstable; urgency=medium\n\n  * %s\n\n -- %s  %s\n\n' \
        "$new" "$deb_note" "$maint" "$(LC_ALL=C date -R)"
    cat "$deb"
} > "$deb.tmp" && mv "$deb.tmp" "$deb"

for page in packaging/man/chimp.1 packaging/man/chimpd.1 packaging/man/chimp-loop.1; do
    sed -i "1s/\"[0-9-]*\" \"chimp [^\"]*\"/\"$(date +%Y-%m)\" \"chimp $new\"/" "$page"
done

echo "chimp $old -> $new. Next:"
echo "  git commit -am \"Release $new\" && git tag -a v$new   # message = release notes"
echo "  git push origin main v$new"
