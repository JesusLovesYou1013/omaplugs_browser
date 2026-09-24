import QtQuick
import Quickshell
import Quickshell.Io

// Background service with no UI and no bar icon. While this plugin is enabled,
// it runs this repo's install.sh from the plugin's own folder, which adds the
// Setup › Plugins › "Browse Plugins" menu row (in the user's own menu
// extension file) and links ~/.local/bin/omarchy-plugins to this folder.
// install.sh is idempotent, so running it on every shell start is safe.
//
// Deliberately NOT a bar-widget: Omarchy asks "which bar section?" when a
// bar-widget is added, and this app is meant to live in the Setup menu, not
// the bar.
Item {
  id: root

  readonly property string pluginId: "io.github.JesusLovesYou1013.omaplugs-browser"

  // Local path of this plugin's folder, wherever omarchy-plugin-add put it.
  readonly property string installPath:
    decodeURIComponent(Qt.resolvedUrl("install.sh").toString().replace(/^file:\/\//, ""))

  // Kept inline (not in install.sh) because `omarchy plugin remove` deletes
  // the plugin folder right after disabling it. The service is also destroyed
  // on a normal shell exit or hot-reload; the shell.json check makes those a
  // no-op. Only undoes what this plugin did: if ~/.local/bin/omarchy-plugins
  // points somewhere else (a manual install.sh from another checkout), both
  // the link and the menu row are left alone.
  readonly property string removeCommand:
    "sleep 1; c=\"${XDG_CONFIG_HOME:-$HOME/.config}\"; " +
    "grep -qF '\"" + pluginId + "\"' \"$c/omarchy/shell.json\" 2>/dev/null && exit 0; " +
    "b=\"$HOME/.local/bin/omarchy-plugins\"; " +
    "case \"$(readlink \"$b\")\" in */omarchy/plugins/" + pluginId + "/*) " +
    "rm -f \"$b\"; m=\"$HOME/.config/omarchy/extensions/omarchy-menu.jsonc\"; " +
    "[ -f \"$m\" ] && sed -i --follow-symlinks '/\"setup.plugin.browse\"/d' \"$m\"; " +
    "command -v omarchy-menu >/dev/null && omarchy-menu refresh;; esac; exit 0"

  Process {
    id: applyProcess
    command: ["bash", root.installPath]
    stderr: StdioCollector {
      onStreamFinished: if (text.trim() !== "") console.warn("omaplugs-browser: " + text.trim())
    }
  }

  Component.onCompleted: applyProcess.running = true
  Component.onDestruction: Quickshell.execDetached(["bash", "-c", root.removeCommand])
}
