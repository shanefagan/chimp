#!/usr/bin/env bash
# Chimp: build and install the Arch/CachyOS package.
#
#   ./install.sh                 build + install the package (sudo), ask about the service
#   ./install.sh --service       ... and turn desktop control on without asking
#   ./install.sh --no-service    ... and leave desktop control as it is
#   ./install.sh --uninstall     remove the package and turn the service off (settings kept)
#
# Re-run after every update: it rebuilds, reinstalls and restarts the service.
# Working on the code instead? `just dev`, then `just run` / `just service`.
set -euo pipefail

DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PKG=chimp
UNIT=chimp.service

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; exit 1; }
ask()  { local reply; [[ -t 0 ]] || return 1; read -r -p "  $1 [Y/n] " reply; [[ ${reply:-y} =~ ^[Yy] ]]; }

mode=ask
case "${1:-}" in
    --service) mode=yes ;;
    --no-service) mode=no ;;
    --uninstall) mode=uninstall ;;
    ""|--ask) ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) fail "unknown option $1 (try --help)" ;;
esac

command -v pacman >/dev/null || fail "this installer is for Arch Linux / CachyOS (pacman)"

if [[ $mode == uninstall ]]; then
    bold "Uninstalling"
    systemctl --user disable --now "$UNIT" 2>/dev/null && ok "service stopped and disabled" || true
    if pacman -Qq "$PKG" >/dev/null 2>&1; then
        sudo pacman -R --noconfirm "$PKG" && ok "package removed"
    fi
    echo "  Settings kept in ~/.config/chimp, presets in ~/.local/share/chimp."
    exit 0
fi

# Before the rename: package "lpd8mk2-editor", service "lpd8mk2-desktop.service"
OLD_PKG=lpd8mk2-editor
OLD_UNIT=lpd8mk2-desktop.service
old_unit_on=0
if systemctl --user is-enabled -q "$OLD_UNIT" 2>/dev/null || systemctl --user is-active -q "$OLD_UNIT" 2>/dev/null; then
    old_unit_on=1
fi

bold "Building the package"
command -v makepkg >/dev/null || fail "makepkg not found (pacman -S base-devel)"
cd "$DIR/packaging/arch"
rm -f ./*.pkg.tar.* ./chimp-*.tar.gz
./make-source.sh >/dev/null
# -s installs the build/check dependencies from the repos (asks for sudo)
makepkg -sf --cleanbuild --noconfirm --needed 2>&1 | sed 's/^/    /'
pkgfile=$(ls -t ./"$PKG"-*.pkg.tar.* 2>/dev/null | grep -v -- '-debug-' | head -1)
[[ -n $pkgfile ]] || fail "makepkg didn't produce a package"
ok "built $(basename "$pkgfile")"

bold "Installing"
if pacman -Qq "$OLD_PKG" >/dev/null 2>&1; then
    sudo pacman -R --noconfirm "$OLD_PKG" | sed 's/^/    /' && ok "removed the old $OLD_PKG package"
fi
sudo pacman -U --noconfirm "$pkgfile" | sed 's/^/    /'
ok "installed $(pacman -Q "$PKG")"

if ! pacman -Qq python-pulsectl >/dev/null 2>&1; then
    if pacman -Si python-pulsectl >/dev/null 2>&1; then
        if ask "Install python-pulsectl for smoother knobs and lower CPU use (recommended)?"; then
            sudo pacman -S --needed --asdeps --noconfirm python-pulsectl | sed 's/^/    /' && ok "python-pulsectl installed"
        fi
    else
        warn "python-pulsectl isn't in your repos (it's in CachyOS and the AUR); volume falls back to wpctl"
    fi
fi

bold "Cleaning up older setups"
cleaned=0
if [[ $old_unit_on == 1 ]]; then
    systemctl --user disable --now "$OLD_UNIT" 2>/dev/null || true
    ok "stopped the old $OLD_UNIT (replaced by $UNIT)"
    [[ $mode == ask ]] && mode=yes
fi
for f in "$HOME/.local/share/applications/lpd8mk2-editor.desktop" \
         "$HOME/.local/share/applications/lpd8mk2-desktop-control.desktop" \
         "$HOME/.local/share/icons/hicolor/scalable/apps/lpd8mk2-editor.svg" \
         "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$OLD_UNIT" \
         "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$UNIT"; do
    if [[ -e $f ]]; then rm -f "$f"; cleaned=1; fi
done
if [[ $cleaned == 1 ]]; then
    systemctl --user daemon-reload 2>/dev/null || true
    [[ -f "$HOME/.local/share/icons/hicolor/icon-theme.cache" ]] && \
        gtk-update-icon-cache -q -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
    ok "removed old launcher entries and user units; the package's are used now"
else
    ok "nothing to clean up"
fi
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true

bold "Desktop control service"
enabled=$(systemctl --user is-enabled "$UNIT" 2>/dev/null || true)
if [[ $mode == ask ]]; then
    if [[ $enabled == enabled ]]; then mode=yes
    elif ask "Let your MIDI controllers control the desktop (now and at every login)?"; then mode=yes
    else mode=no; fi
fi
if [[ $mode == yes ]]; then
    systemctl --user daemon-reload
    systemctl --user enable -q "$UNIT"
    systemctl --user restart "$UNIT"
    sleep 2
    if systemctl --user is-active -q "$UNIT"; then
        ok "running, and starts with your Plasma session"
        journalctl --user -u "$UNIT" -n 6 --no-pager -o cat 2>/dev/null | sed 's/^/    /'
    else
        warn "didn't start; see: journalctl --user -u $UNIT"
    fi
else
    echo "  Left as is. Turn it on with the editor's \"Desktop control\" button or ./install.sh --service"
fi

if pgrep -f -- "(-m (chimp|lpd8mk2_editor)|/chimp)( |\$)" >/dev/null; then
    warn "the editor is open: close and reopen it to load this version"
fi

bold "Done"
echo "  Open \"Chimp\" from the launcher. Pick a device at the top: the LPD8 gets its own"
echo "  editor; any other controller: click Learn controls, move everything, then pick actions."
