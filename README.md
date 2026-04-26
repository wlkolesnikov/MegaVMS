# MegaVMS GTK

GTK-based Hikvision desktop client for Linux/X11.

This repository contains the current GTK implementation of the project that was split out from a larger local workspace. The app works directly with the native Hikvision Linux SDK (`HCNetSDK` + `PlayCtrl`) and is focused on four practical areas:

- system diagnostics and baseline comparison;
- archive discovery and playback;
- archive download queue by time range;
- live grid/focus viewing with snapshots.

## Current status

The project is already usable, but it is not positioned as a finished multi-vendor VMS yet.

Implemented today:

- GTK3 desktop UI with tabs: `Online`, `Archive`, `Reports`, `System`
- startup and periodic diagnostics
- saved runtime configuration
- archive day discovery with calendar marks
- archive timeline, native playback, seek, pause/resume, speed, frame-step
- SDK-backed zoom/pan for archive playback
- archive download queue with a dedicated background worker
- live grid and focus modes
- saved custom views and layout presets
- snapshot capture for visible channels
- single-channel archive coverage report

Not implemented yet:

- fullscreen focus mode
- multi-channel coverage report
- JSON/export-oriented support report
- second vendor backend

## Platform and requirements

Current target environment:

- Linux
- X11 session
- Python 3.10+
- GTK 3 via PyGObject
- Hikvision Linux SDK libraries available locally

The UI uses X11 window binding for native video surfaces. Wayland is not a supported target in the current implementation.

Required Hikvision SDK layout is expected under `HIKVISION_LIB_DIR` or, by default, under `~/.local/lib/hikvision`.

Minimum expected files:

```text
~/.local/lib/hikvision/
├── libhcnetsdk.so
├── libPlayCtrl.so
├── libHCCore.so
├── libhpr.so
└── HCNetSDKCom/
```

## Runtime configuration

The application stores its runtime state in:

```text
.data/runtime_config.json
```

This includes connection settings, diagnostic baseline/current state, and saved online views.

## Environment variables

- `HIKVISION_LIB_DIR`: overrides the default SDK directory
- `HIK_PLAYER_LOG_LEVEL`: logging level, for example `DEBUG` or `INFO`
- `HIK_PLAYER_ARCHIVE_DAYS_FALLBACK_SCAN`: optional archive-day fallback scan toggle used by the backend

## Quick start

Install system dependencies for your distro first. At minimum you need Python, GTK3 introspection bindings, and the Hikvision SDK files shown above.

Run the app from the repository root:

```bash
cd sdk-hik-GTK
python3 app.py
```

On startup the launcher ensures `LD_LIBRARY_PATH` includes the Hikvision SDK directory and `HCNetSDKCom`.

## Project structure

```text
.
├── app.py
├── contracts.py
├── core.py
├── hikvision_plugin.py
├── timeline.py
├── ui.py
├── TODO.md
└── ARCHITECTURE.md
```

High-level roles:

- `app.py`: GTK entrypoint and runtime environment bootstrap
- `contracts.py`: domain models and capability flags
- `core.py`: orchestration layer and worker executors
- `hikvision_plugin.py`: Hikvision-specific SDK integration
- `timeline.py`: archive timeline widget
- `ui.py`: GTK screens and interaction logic

## Development notes

- The backend is currently `HikvisionPlugin` only.
- `core.py` uses a separate executor for archive downloads so that long downloads do not block the rest of the archive UI.
- The codebase is capability-driven: the UI enables features based on backend capability flags instead of backend name checks.

## Documentation

Additional project documents:

- [ARCHITECTURE.md](ARCHITECTURE.md): current code-oriented architecture description
- [TODO.md](TODO.md): implemented scope and deferred items

These documents currently reflect the actual code more closely than older design assumptions.
