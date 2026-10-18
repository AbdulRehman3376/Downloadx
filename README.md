# DownloadX — Free IDM-Style Download Manager for Windows

DownloadX is a Windows download manager built with Python. It provides a desktop interface for managing downloads, tracking progress, organizing the download queue, and supporting pause and resume functionality.

## Current Development Status

**Step 1 — Desktop Application (v0.2.0)**

The current desktop application includes:
- Multi-connection downloads
- Pause and resume functionality
- Download queue management
- Download progress tracking

**Browser Integration:** Chrome integration and automatic download capture are planned for a subsequent development phase. See `STATUS.md` for the latest implementation status.

## Getting Started

1. Install Python 3 from https://www.python.org/downloads/.
2. During installation, enable **Add Python to PATH**.
3. Double-click `DownloadX.bat` to launch the application.
4. Select **Add URL** and paste the download link.
5. Click **Download Now** to start the download.

## Running Tests

Run the automated test suite using:

`run_tests.bat`

## Project Structure

```text
DownloadX/
├── app.py                  Application entry point
├── DownloadX.bat           Application launcher
├── downloadx/
│   ├── engine.py           Download engine
│   ├── gui.py              Desktop user interface
│   └── util.py              Utility functions
├── tests/
│   └── test_engine.py      Automated engine tests
├── extension/              Browser extension components
├── native_host/            Native messaging host components
├── STATUS.md               Development status and roadmap
└── REQUIREMENTS.txt        Requirements and installation guide
```

The `extension` and `native_host` directories contain browser integration components. Refer to `STATUS.md` for their current implementation status.

## Responsible Use

Download only content that you own or are legally authorized to download. Always comply with applicable copyright laws and website terms of service.
