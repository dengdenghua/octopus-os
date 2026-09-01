#!/usr/bin/env python3
"""Minimal native Wayland task window used only by the KWin smoke gate."""

from __future__ import annotations

import os
import sys

if os.environ.get("GDK_BACKEND") != "wayland" or not os.environ.get(
    "WAYLAND_DISPLAY"
):
    print("native Wayland smoke requires GDK_BACKEND and WAYLAND_DISPLAY", file=sys.stderr)
    raise SystemExit(1)

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

window = Gtk.Window(title="Echo Wayland Bridge Smoke")
window.set_default_size(480, 240)
window.connect("destroy", Gtk.main_quit)
button = Gtk.Button(label="Echo Wayland Accessibility Probe")
button.get_accessible().set_name("Echo Wayland Accessibility Probe")
window.add(button)
window.show_all()
print("ECHO_NATIVE_WAYLAND_WINDOW_READY", flush=True)
Gtk.main()
