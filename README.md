# Peanuts Desktop

**A desktop graphical interface for the Peanuts Native build workflow.**

This repository is being used to develop a more approachable desktop GUI for preparing Peanuts Native game builds. The goal is to make the existing command-line workflow easier to understand and use, especially for people who do not want to work directly with Python scripts, command-line arguments and terminal output.

> **This is not a PC port of Peanuts Native.** Peanuts Native is an Android project. Peanuts Desktop is intended to run on a desktop computer (with Linux and SteamOS, including the Steam Deck, as our primary focus) and help prepare builds for compatible Android devices. The AYN Thor is our first target device, while the workflow should remain useful for other supported Android devices too.

## What we are trying to build

A single, clear desktop interface for the existing Peanuts Native desktop build process:

- Select and inspect a user's own game dump or an existing intermediate package.
- Guide the user through the inputs and options that the underlying builder actually supports.
- Run the existing import and packaging steps without requiring users to assemble Python commands themselves.
- Show meaningful progress, current stages, logs and actionable errors.
- Where supported by the existing workflow, help transfer the finished Android build to a connected device using ADB.

The interface should make the process more intuitive and accessible without replacing the underlying builder or duplicating its game-building logic. We will first audit the existing scripts, interfaces, dependencies and platform limitations, then choose the smallest maintainable design that safely reuses them.

## What this project is not

- It is **not** a Windows or Linux version of the Peanuts Native Android app.
- It is **not** a PC game port, emulator, or a way to run the resulting Switch games on the desktop.
- It is **not** a replacement for Peanuts Native's Android app or its runtime.
- It is **not** a new game-building engine. The intention is to provide a GUI for the existing desktop CLI workflow.

The desktop computer is the build host; the intended game runtime remains a compatible Android device.

## Current status

**Early audit and design stage — no new desktop application is being claimed as complete.**

The upstream repository already contains desktop build scripts and a Tk-based `scripts/pack_gui.py` for the packaging stage. That existing GUI is not the same as the broader, integrated workflow we are investigating. We will inspect what it already does and reuse it where appropriate rather than assume we need to rewrite it.

The current builder documentation is the source of truth for supported inputs, options, dependencies and limitations:

- [Peanuts Native desktop builder documentation](peanuts/docs/BUILDER.md)
- [Existing pack-stage GUI](scripts/pack_gui.py)
- [Upstream Peanuts Native project](https://github.com/igawa6/peanuts-native)

Features, platform support and device-transfer behavior will only be advertised here after they have been implemented and verified.

## Design principles

1. **Reuse before rewriting.** Keep the existing builder as the source of truth for build behavior.
2. **Make complexity understandable.** Present clear steps, safe defaults, useful progress and readable errors.
3. **Do not hide limitations.** If the builder cannot handle a game or operation, the GUI must explain that rather than imply success.
4. **SteamOS/Linux first.** Prioritize a reliable experience on Linux desktop and SteamOS, then assess other platforms deliberately.
5. **Android is the destination.** Keep the AYN Thor as the first device target without unnecessarily hard-coding the application to one device.
6. **Security is mandatory.** User keys, firmware, game dumps and credentials stay local and must never be committed, uploaded, logged or transmitted by the GUI.

## Security and user-owned files

This is a public repository. **Never commit or share `prod.keys`, other key files, firmware, game dumps, built game packages, credentials, tokens, private configuration or personal data.** Do not paste their contents into issues, pull requests, logs, screenshots or AI work orders.

Use local files and secure local configuration for sensitive inputs. Review every staged change before committing or pushing. The repository's [SECURITY.md](SECURITY.md) defines the mandatory safeguards for contributors and automated coding agents.

The underlying Peanuts Native builder documentation explains the expected local inputs and workflow. Users are responsible for supplying files they are legally entitled to use.

## Relationship to Peanuts Native

This repository is based on [igawa6/peanuts-native](https://github.com/igawa6/peanuts-native). The upstream project remains the source of the Android application, game profiles, build tools and their documentation. This repository's intended contribution is the desktop user interface around the existing build workflow.

Please consult upstream documentation and preserve its attribution and licensing notices. This project does not claim ownership of Peanuts Native or its underlying components.

## Development approach

Work will proceed in deliberate stages:

1. Audit the builder, existing GUI, command interfaces, dependencies and SteamOS compatibility.
2. Define the smallest useful GUI workflow based on verified capabilities.
3. Implement the interface while keeping build operations delegated to the existing tools.
4. Add tests for input validation, subprocess handling, progress reporting, cancellation, file safety and device transfer.
5. Verify on SteamOS/Linux and the AYN Thor before describing features as supported.

Until those steps are complete, this README describes the project's purpose and direction, not a promise that the proposed features are already available.
