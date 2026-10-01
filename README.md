# FaderPort

[![Omarchy](https://img.shields.io/badge/Omarchy-Quattro-111111)](https://omarchy.org)
[![License: MIT](https://img.shields.io/github/license/fivves/faderport)](LICENSE)
[![kind: bar-widget](https://img.shields.io/badge/kind-bar--widget-5b5b5b)](https://omarchy.org/manual/shell-plugins)

[Changelog](CHANGELOG.md)

An [Omarchy](https://omarchy.org) bar widget (`kind: "bar-widget"`) that turns a
**PreSonus FaderPort FP2** into a volume controller. The motorized fader drives
system volume in both directions, and every button can be reassigned from a
panel in the bar.

This plugin is the assignment UI. It edits `~/.config/faderport/mappings.json`,
which the companion daemon hot-reloads within about a second. **The daemon is
what actually talks to the hardware**, so install it as well (see below).

## What you get

- Fader sets volume while touched, and external volume changes move the motor to
  match, so the fader and the desktop never disagree
- Ten buttons assignable to: nothing, mute toggle, play/pause, stop, next track,
  previous track, volume up, volume down
- Eight app slots (Link, Pan, Channel, Scroll, Master, Click, Section, Marker)
  that route the fader to one app's stream instead of the system volume
- Slots are filled from a **Detect playing apps** picker, so you choose from apps
  that are making sound right now rather than guessing process names
- Encoder assigned to any app slot, with a configurable step size
- Writes are atomic (temp file plus rename) and the daemon applies them live. No
  restart, and no reload of the shell

## Requirements

- A **PreSonus FaderPort FP2** (`194f:0205`). The older classic FaderPort uses a
  different motor protocol and does not work with this daemon.
- PipeWire with WirePlumber (`wpctl`)
- Python 3
- Omarchy (Quattro), which provides the shell and the bar widget API

## Install

### 1. The daemon

```bash
git clone https://github.com/fivves/faderport.git
cd faderport

install -m 755 daemon/faderport-volume.py ~/.local/bin/faderport-volume.py
install -m 644 daemon/faderport-volume.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now faderport-volume.service
```

The udev rule gives your user access to the raw MIDI node, which is what lets the
daemon move the fader without root:

```bash
sudo install -m 644 daemon/99-faderport.rules /etc/udev/rules.d/99-faderport.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Check it came up:

```bash
systemctl --user status faderport-volume.service
```

### 2. The plugin

```bash
omarchy plugin add https://github.com/fivves/faderport.git --enable
```

Or **Setup > Plugins > Add Plugin**, paste the git URL, and enable it when asked.

The widget is a bar icon (🎚️). Place it wherever you like with **Setup > Plugins**
or `omarchy bar move`.

## Uninstall

```bash
omarchy plugin remove io.github.fivves.faderport
systemctl --user disable --now faderport-volume.service
rm ~/.local/bin/faderport-volume.py ~/.config/systemd/user/faderport-volume.service
sudo rm /etc/udev/rules.d/99-faderport.rules
```

Your assignments in `~/.config/faderport/mappings.json` are left alone.

## The panel

Open it from the bar icon.

**Buttons** lists the ten physical buttons that are not app slots, each with a
dropdown. The defaults are the ones most people want: Play toggles playback, Stop
stops it, FFwd and Rewind nudge volume, and Mute, Solo, RecArm and Output all
toggle fader mute, which is how a DAW console behaves.

**App slots** lists all eight slot buttons with a text field each. Pressing one of
those buttons on the device routes the fader to that slot's app instead of the
system volume, with the LED on while it is selected. Each field takes a comma
separated list of process names, and **Detect playing apps** fills it from a live
list of whatever is currently producing sound.

The **encoder** at the bottom gets an app slot and a step size.

Changes are saved when the panel closes. The header shows `live` or `unsaved
changes` so you can tell which state you are looking at.

### The built-in defaults are the author's own apps

The daemon and the panel ship with defaults that map the four main app slots to
Zen Browser, Discord, gTunes and mpv, and point the encoder at gTunes. Those are
what the author runs. Reassign them in the panel, or edit
`~/.config/faderport/mappings.json`; the defaults only apply to keys you have not
set.

## Configuration file

`~/.config/faderport/mappings.json`, hot-reloaded about once a second. Deleting it
restores the built-in defaults, which are exactly this:

```json
{
  "actions": {
    "play": "play-pause",
    "stop": "stop",
    "rec": "none",
    "ffwd": "volume-up",
    "rewind": "volume-down",
    "loop": "none",
    "mute": "mute-toggle",
    "solo": "mute-toggle",
    "recarm": "mute-toggle",
    "output": "mute-toggle",
    "link": "slot-link",
    "pan": "slot-pan",
    "channel": "slot-channel",
    "scroll": "slot-scroll",
    "master": "slot-master",
    "click": "slot-click",
    "section": "slot-section",
    "marker": "slot-marker"
  },
  "slots": {
    "link": { "apps": ["zen"] },
    "pan": { "apps": ["vesktop", "discord"] },
    "channel": { "apps": ["gtunes"] },
    "scroll": { "apps": ["mpv", "delfin"] },
    "master": { "apps": [] },
    "click": { "apps": [] },
    "section": { "apps": [] },
    "marker": { "apps": [] }
  },
  "encoder": { "target": "gtunes", "step": 0.02 },
  "nudge": 0.05
}
```

Actions the daemon understands: `none`, `mute-toggle`, `play-pause`, `stop`,
`next-track`, `previous-track`, `volume-up`, `volume-down`, and `slot-<name>` for
the slot buttons. `nudge` is how much the FFwd and Rewind volume actions move per
press. `encoder.target` is a slot name, and `encoder.step` is the volume change
per click of the knob.

### How a slot finds its app

PipeWire reports the audio stream under a helper process, not the app itself, so
matching by application name alone is unreliable (Zen and Discord both register as
Chromium). The daemon walks the process tree from the stream's PID up to a known
root binary instead, and falls back to substring matching for apps that report no
PID at all, which mpv does.

A slot whose app is not currently producing sound has no stream to attach to. It
falls back to the system volume until the app plays again, which is expected
behavior rather than a fault.

### Mute

`mute-toggle` is a fader mute on whichever target is active, not a global mute. It
remembers that target's volume, sets it to zero and drops the fader. Pressing
again restores the remembered volume. Each app keeps its own mute state, so
muting one app and switching to another does not affect it. Pushing the fader up
while muted cancels the mute and takes the new position.

### Encoder

The knob is an endless encoder with its own target, independent of whichever slot
the fader is on. Counterclockwise lowers, clockwise raises, and turning it on a
muted target lifts the mute and lands at the new level, the same as a fader move.

## How it works

The daemon talks to the device over the raw ALSA rawmidi node, not the ALSA
sequencer, and it does not use HID: the FP2 is a class-compliant USB MIDI device.
The fader position arrives as pitch bend, motor moves go back out as pitch bend on
channel 0, and buttons arrive as note on/off messages whose note numbers double as
LED addresses.

Two details worth knowing if you modify it:

- The fader's own position reports after a motor move would feed back as input, so
  fader input is only accepted while the touch sensor (note 104) is on. Motor
  echoes do not touch the sensor, so they are ignored.
- The device node must be opened read/write. Opened read-only, every motor write
  fails silently and the fader simply never moves.

## Known limits

- FP2 only. The classic FaderPort's motor control is a different MIDI message.
- The udev rule sets the raw MIDI node to `0666`. That is what avoids running the
  daemon as root, and it is a deliberate trade, since the node is a MIDI device on
  a USB port rather than a general-purpose interface.
- ALSA card numbers are not stable across reboots, so the daemon discovers the
  device through `/dev/snd/by-id` instead of a hardcoded card number.
- The hardware can stop streaming after a few minutes untouched. The daemon
  reconnects on its own when that happens.
- `pw-dump` is used for app detection. Streams are cached for a few seconds so a
  scan with many slots stays responsive.
- Only one process can hold the raw MIDI node. Stop the service before poking at
  the device by hand.

## License

[MIT](LICENSE), copyright 2026 Eddie Lucitt.

Built against Omarchy's first-party bar widget API, which is MIT licensed
(copyright David Heinemeier Hansson), but contains no Omarchy source.
