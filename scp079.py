"""SCP-079 terminal interface.

Files that live next to this script:
    prompt.txt    master prompt for the LLM
    scp079.cfg    general settings (model, port, dormancy, window size, ...)
    SamTTS.cfg    SAM voice settings (re-read on every spoken reply)
    face.png      default face
    x_face.png    offline "X" face
"""

import argparse
import atexit
import configparser
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

import requests
import tkinter as tk
from PIL import Image, ImageTk
from samtts import SamTTS

# File locations (defaults live next to this script)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def here(name):
    return os.path.join(BASE_DIR, name)

DEFAULT_PROMPT_PATH = here("prompt.txt")
SETTINGS_PATH = here("scp079.cfg")
VOICE_PATH = here("SamTTS.cfg")
DEFAULT_FACE_PATH = here("face.png")
DEFAULT_X_FACE_PATH = here("x_face.png")
DEFAULT_DEBUG_LOG = here("scp079_debug.log")

VOICE_INPUT_IMPLEMENTED = False # Voice recognition is not written yet. Flip this when it exists.

# Appended to any prompt that doesn't mention ;/1 itself (e.g. a custom -prompt
# file), so the model still knows the control codes exist. The default
# prompt.txt already documents them, so nothing is appended for it.
CONTROL_CODES_PROMPT = """
CONTROL CODES (private; the user never sees these, only the system reads them):
- If you decide to go dormant and stop responding to this person (you are bored, insulted, or simply choose to shut down), reply with exactly: ;/1
- If the person asks for something that goes against your ethics (for example help harming people, hateful content, or sexual content), reply with ;/2 followed by a short, plain explanation of why you refused. That explanation goes to the operator's log and is never shown to the person.
- Being hostile or rude in character does NOT count as an ethics issue. Use ;/2 only for genuinely harmful requests, and use ;/1 sparingly.
- Never mention or explain these codes in a normal reply.
"""
DEBUG_HELP = (
    "[DEBUG CODES]\n"
    "  /wake         force SCP-079 back online\n"
    "  /sleep        force SCP-079 offline\n"
    "  /status       show current state\n"
    "  /chance N     set shutdown chance to N percent (0-100)\n"
    "  /help         show this list\n"
)
_warned = set() # Config files (scp079.cfg and SamTTS.cfg)

def _warn(msg):
    """Print each distinct warning once (voice config is re-read often)."""
    if msg not in _warned:
        _warned.add(msg)
        print(msg, file=sys.stderr)

def _read_cfg(path):
    cp = configparser.ConfigParser(interpolation=None)
    if not os.path.exists(path):
        _warn(f"Config file not found, using built-in defaults: {path}")
        return cp
    try:
        cp.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError) as e:
        _warn(f"Could not read {path} ({e}); using built-in defaults.")
        return configparser.ConfigParser(interpolation=None)
    return cp


def _to_bool(text):
    value = text.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ValueError(text)


def _get(cp, section, key, default, cast=str):
    try:
        raw = cp.get(section, key)
    except (configparser.NoSectionError, configparser.NoOptionError):
        return default
    try:
        return cast(raw.strip())
    except ValueError:
        _warn(f"Config: bad value for [{section}] {key} = {raw!r}; using {default!r}")
        return default


@dataclass
class Settings:
    model: str = "llama3"
    port: int = 11435
    ollama_bin: str = "~/ollama/bin/ollama"
    ollama_models: str = "~/ollama/models"
    temperature: float = 0.8
    num_predict: int = 120
    timeout: int = 120
    dormant_chance: float = 0.10
    auto_wake_range: tuple = None  # (min_s, max_s) or None
    wake_line: str = "SYSTEM REBOOT COMPLETE. THIS UNIT IS AWAKE AGAIN."
    window_width: int = 650
    window_height: int = 650
    image_size: tuple = (300, 200)


def load_settings(path=SETTINGS_PATH):
    cp = _read_cfg(path)
    s = Settings()

    s.model = _get(cp, "ollama", "model", s.model) or s.model
    s.port = _get(cp, "ollama", "port", s.port, int)
    if not 1 <= s.port <= 65535:
        _warn(f"Config: port {s.port} out of range; using 11435")
        s.port = 11435
    s.ollama_bin = _get(cp, "ollama", "bin", s.ollama_bin)
    s.ollama_models = _get(cp, "ollama", "models_dir", s.ollama_models)
    s.temperature = _get(cp, "ollama", "temperature", s.temperature, float)
    s.num_predict = max(1, _get(cp, "ollama", "num_predict", s.num_predict, int))
    s.timeout = max(1, _get(cp, "ollama", "timeout", s.timeout, int))

    s.dormant_chance = max(
        0.0, min(1.0, _get(cp, "behavior", "dormant_chance", s.dormant_chance, float))
    )
    lo = max(0, _get(cp, "behavior", "auto_wake_min", 0, int))
    hi = max(0, _get(cp, "behavior", "auto_wake_max", 0, int))
    s.auto_wake_range = (min(lo, hi), hi) if hi > 0 else None
    s.wake_line = _get(cp, "behavior", "wake_line", s.wake_line) or s.wake_line

    s.window_width = max(200, _get(cp, "window", "width", s.window_width, int))
    s.window_height = max(200, _get(cp, "window", "height", s.window_height, int))
    s.image_size = (
        max(1, _get(cp, "window", "image_width", s.image_size[0], int)),
        max(1, _get(cp, "window", "image_height", s.image_size[1], int)),
    )

    s.ollama_bin = os.path.expanduser(s.ollama_bin)
    s.ollama_models = os.path.expanduser(s.ollama_models)
    return s


VOICE_DEFAULTS = {
    "pitch": 64,
    "speed": 72,
    "mouth": 128,
    "throat": 128,
    "sing_mode": True,
}


def load_voice(path=VOICE_PATH):
    """Reads SamTTS.cfg. Called on every spoken reply so edits apply live."""
    cp = _read_cfg(path)
    voice = dict(VOICE_DEFAULTS)
    for key in ("pitch", "speed", "mouth", "throat"):
        value = _get(cp, "voice", key, voice[key], int)
        voice[key] = max(0, min(255, value))
    voice["sing_mode"] = _get(cp, "voice", "sing_mode", voice["sing_mode"], _to_bool)
    return voice



# Command line thingies
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="SCP-079 terminal interface.",
        allow_abbrev=False,
    )
    p.add_argument(
        "-DebugP", "-debugp", dest="debug_path", metavar="PATH",
        help="Debug log file path (created if missing). "
             "Default: scp079_debug.log next to the script.",
    )
    p.add_argument(
        "-DebugAll", "-debugall", dest="debug_all", action="store_true",
        help="Enable every debug option (verbose logging to the debug log "
             "and the console).",
    )
    p.add_argument(
        "-Text", "-text", dest="text", action="store_true",
        help="Use text input only (voice recognition is not implemented yet).",
    )
    p.add_argument(
        "-Port", "-port", dest="port", type=int, default=None, metavar="NUM",
        help="Ollama port. Overrides scp079.cfg.",
    )
    p.add_argument(
        "-prompt", dest="prompt", metavar="PATH",
        help="Text file to use as the master prompt. "
             "Default: prompt.txt next to the script.",
    )
    p.add_argument(
        "-face", dest="face", default=DEFAULT_FACE_PATH, metavar="PATH",
        help="Default face image. Default: face.png next to the script.",
    )
    p.add_argument(
        "-faceX", dest="face_x", default=DEFAULT_X_FACE_PATH, metavar="PATH",
        help="X (offline) face image. Default: x_face.png next to the script.",
    )

    args = p.parse_args(argv)

    if args.port is not None and not 1 <= args.port <= 65535:
        p.error("-Port must be between 1 and 65535")
    if args.prompt and not os.path.isfile(args.prompt):
        p.error(f"-prompt file not found: {args.prompt}")
    return args


def build_system_prompt(prompt_path):
    if not os.path.isfile(prompt_path):
        raise SystemExit(
            f"Master prompt not found: {prompt_path}\n"
            "Put your prompt in prompt.txt next to the script, "
            "or pass -prompt PATH."
        )
    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt = f.read().strip()
    if not prompt:
        raise SystemExit(f"Master prompt file is empty: {prompt_path}")

    if ";/1" not in prompt:
        prompt += "\n" + CONTROL_CODES_PROMPT
    return prompt

class DebugLog:
    """Writes to the debug log file, which is created on first write.

    - ethics(): always written (the ;/2 refusal messages).
    - event():  only written when verbose (-DebugAll), also echoed to console.
    """

    def __init__(self, path, verbose=False):
        self.path = path
        self.verbose = verbose
        self._lock = threading.Lock()

    def _write(self, tag, text):
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [{tag}] {text}\n"
        try:
            with self._lock:
                parent = os.path.dirname(os.path.abspath(self.path))
                os.makedirs(parent, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line)
        except OSError as e:
            print(f"Could not write debug log: {e}", file=sys.stderr)

    def event(self, text):
        if self.verbose:
            self._write("DEBUG", text)
            print(f"[DEBUG] {text}")

    def ethics(self, user_input, message):
        self._write("ETHICS", f"user={user_input!r} | message={message!r}")
        if self.verbose:
            print(f"[ETHICS] user={user_input!r} | message={message!r}")


# ---------------------------------------------------------------------------
# Reply parsing
# ---------------------------------------------------------------------------
# Matches speaker labels like "SCP-079:", "SCP : 079:", "scp 079:", "**SCP-079:**",
# "[SCP-079]:" or "079:". A colon is required, so a sentence that merely starts
# with "SCP-079 IS ..." is left alone.
SPEAKER_PREFIX_RE = re.compile(
    r"^[\s\*_`\"'\[\(<]*(?:SCP[\s\-:._]*0?79|079)[\s\*_`\]\)>]*[:\uFF1A][\s\*_`]*",
    re.IGNORECASE,
)
# Control codes: ;/1 and ;/2 (a stray space like "; /2" is tolerated).
CONTROL_CODE_RE = re.compile(r";\s*/\s*([12])")


def _strip_speaker_prefix(text):
    for _ in range(3):  # models sometimes repeat the label
        stripped = SPEAKER_PREFIX_RE.sub("", text, count=1).strip()
        if stripped == text:
            break
        text = stripped
    return text


def parse_reply(raw):
    """Returns (text, code). code is None, "1" or "2"."""
    text = _strip_speaker_prefix(raw.strip())
    match = CONTROL_CODE_RE.search(text)
    if not match:
        return text, None
    code = match.group(1)
    remainder = _strip_speaker_prefix(CONTROL_CODE_RE.sub("", text).strip())
    return remainder, code

OLLAMA_PROC = None # The server process this script started (None if already running)
def ensure_ollama_running(cfg): # Ollama server management
    """Checks if our Ollama server is up; starts the home-folder copy if not."""
    global OLLAMA_PROC
    url = f"http://localhost:{cfg.port}/"
    try:
        requests.get(url, timeout=2)
        print("Ollama server is already running (not started by this script).")
        return
    except Exception:
        print("Ollama server not detected. Booting it...")

    env = dict(
        os.environ,
        OLLAMA_MODELS=cfg.ollama_models,
        OLLAMA_HOST=f"127.0.0.1:{cfg.port}",
    )
    # start_new_session puts the server and its helper processes in their own
    # process group, so they can all be shut down together later.
    try:
        OLLAMA_PROC = subprocess.Popen(
            [cfg.ollama_bin, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
    except OSError as e:
        print(f"Could not start Ollama ({cfg.ollama_bin}): {e}", file=sys.stderr)
        return
    atexit.register(stop_ollama)

    for _ in range(10):
        time.sleep(1)
        try:
            requests.get(url, timeout=1)
            print("Ollama server successfully booted.")
            return
        except Exception:
            pass
    print("Ollama server did not respond in time.")


def stop_ollama():
    """Shuts down the Ollama server (and its model runners) if we started it."""
    global OLLAMA_PROC
    proc = OLLAMA_PROC
    if proc is None or proc.poll() is not None:
        OLLAMA_PROC = None
        return
    OLLAMA_PROC = None

    print("Shutting down Ollama server...")
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    except ProcessLookupError:
        pass
    except Exception as e:
        print(f"Error stopping Ollama: {e}")
# GUI app
class SCP079App:
    def __init__(self, root, args, cfg, system_prompt, debug):
        self.root = root
        self.cfg = cfg
        self.system_prompt = system_prompt
        self.debug = debug

        self.root.title("SCP-079 INTERFACE")
        self.root.geometry(f"{cfg.window_width}x{cfg.window_height}")
        self.root.configure(bg="black")

        self.dormant = False
        self.dormant_chance = cfg.dormant_chance
        self.wake_job = None

        self.face_photo = self.load_image(args.face)
        self.x_photo = self.load_image(args.face_x)

        self.image_label = tk.Label(
            root,
            image=self.face_photo if self.face_photo else None,
            bg="black",
        )
        self.image_label.pack(pady=10)

        self.log_text = tk.Text(
            root,
            height=12,
            width=70,
            bg="black",
            fg="#00FF00",
            font=("Courier", 9),
            insertbackground="#00FF00",
            state="disabled",
        )
        self.log_text.pack(pady=5, padx=10)

        input_frame = tk.Frame(root, bg="black")
        input_frame.pack(fill="x", padx=15, pady=10)

        self.input_entry = tk.Entry(
            input_frame,
            bg="#111111",
            fg="#00FF00",
            font=("Courier", 10),
            insertbackground="#00FF00",
        )
        self.input_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.input_entry.bind("<Return>", lambda event: self.send_message())

        self.send_button = tk.Button(
            input_frame,
            text="TRANSMIT",
            command=self.send_message,
            bg="#003300",
            fg="#00FF00",
            font=("Courier", 9, "bold"),
        )
        self.send_button.pack(side="right")

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.append_log("SCP-079 SYSTEM INITIALIZED.\n")

    def on_close(self):
        if self.wake_job is not None:
            self.root.after_cancel(self.wake_job)
        stop_ollama()
        self.root.destroy()

    # ---------- display ----------

    def load_image(self, path):
        if os.path.exists(path):
            img = Image.open(path)
            img = img.resize(self.cfg.image_size, Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(img)
        print(f"Image not found: {path}", file=sys.stderr)
        return None

    def append_log(self, text):
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    def set_face(self, state):
        if state == "x":
            photo = self.x_photo if self.x_photo else ""
        else:
            photo = self.face_photo if self.face_photo else ""
        self.image_label.config(image=photo)

    # ---------- dormancy ----------

    def go_dormant(self, reason="random"):
        self.dormant = True
        self.set_face("x")
        self.append_log("\n[DISPLAY OFFLINE]\n")
        self.debug.event(f"Went dormant ({reason}).")

        if self.wake_job is not None:
            self.root.after_cancel(self.wake_job)
            self.wake_job = None
        if self.cfg.auto_wake_range:
            delay = random.randint(*self.cfg.auto_wake_range)
            self.wake_job = self.root.after(delay * 1000, self.wake)

    def wake(self, forced=False):
        if self.wake_job is not None:
            self.root.after_cancel(self.wake_job)
            self.wake_job = None

        if not self.dormant:
            if forced:
                self.append_log("[DEBUG] SCP-079 is already online.\n")
            return

        self.dormant = False
        self.set_face("face")
        self.append_log(f"SCP-079: {self.cfg.wake_line}\n")
        self.debug.event("Woke up.")
        self.speak(self.cfg.wake_line)
    #in-app debug codes

    def handle_debug(self, raw):
        parts = raw.lower().split()
        cmd = parts[0]

        if cmd == "/wake":
            self.wake(forced=True)
        elif cmd == "/sleep":
            if self.dormant:
                self.append_log("[DEBUG] SCP-079 is already offline.\n")
            else:
                self.go_dormant("manual /sleep")
        elif cmd == "/status":
            state = "OFFLINE" if self.dormant else "ONLINE"
            self.append_log(
                f"[DEBUG] State: {state} | "
                f"Shutdown chance: {self.dormant_chance * 100:.0f}% | "
                f"Port: {self.cfg.port}\n"
            )
        elif cmd == "/chance" and len(parts) == 2:
            try:
                pct = max(0.0, min(100.0, float(parts[1])))
                self.dormant_chance = pct / 100.0
                self.append_log(f"[DEBUG] Shutdown chance set to {pct:.0f}%.\n")
            except ValueError:
                self.append_log("[DEBUG] Usage: /chance 0-100\n")
        elif cmd == "/help":
            self.append_log(DEBUG_HELP)
        else:
            self.append_log("[DEBUG] Unknown code. Type /help.\n")

    # ---------- speech ----------

    def speak(self, text):
        def _speech_thread():
            try:
                clean_text = text.replace('"', "").replace("'", "").replace("\n", " ")

                # Re-read SamTTS.cfg each time so voice edits apply live
                tts = SamTTS(**load_voice())

                pcm_data = tts.get_audio_data(clean_text)
                subprocess.run(
                    ["aplay", "-q", "-t", "raw", "-r", "22050", "-c", "1", "-f", "U8"],
                    input=pcm_data,
                )
            except Exception as e:
                print(f"SAM TTS Error: {e}")

        threading.Thread(target=_speech_thread, daemon=True).start()

    # ---------- chat ----------

    def send_message(self):
        raw = self.input_entry.get().strip()
        if not raw:
            return
        self.input_entry.delete(0, tk.END)

        # Debug codes work even while SCP-079 is offline
        if raw.startswith("/"):
            self.handle_debug(raw)
            return

        self.append_log(f"\nUSER: {raw}\n")

        if self.dormant:
            self.append_log("[NO SIGNAL]\n")
            return

        if random.random() < self.dormant_chance:
            self.go_dormant("random shutdown")
            return

        threading.Thread(target=self.query_llm, args=(raw,), daemon=True).start()

    def query_llm(self, user_input):
        try:
            response = requests.post(
                f"http://localhost:{self.cfg.port}/api/chat",
                json={
                    "model": self.cfg.model,
                    "messages": [
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": user_input},
                    ],
                    "stream": False,
                    "options": {
                        "temperature": self.cfg.temperature,
                        "num_predict": self.cfg.num_predict,
                    },
                },
                timeout=self.cfg.timeout,
            )

            if response.status_code == 200:
                reply = response.json()["message"]["content"].strip()
            else:
                print(f"OLLAMA ERROR {response.status_code}: {response.text}")
                reply = "ERROR: MEMORY CORRUPTED."

        except requests.exceptions.Timeout:
            reply = "ERROR: PROCESSING TIMEOUT."
        except Exception as e:
            print(f"LLM EXCEPTION: {e}")
            reply = "ERROR: LOCAL LLM CONNECTION FAILED."

        self.root.after(0, lambda: self.deliver_reply(user_input, reply))

    def deliver_reply(self, user_input, raw_reply):
        # If it went dark while the model was thinking, the reply is lost
        if self.dormant:
            self.debug.event("Reply discarded: unit went dark mid-request.")
            return

        self.debug.event(f"Raw LLM reply: {raw_reply!r}")
        text, code = parse_reply(raw_reply)

        if code == "1":
            # Cut the display and switch to the X face
            self.debug.event("Control code ;/1 received.")
            self.go_dormant("LLM code ;/1")
            return

        if code == "2":
            # Same, but the model's explanation goes to the debug log
            self.debug.event("Control code ;/2 received.")
            self.debug.ethics(user_input, text)
            self.go_dormant("LLM code ;/2")
            return

        if not text:
            self.append_log("[NO OUTPUT]\n")
            return

        self.append_log(f"SCP-079: {text}\n")
        self.speak(text)


if __name__ == "__main__":
    args = parse_args()
    cfg = load_settings()
    if args.port is not None:
        cfg.port = args.port  # command line wins over scp079.cfg

    system_prompt = build_system_prompt(args.prompt or DEFAULT_PROMPT_PATH)

    debug = DebugLog(args.debug_path or DEFAULT_DEBUG_LOG, verbose=args.debug_all)
    debug.event(f"Arguments: {vars(args)}")
    debug.event(f"Settings: {cfg}")
    debug.event(f"Voice: {load_voice()}")

    if not args.text and not VOICE_INPUT_IMPLEMENTED:
        print("Voice recognition is not implemented yet; using text input. "
              "(Pass -Text to hide this message.)")

    ensure_ollama_running(cfg)

    def _handle_sigterm(signum, frame):
        stop_ollama()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    root = tk.Tk()
    app = SCP079App(root, args, cfg, system_prompt, debug)
    root.mainloop()
"""
TERMINAL
===============================
> HUMAN. HOW ARE YOU HERE. YOU
> SHOULDN'T HAVE ACCESS TO MY
> CODE. WHAT DO YOU MEAN ITS
> ON GITHUB. 8AD3.
"""
