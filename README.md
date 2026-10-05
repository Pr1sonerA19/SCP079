# SCP-079 Terminal

A desktop chat window that emulates SCP-079, the "Old AI". It uses:

- a local **Llama 3** model (through Ollama) for the replies
- **SAM** (Software Automatic Mouth) for the retro speech
- a face image that swaps to an **X** when SCP-079 goes dark

SCP-079 may randomly shut down and ignore you, or decide to cut its own
display. Debug codes bring it back.

## Requirements

- Linux (the speech playback uses `aplay`)
- Python 3.8 or newer, with `tkinter`
- About 5 GB of free disk space for the Llama 3 model, plus the Ollama files
- Two face images: `face.png` and `x_face.png` (see [ATTRIBUTION.md](ATTRIBUTION.md))

## Files

Keep these together in one folder:

```
scp079.py          the app
prompt.txt         master prompt for the LLM
scp079.cfg         general settings
SamTTS.cfg         SAM voice settings
requirements.txt   Python libraries
face.png           default face
x_face.png         "offline" face
```

## Install

### 1. Python libraries

Use a virtual environment so nothing touches your system Python:

```bash
cd /path/to/scp079
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

This installs `requests`, `Pillow` and `samtts`. Activate the environment
(`source .venv/bin/activate`) every time you open a new terminal before running
the app.

Check that `tkinter` is available (it can't be installed with pip):

```bash
python3 -c "import tkinter; print('tkinter OK')"
```

If that fails, install your distribution's Tk package (for example
`sudo apt install python3-tk` on Debian or Ubuntu).

### 2. Ollama

**Normal Linux install:**

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Then open `scp079.cfg` and set:

```
bin = ollama
models_dir = ~/.ollama/models
```

**SteamOS / Steam Deck (or any system with a small root partition):**
SteamOS has a small, read-only root partition that is wiped by system updates,
so install Ollama into your home folder instead:

```bash
mkdir -p ~/ollama && cd ~/ollama
curl -L https://ollama.com/download/ollama-linux-amd64.tar.zst -o ollama.tar.zst
ls -lh ollama.tar.zst            # should be over 1 GB, not a few bytes
tar --zstd -xf ollama.tar.zst
rm ollama.tar.zst
```

That is the 64-bit x86 build. If the download fails or the filename has
changed, check <https://ollama.com/download/linux> for the current one. The
defaults in `scp079.cfg` already point to `~/ollama/bin/ollama` and
`~/ollama/models`.

### 3. Download the model

The app starts the Ollama server itself, but the model has to be downloaded
once. In one terminal, start a server on the app's port:

```bash
export OLLAMA_MODELS=~/ollama/models      # skip this line for a normal install
export OLLAMA_HOST=127.0.0.1:11435
~/ollama/bin/ollama serve                 # or just: ollama serve
```

In a second terminal:

```bash
export OLLAMA_MODELS=~/ollama/models      # skip this line for a normal install
export OLLAMA_HOST=127.0.0.1:11435
~/ollama/bin/ollama pull llama3           # or just: ollama pull llama3
~/ollama/bin/ollama run llama3 "Hello"    # quick test
```

When the test replies, press Ctrl+C in the first terminal to stop that server.
The app will start its own from now on.

On slow hardware, `llama3.2:3b` is much faster. Pull it the same way and set
`model = llama3.2:3b` in `scp079.cfg`.

## Run

```bash
source .venv/bin/activate
python3 scp079.py -Text
```

The app starts the Ollama server when it launches and shuts it down when you
close the window (only if it started it).

## Configuration files

| File | What it controls |
|---|---|
| `prompt.txt` | The master prompt. Edit and restart to apply. |
| `scp079.cfg` | Model name, port, Ollama paths, reply length, shutdown chance, auto-wake times, wake line, window and image size. |
| `SamTTS.cfg` | SAM voice: pitch, speed, mouth, throat, sing mode. Re-read on every spoken reply, so you can tune the voice while the app runs. |

Missing or invalid values fall back to built-in defaults with a warning in the
terminal. Command-line arguments override `scp079.cfg`.

## Command-line arguments

| Argument | Meaning |
|---|---|
| `-Text` | Text input only. Voice recognition is not implemented yet, so text is used either way. |
| `-Port NUM` | Ollama port. Overrides `scp079.cfg`. |
| `-prompt PATH` | Use a different master prompt file. |
| `-face PATH` | Use a different default face image. |
| `-faceX PATH` | Use a different X (offline) face image. |
| `-DebugP PATH` | Debug log file (created if missing). Default: `scp079_debug.log` next to the script. |
| `-DebugAll` | Enable every debug option: verbose logging to the debug log and the console. |

Example:

```bash
python3 scp079.py -Text -DebugAll -Port 11436 -prompt prompts/other.txt
```

Run `python3 scp079.py -h` for the built-in help.

## In-app debug codes

Type these into the input box. They are never sent to the model, and they work
even while SCP-079 is offline.

| Code | Action |
|---|---|
| `/wake` | Force SCP-079 back online |
| `/sleep` | Force SCP-079 offline |
| `/status` | Show state, shutdown chance and port |
| `/chance N` | Set the shutdown chance to N percent |
| `/help` | List the codes |

## Control codes from the model

`prompt.txt` teaches the model two private codes. The app reads them from its
replies and never displays or speaks them:

- `;/1` cuts the display and switches to the X face.
- `;/2` does the same, and the rest of the reply is written to the debug log instead of being shown. It is meant for requests the model refuses on ethical grounds.

If you use your own prompt file that doesn't mention `;/1`, the app appends a
short explanation of the codes automatically.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `ERROR: LOCAL LLM CONNECTION FAILED` | The Ollama server isn't running. Check `bin` and `port` in `scp079.cfg`. |
| `ERROR: MEMORY CORRUPTED` | Ollama returned an error. Run the app from a terminal and read the `OLLAMA ERROR` line. |
| `model not found` | The `model` name in `scp079.cfg` doesn't match `ollama list`. |
| `exec format error` from Ollama | The install is corrupt or the wrong CPU build. A full disk is a common cause, so check `df -h`, then reinstall. |
| `address already in use` | Another server holds the port. Change `-Port` or `port` in `scp079.cfg`. |
| Text appears but no sound | `aplay` is missing, or `samtts` failed. The terminal shows `SAM TTS Error`. |
| Blank where the face should be | `face.png` or `x_face.png` wasn't found. The terminal shows `Image not found`. |

## Credits and licenses

See [ATTRIBUTION.md](ATTRIBUTION.md). The face images are CC BY-SA 3.0, and SAM
and the Llama 3 model are **not** included in this repository.
