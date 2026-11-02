# SPDX-License-Identifier: MIT
#
# Building, testing and packaging Chimp. CI runs these same recipes, so
# what passes here is what passes there.
#
#   just          list the recipes
#   just dev      venv for the rest (uv, on top of the system PySide6/Kirigami)
#   just check    lint, format, bandit and the unit tests
#
# Package recipes (deb, rpm, arch) build in a container or on that distro;
# CI runs them in Debian, Fedora and Arch containers.

python := env_var_or_default("PYTHON", ".venv/bin/python")
version := `sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n1`
# Root in a container, sudo on a desktop or a CI runner.
sudo := if `id -u` == "0" { "" } else { "sudo" }

# List the recipes
default:
    @just --list --unsorted

# ---- develop -------------------------------------------------------------

# Dev venv on top of the system PySide6/Kirigami, editable install + dev tools
dev:
    test -x {{ python }} || uv venv --system-site-packages --python /usr/bin/python3 .venv
    uv pip install --python {{ python }} -e . --group dev

# Run the editor from the source tree
run *args:
    {{ python }} -m chimp {{ args }}

# Run the desktop service from the source tree
service *args:
    {{ python }} -u -m chimp.desktop {{ args }}

# Apply ruff fixes and formatting
fmt:
    {{ python }} -m ruff check --fix .
    {{ python }} -m ruff format .

# ---- checks: CI's test job runs these ------------------------------------

# Lint and format check (ruff)
lint:
    {{ python }} -m ruff check .
    {{ python }} -m ruff format --check .

# Security scan (bandit)
security:
    {{ python }} -m bandit -q -c pyproject.toml -r chimp

# Offline unit tests
test:
    QT_QPA_PLATFORM=offscreen {{ python }} -m pytest -q -p no:cacheprovider

# Every module imports and every QML file compiles (offscreen)
check-install:
    {{ python }} -m chimp --check-install

# Everything that must pass before a change goes in (CI's test job)
check: lint security test check-install

# ---- on real hardware and a Plasma session -------------------------------

# Hardware/desktop checks: need the LPD8 plugged in and a Plasma session
live:
    PYTHONPATH=. {{ python }} tests/live_check.py
    PYTHONPATH=. {{ python }} tests/live_desktop_actions.py

# Can a muted mic still be level-metered? (interactive: talk when asked)
mic-probe:
    {{ python }} tests/mic_mute_probe.py

# Knob-sweep benchmark (processes spawned, CPU)
bench:
    PYTHONPATH=. {{ python }} tests/bench_knob.py

# ---- packages: CI's deb, rpm and arch jobs run these ---------------------

# Debian/Ubuntu package in target/debian/ (needs the Build-Depends in packaging/debian/control)
deb:
    packaging/debian/build.sh

# lintian on the .deb
deb-lint:
    lintian --fail-on warning --suppress-tags initial-upload-closes-no-bugs target/debian/*.deb

# Install the .deb, check it loads, remove and purge it (root; for a container or VM)
deb-smoke:
    {{ sudo }} apt-get install -y ./target/debian/chimp_*.deb
    chimp --version
    chimpd --version
    chimp --check-install
    {{ sudo }} apt-get remove -y chimp
    {{ sudo }} apt-get purge -y chimp
    test ! -e /usr/bin/chimp

# Fedora .src.rpm and .rpm in target/rpm/ (needs the spec's BuildRequires)
rpm:
    packaging/rpm/build.sh

# Only the .src.rpm, for COPR or mock
srpm:
    packaging/rpm/build.sh --srpm

# rpmlint on the packages
rpm-lint:
    rpmlint target/rpm/RPMS/*/*.rpm target/rpm/SRPMS/*.rpm

# Install the .rpm, check it loads, remove it (root; for a container or VM)
rpm-smoke:
    {{ sudo }} dnf -y install target/rpm/RPMS/*/chimp-[0-9]*.rpm
    chimp --version
    chimpd --version
    chimp --check-install
    {{ sudo }} dnf -y remove chimp
    test ! -e /usr/bin/chimp

# Arch/CachyOS package in packaging/arch/ (makepkg; not as root)
arch:
    cd packaging/arch && rm -f ./*.pkg.tar.* ./chimp-*.tar.gz && ./make-source.sh && makepkg -sf --cleanbuild --noconfirm

# Install the Arch package, check it loads, remove it (root; for a container or VM)
arch-smoke:
    {{ sudo }} pacman -U --noconfirm packaging/arch/chimp-{{ version }}-*.pkg.tar.zst
    chimp --version
    chimpd --version
    chimp --check-install
    {{ sudo }} pacman -R --noconfirm chimp
    test ! -e /usr/bin/chimp

# Build the Arch package and install it here (asks for sudo), then set up the service
install:
    ./install.sh

# ---- releases --------------------------------------------------------------

# Set a new version everywhere it's recorded, e.g. just bump 1.2.3 "What changed"
bump new *note:
    packaging/bump-version.sh {{ new }} {{ quote(note) }}

# Fail unless TAG is v<the version in pyproject.toml> (CI's release job)
check-tag tag:
    @test "{{ tag }}" = "v{{ version }}" || { echo "tag {{ tag }}, but pyproject.toml says {{ version }}: run 'just bump'"; exit 1; }

# Remove build output
clean:
    rm -rf target dist build packaging/arch/pkg packaging/arch/src packaging/arch/*.pkg.tar.* packaging/arch/chimp-*.tar.gz
