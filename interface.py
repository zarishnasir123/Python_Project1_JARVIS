"""Jarvis's window: a glowing orb that shows what Jarvis is doing, a Chats page with the
conversation, a microphone button, and a box to type commands.

main.py runs Jarvis's listening and talking in a background thread. It updates the window with
set_state(), add_message(), speaking() and hide_for_screenshot(), and checks `typed`,
`stop_requested` and `muted` for what you type or click. The window itself runs on the main
thread, because tkinter needs that."""
import array
import ctypes
import math
import os
import queue
import random
import signal
import tempfile
import threading
import time
import tkinter as tk
import wave

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageTk

updates = queue.Queue()              # from Jarvis to the window
typed = queue.Queue()                # commands typed in the window
stop_requested = threading.Event()   # Esc, or a click on the orb, while Jarvis is talking
muted = threading.Event()            # the microphone button is off

window_open = False

BLACK = "#000000"
ORB_FRAMES = 72  # pictures in one loop of the orb's animation

# The orb is drawn in brightness only; these turn brightness into colour: (brightness, colour) pairs
PALETTES = {
    "blue": [(0, "#000000"), (30, "#020b2b"), (70, "#06235f"), (120, "#0b5fc4"), (175, "#13b5ff"), (225, "#8eecff"), (255, "#ffffff")],
    "violet": [(0, "#000000"), (30, "#0c0526"), (70, "#24125e"), (120, "#5a35c9"), (175, "#9b7bff"), (225, "#d9ccff"), (255, "#ffffff")],
    "grey": [(0, "#000000"), (30, "#0b0d10"), (70, "#1d2329"), (120, "#3d4852"), (175, "#6b7884"), (225, "#a9b3bc"), (255, "#e5e7eb")],
}

# How each state looks: status text, palette, how fast the orb moves, and how bright it is
STATES = {
    "waiting": ("Available...", "blue", 0.5, 1.0),
    "listening": ("Listening...", "blue", 1.0, 1.2),
    "recognizing": ("Recognizing...", "blue", 1.5, 1.2),
    "thinking": ("Thinking...", "violet", 2.0, 1.1),
    "speaking": ("Speaking...", "blue", 1.0, 1.2),
    "muted": ("Microphone off", "grey", 0.25, 0.9),
}


def set_state(state):
    """Show what Jarvis is doing: "waiting", "listening", "recognizing", "thinking" or "speaking"."""
    if window_open:
        updates.put(("state", state))


def add_message(who, text):
    """Add a message to the chat. `who` is "You" or "Jarvis"."""
    if window_open:
        updates.put(("message", who, text))


def speaking(sound_file):
    """Make the orb pulse with Jarvis's voice while this WAV file plays."""
    if window_open:
        updates.put(("speaking", loudness(sound_file), time.time()))


def hide_for_screenshot(hidden):
    """Minimise the window (True) so it isn't in a screenshot, or bring it back (False)."""
    if window_open:
        updates.put(("hidden", hidden))
        time.sleep(0.5 if hidden else 0)  # give Windows a moment to hide it


def loudness(sound_file):
    """How loud the sound is in each 50 ms, from 0 to 1."""
    with wave.open(sound_file) as wav:
        step = wav.getframerate() // 20
        samples = array.array("h", wav.readframes(wav.getnframes()))

    levels = [max(map(abs, samples[i:i + step])) for i in range(0, len(samples), step)]
    loudest = max(levels, default=0) or 1
    return [level / loudest for level in levels]


def without_emoji(text):
    """tkinter can't show characters like emoji (beyond U+FFFF), so leave them out."""
    return "".join(letter for letter in text if ord(letter) <= 0xFFFF)


def colour_table(palette, brightness):
    """A lookup table that turns the orb's brightness (0-255) into red, green and blue."""
    stops = [(level, [int(colour[i:i + 2], 16) for i in (1, 3, 5)]) for level, colour in PALETTES[palette]]
    table = [[], [], []]

    for value in range(256):
        value = min(255, value * brightness)
        (low, a), (high, b) = next((s, e) for s, e in zip(stops, stops[1:]) if value <= e[0])
        mix = (value - low) / (high - low)
        for channel in range(3):
            table[channel].append(round(a[channel] + (b[channel] - a[channel]) * mix))

    return table[0] + table[1] + table[2]


def draw_orb(size, t):
    """Draw one frame of the orb (brightness only). `t` goes from 0 to 1 over one loop.
    The plasma arcs and sparkles turn a whole number of times per loop, so the loop is seamless."""
    rnd = random.Random(7)  # the same "random" details in every frame
    c, R = size / 2, size * 0.33

    def box(r):
        return (c - r, c - r, c + r, c + r)

    # The dark inner sphere, a little brighter towards its edge...
    sphere = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(sphere)
    for i in range(48):
        f = i / 48
        draw.ellipse(box(R * 0.96 * (1 - f)), fill=int(28 + 44 * (1 - f) ** 3))

    # ...with a soft, lighter crescent near the bottom
    crescent = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(crescent)
    draw.ellipse((c - R * 0.78, c - R * 0.45, c + R * 0.78, c + R * 0.9), fill=115)
    draw.ellipse((c - R * 0.86, c - R * 0.72, c + R * 0.86, c + R * 0.66), fill=0)
    crescent = crescent.rotate(12 * math.sin(2 * math.pi * t), center=(c, c))
    sphere = ImageChops.lighter(sphere, crescent.filter(ImageFilter.GaussianBlur(R * 0.09)))

    # The bright ring: a base circle plus moving arcs of "plasma", and faint wisps outside it
    ring = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(ring)
    draw.ellipse(box(R), outline=140, width=max(2, int(R * 0.035)))

    for _ in range(14):
        start = rnd.uniform(0, 360) + 360 * rnd.choice([1, 1, 2, -1, -2, 3]) * t
        r = R * (1 + rnd.uniform(-0.05, 0.05))
        draw.arc(box(r), start, start + rnd.uniform(30, 160), fill=rnd.randint(150, 255),
                 width=rnd.randint(1, max(2, int(R * 0.05))))

    for _ in range(10):
        start = rnd.uniform(0, 360) + 360 * rnd.choice([1, -1, 2]) * t
        draw.arc(box(R * rnd.uniform(1.04, 1.16)), start, start + rnd.uniform(20, 70),
                 fill=rnd.randint(50, 110), width=rnd.randint(1, 3))

    # Sparkles drifting around the ring
    sparks = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(sparks)
    for _ in range(60):
        angle = math.radians(rnd.uniform(0, 360) + 360 * rnd.choice([1, -1, 2, -2]) * t)
        r = R * rnd.uniform(0.92, 1.22)
        x, y, s = c + r * math.cos(angle), c + r * math.sin(angle), rnd.uniform(0.6, 2.2) * size / 400
        twinkle = 0.6 + 0.4 * math.sin(2 * math.pi * (t * rnd.choice([1, 2, 3]) + rnd.random()))
        draw.ellipse((x - s, y - s, x + s, y + s), fill=int(rnd.randint(120, 255) * twinkle))

    # Glow: blurred copies of the ring, from a wide soft halo to a sharp bright line
    layers = [
        (ring.filter(ImageFilter.GaussianBlur(R * 0.16)), 1.6),
        (ring.filter(ImageFilter.GaussianBlur(R * 0.05)), 1.15),
        (ring.filter(ImageFilter.GaussianBlur(max(1, R * 0.008))), 1.0),
        (sparks.filter(ImageFilter.GaussianBlur(max(1, R * 0.01))), 1.0),
    ]
    image = sphere
    for layer, gain in layers:
        image = ImageChops.add(image, layer.point(lambda v, g=gain: min(255, int(v * g))))

    return image


def orb_frames(size, frames):
    """Fill `frames` with the orb's animation, loading it from disk if it was drawn before
    (drawing it the first time takes a few seconds, so it's done in the background)."""
    folder = os.path.join(tempfile.gettempdir(), f"jarvis_orb_{size}_v1")
    os.makedirs(folder, exist_ok=True)

    for i in range(ORB_FRAMES):
        path = os.path.join(folder, f"{i:02}.png")
        try:
            frames[i] = Image.open(path).copy()
        except OSError:
            frames[i] = draw_orb(size, i / ORB_FRAMES)
            frames[i].save(path)


class Window:
    def __init__(self):
        global window_open

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text and lines on high-resolution screens
        except Exception:
            pass

        self.root = tk.Tk()
        self.scale = self.root.winfo_fpixels("1i") / 96  # how much Windows scales things up on this screen
        self.root.title("Jarvis AI")
        self.root.configure(bg=BLACK)
        self.root.bind("<Escape>", lambda event: stop_requested.set())

        # Fit the window on the screen, leaving room for the taskbar
        width = min(self.px(1000), self.root.winfo_screenwidth() - self.px(80))
        height = min(self.px(700), self.root.winfo_screenheight() - self.px(110))
        self.root.geometry(f"{width}x{height}+{(self.root.winfo_screenwidth() - width) // 2}+{self.px(20)}")
        self.root.minsize(self.px(620), self.px(560))

        self.state = "waiting"
        self.position = 0.0      # where the orb's animation is
        self.voice = []          # how loud each 50 ms of the sentence being spoken is
        self.voice_start = 0.0
        self.tables = {}         # colour lookup tables, made as they're needed

        # The orb's frames are drawn (or loaded) in the background; until then it isn't shown
        self.orb_size = min(self.px(400), height - self.px(270))
        self.frames = [None] * ORB_FRAMES
        threading.Thread(target=orb_frames, args=(self.orb_size, self.frames), daemon=True).start()

        self.build_tabs()
        self.build_home()
        self.build_chats()
        self.show_page("home")
        self.show_state("waiting")
        window_open = True

    def px(self, size):
        """Turn a size meant for a normal screen into pixels on this one."""
        return round(size * self.scale)

    def build_tabs(self):
        bar = tk.Frame(self.root, bg=BLACK)
        bar.pack(fill="x", pady=(self.px(12), 0))
        tabs = tk.Frame(bar, bg=BLACK)
        tabs.pack()
        self.tabs = {name: self.tab_button(tabs, text, name) for name, text in [("home", "⌂  Home"), ("chats", "☰  Chats")]}

    def tab_button(self, parent, text, page):
        button = tk.Button(parent, text=text, command=lambda: self.show_page(page), relief="flat", bd=0,
                           font=("Segoe UI", 10), padx=self.px(16), pady=self.px(5), cursor="hand2",
                           activebackground="#1f2937", activeforeground="#ffffff")
        button.pack(side="left", padx=self.px(4))
        return button

    def build_home(self):
        self.home = tk.Frame(self.root, bg=BLACK)

        self.orb = tk.Label(self.home, bg=BLACK, cursor="hand2")
        self.orb.pack(pady=(self.px(8), 0))
        self.orb.bind("<Button-1>", lambda event: stop_requested.set())  # click the orb to stop Jarvis talking
        self.photo = ImageTk.PhotoImage(Image.new("RGB", (self.orb_size, self.orb_size)))
        self.orb.configure(image=self.photo)

        self.status = tk.Label(self.home, bg=BLACK, fg="#e6f4ff", font=("Segoe UI", 12))
        self.status.pack(pady=(self.px(4), 0))

        self.caption = tk.Label(self.home, bg=BLACK, fg="#6f8aa3", font=("Segoe UI", 10),
                                wraplength=self.px(620), justify="center")
        self.caption.pack(pady=(self.px(4), self.px(8)))

        size = self.px(64)
        self.mic = tk.Canvas(self.home, width=size, height=size, bg=BLACK, highlightthickness=0, cursor="hand2")
        self.mic.pack()
        self.mic.bind("<Button-1>", lambda event: self.toggle_mute())
        self.mic.bind("<Enter>", lambda event: self.draw_mic(hover=True))
        self.mic.bind("<Leave>", lambda event: self.draw_mic())
        self.draw_mic()

    def draw_mic(self, hover=False):
        """The round microphone button: blue when Jarvis is listening, grey and crossed out when muted."""
        c, s, px = self.mic, self.px(64), self.px
        c.delete("all")
        off = muted.is_set()
        c.create_oval(px(4), px(4), s - px(4), s - px(4), outline="",
                      fill=("#4b5563" if hover else "#374151") if off else ("#5b9bff" if hover else "#3b82f6"))

        # The microphone: a rounded capsule, a cradle under it and a stand
        x, top, w, h = s / 2, px(17), px(11), px(19)
        c.create_oval(x - w / 2, top, x + w / 2, top + w, fill="white", outline="")
        c.create_rectangle(x - w / 2, top + w / 2, x + w / 2, top + h - w / 2, fill="white", outline="")
        c.create_oval(x - w / 2, top + h - w, x + w / 2, top + h, fill="white", outline="")
        c.create_arc(x - px(11), top + px(2), x + px(11), top + h + px(5), start=180, extent=180,
                     style=tk.ARC, outline="white", width=px(2))
        c.create_line(x, top + h + px(5), x, top + h + px(10), fill="white", width=px(2))

        if off:
            c.create_line(px(19), px(19), s - px(19), s - px(19), fill="white", width=px(3))

    def build_chats(self):
        self.chats = tk.Frame(self.root, bg=BLACK)

        # The typing bar goes in first, so it stays visible and the chat shrinks if space is short
        bar = tk.Frame(self.chats, bg=BLACK)
        bar.pack(side="bottom", fill="x", padx=self.px(28), pady=(self.px(10), self.px(18)))

        self.entry = tk.Entry(bar, bg="#111827", fg="#f3f4f6", insertbackground="#3b82f6", relief="flat",
                              font=("Segoe UI", 11))
        self.entry.pack(side="left", fill="x", expand=True, ipady=self.px(9), padx=(0, self.px(10)))
        self.entry.bind("<Return>", lambda event: self.send())

        tk.Button(bar, text="Send", command=self.send, bg="#3b82f6", fg="white", activebackground="#5b9bff",
                  activeforeground="white", relief="flat", bd=0, font=("Segoe UI", 10, "bold"),
                  padx=self.px(18), pady=self.px(7), cursor="hand2").pack(side="left")

        self.chat = tk.Text(self.chats, bg=BLACK, fg="#e5e7eb", font=("Segoe UI", 11), wrap="word", relief="flat",
                            padx=self.px(10), pady=self.px(10), state="disabled", cursor="arrow")
        self.chat.pack(fill="both", expand=True, padx=self.px(28), pady=(self.px(14), 0))

        # Your messages go on the right, Jarvis's on the left
        self.chat.tag_configure("You", justify="right", spacing3=self.px(10))
        self.chat.tag_configure("Jarvis", justify="left", spacing3=self.px(10))

    def show_page(self, page):
        for name, frame in [("home", self.home), ("chats", self.chats)]:
            frame.pack_forget()
            self.tabs[name].configure(bg="#1f2937" if name == page else "#0b0f14",
                                      fg="#ffffff" if name == page else "#9ca3af")

        (self.home if page == "home" else self.chats).pack(fill="both", expand=True)

        if page == "chats":
            self.entry.focus_set()

    def send(self):
        text = self.entry.get().strip()

        if text:
            typed.put(text)
            self.entry.delete(0, "end")

    def toggle_mute(self):
        if muted.is_set():
            muted.clear()
        else:
            muted.set()

        self.draw_mic(hover=True)
        self.show_state(self.state)

    def show_state(self, state):
        self.state = state
        self.status.configure(text=STATES["muted" if muted.is_set() else state][0])

    def add_line(self, who, text):
        """Add a chat bubble (blue for you, dark grey for Jarvis), and show the text under the orb."""
        text = without_emoji(text)
        bubble = tk.Label(self.chat, text=text, wraplength=self.px(520), justify="left", font=("Segoe UI", 11),
                          bg="#1d4ed8" if who == "You" else "#111827", fg="#ffffff" if who == "You" else "#dbeafe",
                          padx=self.px(14), pady=self.px(8))
        bubble.bind("<MouseWheel>", lambda event: self.chat.yview_scroll(-event.delta // 120, "units"))

        self.chat.configure(state="normal")
        start = self.chat.index("end-1c")
        self.chat.window_create("end", window=bubble)
        self.chat.insert("end", "\n")
        self.chat.tag_add(who, start, "end-1c")  # puts the bubble on the right or the left
        self.chat.configure(state="disabled")
        self.chat.see("end")

        self.caption.configure(text=text if len(text) <= 160 else text[:157] + "...")

    def animate(self):
        """Draw the next frame (about 30 a second), after applying any updates from Jarvis."""
        while not updates.empty():
            kind, *details = updates.get()

            if kind == "state":
                self.show_state(details[0])
            elif kind == "message":
                self.add_line(*details)
            elif kind == "speaking":
                self.voice, self.voice_start = details
                self.show_state("speaking")
            elif kind == "hidden":
                self.root.iconify() if details[0] else self.root.deiconify()
            elif kind == "page":
                self.show_page(details[0])

        _, palette, speed, brightness = STATES["muted" if muted.is_set() else self.state]
        self.position = (self.position + speed) % ORB_FRAMES
        frame = self.frames[int(self.position)]

        if frame is not None:
            # While Jarvis speaks, the orb swells with its voice
            moment = int((time.time() - self.voice_start) * 20)
            if self.state == "speaking" and moment < len(self.voice):
                grow = int(self.orb_size * 0.12 * self.voice[moment])
                big = frame.resize((self.orb_size + grow, self.orb_size + grow), Image.BILINEAR)
                frame = big.crop((grow // 2, grow // 2, grow // 2 + self.orb_size, grow // 2 + self.orb_size))

            if (palette, brightness) not in self.tables:
                self.tables[palette, brightness] = colour_table(palette, brightness)

            self.photo.paste(frame.convert("RGB").point(self.tables[palette, brightness]))

        self.root.after(33, self.animate)

    def run(self):
        """Show the window until you close it. Ctrl+C in the terminal closes it too."""
        global window_open
        signal.signal(signal.SIGINT, lambda *args: self.root.destroy())
        self.animate()
        self.root.mainloop()
        window_open = False
