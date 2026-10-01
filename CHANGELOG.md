# Changelog

All notable changes to this project are documented here.

## [1.0.0] - 2026-10-01

Initial release.

- Bar widget (`kind: bar-widget`) with an assign panel for the FaderPort FP2
- Ten physical buttons assignable to mute toggle, play/pause, stop, next track,
  previous track, volume up, volume down, or nothing
- Eight app slots whose buttons route the fader to a single app's stream
- **Detect playing apps** picker fills a slot from apps currently producing sound
- Encoder assigned to an app slot with a configurable step
- Atomic writes to `~/.config/faderport/mappings.json`, applied live by the daemon
- Companion daemon: bidirectional fader control over PipeWire, per-target fader
  mute with remembered volume, process-tree app matching, and auto-reconnect
- ALSA card auto-discovery through `/dev/snd/by-id`, so the daemon survives
  reboots that shuffle card numbers
