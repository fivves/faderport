// ConfigStore.js — read/write helpers for the faderport mappings config.
// Reading goes through a tiny python one-liner so we never have to parse
// JSON with a bash pipeline (fragile). Writing shells out to python for an
// atomic tmp+rename write.

// Default config == the daemon's gold-standard behavior (mirrors the
// DEFAULT_ACTIONS / DEFAULT_SLOTS / DEFAULT_ENCODER tables in the daemon).
function defaultConfig() {
  return {
    actions: {
      play: "play-pause",
      stop: "stop",
      rec: "none",
      ffwd: "volume-up",
      rewind: "volume-down",
      loop: "none",
      mute: "mute-toggle",
      solo: "mute-toggle",
      recarm: "mute-toggle",
      output: "mute-toggle",
      link: "slot-link",
      pan: "slot-pan",
      channel: "slot-channel",
      scroll: "slot-scroll",
      master: "slot-master",
      click: "slot-click",
      section: "slot-section",
      marker: "slot-marker"
    },
    slots: {
      link:    { apps: ["zen"] },
      pan:     { apps: ["vesktop", "discord"] },
      channel: { apps: ["gtunes"] },
      scroll:  { apps: ["mpv", "delfin"] },
      master:  { apps: [] },
      click:   { apps: [] },
      section: { apps: [] },
      marker:  { apps: [] }
    },
    encoder: { target: "gtunes", step: 0.02 },
    nudge: 0.05
  }
}

function readScript() {
  // Return argv array (not a single string — Quickshell Process needs
  // separate elements, a string with embedded spaces/quotes breaks exec).
  return ["/usr/bin/python3", "-c", "import json,os; p=os.path.expanduser('~/.config/faderport/mappings.json'); print(open(p).read() if os.path.exists(p) else '')"]
}

function parseConfig(raw) {
  var text = String(raw || "").trim()
  if (!text) return null
  try {
    var obj = JSON.parse(text)
    if (!obj || typeof obj !== "object") return null
    // Merge over defaults so partial files still give a complete UI
    var def = defaultConfig()
    var merged = JSON.parse(JSON.stringify(def))
    for (var k in obj) merged[k] = obj[k]
    return merged
  } catch (e) {
    return null
  }
}

function writeScript(jsonText) {
  // Pass the JSON via a temp file to avoid any argv quoting issues.
  var tmpFile = "/tmp/faderport-mappings-write.json"
  // (The file is written by the QML side before running; see save().)
  return ["/usr/bin/python3", "-c", "import os,json,sys; d=os.path.expanduser('~/.config/faderport'); os.makedirs(d,exist_ok=True); p=d+'/mappings.json'; tmp=p+'.tmp'; open(tmp,'w').write(open('/tmp/faderport-mappings-write.json').read()); os.replace(tmp,p)"]
}
