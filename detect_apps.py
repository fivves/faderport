#!/usr/bin/env python3
"""List apps currently making sound (PipeWire output streams) for the
FaderPort assign panel. Prints one line per app: displayLabel<TAB>matchValue

The matchValue is the ROOT process name of the stream's process tree
(walked up to the top ancestor), because that's exactly what the daemon's
find_app_stream matches against. displayLabel is the friendly
application.name (e.g. "Zen", "Discord", "gtunes") — but for Chromium-based
apps the label may be generic, so the value is what actually lands in
mappings.json slots.
"""
import json
import os
import subprocess
import sys


def root_process_name(pid):
    """Walk the process tree from pid up to the root; return the top
    ancestor's comm. Matches the daemon's process_is_app logic."""
    comm = ""
    seen = set()
    while pid and pid not in seen and len(seen) < 20:
        seen.add(pid)
        try:
            with open(f"/proc/{pid}/comm") as f:
                comm = f.read().strip()
            with open(f"/proc/{pid}/stat") as f:
                parts = f.read().rsplit(")", 1)[1].split()
            ppid = int(parts[1])
            if ppid == pid or ppid == 0 or ppid in seen:
                return comm or "unknown"
            pid = ppid
        except (OSError, ValueError, IndexError):
            return comm or "unknown"
    return comm or "unknown"


def main():
    try:
        r = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=10)
        data = json.loads(r.stdout)
    except Exception as e:
        print(f"pw-dump failed: {e}", file=sys.stderr)
        return

    apps = {}  # matchValue -> displayLabel
    for obj in data:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        p = obj.get("info", {}).get("props", {})
        mc = p.get("media.class", "")
        if "Stream/Output" not in mc:
            continue
        name = p.get("application.name") or p.get("node.name") or "unknown"
        node_name = p.get("node.name") or ""
        # Skip virtual loopback/split streams and ALSA sinks — not apps.
        if "sink.split" in node_name or ".monitor" in node_name:
            continue
        if node_name.startswith("alsa_output") or node_name.startswith("alsa_input"):
            continue
        # Skip the shell/daemon itself and pipewire internals.
        low = name.lower()
        if any(x in low for x in ("quickshell", "omarchy", "pipewire", "wireplumber")):
            continue

        pid = p.get("application.process.id")
        if pid:
            value = root_process_name(pid)
            # If the walk landed on systemd/init (app launched via a
            # service wrapper), fall back to the direct comm or binary.
            if value in ("systemd", "init", "1"):
                try:
                    with open(f"/proc/{pid}/comm") as f:
                        direct = f.read().strip()
                    if direct and direct not in ("systemd", "init"):
                        value = direct
                except OSError:
                    pass
        else:
            # Some apps (mpv) report streams with pid=None — fall back to
            # the binary basename, then the node name.
            binary = p.get("application.process.binary") or ""
            value = os.path.basename(binary) if binary else name
        key = value.lower()
        if key not in apps:
            apps[key] = (name, value)

    for label, value in sorted(apps.values(), key=lambda t: t[1].lower()):
        print(f"{label}\t{value}")


if __name__ == "__main__":
    main()
