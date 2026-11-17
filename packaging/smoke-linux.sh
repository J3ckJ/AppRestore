#!/usr/bin/env bash
# Run as root in a clean distro container. /assets contains the *same* built ZIPs.
set -euo pipefail
if command -v dnf >/dev/null; then
  dnf install -y unzip libstdc++ libgcc mesa-libGL mesa-libEGL libglvnd-opengl fontconfig freetype libxkbcommon-x11 dbus-libs glib2 \
    xcb-util-cursor xcb-util-wm xcb-util-image xcb-util-keysyms xcb-util-renderutil \
    xorg-x11-server-Xvfb xorg-x11-xauth
else
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    unzip libstdc++6 libgcc-s1 libgl1 libegl1 libopengl0 libfontconfig1 libfreetype6 libxkbcommon-x11-0 libdbus-1-3 libglib2.0-0 \
    libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
    libxcb-xinerama0 xvfb xauth
fi
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
export HOME="$work/home" XDG_RUNTIME_DIR="$work/runtime"
mkdir -p "$HOME" "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
cd "$work"
unzip -q /assets/AppRestore-CLI-Linux-x86_64.zip
unzip -q /assets/AppRestore-GUI-Linux-x86_64.zip
./AppRestore-CLI/apprestore --version
./AppRestore-CLI/apprestore --json doctor
./AppRestore-CLI/apprestore --apprestore-run-module pymobiledevice3 version
./AppRestore-CLI/ipatool --version
QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software ./AppRestore/AppRestore --self-test
# Also load QML through XCB: offscreen alone would miss missing X11 dependencies.
QT_QPA_PLATFORM=xcb QT_QUICK_BACKEND=software xvfb-run -a ./AppRestore/AppRestore --self-test

# Use the default renderer too: older bundled libstdc++ breaks Fedora Mesa.
QT_QPA_PLATFORM=xcb xvfb-run -a ./AppRestore/AppRestore --self-test
