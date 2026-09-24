import QtQuick
import qs.Ui

// Bar-icon entry point. Mirrors omarchy.menu's own BarWidget.qml pattern:
// a single WidgetButton that runs a shell command through the plugin-facing
// bar API (PluginBarApi.run), which is the sanctioned way for a third-party
// plugin to launch an external process without starting a second Quickshell
// instance.
//
// The launch command points at this plugin's own install directory
// ($HOME/.config/omarchy/plugins/<id>/omarchy-plugins) rather than a bare
// "omarchy-plugins" on PATH. omarchy-plugin-add always clones a third-party
// plugin's repo there, so that path is guaranteed to exist for anyone who
// installs this from the marketplace -- unlike a PATH symlink, which only
// exists on machines that separately ran this repo's install.sh.
BarWidget {
  id: root
  moduleName: "io.github.JesusLovesYou1013.omaplugs-browser"

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰍉"
    fontFamily: root.bar ? root.bar.fontFamily : ""
    horizontalMargin: 7.5
    onPressed: function(button) {
      if (!root.bar) return
      root.bar.run("uwsm-app -- \"$HOME/.config/omarchy/plugins/io.github.JesusLovesYou1013.omaplugs-browser/omarchy-plugins\"")
    }
  }
}
