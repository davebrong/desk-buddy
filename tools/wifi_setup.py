"""Put Lump on your Wi-Fi.

Run it yourself with the board plugged into the Mac by USB (data cable):
    ~/.desk-buddy/venv/bin/python tools/wifi_setup.py          # set up / change network
    ~/.desk-buddy/venv/bin/python tools/wifi_setup.py --forget # turn Wi-Fi off again
On Windows: ~/.desk-buddy/venv/Scripts/python tools/wifi_setup.py

It asks for the network name and password (the password isn't shown, logged or saved on the Mac:
it goes straight over USB into the board's own flash), makes a pairing token so only your
bridge can drive him over the network, and tells him your time zone so he can keep time from
the internet. The token goes into ~/.desk-buddy/config.json for the bridge.
The ESP32 only does 2.4 GHz Wi-Fi.
"""
import getpass, glob, json, os, re, secrets, subprocess, sys, time

import serial

HOME = os.path.expanduser("~/.desk-buddy")
CONFIG = os.path.join(HOME, "config.json")
BRIDGE = "com.deskbuddy.bridge"
BRIDGE_PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{BRIDGE}.plist")
WINDOWS = sys.platform == "win32"
WIN_TASK = "DeskBuddyBridge"                 # tools/install_bridge.ps1
USB_VIDS = {0x1A86, 0x303A}                  # WCH CH343, Espressif USB


def usb_ports():
    if WINDOWS:
        from serial.tools import list_ports
        return sorted(p.device for p in list_ports.comports() if p.vid in USB_VIDS)
    return sorted(glob.glob("/dev/cu.usbmodem*"))


def bridge(run):
    """Pause (run=False) or restart the bridge, which holds the USB port."""
    if WINDOWS:
        subprocess.run(["schtasks", "/Run" if run else "/End", "/TN", WIN_TASK], capture_output=True)
    elif run:
        bridge(True)
    else:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{BRIDGE}"], capture_output=True)


def posix_tz():
    """Your time zone as a POSIX rule (e.g. EST5EDT,M3.2.0,M11.1.0), from the footer of
    /etc/localtime, which is what the ESP32's clock understands. Windows has no /etc/localtime:
    there the zone's name comes from tzlocal and its TZif file from the tzdata package."""
    try:
        if WINDOWS:
            import importlib.resources, tzlocal
            name = tzlocal.get_localzone_name()
            data = importlib.resources.files("tzdata.zoneinfo").joinpath(*name.split("/")).read_bytes()
        else:
            data = open("/etc/localtime", "rb").read()
        footer = data.rstrip(b"\n").rsplit(b"\n", 1)[-1].decode()
        return footer if re.fullmatch(r"[A-Za-z<>+\-0-9,./:]+", footer) else ""
    except Exception:
        return ""


def current_ssid():
    try:
        if WINDOWS:
            out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True, timeout=3).stdout
            m = re.search(r"^\s*SSID\s*: (.+)$", out, re.M)
            return m.group(1).strip() if m else ""
        out = subprocess.run(["ipconfig", "getsummary", "en0"], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"^\s*SSID : (.+)$", out, re.M)
        return m.group(1).strip() if m and "redacted" not in m.group(1) else ""
    except Exception:
        return ""


def read_lines(ser, until, secs):
    """Print the board's lines until one contains `until` (or time runs out); return it."""
    end = time.time() + secs
    buf = b""
    while time.time() < end:
        buf += ser.read(256)
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            text = line.decode(errors="replace").strip()
            if text and not text.startswith(("ok mode", "ESP-ROM", "Build:", "rst:", "SPIWP", "mode:", "load:", "entry")):
                print("  board:", text)
            if until in text:
                return text
    return None


def main():
    forget = "--forget" in sys.argv
    ports = usb_ports()
    if not ports:
        sys.exit("Lump isn't plugged in by USB (needs a data cable).")
    cfg = {}
    if os.path.exists(CONFIG):
        with open(CONFIG) as f:
            cfg = json.load(f)

    if forget:
        line = "w"
    else:
        default = current_ssid()
        ssid = input(f"Wi-Fi network name{f' [{default}]' if default else ''}: ").strip() or default
        if not ssid:
            sys.exit("No network name given.")
        password = getpass.getpass(f"Password for {ssid} (not shown): ")
        if "\t" in ssid + password or "\n" in ssid + password:
            sys.exit("Tabs and newlines can't be sent; sorry.")
        token = cfg.get("lump_token") or secrets.token_hex(12)
        tz = posix_tz()
        line = f"w {ssid}\t{password}\t{token}\t{tz}"

    # the bridge holds the USB port: pause it while we talk to the board
    bridge(False)
    time.sleep(1)
    try:
        ser = serial.Serial(); ser.port, ser.baudrate, ser.timeout = ports[0], 115200, 0.3
        ser.dtr = False; ser.rts = False
        ser.open()
        read_lines(ser, "lump ready", 4)          # opening the port may restart him: let him boot
        ser.reset_input_buffer()
        ser.write((line + "\n").encode()); ser.flush()
        del line                                    # don't keep the password around
        if not read_lines(ser, "restarting", 5):
            sys.exit("The board didn't confirm. Is the new firmware flashed (tools/flash.sh)?")
        if forget:
            cfg.pop("lump_token", None)
        else:
            cfg["lump_token"] = token
            cfg.setdefault("lump_host", "lump.local")
            os.makedirs(HOME, exist_ok=True)
        with open(CONFIG + ".tmp", "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(CONFIG + ".tmp", CONFIG)
        if forget:
            print("Wi-Fi forgotten.")
            return
        read_lines(ser, "lump ready", 8)
        print("Joining the network...")
        status = None
        for _ in range(20):
            ser.write(b"W\n"); ser.flush()
            status = read_lines(ser, "ok wifi=", 2)
            if status and "wifi=connected" in status:
                break
            time.sleep(1)
        ser.close()
        if status and "wifi=connected" in status:
            print("\nLump is on Wi-Fi. Unplug him from the computer and power him from any USB charger:")
            print("the bridge will find him at lump.local within a few seconds.")
        else:
            print("\nHe saved the details but hasn't joined yet. Check the network name and password")
            print("(and that it's a 2.4 GHz network), then run this again.")
    finally:
        bridge(True)


if __name__ == "__main__":
    main()
