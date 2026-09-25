#!/bin/bash
# Installs "Browse Plugins" into Omarchy, entirely per-user -- no sudo, no
# system files touched, ever:
#   1. links ~/.local/bin/omarchy-plugins -> this checkout
#   2. adds Setup > Plugins > Browse Plugins to your own menu extension file
#        ~/.config/omarchy/extensions/omarchy-menu.jsonc
#
#      Omarchy's menu merges its own default file first, then this one, so a
#      row added here always lands after the built-in rows in its submenu --
#      here, that means below "Remove Plugin", not above "Enable Plugin".
#      That's the deliberate tradeoff for staying entirely user-scoped: no
#      admin rights needed, and nothing here can be wiped by an Omarchy
#      package update (unlike editing the root-owned default menu file
#      directly, which an earlier version of this script did; that needed
#      sudo and could be undone by an Omarchy update, so it was abandoned).
#
#   ./install.sh              install
#   ./install.sh --uninstall  remove both again
#
#   ~/.local/bin/omarchy-plugins is only ever replaced when it is missing, is a
#   link to this copy, or is a broken link left by a removed OmaPlugs plugin.
#   If it belongs to the user, or to another installed OmaPlugs copy (e.g. the
#   marketplace plugin plus a manual install), setup stops with an error and a
#   desktop notification, and nothing is linked, added or changed.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin/omarchy-plugins"
MENU="$HOME/.config/omarchy/extensions/omarchy-menu.jsonc"
KEY="setup.plugin.browse"
ENTRY='  "setup.plugin.browse": {"icon":"󰍉","label":"Browse Plugins","aliases":["search-plugins","plugin-search"],"description":"Browse, add and manage every available plugin","action":"uwsm-app -- omarchy-plugins"},'

refresh_menu() {
  command -v omarchy-menu >/dev/null && omarchy-menu refresh || true
}

# What is at $BIN now: none | this | omaplugs (another copy) | stale | foreign
bin_state() {
  [[ -e $BIN || -L $BIN ]] || { echo none; return; }
  [[ -L $BIN ]] || { echo foreign; return; }
  local target real
  target="$(readlink "$BIN")"
  [[ $target == "$HERE/omarchy-plugins" ]] && { echo this; return; }
  if [[ -e $BIN ]]; then
    real="$(readlink -f "$BIN")"
    if [[ ${real##*/} == omarchy-plugins ]] && grep -qF "\"$KEY\"" "${real%/*}/install.sh" 2>/dev/null; then
      echo omaplugs
    else
      echo foreign
    fi
  elif [[ $target == */omarchy/plugins/*/omarchy-plugins ]]; then
    echo stale  # broken link left behind by a removed OmaPlugs plugin
  else
    echo foreign
  fi
}

if [[ ${1:-} == "--uninstall" ]]; then
  if [[ $(bin_state) == this ]]; then
    rm -f "$BIN" && echo "Removed $BIN"
  fi
  # Keep the row while $BIN still launches another OmaPlugs copy (e.g. the installed plugin).
  if [[ $(bin_state) != omaplugs ]] && [[ -f $MENU ]] && grep -q "\"$KEY\"" "$MENU"; then
    tmp="$(mktemp)"
    grep -v "\"$KEY\"" "$MENU" >"$tmp"
    mv "$tmp" "$MENU"
    echo "Removed menu entry from $MENU"
    refresh_menu
  fi
  exit 0
fi

# Stop with an error and a desktop notification; nothing is changed.
fail() {
  echo "$1: $2" >&2
  command -v notify-send >/dev/null && notify-send -a "OmaPlugs Browser" "$1" "$2" || true
  exit 1
}

case "$(bin_state)" in
  none | this | stale) ;;
  omaplugs)
    other="$(readlink -f "$BIN")"; other="${other%/*}"
    if [[ $HERE == */omarchy/plugins/* ]]; then
      fix="Remove it, then disable and re-enable this plugin."
    else
      fix="Disable or remove it, then run install.sh again."
    fi
    fail "OmaPlugs Browser failed to install" \
      "Another version of OmaPlugs Browser is already installed (at ${other/#$HOME/\~}). $fix"
    ;;
  *)
    fail "OmaPlugs Browser couldn't finish setup" \
      "~/.local/bin/omarchy-plugins already exists and isn't OmaPlugs Browser, so it was left alone. Rename or remove it, then disable and re-enable the plugin."
    ;;
esac
mkdir -p "$(dirname "$BIN")"
ln -sfn "$HERE/omarchy-plugins" "$BIN"
echo "Linked $BIN -> $HERE/omarchy-plugins"

mkdir -p "$(dirname "$MENU")"
[[ -f $MENU ]] || printf '{\n}\n' >"$MENU"

if grep -q "\"$KEY\"" "$MENU"; then
  echo "Menu entry already present."
  exit 0
fi

tmp="$(mktemp)"
ENTRY="$ENTRY" awk '
  /^}[[:space:]]*$/ && !done { print ENVIRON["ENTRY"]; done=1 }
  { print }
' "$MENU" >"$tmp"
mv "$tmp" "$MENU"
echo "Added Setup > Plugins > Browse Plugins to $MENU (after the built-in rows)"
refresh_menu
