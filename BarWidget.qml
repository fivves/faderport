// FaderPort FP2 - bar widget entry. Click opens the assign panel.
import QtQuick
import Quickshell.Io
import qs.Ui
import qs.Commons

BarWidget {
  id: root
  moduleName: "io.github.fivves.faderport"

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "🎚️"
    tooltipText: "FaderPort"
    onPressed: function(b) {
      if (b === Qt.LeftButton) root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: panelContent
    contentWidth: panel.fittedContentWidth(Style.space(380))
    // PanelContent is an Item (implicitHeight = 0), so size the card from
    // its content explicitly: header + 10 non-slot button rows + 8 compact
    // app-slot rows + encoder + footer, capped so it scrolls if needed.
    contentHeight: panel.fittedContentHeight(Style.space(500), Style.space(620))

    PanelContent {
      id: panelContent
      anchors.fill: parent
    }
  }

  readonly property bool opened: panel.open

  function open() { panel.open = true }
  function close() { panel.open = false }
  function toggle() { root.opened ? close() : open() }
}
