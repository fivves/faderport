// FaderPort assign panel — content of the popup.
import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons
import "ConfigStore.js" as ConfigStore

Item {
  id: root

  // ---- data -------------------------------------------------------------
  property var cfg: ({ actions: {}, slots: {}, encoder: {} })
  property bool dirty: false
  // Directory this plugin lives in (for detect_apps.py)
  readonly property string pluginDir: {
    var url = Qt.resolvedUrl(".")
    var s = String(url)
    if (s.indexOf("file://") === 0) s = s.slice(7)
    return decodeURIComponent(s)
  }
  // Apps currently making sound: [{ label, value }] from detect_apps.py
  property var liveApps: []
  property bool scanning: false
  property string scanError: ""

  // Physical buttons (stable id -> label) — same order as the FP2 face.
  // The slot buttons (Link/Pan/Channel/Scroll/Master/Click/Section/Marker)
  // are NOT here: their button->slot assignment is fixed in mappings.json
  // and never changes. Only their app lists are editable (App slots section).
  readonly property var buttons: [
    { id: "play",     label: "Play" },
    { id: "stop",     label: "Stop" },
    { id: "rec",      label: "Rec" },
    { id: "ffwd",     label: "FFwd" },
    { id: "rewind",   label: "Rewind" },
    { id: "loop",     label: "Loop" },
    { id: "mute",     label: "Mute" },
    { id: "solo",     label: "Solo" },
    { id: "recarm",   label: "RecArm" },
    { id: "output",   label: "Output" }
  ]

  // Slots can carry app names (comma-separated process substrings)
  readonly property var slotNames: [ "link", "pan", "channel", "scroll", "master", "click", "section", "marker" ]

  // ---- actions list -----------------------------------------------------
  readonly property var actionOptions: [
    { value: "none",         label: "— nothing —" },
    { value: "mute-toggle",  label: "Mute toggle" },
    { value: "play-pause",   label: "Play / pause" },
    { value: "stop",         label: "Stop" },
    { value: "next-track",   label: "Next song" },
    { value: "previous-track", label: "Previous song" },
    { value: "volume-up",    label: "Volume up" },
    { value: "volume-down",  label: "Volume down" }
  ]

  function actionLabel(value) {
    for (var i = 0; i < root.actionOptions.length; i++)
      if (root.actionOptions[i].value === value) return root.actionOptions[i].label
    return value
  }

  // ---- load / save ------------------------------------------------------
  function load() {
    cfgLoadProc.command = ConfigStore.readScript()
    cfgLoadProc.running = true
  }

  function save() {
    dirty = false
    // Write the JSON to a temp file first (avoids argv quoting entirely),
    // then atomically move it into place via the python helper.
    cfgWriteTmp.command = ["bash", "-lc",
      "cat > /tmp/faderport-mappings-write.json <<'FPMEOF'\n" +
      JSON.stringify(root.cfg, null, 2) + "\nFPMEOF"]
    cfgWriteTmp.running = true
  }

  function finishSave() {
    cfgWriteProc.command = ConfigStore.writeScript("")
    cfgWriteProc.running = true
  }

  function setAction(buttonId, value) {
    var next = JSON.parse(JSON.stringify(root.cfg))
    next.actions[buttonId] = value
    root.cfg = next
    root.dirty = true
  }

  function setSlotApps(slotId, text) {
    var next = JSON.parse(JSON.stringify(root.cfg))
    var apps = text.split(",").map(function(s) { return s.trim() })
      .filter(function(s) { return s.length > 0 })
    next.slots[slotId].apps = apps
    root.cfg = next
    root.dirty = true
  }

  // ---- live app detection ----------------------------------------------
  function scanApps() {
    if (root.scanning) return
    root.scanning = true
    root.scanError = ""
    scanProc.command = ["/usr/bin/python3",
      root.pluginDir + "/detect_apps.py"]
    scanProc.running = true
  }

  function applyScanOutput(raw) {
    var rows = []
    var lines = String(raw || "").split("\n")
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].trim()
      if (!line) continue
      var parts = line.split("\t")
      if (parts.length >= 2) rows.push({ label: parts[0], value: parts[1] })
    }
    root.liveApps = rows
    root.scanning = false
  }

  function setEncoderTarget(value) {
    var next = JSON.parse(JSON.stringify(root.cfg))
    next.encoder.target = value
    root.cfg = next
    root.dirty = true
  }

  function setEncoderStep(value) {
    var next = JSON.parse(JSON.stringify(root.cfg))
    next.encoder.step = parseFloat(value) || 0.02
    root.cfg = next
    root.dirty = true
  }

  function resetAll() {
    var next = ConfigStore.defaultConfig()
    root.cfg = next
    root.dirty = true
  }

  // ---- save on close ----------------------------------------------------
  function close() {
    if (root.dirty) root.save()
  }

  Component.onCompleted: root.load()

  // ---- read the current config via a helper script ----------------------
  Process {
    id: cfgLoadProc
    running: false
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var parsed = ConfigStore.parseConfig(text)
        if (parsed) root.cfg = parsed
        else root.cfg = ConfigStore.defaultConfig()
      }
    }
    onExited: {
      // No-op; the collector already applied the result.
    }
  }

  Process {
    id: cfgWriteTmp
    running: false
    onExited: {
      if (exitCode === 0) root.finishSave()
    }
  }

  Process {
    id: cfgWriteProc
    running: false
    onExited: {
      root.dirty = false
    }
  }

  Process {
    id: scanProc
    running: false
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.applyScanOutput(text)
    }
    onExited: {
      root.scanning = false
    }
  }

  // ---- UI ---------------------------------------------------------------
  Column {
    anchors.fill: parent
    spacing: Style.spacing.md

    // header
    Item {
      width: parent.width
      height: Style.space(40)
      Text {
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        text: "FaderPort"
        color: Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.title
        font.weight: Font.Medium
      }
      Text {
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        text: root.dirty ? "unsaved changes" : "live"
        color: root.dirty ? Color.urgent : Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.bodySmall
      }
    }

    ScrollView {
      id: scroll
      width: parent.width
      height: parent.height - Style.space(40) - Style.spacing.lg - Style.space(36)
      clip: true
      ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
      ScrollBar.vertical.policy: ScrollBar.AsNeeded

      Column {
        width: scroll.width
        spacing: Style.spacing.md

        PanelSectionHeader { text: "Buttons" }

        Repeater {
          model: root.buttons

          Row {
            required property var modelData
            width: parent.width
            height: Style.space(34)
            spacing: Style.spacing.md

            Text {
              width: Style.space(96)
              anchors.verticalCenter: parent.verticalCenter
              text: modelData.label
              color: Color.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.body
              elide: Text.ElideRight
            }

            Dropdown {
              id: dd
              width: parent.width - Style.space(96) - Style.spacing.md
              anchors.verticalCenter: parent.verticalCenter
              value: (root.cfg.actions && root.cfg.actions[modelData.id]) || "none"
              options: root.actionOptions
              showLabel: false
              onChanged: function(value) {
                root.setAction(modelData.id, value)
              }
            }
          }
        }

        PanelSeparator { width: parent.width }

        // App slots header with the shared scan control (one scan feeds all
        // the slot dropdowns — the detected list is global, not per-slot).
        Row {
          width: parent.width
          height: Style.space(24)
          spacing: Style.spacing.md

          PanelSectionHeader {
            text: "App slots"
            width: parent.width - Style.space(160) - Style.spacing.md
            anchors.verticalCenter: parent.verticalCenter
          }

          Button {
            width: Style.space(160)
            height: Style.space(22)
            anchors.verticalCenter: parent.verticalCenter
            text: root.scanning ? "Scanning…" : "Detect playing apps"
            enabled: !root.scanning
            onClicked: root.scanApps()
          }
        }

        Repeater {
          model: root.slotNames

          // One compact row per slot: name | textbox | add-from-detected.
          Row {
            required property string modelData
            width: parent.width
            height: Style.space(32)
            spacing: Style.spacing.sm

            Text {
              width: Style.space(72)
              anchors.verticalCenter: parent.verticalCenter
              text: modelData.charAt(0).toUpperCase() + modelData.slice(1)
              color: Color.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.body
              font.bold: true
              elide: Text.ElideRight
            }

            TextField {
              width: parent.width - Style.space(72) - Style.spacing.sm
                - Style.space(140) - Style.spacing.sm
              anchors.verticalCenter: parent.verticalCenter
              text: (root.cfg.slots && root.cfg.slots[modelData]
                && root.cfg.slots[modelData].apps)
                ? root.cfg.slots[modelData].apps.join(", ") : ""
              placeholderText: "comma-separated apps"
              onEditingFinished: root.setSlotApps(modelData, text)
            }

            Dropdown {
              id: liveDd
              width: Style.space(140)
              height: Style.space(30)
              anchors.verticalCenter: parent.verticalCenter
              value: ""
              options: [{ value: "", label: "add app…" }].concat(
                root.liveApps.map(function(a) {
                  return { value: a.value, label: a.label }
                }))
              showLabel: false
              onChanged: function(value) {
                if (!value) return
                // Append the picked app to the slot's list if not present
                var cur = (root.cfg.slots && root.cfg.slots[modelData]
                  && root.cfg.slots[modelData].apps) || []
                if (cur.indexOf(value) < 0) {
                  cur = cur.concat([value])
                  root.setSlotApps(modelData, cur.join(", "))
                }
                liveDd.value = ""
              }
            }
          }
        }

        PanelSeparator { width: parent.width }

        PanelSectionHeader { text: "Encoder" }

        Row {
          width: parent.width
          height: Style.space(34)
          spacing: Style.spacing.md

          Text {
            width: Style.space(96)
            anchors.verticalCenter: parent.verticalCenter
            text: "Target"
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.body
          }

          TextField {
            width: parent.width - Style.space(96) - Style.spacing.md
            anchors.verticalCenter: parent.verticalCenter
            text: (root.cfg.encoder && root.cfg.encoder.target) || ""
            placeholderText: "gtunes, or master"
            onEditingFinished: root.setEncoderTarget(text)
          }
        }

        Row {
          width: parent.width
          height: Style.space(34)
          spacing: Style.spacing.md

          Text {
            width: Style.space(96)
            anchors.verticalCenter: parent.verticalCenter
            text: "Step"
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.body
          }

          TextField {
            width: parent.width - Style.space(96) - Style.spacing.md
            anchors.verticalCenter: parent.verticalCenter
            text: (root.cfg.encoder && root.cfg.encoder.step) ? String(root.cfg.encoder.step) : "0.02"
            placeholderText: "0.02"
            validator: DoubleValidator { bottom: 0.001; top: 1.0 }
            onEditingFinished: root.setEncoderStep(text)
          }
        }
      }
    }

    // footer buttons
    Row {
      width: parent.width
      height: Style.space(36)
      spacing: Style.spacing.md

      Button {
        text: "Reset to defaults"
        onClicked: root.resetAll()
      }

      Item { width: Style.spacing.md; height: 1 }

      Button {
        text: "Save"
        enabled: root.dirty
        onClicked: root.save()
      }

      Item { width: Style.spacing.md; height: 1 }

      Button {
        text: "Done"
        onClicked: root.close()
      }
    }
  }
}
