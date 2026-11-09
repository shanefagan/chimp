# SPDX-License-Identifier: MIT
#
# Builds offline from two tarballs: Chimp's source and python-rtmidi's
# sdist from PyPI. Fedora doesn't package python-rtmidi, so it is built
# here and kept in a private directory that only Chimp puts on sys.path;
# a python3-rtmidi package, if one appears, takes precedence.
# packaging/rpm/build.sh makes both tarballs from a checkout and runs
# rpmbuild; `spectool -g` fetches the same two for a release.

%global rtmidi_version 1.5.8
%global privdir %{_libdir}/%{name}/python
# The one compiled file is rtmidi's extension, built by meson-python
# outside find-debuginfo's view: no -debuginfo package; it's stripped below.
%global debug_package %{nil}

# python-rtmidi is satisfied by the bundled copy, not a package.
%global __requires_exclude ^python3(\\.[0-9]+)?dist\\(python-rtmidi\\)

Name:           chimp
Version:        0.7.2
Release:        1%{?dist}
Summary:        MIDI controllers as a KDE Plasma control deck

# Chimp and python-rtmidi are MIT; RtMidi (inside python-rtmidi) is MIT
# with a non-binding request to send changes upstream.
License:        MIT
URL:            https://github.com/shanefagan/chimp
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz
Source1:        https://files.pythonhosted.org/packages/source/p/python-rtmidi/python_rtmidi-%{rtmidi_version}.tar.gz

BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  python3-hatchling
BuildRequires:  python3-pip
BuildRequires:  python3-pytest
BuildRequires:  python3-pyside6
BuildRequires:  python3-numpy
BuildRequires:  python3-pyyaml
BuildRequires:  desktop-file-utils
# python-rtmidi
BuildRequires:  gcc-c++
BuildRequires:  alsa-lib-devel
BuildRequires:  python3-cython
BuildRequires:  python3-meson-python
BuildRequires:  meson
BuildRequires:  ninja-build
BuildRequires:  binutils

Requires:       python3dist(pyside6)
Requires:       qt6-qtdeclarative
Requires:       qt6-qtwebsockets
Requires:       qt6-qtmultimedia
Requires:       kf6-kirigami
Requires:       kf6-qqc2-desktop-style
Requires:       layer-shell-qt
Requires:       wireplumber
Requires:       gtk3
Requires:       systemd
Requires:       hicolor-icon-theme
Recommends:     python3-pulsectl
Recommends:     python3-evdev
Recommends:     pipewire-pulseaudio
Suggests:       obs-studio
Provides:       bundled(python3dist(python-rtmidi)) = %{rtmidi_version}
Provides:       bundled(rtmidi)

%description
Chimp turns MIDI controllers into a control deck for the KDE Plasma
desktop: knobs for system, focused-app, playing-app and game volume,
brightness and microphone; pads for mute, media keys, shortcuts, apps,
commands and OBS scenes. It shows live values on the device's pad lights
and in an on-screen overlay.

It also replaces Akai's editor for the LPD8 mk2 (pads, knobs, programs
over SysEx), and learns any class-compliant controller (Teenage Engineering
KO II, Arturia MicroFreak and others) into profiles.

Chimp Loop plays a drum kit from any controller and loops it, snapped to a
grid or not, with overdub layers, and exports loops as WAV or MIDI files.

%prep
%autosetup -a1
cat >> chimp/__init__.py <<'EOF'

# Fedora package: python-rtmidi is bundled here (appended, so a system
# python3-rtmidi wins if installed).
import sys as _sys

_sys.path.append("%{privdir}")
EOF

%generate_buildrequires
# -R: the runtime python-rtmidi is the bundled one.
%pyproject_buildrequires -R

%build
%pyproject_wheel
# python-rtmidi: ALSA only, with the distribution's compiler flags.
%set_build_flags
(cd python_rtmidi-%{rtmidi_version} && \
    %{python3} -m pip wheel --no-deps --no-build-isolation --no-index \
        -Csetup-args=-Djack=false -Csetup-args=-Dalsa=true \
        -Cbuild-dir=build -w ../rtmidi-wheel .)

%install
%pyproject_install
%pyproject_save_files chimp
mkdir -p %{buildroot}%{privdir}
%{python3} -m zipfile -e rtmidi-wheel/python_rtmidi-*.whl %{buildroot}%{privdir}
rm -rf %{buildroot}%{privdir}/python_rtmidi-*.dist-info
strip --strip-unneeded %{buildroot}%{privdir}/rtmidi/*.so
%py_byte_compile %{python3} %{buildroot}%{privdir}
install -Dpm0644 packaging/chimp.desktop %{buildroot}%{_datadir}/applications/chimp.desktop
install -Dpm0644 packaging/chimp-loop.desktop %{buildroot}%{_datadir}/applications/chimp-loop.desktop
install -Dpm0644 packaging/chimp.service %{buildroot}%{_userunitdir}/chimp.service
install -Dpm0644 chimp/icons/chimp.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/chimp.svg
install -Dpm0644 packaging/man/chimp.1 %{buildroot}%{_mandir}/man1/chimp.1
install -Dpm0644 packaging/man/chimpd.1 %{buildroot}%{_mandir}/man1/chimpd.1
install -Dpm0644 packaging/man/chimp-loop.1 %{buildroot}%{_mandir}/man1/chimp-loop.1

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/chimp.desktop
desktop-file-validate %{buildroot}%{_datadir}/applications/chimp-loop.desktop
export QT_QPA_PLATFORM=offscreen
export PYTHONPATH=%{buildroot}%{python3_sitelib}:%{buildroot}%{privdir}
%pytest -q -p no:cacheprovider tests

%files -f %{pyproject_files}
%license LICENSE python_rtmidi-%{rtmidi_version}/LICENSE.md
%doc README.md
%{_bindir}/chimp
%{_bindir}/chimpd
%{_bindir}/chimp-loop
%{_libdir}/%{name}/
%{_datadir}/applications/chimp.desktop
%{_datadir}/applications/chimp-loop.desktop
%{_datadir}/icons/hicolor/scalable/apps/chimp.svg
%{_userunitdir}/chimp.service
%{_mandir}/man1/chimp.1*
%{_mandir}/man1/chimpd.1*
%{_mandir}/man1/chimp-loop.1*

%changelog
* Mon Nov 09 2026 Shane Fagan <mail@shanefagan.com> - 0.7.2-1
- Game volume works for Proton games that play straight to PipeWire; desktop.json is owner-only (0.7.1 didn't build: lint)

* Mon Nov 09 2026 Shane Fagan <mail@shanefagan.com> - 0.7.1-1
- Game volume works for Proton games that play straight to PipeWire; desktop.json is owner-only

* Tue Nov 03 2026 Shane Fagan <mail@shanefagan.com> - 0.7.0-1
- Chimp Loop, a drum machine and looper for any MIDI controller; overlay text readable on any pad colour

* Mon Nov 02 2026 Shane Fagan <mail@shanefagan.com> - 0.6.1-1
- CI fixes; Chimp no longer fails to start when pulsectl is installed without libpulse

* Mon Nov 02 2026 Shane Fagan <mail@shanefagan.com> - 0.6.0-1
- Debian and Fedora packages; device hotplug, 14-bit learn and game-volume fixes

* Mon Nov 02 2026 Shane Fagan <mail@shanefagan.com> - 0.5.1-1
- Initial package
