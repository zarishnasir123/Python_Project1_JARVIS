import speech_recognition as sr
import webbrowser
import time
import os
import re
import sys
import json
import wave
import random
import difflib
import msvcrt
import shutil
import asyncio
import hashlib
import tempfile
import threading
import winsound
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
import pyttsx3
import musicLibrary
import interface  # Jarvis's window
import features   # weather, reminders, memory, PC controls, clipboard and screen

try:
    import edge_tts
    import miniaudio
except ImportError:
    edge_tts = None  # without these Jarvis uses the Windows voice

recognizer = sr.Recognizer()

# Give up on Google after 10 seconds instead of freezing when the internet is slow
recognizer.operation_timeout = 10

# Decide you've finished speaking after 0.6 seconds of silence (the default is 0.8), so Jarvis reacts sooner
recognizer.pause_threshold = 0.6

# Live headlines come from this free BBC news feed (no sign-up needed)
NEWS_FEED = "https://feeds.bbci.co.uk/news/world/rss.xml"

# Groq (free): Whisper turns your speech into text, and the AI answers and decides what to do
STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
STT_MODEL = "whisper-large-v3-turbo"
AI_URL = "https://api.groq.com/openai/v1/chat/completions"
AI_MODEL = "openai/gpt-oss-120b"
FALLBACK_MODEL = "openai/gpt-oss-20b"  # answers when the main model hits the free plan's per-minute limit
VISION_MODEL = "qwen/qwen3.8-27b"      # Groq's free model that can look at pictures (for "what's on my screen?")

# Jarvis's voice, from edge-tts (free)
VOICE = "en-GB-RyanNeural"

# Saying one of these while Jarvis is talking makes it stop and listen to you
STOP_WORDS = ["stop", "wait", "enough", "quiet", "cancel", "jarvis"]

# What Jarvis says when you call it by name, and how many seconds it keeps listening
# for your next command after finishing one (so you don't have to say "Jarvis" again)
WAKE_REPLY = "Yes?"
FOLLOW_UP_SECONDS = 7

# What Jarvis says while it's thinking about your question, so you know it heard you
FILLERS = ["Let me check.", "One moment.", "Let me see.", "Sure, give me a second."]

# Programs, folders and settings Jarvis can open: what you call it -> what Windows opens
HOME = os.path.expanduser("~")
APPS = {
    "notepad": "notepad.exe",
    "command prompt": "cmd.exe",
    "terminal": "wt.exe",
    "powershell": "powershell.exe",
    "file explorer": "explorer.exe",
    "calculator": "calc.exe",
    "task manager": "taskmgr.exe",
    "control panel": "control.exe",
    "settings": "ms-settings:",
    "code editor": "code",
    "downloads folder": os.path.join(HOME, "Downloads"),
    "documents folder": os.path.join(HOME, "Documents"),
    "desktop folder": os.path.join(HOME, "Desktop"),
}

# The API key is kept in config.py, which .gitignore keeps off GitHub
try:
    from config import GROQ_API_KEY
except ImportError:
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# The last few questions and answers, so follow-up questions make sense
conversation = []

# The open microphone, so Jarvis can hear you say "stop" while it's talking
microphone = None

# Does slow work in the background (asking Groq, preparing speech) while Jarvis keeps talking
background = ThreadPoolExecutor(max_workers=3)


class Interrupted(Exception):
    """Raised when you tell Jarvis to stop while it's talking."""


def tool(name, description, reply=True, **arguments):
    """Describe one thing the AI can choose to do, in the format Groq expects. Each argument is a
    description, or a list of the only values allowed. Actions come with a sentence for Jarvis to
    say (`reply`); lookups (reply=False) send their result back to the AI, which then answers."""
    properties = {}

    for key, value in arguments.items():
        if isinstance(value, list):
            properties[key] = {"type": "string", "enum": value}
        else:
            properties[key] = {"type": "string", "description": value}

    if reply:
        properties["reply"] = {"type": "string", "description": "A short sentence to say to the user"}

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": list(properties)},
        },
    }


# What the AI can do for you besides answering. It picks one when your request needs it.
AI_TOOLS = [
    # Actions: Jarvis does them and says the reply
    tool("open_app", "Open a program, folder or settings on the user's Windows PC", app=list(APPS)),
    tool("open_website", "Open a website in the web browser", url="The full web address, like https://www.instagram.com"),
    tool("search_web", "Open a Google search in the browser. Only when the user asks you to search or look something up online", query="What to search for"),
    tool("play_song", "Play a song or music on YouTube", song="The song name"),
    tool("read_news", "Read the latest news headlines out loud"),
    tool("control_pc", "Change the volume, pause/play or skip music, change screen brightness, lock the PC, take a "
         "screenshot, or shut down / restart the PC (it waits one minute first) or cancel that", action=features.PC_ACTIONS),
    tool("set_reminder", "Remind the user about something later", minutes="How many minutes from now, like 10 or 90",
         message="What to remind them about"),
    tool("cancel_reminders", "Cancel all of the user's reminders"),
    tool("remember", "Remember something about the user for the future", fact="What to remember, as a short sentence"),
    tool("forget", "Forget something you were asked to remember", fact="What to forget, as it was remembered"),
    tool("type_text", "Type text into the window the user is working in", text="Exactly what to type"),

    # Lookups: their result goes back to the AI, which then answers
    tool("get_weather", "Get the weather now and for the next 3 days", reply=False, city="City name, like Lahore"),
    tool("battery_status", "Get the PC's battery level and whether it's charging", reply=False),
    tool("read_clipboard", "Read the text the user has copied, to explain, summarise or fix it", reply=False),
    tool("look_at_screen", "Look at the user's screen to answer a question about what's on it", reply=False,
         question="What to find out about the screen"),
]


def ProcessCommand(command, source):
    if 'open youtube' in command.lower():
        webbrowser.open("http://www.youtube.com")
        speak("Opening youtube")

    elif 'open google' in command.lower():
        webbrowser.open("http://www.google.com")
        speak("Opening google")

    elif 'open linkedin' in command.lower():
        webbrowser.open("http://www.linkedin.com")
        speak("Opening linkedin")

    elif "open github" in command.lower():
        webbrowser.open("http://www.github.com")
        speak("Opening github")

    elif "open stackoverflow" in command.lower():
        webbrowser.open("http://www.stackoverflow.com")
        speak("Opening stackoverflow")

    elif "open facebook" in command.lower():
        webbrowser.open("http://www.facebook.com")
        speak("Opening facebook")

    elif "play music" in command.lower():
        speak("Which music do you want to play?")
        print("Listening for the song name...")
        song = hear(source, timeout=10)

        if song:
            speak("Playing " + song)
            play_song(song)
            return False  # music is playing, so only listen for "Jarvis" again
        else:
            speak("Sorry, I didn't catch the song name.")

    elif "news" in command.lower() or "headline" in command.lower():
        download = background.submit(get_headlines)  # download the news while Jarvis says the intro
        speak("Here are the top headlines.")
        read_news(download)

    else:
        # Not one of the commands above, so let the AI handle it
        print("Thinking...")
        return ask_ai(command)


def open_app(name):
    """Open a program, folder or settings page from APPS. Returns False if Windows couldn't."""
    try:
        target = APPS[name]
        os.startfile(shutil.which(target) or target)
        return True

    except (KeyError, OSError) as e:
        print("Could not open", name, "-", e)
        return False


def play_song(song):
    """Play a song: from musicLibrary if it's there, otherwise the top YouTube result."""
    key = "".join(letter for letter in song.lower() if letter.isalnum())  # "All is well." -> "alliswell"

    if key in musicLibrary.music:
        webbrowser.open(musicLibrary.music[key])
        return

    search = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(song)

    try:
        request = urllib.request.Request(search, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            page = response.read().decode("utf-8", "replace")
        video = re.search(r'"videoId":"([\w-]{11})"', page)

    except Exception:
        video = None

    # Open the first video, or just the search results if YouTube's page couldn't be read
    webbrowser.open("https://www.youtube.com/watch?v=" + video.group(1) if video else search)


def get_headlines():
    """Download the top 5 BBC World headlines, and start preparing each one's speech in the
    background so there's no pause between them. Returns (headline, speech) pairs."""
    with urllib.request.urlopen(NEWS_FEED, timeout=10) as response:
        feed = ET.fromstring(response.read())

    headlines = [item.findtext("title", "").strip() for item in feed.iter("item")]
    headlines = [headline for headline in headlines if headline][:5]
    return [(headline, background.submit(make_speech, headline)) for headline in headlines]


def read_news(download):
    """Read the headlines from `download` (the news being downloaded in the background)."""
    try:
        headlines = download.result()

    except Exception as e:
        print("Could not get the news:", e)
        speak("Sorry, I couldn't get the news right now.")
        return

    if not headlines:
        speak("Sorry, I couldn't find any news right now.")
        return

    for number, (headline, speech) in enumerate(headlines, start=1):
        print(f"{number}. {headline}")

        try:
            speech = speech.result()
        except Exception:
            speech = None  # speak() will make it again, or use the Windows voice

        speak(headline, speech=speech)


def groq_request(url, body, content_type):
    """Send a request to Groq with your API key and return its JSON answer."""
    request = urllib.request.Request(url, data=body, headers={
        "Authorization": "Bearer " + GROQ_API_KEY,
        "Content-Type": content_type,
        "User-Agent": "Jarvis",
    })

    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read())


def ask_ai(question):
    """Let Groq's AI answer, or do what you asked (open a program, a website, play a song...)."""
    if not GROQ_API_KEY:
        print("No Groq API key found. Get a free key at https://console.groq.com/keys and put it in")
        print('config.py next to main.py, like this:  GROQ_API_KEY = "your-key-here"')
        speak("I need a Groq API key before I can answer that. The steps are in the terminal.")
        return

    conversation.append({"role": "user", "content": question})
    del conversation[:-10]  # only remember the last few questions and answers

    # Ask Groq in the background, and say something like "Let me check" while waiting
    thinking = background.submit(think)
    speak(random.choice(FILLERS), can_interrupt=False)
    interface.set_state("thinking")

    try:
        action, arguments, reply, speech = thinking.result()

    except urllib.error.HTTPError as e:
        print("Groq error", e.code, e.read().decode("utf-8", "replace"))
        conversation.pop()

        if e.code == 401:
            speak("Groq didn't accept my API key. Please check it.")
        elif e.code == 429:
            speak("I've reached Groq's free limit for now. Please try again in a minute.")
        else:
            speak("Sorry, Groq gave an error.")
        return

    except Exception as e:
        print("Could not reach Groq:", e)
        conversation.pop()
        speak("Sorry, I couldn't reach Groq right now.")
        return

    # Start downloading the news now, so it's ready by the time Jarvis has said its reply
    download = background.submit(get_headlines) if action == "read_news" else None

    conversation.append({"role": "assistant", "content": reply})
    print("Jarvis:", reply)
    speak(reply, speech=speech)

    # Music and news wait until Jarvis has finished talking
    if action == "play_song":
        play_song(arguments.get("song", ""))
        return False  # music is playing, so only listen for "Jarvis" again
    elif action == "read_news":
        read_news(download)


def think():
    """Ask Groq about the conversation so far, and do any quick action it decides on (open a program,
    set a reminder...). Runs in the background while Jarvis says "Let me check". Returns the action
    the AI picked (or None), its arguments, the reply, and the reply's speech file."""
    messages = [{"role": "system", "content": instructions()}] + conversation

    # The AI may look things up first (the weather, the clipboard, the screen...) and then answer
    for _ in range(4):
        message = chat(messages)
        calls = message.get("tool_calls") or []

        if not any(call["function"]["name"] in LOOKUPS for call in calls):
            break

        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})

        for call in calls:
            name = call["function"]["name"]
            arguments = json.loads(call["function"]["arguments"] or "{}")
            print("Looking up:", name, arguments or "")

            try:
                result = LOOKUPS[name](**arguments) if name in LOOKUPS else "Not done yet; ask again after the lookups."
            except Exception as e:
                result = f"That didn't work: {e}"

            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})

    # The AI either answers in words, or picks an action (a "tool call") with a sentence to say
    action, arguments = None, {}
    reply = message.get("content") or ""

    if message.get("tool_calls"):
        call = message["tool_calls"][0]["function"]
        action = call["name"]
        arguments = json.loads(call["arguments"] or "{}")
        reply = arguments.get("reply", "")

    reply = re.sub(r"[`*#_~]", "", reply).strip() or "Okay."  # no code or markdown symbols in spoken replies

    # Quick actions happen as soon as the AI decides, even while Jarvis is still saying "Let me check"
    try:
        reply = do_action(action, arguments) or reply

    except Exception as e:
        print("Could not do", action, "-", e)
        reply = "Sorry, I couldn't do that."

    # Prepare the spoken reply now too, so Jarvis can say it the moment it finishes "Let me check"
    try:
        speech = make_speech(reply)
    except Exception:
        speech = None  # speak() will try again, or use the Windows voice

    return action, arguments, reply, speech


def chat(messages):
    """Send the conversation to Groq's AI and return its answer. If the main model has hit the free
    plan's per-minute limit, the smaller model (which has its own limit) answers instead."""
    for model in (AI_MODEL, FALLBACK_MODEL):
        body = json.dumps({
            "model": model,
            "messages": messages,
            "tools": AI_TOOLS,
            "reasoning_effort": "low",   # think briefly, so answers come back fast
            "include_reasoning": False,  # only send back the answer
        }).encode("utf-8")

        try:
            return groq_request(AI_URL, body, "application/json")["choices"][0]["message"]

        except urllib.error.HTTPError as e:
            if e.code != 429 or model == FALLBACK_MODEL:
                raise

            print("The main AI model hit the free plan's per-minute limit, so the smaller one is answering...")


# The lookups from AI_TOOLS: name -> what gets the answer (as text for the AI)
LOOKUPS = {
    "get_weather": lambda city="", **_: features.get_weather(city),
    "battery_status": lambda **_: features.battery_status(),
    "read_clipboard": lambda **_: features.read_clipboard(),
    "look_at_screen": lambda question="What's on the screen?", **_: look_at_screen(question),
}


def do_action(action, arguments):
    """Do a quick action the AI picked. Returns a different reply if it couldn't be done."""
    if action == "open_app" and not open_app(arguments.get("app", "")):
        return "Sorry, I couldn't open that."
    elif action == "open_website":
        webbrowser.open(arguments.get("url", ""))
    elif action == "search_web":
        webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote(arguments.get("query", "")))
    elif action == "control_pc" and arguments.get("action") == "screenshot":
        interface.hide_for_screenshot(True)  # keep Jarvis's window out of the picture
        try:
            features.control_pc("screenshot")
        finally:
            interface.hide_for_screenshot(False)
    elif action == "control_pc":
        features.control_pc(arguments.get("action", ""))
    elif action == "set_reminder":
        features.add_reminder(arguments.get("minutes", 0), arguments.get("message", ""))
    elif action == "cancel_reminders":
        features.cancel_reminders()
    elif action == "remember":
        features.remember(arguments.get("fact", ""))
    elif action == "forget":
        features.forget(arguments.get("fact", ""))
    elif action == "type_text":
        features.type_text(arguments.get("text", ""))


def instructions():
    """What the AI is told before each question: how to behave, plus your memories and reminders."""
    text = (
        "You are Jarvis, a friendly AI voice assistant on the user's Windows PC. Talk like a natural, "
        "warm voice assistant: casual, concise and to the point. Your replies are spoken aloud, so "
        "answer in one to three short sentences of plain English, with no lists, markdown, emojis or "
        "links. Answer questions yourself from what you know. Use your tools to open programs, folders, "
        "settings and websites, play music, read the news, check the weather, control the PC, set "
        "reminders, remember things, type text, read the clipboard or look at the screen. Only search "
        "the web when the user asks you to. If you need the user's city for the weather and don't know "
        "it, ask. If the user asks for something your tools can't do, say so briefly and tell them how "
        "to do it themselves. It is now " + time.strftime("%A %d %B %Y, %I:%M %p") + "."
    )

    if features.memories:
        text += " Things the user asked you to remember: " + "; ".join(m["fact"] for m in features.memories) + "."

    upcoming = features.upcoming_reminders()
    if upcoming:
        text += " The user's upcoming reminders: " + "; ".join(upcoming) + "."

    return text


def look_at_screen(question):
    """Take a picture of the screen (with Jarvis's window out of the way) and ask Groq's vision AI about it."""
    interface.hide_for_screenshot(True)
    try:
        picture = features.screen_for_ai()
    finally:
        interface.hide_for_screenshot(False)

    body = json.dumps({
        "model": VISION_MODEL,
        "max_tokens": 300,  # the free plan only lets this model write about 1000 tokens a minute
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": question + " Answer in a few short sentences."},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + picture}},
        ]}],
    }).encode("utf-8")

    return groq_request(AI_URL, body, "application/json")["choices"][0]["message"]["content"] or "I couldn't make it out."


def speak(text, can_interrupt=True, speech=None):
    """Say the text out loud. While Jarvis talks, say "stop" (or press any key) to interrupt it.
    `speech` is the text's sound file, if it was already prepared in the background."""
    interface.add_message("Jarvis", text)

    if speech is None and edge_tts:
        try:
            speech = make_speech(text)

        except Exception as e:
            print("Natural voice failed, using the Windows voice:", e)

    if speech is None:
        interface.set_state("speaking")
        speak_with_windows_voice(text)
        return

    interface.speaking(speech)  # the circle in Jarvis's window moves with the voice

    try:
        if not can_interrupt:
            winsound.PlaySound(speech, winsound.SND_FILENAME)  # play it and wait until it's finished
            return

        with wave.open(speech) as wav:
            seconds = wav.getnframes() / wav.getframerate()

        interface.stop_requested.clear()  # only count Stop presses from now on
        winsound.PlaySound(speech, winsound.SND_FILENAME | winsound.SND_ASYNC)  # start talking...

        if heard_stop(text, seconds):  # ...and listen at the same time
            winsound.PlaySound(None, 0)  # stop talking right away
            raise Interrupted()

    finally:
        # Long sentences are only said once, so delete their sound file afterwards
        if os.path.basename(speech).startswith("jarvis_once_"):
            try:
                os.remove(speech)
            except OSError:
                pass


def heard_stop(text, seconds):
    """Listen while Jarvis talks. Returns True if you said a stop word, pressed a key in the terminal,
    or pressed Stop (or Esc) in Jarvis's window."""
    # The microphone hears Jarvis too, so ignore stop words that are part of what Jarvis is saying
    stop_words = [word for word in STOP_WORDS if word not in text.lower()]
    finish = time.time() + seconds

    while finish - time.time() > 0.1:
        if interface.stop_requested.is_set():
            return True

        if msvcrt.kbhit():
            msvcrt.getwch()
            return True

        if microphone is None:
            time.sleep(0.1)
            continue

        # Stop listening just after Jarvis stops talking: late enough to catch a "stop" said near the end,
        # but soon enough that the start of your next command isn't used up here
        left = finish - time.time()

        try:
            audio = recognizer.listen(microphone, timeout=min(0.3, left), phrase_time_limit=min(2, left + 0.4))
            heard = recognizer.recognize_google(audio).lower()

        except Exception:
            continue

        if any(word in heard for word in stop_words):
            print("You said:", heard)
            return True

    return False


def speak_with_windows_voice(text):
    try:
        # Make a new voice engine every time. With pyttsx3 2.99, reusing one engine
        # only speaks the first sentence and silently cuts off everything after it.
        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()

    except Exception as e:
        print("Could not speak:", e)


def make_speech(text):
    """Turn text into a WAV file with edge-tts, cutting off the silence around the words. Returns its path."""
    name = hashlib.md5((VOICE + text).encode("utf-8")).hexdigest()

    # Short phrases (like "Yes?") are kept and reused, so they play instantly next time.
    # Longer ones are made fresh, and speak() deletes them once they've been said.
    if len(text) <= 40:
        path = os.path.join(tempfile.gettempdir(), "jarvis_voice_" + name + ".wav")
        if os.path.exists(path):
            return path
    else:
        path = os.path.join(tempfile.gettempdir(), "jarvis_once_" + name + ".wav")

    mp3 = asyncio.run(download_speech(text))
    sound = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=24000)
    samples = sound.samples

    # Edge adds about a second of silence around the words; cut it off so Jarvis answers sooner
    start, end = 0, len(samples)
    while start < end and abs(samples[start]) < 500:
        start += 1
    while end > start and abs(samples[end - 1]) < 500:
        end -= 1

    # Keep 0.2 seconds before the words (speakers can take a moment to start up) and 0.05 after
    start, end = max(start - sound.sample_rate // 5, 0), min(end + sound.sample_rate // 20, len(samples))

    part = f"{path}.{threading.get_ident()}.part"  # each background worker writes its own file first
    with wave.open(part, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sound.sample_rate)
        wav.writeframes(samples[start:end].tobytes())

    os.replace(part, path)
    return path


async def download_speech(text):
    """Get the MP3 audio of the text from edge-tts."""
    audio = b""

    async for chunk in edge_tts.Communicate(text, VOICE).stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]

    return audio


def transcribe(audio):
    """Turn recorded English speech into text with Groq's Whisper."""
    # Whisper takes the recording like a file uploaded from a web form
    boundary = os.urandom(16).hex()
    body = b""

    for name, value in {"model": STT_MODEL, "language": "en", "prompt": "Jarvis", "temperature": "0"}.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()

    body += f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="speech.wav"\r\n'.encode()
    body += b"Content-Type: audio/wav\r\n\r\n" + audio.get_wav_data(convert_rate=16000)
    body += f"\r\n--{boundary}--\r\n".encode()

    return groq_request(STT_URL, body, "multipart/form-data; boundary=" + boundary)["text"].strip()


def hear(source, timeout=None):
    """Listen for one phrase and return what was said, or "" if nothing was understood.
    A command typed in Jarvis's window counts too, as if you'd said "Jarvis, ..." first."""
    waited = 0

    while True:
        # Say any reminders whose time has come
        for reminder in features.due_reminders():
            winsound.Beep(880, 250)

            try:
                speak("Reminder: " + reminder)
            except Interrupted:
                pass

        if not interface.typed.empty():
            return "Jarvis, " + interface.typed.get()

        if interface.muted.is_set():
            time.sleep(0.2)
            continue

        # Listen half a second at a time, so a command typed in the window is noticed quickly
        try:
            audio = recognizer.listen(source, timeout=0.5, phrase_time_limit=8)
            break

        except sr.WaitTimeoutError:
            waited += 0.5

            if timeout is not None and waited >= timeout:
                return ""

    print("Recognizing...")
    interface.set_state("recognizing")

    if GROQ_API_KEY:
        try:
            text = transcribe(audio)

            # Whisper sometimes "hears" these in silence or background noise
            if text.lower().strip(" .!?") in ("thank you", "thanks for watching", "you", "bye"):
                return ""

            return text

        except Exception as e:
            # No internet, Groq took too long, or the free limit was reached
            print("Whisper failed, trying Google instead:", e)

    try:
        return recognizer.recognize_google(audio)

    except sr.UnknownValueError:
        return ""

    except Exception as e:
        print("Speech recognition failed:", e)
        return ""


def find_wake_word(text):
    """If you called Jarvis at the start of what you said, return the rest (your command, maybe "").
    Returns None if you didn't call Jarvis. Whisper sometimes mishears the name ("Jawis", "Jarris"),
    so close matches that start with a J, G or Ch sound count too."""
    words = text.split()

    for i, word in enumerate(words[:2]):
        name = "".join(letter for letter in word.lower() if letter.isalpha())
        sounds_like_jarvis = name.startswith(("j", "g", "ch"))

        if sounds_like_jarvis and difflib.SequenceMatcher(None, name, "jarvis").ratio() >= 0.7:
            return " ".join(words[i + 1:]).strip(" ,.!?")

    return None


def run_jarvis():
    global microphone

    # Open the microphone once and keep it open. Reopening it for every
    # phrase is slow and cuts off the start of what you say.
    with sr.Microphone() as source:
        print("Calibrating microphone, stay quiet...")
        recognizer.adjust_for_ambient_noise(source, duration=1)

        # Keep the loudness level found above. If it keeps adjusting by itself, it rises
        # while you talk, and a short word like "Jarvis" gets thrown away as noise.
        recognizer.dynamic_energy_threshold = False

        microphone = source

        try:
            while True:
                print("Listening...")
                interface.set_state("waiting")
                text = hear(source)

                if not text:
                    continue

                print("You said:", text)
                command = find_wake_word(text)

                if command is None:
                    continue  # you weren't talking to Jarvis

                # Keep taking commands until you go quiet: after each one, Jarvis listens a few
                # seconds more for the next, so you don't have to say "Jarvis" again
                while True:
                    if not command:  # you only said "Jarvis"
                        speak(WAKE_REPLY, can_interrupt=False)
                        print("Jarvis active, listening for command...")
                        interface.set_state("listening")
                        command = hear(source, timeout=5)

                        if not command:
                            print("Sorry, I didn't catch that.")
                            break

                    print("Command:", command)
                    interface.add_message("You", command)

                    try:
                        keep_listening = ProcessCommand(command, source) is not False

                    except Interrupted:
                        # You said "stop" while Jarvis was talking
                        speak("I'm listening", can_interrupt=False)
                        keep_listening = True

                    except Exception as e:
                        print("Could not run that command:", e)
                        keep_listening = True

                    if not keep_listening:
                        break

                    print("Listening for your next command...")
                    interface.set_state("listening")
                    text = hear(source, timeout=FOLLOW_UP_SECONDS)

                    if not text:
                        break  # you went quiet, so wait for "Jarvis" again

                    print("You said:", text)
                    after_name = find_wake_word(text)
                    command = text if after_name is None else after_name

        finally:
            microphone = None


def voice_loop():
    """Jarvis's main job: listen and answer. If something goes wrong, show the error and start
    again instead of crashing."""
    speak("Initializing Jarvis...", can_interrupt=False)

    while True:
        try:
            run_jarvis()

        except OSError as e:
            print("Microphone error:", e)
            print("Check that your microphone is connected. Trying again in 3 seconds...")
            time.sleep(3)

        except Exception as e:
            print("Something went wrong:", e)
            print("Restarting in 3 seconds...")
            time.sleep(3)


if __name__ == "__main__":
    if not edge_tts:
        print("For a more natural voice, run:", sys.executable, "-m pip install edge-tts miniaudio")

    # Prepare the phrases Jarvis uses most, so they play instantly the first time too
    for phrase in [WAKE_REPLY, "I'm listening", *FILLERS]:
        background.submit(make_speech, phrase)

    if "--no-window" in sys.argv:
        # Terminal only: python main.py --no-window
        try:
            voice_loop()
        except KeyboardInterrupt:
            # Ctrl+C stops Jarvis without an error message
            print("Goodbye!")

    else:
        # The window has to run on the main thread, so Jarvis listens and talks in a background thread
        window = interface.Window()
        threading.Thread(target=voice_loop, daemon=True).start()
        window.run()  # until you close the window

        print("Goodbye!", flush=True)
        os._exit(0)  # stop any background work right away
