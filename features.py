"""Things Jarvis can do on your PC besides talking: the weather, reminders, memory, PC controls,
the clipboard and screenshots. main.py offers these to the AI as tools."""
import base64
import ctypes
import io
import json
import os
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

from PIL import ImageGrab

HERE = os.path.dirname(os.path.abspath(__file__))

# Your memories and reminders are saved next to main.py (and .gitignore keeps them off GitHub)
MEMORY_FILE = os.path.join(HERE, "memory.json")
REMINDERS_FILE = os.path.join(HERE, "reminders.json")


def load(path):
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return []


def save(path, data):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Jarvis"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


# ---------- Weather (from Open-Meteo: free, no key needed) ----------

# Open-Meteo describes the sky with a number; these are the words for them
SKY = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "foggy", 48: "foggy",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers",
       81: "rain showers", 82: "heavy rain showers", 85: "snow showers", 86: "snow showers",
       95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail"}


places = {}  # cities already looked up, so asking about them again is quicker


def get_weather(city):
    """The weather now and for the next 3 days in a city, as text for the AI."""
    if city.lower() not in places:
        found = get_json("https://geocoding-api.open-meteo.com/v1/search?count=1&name=" + urllib.parse.quote(city))

        if not found.get("results"):
            return f"No place called {city} was found."

        places[city.lower()] = found["results"][0]

    place = places[city.lower()]
    weather = get_json(
        f"https://api.open-meteo.com/v1/forecast?latitude={place['latitude']}&longitude={place['longitude']}"
        "&current=temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        "&timezone=auto&forecast_days=3"
    )
    now, days = weather["current"], weather["daily"]

    return json.dumps({
        "place": f"{place['name']}, {place.get('country', '')}",
        "now": {"sky": SKY.get(now["weather_code"], "unknown"), "temperature_c": now["temperature_2m"],
                "feels_like_c": now["apparent_temperature"], "humidity_percent": now["relative_humidity_2m"],
                "wind_kmh": now["wind_speed_10m"]},
        "next_days": [{"date": date, "sky": SKY.get(code, "unknown"), "high_c": high, "low_c": low,
                       "rain_chance_percent": rain}
                      for date, code, high, low, rain in zip(days["time"], days["weather_code"],
                                                             days["temperature_2m_max"], days["temperature_2m_min"],
                                                             days["precipitation_probability_max"])],
    })


# ---------- Reminders ----------

reminders = load(REMINDERS_FILE)  # [{"due": seconds since 1970, "message": "..."}]
reminders_lock = threading.Lock()  # reminders are added and checked from different threads


def add_reminder(minutes, message):
    with reminders_lock:
        reminders.append({"due": time.time() + float(minutes) * 60, "message": message})
        save(REMINDERS_FILE, reminders)


def cancel_reminders():
    with reminders_lock:
        reminders.clear()
        save(REMINDERS_FILE, reminders)


def due_reminders():
    """Take out the reminders whose time has come, and return what they say."""
    with reminders_lock:
        now = time.time()
        due = [reminder["message"] for reminder in reminders if reminder["due"] <= now]

        if due:
            reminders[:] = [reminder for reminder in reminders if reminder["due"] > now]
            save(REMINDERS_FILE, reminders)

    return due


def upcoming_reminders():
    """The reminders still to come, as text for the AI."""
    with reminders_lock:
        return [f"{datetime.fromtimestamp(reminder['due']):%A %d %B %I:%M %p}: {reminder['message']}"
                for reminder in sorted(reminders, key=lambda reminder: reminder["due"])]


# ---------- Memory ----------

memories = load(MEMORY_FILE)  # [{"fact": "...", "saved": "2026-10-02"}]


def remember(fact):
    memories.append({"fact": fact, "saved": f"{datetime.now():%Y-%m-%d}"})
    save(MEMORY_FILE, memories)


def forget(fact):
    """Forget the memories that match `fact`. Returns how many were forgotten."""
    keep = [memory for memory in memories
            if fact.lower() not in memory["fact"].lower() and memory["fact"].lower() not in fact.lower()]
    forgotten = len(memories) - len(keep)
    memories[:] = keep
    save(MEMORY_FILE, memories)
    return forgotten


# ---------- PC controls ----------

# The volume and music keys found on many keyboards
MEDIA_KEYS = {"volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD,
              "play_pause": 0xB3, "next_track": 0xB0, "previous_track": 0xB1}

PC_ACTIONS = list(MEDIA_KEYS) + ["brightness_up", "brightness_down", "lock", "screenshot",
                                 "shutdown", "restart", "cancel_shutdown"]


def press(key, times=1):
    """Press a key, like the volume and music keys on a keyboard."""
    for _ in range(times):
        ctypes.windll.user32.keybd_event(key, 0, 0, 0)
        ctypes.windll.user32.keybd_event(key, 0, 2, 0)  # 2 = let go of the key


def control_pc(action):
    """Do one of PC_ACTIONS. Shutting down or restarting waits a minute, so it can still be cancelled."""
    if action in ("volume_up", "volume_down"):
        press(MEDIA_KEYS[action], 5)  # each press is about 2%
    elif action in MEDIA_KEYS:
        press(MEDIA_KEYS[action])
    elif action == "lock":
        ctypes.windll.user32.LockWorkStation()
    elif action in ("brightness_up", "brightness_down"):
        change_brightness(20 if action == "brightness_up" else -20)
    elif action == "screenshot":
        take_screenshot()
    elif action == "shutdown":
        subprocess.run(["shutdown", "/s", "/t", "60"], check=True)
    elif action == "restart":
        subprocess.run(["shutdown", "/r", "/t", "60"], check=True)
    elif action == "cancel_shutdown":
        subprocess.run(["shutdown", "/a"], check=True)
    else:
        raise ValueError("unknown PC action: " + action)


def change_brightness(step):
    """Make a laptop screen brighter or darker (most separate monitors can't be changed this way)."""
    script = (
        "$now = (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness; "
        f"$new = [Math]::Max(0, [Math]::Min(100, $now + {step})); "
        "Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | "
        "Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{Timeout = 1; Brightness = [byte]$new}"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True, timeout=20)


def take_screenshot():
    """Save a picture of the screen in Pictures/Screenshots, and return where it went."""
    folder = os.path.join(os.path.expanduser("~"), "Pictures", "Screenshots")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"Jarvis {datetime.now():%Y-%m-%d %H-%M-%S}.png")
    ImageGrab.grab(all_screens=True).save(path)
    return path


class PowerStatus(ctypes.Structure):
    """What Windows reports about the battery (SYSTEM_POWER_STATUS)."""
    _fields_ = [("plugged_in", ctypes.c_ubyte), ("battery_flag", ctypes.c_ubyte), ("percent", ctypes.c_ubyte),
                ("saver", ctypes.c_ubyte), ("seconds_left", ctypes.c_ulong), ("seconds_when_full", ctypes.c_ulong)]


def battery_status():
    """The battery level, whether it's charging and the time left, as text for the AI."""
    status = PowerStatus()
    ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status))

    if status.battery_flag == 128 or status.percent == 255:
        return "This PC has no battery; it runs on mains power."

    return json.dumps({
        "battery_percent": status.percent,
        "plugged_in": status.plugged_in == 1,
        "minutes_left": status.seconds_left // 60 if status.seconds_left != 0xFFFFFFFF else "unknown",
    })


# ---------- Clipboard and typing ----------

def get_clipboard():
    """The text you've copied, or "" if what's copied isn't text."""
    import win32clipboard  # comes with pywin32, which pyttsx3 installs

    win32clipboard.OpenClipboard()
    try:
        return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
    except Exception:
        return ""
    finally:
        win32clipboard.CloseClipboard()


def set_clipboard(text):
    import win32clipboard

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def read_clipboard():
    """The text you've copied (up to 6000 characters), for the AI to read."""
    return get_clipboard()[:6000] or "There's no text on the clipboard."


def type_text(text):
    """Type text into the window you're using, by pasting it. Your clipboard is put back afterwards."""
    old = get_clipboard()
    set_clipboard(text)
    ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)  # hold Ctrl...
    press(0x56)                                       # ...press V...
    ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)  # ...and let go of Ctrl
    time.sleep(0.5)  # give the app a moment to paste before the clipboard changes back
    set_clipboard(old)


# ---------- Screen ----------

def screen_for_ai():
    """A smaller JPEG picture of the main screen, as base64 text for sending to the AI."""
    picture = ImageGrab.grab()
    picture.thumbnail((1600, 1600))
    buffer = io.BytesIO()
    picture.convert("RGB").save(buffer, "JPEG", quality=80)
    return base64.b64encode(buffer.getvalue()).decode()
