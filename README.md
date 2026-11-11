# qr-keyboard-wedge

Turns any V4L2 webcam into a barcode/QR **keyboard wedge**: it decodes codes from the video
stream and types each one into whatever application has focus, followed by a terminator key
(Enter by default) -- the same behaviour as a USB barcode scanner, just using a plain webcam as
the sensor instead of dedicated scanner hardware.

It works by creating a real virtual keyboard device through `/dev/uinput` (the same mechanism
scanner-emulation keyboards use), so to every application it genuinely looks like a second
keyboard, not like synthetic input from an automation tool.

## How it works

- [`zbar`](https://github.com/mchehab/zbar)'s `zbarcam` reads the webcam and decodes symbols; only
  the symbologies you ask for (`qrcode` by default) are enabled, to avoid noise from unrelated
  barcodes.
- Each decoded value is typed through a uinput keyboard device, followed by a terminator key, with
  a short debounce so holding the same code in front of the camera doesn't repeat it.
- If `zbarcam` exits (e.g. the camera is unplugged) it's restarted automatically.

## Requirements

- `zbar` (provides `zbarcam`) -- e.g. `pacman -S zbar`, `apt install zbar-tools`.
- Python with the `evdev` module (`pip install -r requirements.txt`).
- Access to `/dev/uinput`. On most distributions that means running as root. You *can* instead add
  your user to a group that udev grants access to `/dev/uinput`, but that's a bigger grant than it
  sounds: anyone in that group can inject arbitrary keystrokes and mouse events on the whole
  system, not just run this tool. Prefer running this script as a dedicated, narrowly-scoped
  system service (see `deploy/qr-keyboard-wedge.service`).

## Usage

```
python qr_keyboard_wedge.py --video /dev/video0 --symbology qrcode
```

Options:

| Flag | Default | Meaning |
|---|---|---|
| `--video` | `/dev/video0` | V4L2 device to read |
| `--symbology` | `qrcode` | comma-separated zbar symbologies to enable, or `all` |
| `--terminator` | `enter` | key sent after each code: `enter`, `tab` or `none` |
| `--prefix` / `--suffix` | empty | text typed around each code |
| `--debounce` | `2.0` | seconds to ignore a repeat of the same code |
| `--key-delay` | `0.004` | seconds between key events |
| `--name` | `QR Webcam Keyboard` | name of the virtual keyboard device |
| `--quiet` | off | disable zbarcam's beep |

## Keyboard layout caveat

Key codes are emitted assuming a standard US QWERTY physical layout (letters, digits, and the
usual US punctuation positions). What character actually appears depends on the keyboard layout
configured in the session that receives the keystrokes. Letters and digits are the same on nearly
every layout, but punctuation (`: / . ? & = -` and friends, common in URLs) can land on different
keys on non-US layouts. **Test with a real scan before relying on this** -- if punctuation comes
out wrong, your session is using a different layout, and you'll need to either switch it or adjust
`CHARMAP` in the script.

## License

GPL-3.0-or-later, see `LICENSE`.

## Caps Lock

A hardware scanner sends Shift only for uppercase characters, so with Caps Lock on the host
inverts the case of every letter, which breaks case-sensitive payloads such as URL tokens.
Before typing each code the wedge reads the Caps Lock LED state from
`/sys/class/leds/*::capslock/brightness` and, when it is on, inverts Shift for letters so the
text arrives exactly as encoded.
