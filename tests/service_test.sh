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

echo; (( fails == 0 )) && echo "all passed" || echo "$fails failed"
exit $(( fails > 0 ))
