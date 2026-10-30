# SAIRA — Voice Control Module

Saira Voice Control Engine is a **desktop voice-control engine prototype** in the
SAIRA personal AI ecosystem. It is the existing Python voice module, not a new
assistant or a finished public product.

## Current capabilities

- Detects and selects microphone inputs, can select a device by index or name,
  and retries after stream failures.
- Listens for the “wake up Saira” phrase, recognizes speech, and routes commands.
- Speaks responses with gTTS and `mpg123`; a popup terminal supports typed commands.
- Launches configured desktop applications and discovers installed `.desktop` apps.
- Opens configured web shortcuts; supports time/date, Wikipedia lookups, project
  template workflows, and the existing system-control commands.
- Includes microphone listing and recording/recognition diagnostics.
- Keeps arbitrary shell command execution disabled by default.

Speech recognition, Wikipedia lookup, and gTTS require network access. No LLM
dependency is used. See [Limitations](#limitations) for details.

## Project structure

```text
main.py                 CLI entry point
config.py               Runtime and device settings
audio/                  Microphone, diagnostics, audio environment, TTS
commands/               System, application, web, Wikipedia, and project routes
core/                   Assistant loop, state, wake phrase, command router
ui/                     Popup terminal client and server
utils/                  Logging and shared process/text helpers
tests/                  Offline smoke tests
templates/               Project templates (new_project is currently empty)
logs/                    Runtime log location; generated logs are ignored
requirements.txt         Python dependencies
run_saira.sh              Prototype launcher
```

## Requirements

- Ubuntu 24.04 with Python 3.12.
- A working microphone and PipeWire/PulseAudio or ALSA input.
- PortAudio development headers to build PyAudio, plus `mpg123` for speech output.
- Internet access for Google speech recognition, gTTS, and Wikipedia lookups.
- A terminal emulator for the optional popup console. VS Code is needed for the
  project-creation commands.

Install system packages and Python dependencies:

```bash
sudo apt update
sudo apt install python3.12-venv python3-dev portaudio19-dev build-essential mpg123
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

In VS Code, select `.venv/bin/python` as the workspace interpreter after creating
the environment.

## Run

From the project directory:

```bash
./run_saira.sh
```

The launcher uses `.venv` when present, otherwise Python 3.12 or later from
`PATH`. The direct entry point is:

```bash
python main.py
```

Useful options:

```bash
python main.py --help
python main.py --list-mics
python main.py --test-mic
python main.py --mic 6
python main.py --debug
python main.py --no-popup
python main.py --mute
```

`--test-mic` records short ambient and speech samples in `/tmp`, then sends up to
three captured phrases to Google for recognition. It is not an offline test.

## Microphone selection and troubleshooting

Run `python main.py --list-mics` to see the inputs and automatic preference order.
Use `python main.py --mic <index-or-name>` for one run. The same choice can be set
with `SAIRA_MIC`.
Alternatively set `MIC_DEVICE_INDEX` in `config.py` to `None`/`"auto"`, an index,
or a substring of the device name.

If the microphone is missing or quiet:

1. Check Ubuntu **Settings → Sound → Input** and confirm the input is not muted.
2. Run `python main.py --list-mics`, then try a listed device with `--mic`.
3. Run `python main.py --test-mic` and review its level/clipping advice.
4. Check the log at `logs/saira.log`. `pactl` is optional; without it the app can
   still enumerate PortAudio devices.

## Configuration

Edit `config.py` for the wake phrase/name, microphone preference, recognition
language, timing, TTS, popup terminal, application/web registries, project paths,
and logging. `TTS_ENABLED = False` disables speech output; `SHELL_ENABLED` is
false by default and should remain so unless the fixed allow-list is deliberately
reviewed. Set `SAIRA_PROJECTS_DIR` to override the default project directory
(`~/Documents/vscode/Project`).

## Tests

Offline smoke tests do not require a microphone, speaker, or internet connection:

```bash
python -m unittest tests.smoke_test -v
```

## Limitations

- Speech recognition and speech synthesis use external Google services; there is
  no offline recognizer or synthesizer and no LLM.
- A visible microphone device is not proof that recording or recognition works;
  hardware and network behavior must be checked on the target machine.
- Application launching depends on installed Linux desktop entries and executables.
- Closing browser tabs depends on Chrome exposing its local DevTools endpoint.
- The `templates/new_project/` directory is an empty placeholder; its command
  creates a folder but has no project files to copy until a template is supplied.
- System suspend, reboot, and power-off commands invoke Ubuntu `systemctl`;
  restart and shutdown require confirmation.

This repository is a private prototype for integration into SAIRA. It is not a
finished, supported, or publicly released product.