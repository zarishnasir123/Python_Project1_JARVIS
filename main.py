import speech_recognition as sr
import webbrowser
import time
import pyttsx3
import musicLibrary

recognizer = sr.Recognizer()

# Give up on Google after 10 seconds instead of freezing when the internet is slow
recognizer.operation_timeout = 10


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
        guesses = hear(source, timeout=10)

        # Song names in musicLibrary have no spaces (like "alliswell"), so compare without spaces
        found = [guess for guess in guesses if guess.replace(" ", "") in musicLibrary.music]

        if found:
            webbrowser.open(musicLibrary.music[found[0].replace(" ", "")])
            speak("Playing " + found[0])
        else:
            speak("Sorry, I don't have that music in my library. Please try again.")


def speak(text):
    try:
        # Make a new voice engine every time. With pyttsx3 2.99, reusing one engine
        # only speaks the first sentence and silently cuts off everything after it.
        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()

    except Exception as e:
        print("Could not speak:", e)


def hear(source, timeout=None):
    """Listen for one phrase and return Google's guesses for it (best first), or [] if nothing was understood."""
    try:
        audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=8)

    except sr.WaitTimeoutError:
        return []

    print("Recognizing...")

    try:
        result = recognizer.recognize_google(audio, show_all=True)

    except sr.UnknownValueError:
        return []

    except Exception as e:
        # No internet, Google took too long, or the connection dropped
        print("Speech recognition failed:", e)
        return []

    return [
        guess["transcript"].lower()
        for guess in result["alternative"]
        if "transcript" in guess
    ]


def run_jarvis():
    # Open the microphone once and keep it open. Reopening it for every
    # phrase is slow and cuts off the start of what you say.
    with sr.Microphone() as source:
        print("Calibrating microphone, stay quiet...")
        recognizer.adjust_for_ambient_noise(source, duration=1)

        # Keep the loudness level found above. If it keeps adjusting by itself, it rises
        # while you talk, and a short word like "Jarvis" gets thrown away as noise.
        recognizer.dynamic_energy_threshold = False

        while True:
            print("Listening...")
            guesses = hear(source)

            if not guesses:
                continue

            print("You said:", guesses[0])

            # Google's best guess is sometimes wrong, so look for "jarvis" in all of its guesses
            matches = [guess for guess in guesses if "jarvis" in guess]

            if not matches:
                continue

            # "Jarvis open YouTube" in one sentence: the command is what comes after "jarvis"
            command = matches[0].split("jarvis", 1)[1].strip()

            if not command:
                speak("Ya")
                print("Jarvis active, listening for command...")
                guesses = hear(source, timeout=5)

                if not guesses:
                    print("Sorry, I didn't catch that.")
                    continue

                command = guesses[0]

            print("Command:", command)

            try:
                ProcessCommand(command, source)

            except Exception as e:
                print("Could not run that command:", e)


if __name__ == "__main__":
    try:
        speak("Initializing Jarvis...")

        # If something goes wrong, show the error and start again instead of crashing
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

    except KeyboardInterrupt:
        # Ctrl+C stops Jarvis without an error message
        print("Goodbye!")
