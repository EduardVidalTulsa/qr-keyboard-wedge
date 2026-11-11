#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Eduard Vidal i Tulsà
#
# Turns a V4L2 webcam into a barcode/QR "keyboard wedge": it decodes codes from the video stream
# (via zbarcam) and types each one, followed by a terminator key, through a virtual keyboard
# device created with /dev/uinput -- exactly what a USB barcode scanner does, just with a webcam
# as the sensor. Whatever application has focus receives the keystrokes normally.
#
# Requires: the `zbar` package (for the `zbarcam` command) and the `evdev` Python module.
# Creating a uinput device needs access to /dev/uinput, which on most distributions means running
# as root (see deploy/qr-keyboard-wedge.service) unless you set up a udev rule yourself to grant a
# group access to it -- be aware that doing so lets any member of that group inject arbitrary
# keystrokes system-wide, not only run this tool.
#
# Key codes are emitted assuming a standard US QWERTY physical layout. What character actually
# shows up depends on the keyboard layout configured in the session that receives the keystrokes;
# if punctuation comes out wrong, that session is using a different layout. Test with a real scan
# before relying on this.
#
# usage: qr_keyboard_wedge.py [--video /dev/video0] [--symbology qrcode] [--terminator enter]
#                              [--prefix STR] [--suffix STR] [--debounce 2.0]
import argparse, glob, logging, subprocess, sys, time
from evdev import UInput, ecodes as e

log = logging.getLogger("qr-keyboard-wedge")

# character -> (KEY_*, needs_shift), standard US QWERTY
CHARMAP = {}
for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    CHARMAP[c] = (getattr(e, f"KEY_{c.upper()}"), False)
    CHARMAP[c.upper()] = (getattr(e, f"KEY_{c.upper()}"), True)
for d, sym in zip("1234567890", "!@#$%^&*()"):
    CHARMAP[d] = (getattr(e, f"KEY_{d}"), False)
    CHARMAP[sym] = (getattr(e, f"KEY_{d}"), True)
CHARMAP.update({
    "-": (e.KEY_MINUS, False), "_": (e.KEY_MINUS, True),
    "=": (e.KEY_EQUAL, False), "+": (e.KEY_EQUAL, True),
    "[": (e.KEY_LEFTBRACE, False), "{": (e.KEY_LEFTBRACE, True),
    "]": (e.KEY_RIGHTBRACE, False), "}": (e.KEY_RIGHTBRACE, True),
    "\\": (e.KEY_BACKSLASH, False), "|": (e.KEY_BACKSLASH, True),
    ";": (e.KEY_SEMICOLON, False), ":": (e.KEY_SEMICOLON, True),
    "'": (e.KEY_APOSTROPHE, False), '"': (e.KEY_APOSTROPHE, True),
    ",": (e.KEY_COMMA, False), "<": (e.KEY_COMMA, True),
    ".": (e.KEY_DOT, False), ">": (e.KEY_DOT, True),
    "/": (e.KEY_SLASH, False), "?": (e.KEY_SLASH, True),
    "`": (e.KEY_GRAVE, False), "~": (e.KEY_GRAVE, True),
    " ": (e.KEY_SPACE, False),
    "\t": (e.KEY_TAB, False),
})
TERMINATORS = {"enter": e.KEY_ENTER, "tab": e.KEY_TAB, "none": None}


def make_uinput(name):
    keys = sorted({code for code, _ in CHARMAP.values()} | {e.KEY_ENTER, e.KEY_TAB, e.KEY_LEFTSHIFT})
    return UInput({e.EV_KEY: keys}, name=name)


def caps_lock_on():
    """True if Caps Lock is on, read from the keyboard LEDs the session keeps in sync.

    A real scanner sends Shift only for uppercase letters, so with Caps Lock on the host
    inverts the case of every letter; QR payloads such as tokens are case sensitive."""
    for path in glob.glob("/sys/class/leds/*::capslock/brightness"):
        try:
            with open(path) as f:
                if int(f.read().strip() or 0) > 0:
                    return True
        except (OSError, ValueError):
            pass
    return False


def type_text(ui, text, key_delay, warned=set()):
    caps = caps_lock_on()
    if caps:
        log.info("Caps Lock is on: inverting Shift for letters")
    for ch in text:
        mapped = CHARMAP.get(ch)
        if mapped is None:
            if ch not in warned:
                log.warning("no key mapping for %r, skipping", ch)
                warned.add(ch)
            continue
        code, shift = mapped
        if caps and ch.isascii() and ch.isalpha():
            shift = not shift
        if shift:
            ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 1)
        ui.write(e.EV_KEY, code, 1)
        ui.write(e.EV_KEY, code, 0)
        if shift:
            ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 0)
        ui.syn()
        time.sleep(key_delay)


def press_key(ui, code, key_delay):
    ui.write(e.EV_KEY, code, 1)
    ui.write(e.EV_KEY, code, 0)
    ui.syn()
    time.sleep(key_delay)


def run_zbarcam(video, symbology, quiet):
    cmd = ["zbarcam", "--raw", "--nodisplay", "--nodbus", "-Sdisable"]
    syms = [s.strip() for s in symbology.split(",")] if symbology != "all" else []
    for s in syms:
        cmd.append(f"-S{s}.enable")
    if not syms:
        cmd = [a for a in cmd if a != "-Sdisable"]  # "all": leave every symbology enabled
    if quiet:
        cmd.append("-q")
    cmd.append(video)
    log.info("starting: %s", " ".join(cmd))
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, bufsize=1)


def main():
    ap = argparse.ArgumentParser(description="Webcam QR/barcode reader that types like a keyboard-wedge scanner")
    ap.add_argument("--video", default="/dev/video0", help="V4L2 device (default /dev/video0)")
    ap.add_argument("--symbology", default="qrcode",
                     help="comma-separated zbar symbologies to enable, or 'all' (default qrcode)")
    ap.add_argument("--terminator", choices=TERMINATORS, default="enter",
                     help="key sent after each code (default enter)")
    ap.add_argument("--prefix", default="", help="text typed before each code")
    ap.add_argument("--suffix", default="", help="text typed after each code, before the terminator")
    ap.add_argument("--debounce", type=float, default=2.0,
                     help="seconds to ignore a repeat of the same code (default 2.0)")
    ap.add_argument("--key-delay", type=float, default=0.004,
                     help="seconds between key events (default 0.004)")
    ap.add_argument("--name", default="QR Webcam Keyboard", help="name of the virtual keyboard device")
    ap.add_argument("--quiet", action="store_true", help="disable zbarcam's beep")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    ui = make_uinput(args.name)
    log.info("virtual keyboard %r ready", args.name)

    last_code, last_time = None, 0.0
    while True:
        proc = run_zbarcam(args.video, args.symbology, args.quiet)
        for line in proc.stdout:
            code = line.rstrip("\n")
            if not code:
                continue
            now = time.monotonic()
            if code == last_code and now - last_time < args.debounce:
                last_time = now
                continue
            last_code, last_time = code, now
            log.info("scanned %r", code)
            type_text(ui, args.prefix + code + args.suffix, args.key_delay)
            term = TERMINATORS[args.terminator]
            if term is not None:
                press_key(ui, term, args.key_delay)
        proc.wait()
        log.warning("zbarcam exited (code %s), restarting in 2s", proc.returncode)
        time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
