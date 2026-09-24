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
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin/omarchy-plugins"
MENU="$HOME/.config/omarchy/extensions/omarchy-menu.jsonc"
KEY="setup.plugin.browse"
ENTRY='  "setup.plugin.browse": {"icon":"󰍉","label":"Browse Plugins","aliases":["search-plugins","plugin-search"],"description":"Browse, add and manage every available plugin","action":"uwsm-app -- omarchy-plugins"},'

refresh_menu() {
  command -v omarchy-menu >/dev/null && omarchy-menu refresh || true
}

if [[ ${1:-} == "--uninstall" ]]; then
  [[ -L $BIN ]] && rm -f "$BIN" && echo "Removed $BIN"
  if [[ -f $MENU ]] && grep -q "\"$KEY\"" "$MENU"; then
    tmp="$(mktemp)"
    grep -v "\"$KEY\"" "$MENU" >"$tmp"
    mv "$tmp" "$MENU"
    echo "Removed menu entry from $MENU"
    refresh_menu
  fi
  exit 0
fi

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
