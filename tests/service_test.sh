#!/bin/bash
# Headless tests for the plugin form: install.sh run from the plugin folder (what
# Service.qml does on enable) and Service.qml's inline remove command. Runs
# entirely inside a throwaway fake home: HOME and XDG_CONFIG_HOME both point at
# a temp dir, and the script refuses to start if either would reach a real one.

set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
id="io.github.jesuslovesyou1013.omaplugs-browser"
fails=0

fake=$(mktemp -d) || exit 1
trap 'rm -rf "$fake"' EXIT
export HOME="$fake" XDG_CONFIG_HOME="$fake/.config"
[[ $HOME == /tmp/* && $XDG_CONFIG_HOME == /tmp/* ]] || { echo "refusing: not a temp home" >&2; exit 1; }
cfg="$XDG_CONFIG_HOME"

# Stub omarchy-menu so a refresh never reaches the real shell.
mkdir -p "$fake/stub"; printf '#!/bin/sh\nexit 0\n' >"$fake/stub/omarchy-menu"; chmod +x "$fake/stub/omarchy-menu"
# Stub notify-send too: record the notification instead of showing it.
printf '#!/bin/sh\nprintf "%%s|" "$@" >>"%s/notified"\n' "$fake" >"$fake/stub/notify-send"; chmod +x "$fake/stub/notify-send"
export PATH="$fake/stub:$PATH"

ok()   { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; fails=$((fails + 1)); }
check() { if eval "$2"; then ok "$1"; else fail "$1"; fi; }

# Pull the remove command out of Service.qml exactly as the shell would build it.
remove_cmd=$(python3 - "$here/Service.qml" "$id" <<'PY'
import re, sys, json
src = open(sys.argv[1]).read()
body = re.search(r'removeCommand:\n(.*?)\n\n', src, re.S).group(1)
body = body.replace('pluginId', json.dumps(sys.argv[2]))
print(eval(body.replace('\n', ' ').strip()))  # QML string concatenation == Python here
PY
)
[[ -n $remove_cmd ]] || { echo "could not extract removeCommand" >&2; exit 1; }

# A copy of the repo where omarchy-plugin-add would clone it.
plug="$cfg/omarchy/plugins/$id"
mkdir -p "$plug"; cp -r "$here/install.sh" "$here/omarchy-plugins" "$plug/"
link="$HOME/.local/bin/omarchy-plugins"
menu="$HOME/.config/omarchy/extensions/omarchy-menu.jsonc"
mkdir -p "$(dirname "$menu")"
printf '{\n  "my.own.row": {"label":"Mine","action":"true"},\n}\n' >"$menu"; cp "$menu" "$fake/menu.orig"

apply()    { bash "$plug/install.sh" >/dev/null; }
remove()   { bash -c "$remove_cmd"; }
enabled()  { echo "{\"plugins\":[\"$id\"]}" >"$cfg/omarchy/shell.json"; }
disabled() { echo '{"plugins":[]}' >"$cfg/omarchy/shell.json"; }
rows()     { grep -c '"setup.plugin.browse"' "$menu"; }

enabled; apply
check "enable adds the menu row" "[[ \$(rows) == 1 ]]"
check "enable links the launcher into the plugin folder" "[[ \$(readlink '$link') == '$plug/omarchy-plugins' ]]"
apply
check "running again (shell restart) adds no duplicate row" "[[ \$(rows) == 1 ]]"

remove
check "shell exit while still enabled keeps the row" "[[ \$(rows) == 1 && -L '$link' ]]"

disabled; remove
check "disable removes the menu row, file otherwise identical" "cmp -s '$menu' '$fake/menu.orig'"
check "disable removes the launcher link" "[[ ! -e '$link' && ! -L '$link' ]]"

# A manual install.sh from some other checkout must be left alone.
mkdir -p "$fake/checkout"; cp "$here/install.sh" "$here/omarchy-plugins" "$fake/checkout/"
bash "$fake/checkout/install.sh" >/dev/null
disabled; remove
check "someone else's install (link elsewhere) is left alone" "[[ \$(rows) == 1 && \$(readlink '$link') == '$fake/checkout/omarchy-plugins' ]]"

# ~/.local/bin/omarchy-plugins that isn't ours must never be replaced (marketplace review, #8578).
reset() { disabled; remove; rm -f "$link"; cp "$fake/menu.orig" "$menu"; }
checkout="$fake/checkout"
manual() { bash "$checkout/install.sh" "$@" >/dev/null 2>&1; }

reset; printf '#!/bin/sh\necho mine\n' >"$link"; chmod +x "$link"; cp "$link" "$fake/mine"
enabled; rm -f "$fake/notified"; warn=$(bash "$plug/install.sh" 2>&1 >/dev/null); rc=$?
check "a user's own file is kept on enable" "[[ ! -L '$link' ]] && cmp -s '$link' '$fake/mine'"
check "  ...setup fails (exit status 1)" "[[ $rc == 1 ]]"
check "  ...and no menu row is added" "[[ \$(rows) == 0 ]] && cmp -s '$menu' '$fake/menu.orig'"
check "  ...and the error says why" "[[ \$warn == *\"isn't OmaPlugs Browser\"* ]]"
check "  ...and a desktop notification tells the user" "grep -q \"couldn't finish setup\" '$fake/notified'"
manual; rc=$?
check "a manual install fails too and keeps the user's file" "[[ $rc == 1 ]] && cmp -s '$link' '$fake/mine' && [[ \$(rows) == 0 ]]"
manual --uninstall
check "a user's own file is kept by --uninstall" "cmp -s '$link' '$fake/mine'"

reset; ln -s /usr/bin/true "$link"; enabled; apply 2>/dev/null
check "a user's own link to another program is kept" "[[ \$(readlink '$link') == /usr/bin/true && \$(rows) == 0 ]]"

reset; ln -s "$fake/gone/my-tool" "$link"; enabled; apply 2>/dev/null
check "a user's own broken link is kept" "[[ \$(readlink '$link') == '$fake/gone/my-tool' ]]"

reset; ln -s "$cfg/omarchy/plugins/old.omaplugs/omarchy-plugins" "$link"; enabled; apply
check "a broken link left by a removed OmaPlugs plugin is replaced" "[[ \$(readlink '$link') == '$plug/omarchy-plugins' && \$(rows) == 1 ]]"

# Two OmaPlugs copies must not both install: the second one fails and says where the first is.
reset; manual; cp "$menu" "$fake/menu.manual"; enabled; rm -f "$fake/notified"
err=$(bash "$plug/install.sh" 2>&1 >/dev/null); rc=$?
check "enabling the plugin over a manual install fails (exit status 1)" "[[ $rc == 1 ]]"
check "  ...keeps the manual install's link and menu untouched" "[[ \$(readlink '$link') == '$checkout/omarchy-plugins' ]] && cmp -s '$menu' '$fake/menu.manual'"
check "  ...notifies that another version is installed, and where" "grep -q 'failed to install|Another version of OmaPlugs Browser is already installed (at ~/checkout)' '$fake/notified'"
check "  ...and says to remove it, then re-enable" "[[ \$err == *'Remove it, then disable and re-enable this plugin.'* ]]"

reset; enabled; apply; cp "$menu" "$fake/menu.plugin"; rm -f "$fake/notified"
err=$(bash "$checkout/install.sh" 2>&1 >/dev/null); rc=$?
check "a manual install over the enabled plugin fails (exit status 1)" "[[ $rc == 1 ]]"
check "  ...keeps the plugin's link and menu untouched" "[[ \$(readlink '$link') == '$plug/omarchy-plugins' ]] && cmp -s '$menu' '$fake/menu.plugin'"
check "  ...notifies where the other version is" "grep -q 'Another version of OmaPlugs Browser is already installed (at ~/.config/omarchy/plugins/$id)' '$fake/notified'"
check "  ...and says to disable it, then run install.sh again" "[[ \$err == *'Disable or remove it, then run install.sh again.'* ]]"
manual --uninstall
check "manual --uninstall then leaves the installed plugin's link and row" "[[ \$(readlink '$link') == '$plug/omarchy-plugins' && \$(rows) == 1 ]]"

reset; manual; manual --uninstall
check "manual install then --uninstall removes its own link and the row" "[[ ! -e '$link' && ! -L '$link' && \$(rows) == 0 ]]"

echo; (( fails == 0 )) && echo "all passed" || echo "$fails failed"
exit $(( fails > 0 ))
