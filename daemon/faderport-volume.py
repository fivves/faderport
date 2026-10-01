#!/usr/bin/env python3
"""
FaderPort Volume Control Daemon (faderport-volume) — CONFIG-DRIVEN EDITION

Bidirectional system + per-app volume control via PipeWire (wpctl) on
Omarchy/Hyprland desktops. Built from a raw USB-MIDI protocol reverse-
engineering session against a PreSonus FaderPort FP2.

Controls (defaults — every mapping is configurable via mappings.json):
  - Fader (touch) -> sets volume of the ACTIVE TARGET (master or selected app)
  - Link / Pan / Channel / Scroll buttons -> select per-app volume slots
      Link    = Zen Browser
      Pan     = Discord (vesktop)
      Channel = gTunes
      Scroll  = mpv + delfin (shared)
    Press = select (LED on, fader controls that app)
    Press again = deselect (LED off, fader controls master again)
  - External volume changes -> motor moves fader to match (master only)
  - Mute/Solo/RecArm/Output -> fader-mute toggle on the ACTIVE TARGET
    (DAW console style: mute remembers the current volume, sets it to 0
    and drops the fader to bottom; unmute restores the remembered volume
    and flies the fader back). State is per-target — each slot keeps its
    own pre-mute volume, and switching slots swaps the mute LED to that
    slot's state.
  - Play/Stop -> playerctl play-pause / stop
  - FFwd/Rewind -> volume nudges (+/-0.05 of current target)
  - Encoder (knob) -> dedicated gTunes volume, always

CONFIG: ~/.config/faderport/mappings.json (hot-reloaded on change; the
Omarchy bar widget io.github.fivves.faderport writes it). Missing keys fall
back to the defaults above, so a fresh/empty file behaves exactly like the
original daemon.

Schema:
{
  "version": 1,
  "actions": {
    "play":   "play-pause",   // any of the action catalog below
    "stop":   "stop",
    "rec":    "none",
    "ffwd":   "volume-up",
    "rewind": "volume-down",
    "loop":   "none",
    "mute":   "mute-toggle",
    "solo":   "mute-toggle",
    "recarm": "mute-toggle",
    "output": "mute-toggle",
    "link":   "slot-link",    // slot buttons bind to slot-<name>
    "pan":    "slot-pan",
    "channel":"slot-channel",
    "scroll": "slot-scroll"
  },
  "slots": {
    "link":    { "apps": ["zen"] },
    "pan":     { "apps": ["vesktop", "discord"] },
    "channel": { "apps": ["gtunes"] },
    "scroll":  { "apps": ["mpv", "delfin"] }
  },
  "encoder": { "target": "gtunes", "step": 0.02 }
}

Action catalog:
  none          do nothing
  mute-toggle   fader-mute toggle on the ACTIVE TARGET (slot or master):
                mute = remember volume, set 0, fader to bottom; unmute =
                restore volume, fader back. LED glows while muted.
  play-pause    playerctl play-pause
  stop          playerctl stop
  next-track    playerctl next
  previous-track playerctl previous
  volume-up     nudge current target volume +step
  volume-down   nudge current target volume -step
  slot-<name>   select/deselect the named app slot (LED on when active)

Reads/writes RAW MIDI bytes from the ALSA rawmidi device node directly.

Protocol (decoded from a live capture of an FP2, Aug 2026):
  - Fader (read)       -> Pitch Bend (0xE0 lsb msb)  14-bit, -8192..+8192
  - Fader (write/motor)-> Pitch Bend (0xE0 lsb msb)  14-bit, 0..16383
  - Fader touch        -> Note 104 on/off
  - Buttons (link/pan/channel/scroll) -> Notes 5 / 42 / 54 / 56
  - LEDs: send `90 <note> 7F` on, `90 <note> 00` off, `90 <note> 01` blink
  - Mute/Solo/RecArm   -> Notes 16 / 8 / 0
  - Play/Stop/Rec      -> Notes 94 / 93 / 95
  - Encoder            -> CC16 (fine volume nudge)

Known FP2 quirk: firmware goes silent after a few minutes. Watchdog
resets via usbreset (udev rule 99-faderport.rules grants write access).
"""

import json
import os
import re
import select
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------

# None = auto-discover the FP2 by identity (see find_fp2_device). ALSA card
# numbers are NOT stable across reboots (USB enumeration order shuffles them
# — the FP2 was card 5 one boot, card 0 the next), so never hardcode
# /dev/snd/midiC5D0. Pass an explicit path as argv[1] to override.
DEFAULT_MIDI_DEV = None


def find_fp2_device():
    """Locate the PreSonus FP2's raw MIDI node by identity, not card number.

    /dev/snd/by-id/ contains stable symlinks (usb-PreSonus_PreSonus_FP2_*)
    that always point at the right control node regardless of card number:
        usb-PreSonus_PreSonus_FP2_FP2C23320024-00 -> ../controlC0
    Resolve that to /dev/snd/midiC<N>D0. Returns None if the device is not
    currently enumerated (caller retries — the FP2 may still be probing at
    boot, and hot-unplug/replug must recover without a service restart)."""
    try:
        entries = os.listdir("/dev/snd/by-id")
    except OSError:
        return None
    for name in entries:
        if "FP2" not in name or "PreSonus" not in name:
            continue
        link = os.path.join("/dev/snd/by-id", name)
        if not os.path.islink(link):
            continue
        m = re.match(r"\.\./controlC(\d+)$", os.readlink(link))
        if not m:
            continue
        midi = f"/dev/snd/midiC{m.group(1)}D0"
        if os.path.exists(midi):
            return midi
    return None


class DeviceNotFound(Exception):
    """FP2 not enumerated yet; caller should retry connect()."""

# ---------------------------------------------------------------------------
# Volume control (PipeWire via wpctl)
# ---------------------------------------------------------------------------

DEFAULT_SINK = "@DEFAULT_AUDIO_SINK@"


def wpctl(args):
    return subprocess.run(
        ["wpctl", *args], capture_output=True, text=True, timeout=5
    )


def set_volume_absolute(fraction: float, node_id=None):
    fraction = max(0.0, min(1.0, fraction))
    target = str(node_id) if node_id is not None else DEFAULT_SINK
    wpctl(["set-volume", target, f"{fraction:.3f}"])


def get_volume(node_id=None) -> float:
    target = str(node_id) if node_id is not None else DEFAULT_SINK
    r = wpctl(["get-volume", target])
    out = r.stdout.strip()
    if "Volume:" in out:
        try:
            return float(out.split("Volume:")[1].split()[0])
        except (ValueError, IndexError):
            pass
    return 0.0


def set_muted(muted: bool):
    wpctl(["set-mute", DEFAULT_SINK, "1" if muted else "0"])


def is_muted() -> bool:
    r = wpctl(["get-volume", DEFAULT_SINK])
    return "MUTED" in r.stdout.upper()


# ---------------------------------------------------------------------------
# Configuration (mappings.json, hot-reloaded)
# ---------------------------------------------------------------------------

CONFIG_PATH = os.path.expanduser("~/.config/faderport/mappings.json")

# Canonical button table: name -> MIDI note (physical buttons on the FP2)
BUTTONS = {
    "play":    94,
    "stop":    93,
    "rec":     95,
    "ffwd":    92,
    "rewind":  91,
    "loop":    86,
    "mute":    16,
    "solo":    8,
    "recarm":  0,
    "output":  70,
    "link":    5,
    "pan":     42,
    "channel": 54,
    "scroll":  56,
    "master":  58,
    "click":   59,
    "section": 60,
    "marker":  61,
}
NOTE_TO_BUTTON = {note: name for name, note in BUTTONS.items()}

# Default action per button = exact gold-standard behavior.
DEFAULT_ACTIONS = {
    "play":    "play-pause",
    "stop":    "stop",
    "rec":     "none",
    "ffwd":    "volume-up",
    "rewind":  "volume-down",
    "loop":    "none",
    "mute":    "mute-toggle",
    "solo":    "mute-toggle",
    "recarm":  "mute-toggle",
    "output":  "mute-toggle",
    "link":    "slot-link",
    "pan":     "slot-pan",
    "channel": "slot-channel",
    "scroll":  "slot-scroll",
    "master":  "slot-master",
    "click":   "slot-click",
    "section": "slot-section",
    "marker":  "slot-marker",
}

# Default slot -> app root process name(s)
DEFAULT_SLOTS = {
    "link":    {"apps": ["zen"]},
    "pan":     {"apps": ["vesktop", "discord"]},
    "channel": {"apps": ["gtunes"]},
    "scroll":  {"apps": ["mpv", "delfin"]},
    "master":  {"apps": []},
    "click":   {"apps": []},
    "section": {"apps": []},
    "marker":  {"apps": []},
}
SLOT_ORDER = ["link", "pan", "channel", "scroll", "master", "click", "section", "marker"]

DEFAULT_ENCODER = {"target": "gtunes", "step": 0.02}

# Volume nudge step for volume-up/volume-down (overridable via config)
DEFAULT_NUDGE = 0.05


def load_config():
    """Load mappings.json, merge over defaults. Never throws — a broken or
    missing file yields the default configuration (gold-standard behavior)."""
    actions = dict(DEFAULT_ACTIONS)
    slots = {k: {"apps": list(v["apps"])} for k, v in DEFAULT_SLOTS.items()}
    encoder = dict(DEFAULT_ENCODER)
    nudge = DEFAULT_NUDGE
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError("config root must be an object")
        a = cfg.get("actions")
        if isinstance(a, dict):
            for name, action in a.items():
                if name in actions and isinstance(action, str):
                    actions[name] = action
        s = cfg.get("slots")
        if isinstance(s, dict):
            for name, info in s.items():
                if name in slots and isinstance(info, dict) \
                        and isinstance(info.get("apps"), list):
                    slots[name]["apps"] = [str(x) for x in info["apps"] if x]
        e = cfg.get("encoder")
        if isinstance(e, dict):
            if isinstance(e.get("target"), str) and e["target"]:
                encoder["target"] = e["target"]
            try:
                encoder["step"] = abs(float(e.get("step", encoder["step"])))
            except (TypeError, ValueError):
                pass
        if isinstance(cfg.get("nudge"), (int, float)):
            nudge = abs(float(cfg["nudge"]))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[FP] config load failed ({e}) — using defaults")
    return {"actions": actions, "slots": slots, "encoder": encoder,
            "nudge": nudge}


# ---------------------------------------------------------------------------
# App slot matching (PipeWire stream -> app)
# ---------------------------------------------------------------------------


def get_audio_streams():
    """Return list of {id, name, pid} for active output streams via pw-dump.
    Cached for 3s: slot presses call this up to 16x (8 slots x 2 passes), and
    spawning pw-dump per call is the noticeable press-to-activate delay."""
    now = time.monotonic()
    if get_audio_streams._cache_ts and now - get_audio_streams._cache_ts < 3.0:
        return get_audio_streams._cache
    try:
        r = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=10)
        data = json.loads(r.stdout)
    except Exception as e:
        print(f"[FP] pw-dump failed: {e}")
        return []
    streams = []
    for obj in data:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        p = obj.get("info", {}).get("props", {})
        mc = p.get("media.class", "")
        if "Stream/Output" not in mc:
            continue
        name = p.get("application.name") or p.get("node.name") or ""
        pid = p.get("application.process.id")
        streams.append({"id": obj.get("id"), "name": name, "pid": pid})
    get_audio_streams._cache = streams
    get_audio_streams._cache_ts = now
    return streams


get_audio_streams._cache = []
get_audio_streams._cache_ts = 0


def process_is_app(pid, app_roots):
    """Walk up the process tree from pid; True if any ancestor's cmdline
    matches one of app_roots (substring match on the binary name)."""
    seen = set()
    while pid and pid not in seen and len(seen) < 20:
        seen.add(pid)
        try:
            with open(f"/proc/{pid}/comm") as f:
                comm = f.read().strip()
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\x00", b" ").decode(errors="ignore")
            for root in app_roots:
                if root in comm.lower() or root in cmdline.lower():
                    return True
            with open(f"/proc/{pid}/stat") as f:
                parts = f.read().rsplit(")", 1)[1].split()
            pid = int(parts[1])  # ppid
        except (FileNotFoundError, ValueError, IndexError):
            return False
    return False


def find_app_stream(app_roots):
    """Find the PipeWire stream id whose process tree matches app_roots.
    Fallback: match application.name directly when pid is unavailable
    (some apps like mpv report streams with pid=None)."""
    streams = get_audio_streams()  # cached; one pw-dump for all callers
    for s in streams:
        if s["pid"] and process_is_app(s["pid"], app_roots):
            return s["id"]
    # Fallback: name match (case-insensitive substring)
    for s in streams:
        name = (s["name"] or "").lower()
        for root in app_roots:
            if root in name:
                return s["id"]
    return None


# ---------------------------------------------------------------------------
# FaderPort protocol
# ---------------------------------------------------------------------------

NOTE_FADER_TOUCH = 104

# LED velocity values
LED_OFF = 0x00
LED_ON = 0x7F
LED_BLINK = 0x01

# MIDI status bytes
MIDI_NOTE_OFF = 0x80
MIDI_NOTE_ON = 0x90
MIDI_CC = 0xB0
MIDI_PITCHBEND = 0xE0

PB_MIN = -8192
PB_MAX = 8192


def pb_to_volume(pb: int) -> float:
    return (pb - PB_MIN) / (PB_MAX - PB_MIN)


def volume_to_pb(vol: float) -> int:
    return int(PB_MIN + max(0.0, min(1.0, vol)) * (PB_MAX - PB_MIN))


def pb_to_14bit(pb: int) -> int:
    return max(0, min(16383, pb + 8192))


def fader_bytes(vol: float) -> bytes:
    raw14 = pb_to_14bit(volume_to_pb(vol))
    lsb = raw14 & 0x7F
    msb = (raw14 >> 7) & 0x7F
    return bytes([MIDI_PITCHBEND, lsb, msb])


def led_bytes(note: int, state: int) -> bytes:
    return bytes([MIDI_NOTE_ON, note, state])


class FaderPortVolume:
    WATCHDOG_IDLE_S = 999999  # DISABLED: FP2 is silent by design when idle.
    # Idle silence is NORMAL (no touch = no events). An idle-based watchdog
    # false-fired and reset the device every idle stretch (18 resets in 30 min
    # on one debugging session), which read as the device "sleeping" but was
    # the watchdog breaking a perfectly healthy device.
    # Real lockups surface as read errors (ENODEV/EIO), handled by the
    # reconnect path in run(). Keep the constant so code structure stays.
    RESET_COOLDOWN_S = 20
    KEEPALIVE_S = 999999  # DISABLED: motor pings caused fader twitch + didn't
    # prevent anything. Device stays awake on its own after a clean start.
    VOLUME_POLL_S = 0.5
    MOTOR_HYSTERESIS = 0.01
    STREAM_REFRESH_S = 3.0  # re-scan pw-dump for app streams this often
    CONFIG_POLL_S = 1.0     # check mappings.json mtime this often

    def __init__(self, midi_dev: str = DEFAULT_MIDI_DEV):
        self.midi_dev = midi_dev
        self.auto_discover = midi_dev is None
        self.fd = None
        self.last_fader_pb = None
        self.touch_active = False
        self.last_mute_toggle = 0
        self.mute_debounce = 0.35
        self.mute_states = {}  # target key ("master"/slot) -> pre-mute volume
        self.last_event_time = time.monotonic()
        self.last_reset_time = 0
        self.last_known_volume = None
        self.last_motor_vol = None
        self.last_mute_state = None  # for LED tracking of external mutes
        # config (hot-reloaded)
        self.config = load_config()
        self.config_mtime = self._config_mtime()
        # action -> button notes that currently map to it (for LED sync)
        self.action_notes = self._build_action_notes()
        # app slot state
        self.active_slot = None  # None = master, else "link"/"pan"/...
        self.stream_ids = {}  # slot -> stream id (refreshed periodically)
        self.last_stream_refresh = 0

    # -- config ------------------------------------------------------------

    def _config_mtime(self):
        try:
            return os.path.getmtime(CONFIG_PATH)
        except OSError:
            return 0

    def _build_action_notes(self):
        """note set per action, for LED sync (mute LED etc)."""
        notes = {}
        for name, action in self.config["actions"].items():
            note = BUTTONS.get(name)
            if note is None:
                continue
            notes.setdefault(action, []).append(note)
        return notes

    def reload_config_if_changed(self):
        mtime = self._config_mtime()
        if mtime == self.config_mtime:
            return
        self.config_mtime = mtime
        self.config = load_config()
        self.action_notes = self._build_action_notes()
        print("[FP] config reloaded")
        # If the active slot's app list changed, drop to master + re-resolve
        if self.active_slot is not None:
            if (self.config["actions"].get("slot-" + self.active_slot)
                    != "slot-" + self.active_slot
                    or self.config["slots"].get(self.active_slot) is None):
                print(f"[FP] slot {self.active_slot} removed by config — back to master")
                self.active_slot = None
            else:
                self.refresh_stream_ids(force=True)
        # LED state may have changed with the mappings (e.g. mute no longer bound)
        self.sync_mute_led()
        self.refresh_slot_leds()

    # -- connection --------------------------------------------------------

    def connect(self):
        if self.auto_discover:
            dev = find_fp2_device()
            if dev is None:
                raise DeviceNotFound(
                    "FP2 not enumerated yet — retrying"
                )
            self.midi_dev = dev
        self.fd = os.open(self.midi_dev, os.O_RDWR | os.O_NONBLOCK)
        print(f"[FP] Opened {self.midi_dev}")
        try:
            vol = get_volume()
            self.last_known_volume = vol
            self.move_fader(vol)
        except Exception as e:
            print(f"[FP] initial sync failed: {e}")
        self.sync_mute_led()
        self.refresh_slot_leds()

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def write(self, data: bytes):
        if self.fd is None:
            return
        try:
            n = os.write(self.fd, data)
            if n != len(data):
                print(f"[FP] short write: {n}/{len(data)}")
        except OSError as e:
            print(f"[FP] write error: {e}")

    # -- LED helpers -------------------------------------------------------

    def set_led(self, note: int, state: int):
        self.write(led_bytes(note, state))

    def set_all_leds(self, notes, state: int):
        for note in notes:
            self.set_led(note, state)

    def sync_mute_led(self):
        """Light every button bound to mute-toggle to match the ACTIVE
        TARGET's muted state (per-target fader-mute). For master, also
        reflects a flag-based external mute."""
        notes = self.action_notes.get("mute-toggle", [])
        if not notes:
            return
        node_id, label = self.current_target()
        key = self._mute_key(node_id, label)
        muted = key in self.mute_states
        if key == "__sink__":
            try:
                muted = muted or is_muted()
            except Exception:
                pass
        self.set_all_leds(notes, LED_ON if muted else LED_OFF)
        print(f"[FP] mute LED {'ON' if muted else 'OFF'} [{label}]")

    def refresh_slot_leds(self):
        """Light only the active slot's button LED."""
        for slot in SLOT_ORDER:
            # The note bound to this slot (if any)
            note = None
            for name, action in self.config["actions"].items():
                if action == "slot-" + slot:
                    note = BUTTONS.get(name)
                    break
            if note is None:
                continue
            state = LED_ON if slot == self.active_slot else LED_OFF
            self.set_led(note, state)

    # -- target resolution -------------------------------------------------

    def current_target(self):
        """Return (node_id_or_None, label). None = master sink."""
        if self.active_slot is None:
            return None, "master"
        sid = self.stream_ids.get(self.active_slot)
        if sid:
            return sid, self.active_slot
        return None, f"{self.active_slot} (no stream)"

    def refresh_stream_ids(self, force=False):
        """Re-scan pw-dump to keep app stream ids current."""
        now = time.monotonic()
        if not force and now - self.last_stream_refresh < self.STREAM_REFRESH_S:
            return
        self.last_stream_refresh = now
        for slot in SLOT_ORDER:
            apps = self.config["slots"].get(slot, {}).get("apps", [])
            sid = find_app_stream(apps)
            self.stream_ids[slot] = sid

    # -- motor -------------------------------------------------------------

    def move_fader(self, vol: float):
        data = fader_bytes(vol)
        self.write(data)
        self.last_motor_vol = vol
        print(f"[FP] motor -> {vol:.3f} (PB {data.hex()})")

    def keepalive(self):
        """Re-command the motor to its current position. The FP2 firmware
        idles out after ~1-2 min of inactivity (stops streaming until a
        physical replug). Poking the motor every KEEPALIVE_S keeps the
        firmware awake — the fader doesn't move because it's already there.
        Falls back to a full reset if the write errors."""
        target = self.last_motor_vol if self.last_motor_vol is not None else 0.5
        data = fader_bytes(target)
        try:
            n = os.write(self.fd, data)
            if n != len(data):
                raise OSError("short write")
        except OSError as e:
            print(f"[FP] keepalive failed ({e}) — resetting device")
            self.reset_device()

    # -- MIDI parsing ------------------------------------------------------

    def parse_stream(self, data: bytes):
        i = 0
        n = len(data)
        while i < n:
            b = data[i]
            if b & 0x80:
                if b == 0xF0 or b == 0xF7:
                    i += 1
                    while i < n and not (data[i] & 0x80):
                        i += 1
                    continue
                status = b
                i += 1
                if i >= n:
                    return
                d1 = data[i]
                i += 1
                if status in (MIDI_NOTE_ON, MIDI_NOTE_OFF, MIDI_CC):
                    if i >= n:
                        return
                    d2 = data[i]
                    i += 1
                    if status == MIDI_NOTE_ON:
                        self.on_note(d1, d2 > 0)
                    elif status == MIDI_NOTE_OFF:
                        self.on_note(d1, False)
                    elif status == MIDI_CC:
                        self.on_cc(d1, d2)
                elif status == MIDI_PITCHBEND:
                    pb = (d1 | (0 if i >= n else data[i] << 7)) - 8192
                    self.on_pitchbend(pb)
                    if i < n:
                        i += 1
            else:
                i += 1

    # -- handlers ----------------------------------------------------------

    def run_action(self, action: str, now: float):
        """Dispatch a configured action. Slot toggles handle their own state."""
        if action == "none" or not action:
            return
        if action.startswith("slot-"):
            self.toggle_slot(action[len("slot-"):])
        elif action == "mute-toggle":
            self.toggle_mute(now)
        elif action == "play-pause":
            self.toggle_playback()
        elif action == "stop":
            self.stop_playback()
        elif action == "next-track":
            subprocess.run(["playerctl", "next"], capture_output=True, timeout=3)
        elif action == "previous-track":
            subprocess.run(["playerctl", "previous"], capture_output=True, timeout=3)
        elif action == "volume-up":
            self.nudge_volume(+self.config["nudge"])
        elif action == "volume-down":
            self.nudge_volume(-self.config["nudge"])
        else:
            print(f"[FP] unknown action: {action}")

    def on_note(self, note: int, on: bool):
        print(f"[FP] note {'ON ' if on else 'OFF'} {note}")
        now = time.monotonic()

        if note == NOTE_FADER_TOUCH:
            self.touch_active = on
            return

        if not on:
            return

        # Dispatch through the configured mapping (gold standard by default)
        button = NOTE_TO_BUTTON.get(note)
        action = self.config["actions"].get(button, "none") if button else "none"
        print(f"[FP] button {button or note} -> {action}")
        self.run_action(action, now)

    def toggle_slot(self, slot):
        if slot not in self.config["slots"]:
            print(f"[FP] slot {slot} not configured")
            return
        if self.active_slot == slot:
            print(f"[FP] slot {slot} deselected -> master")
            self.active_slot = None
        else:
            print(f"[FP] slot {slot} selected")
            self.active_slot = slot
            self.refresh_stream_ids(force=True)
            node_id, label = self.current_target()
            if node_id is None:
                print(f"[FP] slot {slot}: no audio stream for app(s) "
                      f"{self.config['slots'][slot]['apps']} — controlling "
                      f"master until it plays")
        self.refresh_slot_leds()
        self.sync_mute_led()  # LED follows the newly selected target's state
        # Sync fader to target's current volume (or 0 if that target is muted)
        node_id, label = self.current_target()
        key = self._mute_key(node_id, label)
        if key in self.mute_states:
            vol = 0.0
        else:
            vol = get_volume(node_id)
        self.last_known_volume = vol
        self.move_fader(vol)

    def on_pitchbend(self, pb: int):
        if not self.touch_active:
            print(f"[FP] fader {pb} (motor echo, ignoring)")
            return
        vol = pb_to_volume(pb)
        node_id, label = self.current_target()
        print(f"[FP] fader {pb} -> vol {vol:.3f} [{label}]")
        self.last_fader_pb = pb
        self.last_known_volume = vol
        # Dragging the fader while muted = manual override: cancel the mute
        # and take the new position (DAW console behavior). Only once the
        # fader actually moves off bottom — a touch report at 0 (fader is
        # parked there) should NOT drop the mute, or just touching the
        # fader would turn the mute LED off.
        key = self._mute_key(node_id, label)
        if key in self.mute_states and vol > 0.001:
            print(f"[FP] fader moved while muted [{label}] — unmuting")
            self.mute_states.pop(key)
            if key == "__sink__":
                try:
                    if is_muted():
                        set_muted(False)
                except Exception:
                    pass
            self.sync_mute_led()
        set_volume_absolute(vol, node_id)

    def on_cc(self, cc: int, value: int):
        print(f"[FP] cc {cc} = {value}")
        if cc != 16:
            return
        # Encoder protocol (polarity verified on hardware):
        #   bit 6 (0x40) set  = down / counterclockwise (LOWER volume)
        #   bit 6 (0x40) clear = up / clockwise (RAISE volume)
        #   127 = full-rev / position reset marker — skip (no direction)
        if value == 0x7F:
            return
        target = self.config["encoder"]["target"]
        step = self.config["encoder"]["step"]
        node_id = find_app_stream([target]) if target != "master" else None
        if target != "master" and node_id is None:
            print(f"[FP] knob: no {target} stream, skipping")
            return
        label = target
        if value & 0x40:
            self.nudge_target_volume(node_id, -step, label)
        else:
            self.nudge_target_volume(node_id, +step, label)

    # -- actions -----------------------------------------------------------

    def _mute_key(self, node_id, label):
        """Mute-state dict key, unique per AUDIBLE TARGET:

          - no slot selected          -> "master" (the real main sink)
          - slot has a live stream    -> ("app", <stream node id>)
          - slot selected but silent  -> ("slot", <slot name>)

        Old bug: keying by slot NAME collides when a physical slot button is
        itself named "master" (here, a "master" slot routing to zen).
        Muting zen stored the state under "master", and pressing master again
        to return to main ALSO resolved to "master" — so main showed muted.
        Keying by stream node id makes each app's mute unique and never
        aliases the real main sink."""
        if self.active_slot is None:
            return "master"
        if node_id is not None:
            return ("app", node_id)
        return ("slot", self.active_slot)

    def toggle_mute(self, now: float):
        """Fader-mute on the ACTIVE TARGET (DAW console style).

        Mute: remember the current volume, set it to 0, drop the fader.
        Unmute: restore the remembered volume, fly the fader back.
        State is per-target (mute_states keyed by resolved target), so
        each slot remembers its own pre-mute volume."""
        if now - self.last_mute_toggle < self.mute_debounce:
            return
        self.last_mute_toggle = now
        node_id, label = self.current_target()
        key = self._mute_key(node_id, label)
        # A slot with no live stream has no node of its own to mute: the
        # mute is remembered per-slot but never writes to MASTER. Only a
        # real resolved node (or master itself, node_id None + key master)
        # gets audio changes. This stops app-mutes from leaking into main.
        audible = node_id is not None or key == "master"
        if key in self.mute_states:
            # Unmute: restore remembered volume
            vol = self.mute_states.pop(key)
            if audible:
                set_volume_absolute(vol, node_id)
                if key == "__sink__":
                    # Lift any flag-based external mute so sound returns
                    try:
                        if is_muted():
                            set_muted(False)
                    except Exception:
                        pass
                self.last_known_volume = vol
                self.move_fader(vol)
            print(f"[FP] unmuted [{label}] -> {vol if audible else 'n/a':.3f}"
                  if audible else f"[FP] unmuted [{label}] (had no stream)")
        else:
            vol = get_volume(node_id) if audible else None
            self.mute_states[key] = vol
            if audible:
                set_volume_absolute(0.0, node_id)
                self.last_known_volume = 0.0
                self.move_fader(0.0)
            print(f"[FP] muted [{label}] (was {vol if not isinstance(vol, float) else f'{vol:.3f}'})"
                  if vol is not None and isinstance(vol, float)
                  else f"[FP] muted [{label}] (was n/a)")
        self.sync_mute_led()

    def toggle_playback(self):
        subprocess.run(["playerctl", "play-pause"], capture_output=True, timeout=3)

    def stop_playback(self):
        subprocess.run(["playerctl", "stop"], capture_output=True, timeout=3)

    def nudge_volume(self, delta: float):
        node_id, label = self.current_target()
        key = self._mute_key(node_id, label)
        if key in self.mute_states:
            # Nudging a muted target unmutes and goes to the new level
            print(f"[FP] nudge while muted [{label}] — unmuting")
            self.mute_states.pop(key)
            self.sync_mute_led()
        v = get_volume(node_id)
        set_volume_absolute(v + delta, node_id)
        print(f"[FP] nudge {delta:+.2f} -> {v + delta:.3f} [{label}]")

    def nudge_target_volume(self, node_id, delta: float, label: str):
        # If the nudge target is the active slot's muted target, unmute
        # first so the knob lands at the new level (console behavior).
        if self.active_slot is not None:
            cur_id, cur_label = self.current_target()
            if cur_id is not None and cur_id == node_id:
                key = self._mute_key(cur_id, cur_label)
                if key in self.mute_states:
                    print(f"[FP] nudge while muted [{label}] — unmuting")
                    self.mute_states.pop(key)
                    self.sync_mute_led()
        v = get_volume(node_id)
        set_volume_absolute(v + delta, node_id)
        print(f"[FP] nudge {delta:+.2f} -> {v + delta:.3f} [{label}]")

    # -- external volume sync ----------------------------------------------

    def poll_external_volume(self):
        """If the master volume changed externally, move the motor to match.
        Only syncs when no app slot is selected (master mode).
        Also tracks mute state changes to keep the Mute LED in sync."""
        try:
            vol = get_volume()
            muted = is_muted()
        except Exception:
            return
        # Mute LED tracking (works in any mode) — route through sync_mute_led
        # so it ORs the flag with fader-mute state per target.
        if muted != self.last_mute_state:
            self.last_mute_state = muted
            self.sync_mute_led()
        if self.active_slot is not None:
            return
        if self._mute_key(None, "master") in self.mute_states:
            # Fader-muted on master: ENFORCE zero. If an external volume
            # change comes in while muted (media keys etc), re-zero so the
            # mute can't silently leak audio. The remembered restore volume
            # stays untouched. Fader stays parked at bottom.
            if vol > 0.001:
                print(f"[FP] external volume {vol:.3f} while muted — re-muting")
                set_volume_absolute(0.0)
            return
        if self.last_known_volume is None:
            self.last_known_volume = vol
            return
        if abs(vol - self.last_known_volume) < self.MOTOR_HYSTERESIS:
            return
        print(f"[FP] external volume {vol:.3f} (was {self.last_known_volume:.3f})")
        self.last_known_volume = vol
        if self.last_motor_vol is None or abs(vol - self.last_motor_vol) > self.MOTOR_HYSTERESIS:
            self.move_fader(vol)

    # -- watchdog ----------------------------------------------------------

    def reset_device(self):
        now = time.monotonic()
        if now - self.last_reset_time < self.RESET_COOLDOWN_S:
            return
        self.last_reset_time = now
        print("[FP] WATCHDOG: device silent too long, resetting via usbreset...")
        try:
            r = subprocess.run(
                ["usbreset", "194f:0205"], capture_output=True, text=True, timeout=10
            )
            print(f"[FP] usbreset rc={r.returncode}: {r.stdout.strip()} {r.stderr.strip()}")
        except Exception as e:
            print(f"[FP] usbreset failed: {e}")
        self.close()
        time.sleep(2)
        try:
            self.connect()
            self.refresh_slot_leds()
            print("[FP] reconnected after reset")
        except Exception as e:
            print(f"[FP] reconnect failed: {e}")

    # -- main loop ---------------------------------------------------------

    def run(self):
        print("[FP] faderport-volume running (config-driven). Ctrl+C to quit.")
        last_poll = 0.0
        last_config_check = 0.0
        while True:
            try:
                readable, _, _ = select.select([self.fd], [], [], 0.1)
                if readable:
                    data = os.read(self.fd, 4096)
                    if data:
                        self.last_event_time = time.monotonic()
                        self.parse_stream(data)
            except (BlockingIOError, InterruptedError):
                pass
            except OSError as e:
                print(f"[FP] read error: {e} — reconnecting...")
                self.close()
                time.sleep(1)
                try:
                    self.connect()
                except Exception as e2:
                    print(f"[FP] reconnect failed: {e2}")
                    time.sleep(3)

            now = time.monotonic()
            if now - last_config_check > self.CONFIG_POLL_S:
                last_config_check = now
                self.reload_config_if_changed()
            if now - last_poll > self.VOLUME_POLL_S:
                last_poll = now
                self.refresh_stream_ids()
                self.poll_external_volume()

            # Idle watchdog DISABLED (see WATCHDOG_IDLE_S note): the FP2 is
            # silent by design when idle, and resetting on idle silence broke
            # a healthy device. Only the read-error path recovers real lockups.
            if now - self.last_event_time > self.WATCHDOG_IDLE_S:
                self.reset_device()
                self.last_event_time = time.monotonic()


def main():
    # DEFAULT_MIDI_DEV=None → auto-discover the FP2 by identity; argv[1]
    # overrides with an explicit path (useful for testing).
    dev = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MIDI_DEV
    fp = FaderPortVolume(dev)
    try:
        while True:
            try:
                fp.connect()
                break
            except DeviceNotFound as e:
                print(f"[FP] {e}")
                time.sleep(3)
        fp.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"[FP] ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        fp.close()
        print("[FP] exiting")


if __name__ == "__main__":
    main()
