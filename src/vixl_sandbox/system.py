"""Where the app keeps its data, remembered keys, and opening files with the computer's own apps."""

import os
from pathlib import Path
import subprocess
import sys

SERVICE = "VixlSandbox"


def home():
    """The app's data folder: ``VIXL_SANDBOX_HOME``, else %LOCALAPPDATA%\\VixlSandbox on Windows,
    ~/Library/Application Support/VixlSandbox on macOS and ~/.local/share/VixlSandbox elsewhere."""
    if os.environ.get("VIXL_SANDBOX_HOME"):
        folder = Path(os.environ["VIXL_SANDBOX_HOME"])
    elif os.name == "nt":
        folder = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "VixlSandbox"
    elif sys.platform == "darwin":
        folder = Path.home() / "Library" / "Application Support" / "VixlSandbox"
    else:
        folder = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "VixlSandbox"
    folder = folder.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def exports_folder():
    """Downloads\\VixlSandbox when the user has a Downloads folder, else the app's own exports folder."""
    downloads = Path.home() / "Downloads"
    folder = downloads / "VixlSandbox" if downloads.is_dir() else home() / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def open_path(path, reveal=False):
    """Open a file with its default app, or show it in Explorer/Finder when ``reveal``."""
    path = Path(path)
    if os.name == "nt":
        if reveal:
            subprocess.Popen(["explorer", f"/select,{path}"])  # noqa: S603, S607 - fixed program, a path we resolved
        else:
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606 - opening a file the user clicked
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if reveal else ["open", str(path)])  # noqa: S603, S607
    else:
        subprocess.Popen(["xdg-open", str(path.parent if reveal else path)])  # noqa: S603, S607


class KeyStore:
    """API keys remembered on this computer, in the operating system's credential store (Windows Credential
    Manager, the macOS Keychain or the Secret Service), never in the app's files. Without a usable store, nothing
    is remembered."""

    def __init__(self, backend=None):
        if backend is None:
            try:
                import keyring

                backend = keyring
            except ImportError:
                backend = None
        self.backend = backend

    def available(self):
        if self.backend is None:
            return False
        try:
            name = type(self.backend.get_keyring()).__name__ if hasattr(self.backend, "get_keyring") else "custom"
        except Exception:  # noqa: BLE001
            return False
        return "fail" not in name.lower() and "null" not in name.lower()

    def get(self, slot):
        if not self.available():
            return ""
        try:
            return self.backend.get_password(SERVICE, slot) or ""
        except Exception:  # noqa: BLE001 - a locked or broken store means "not remembered"
            return ""

    def set(self, slot, key):
        if not self.available():
            raise ValueError("this computer has no credential store to remember keys in")
        self.backend.set_password(SERVICE, slot, key)

    def forget(self, slot):
        if not self.available():
            return
        try:
            self.backend.delete_password(SERVICE, slot)
        except Exception:  # noqa: BLE001 - forgetting a key that was never saved is fine
            pass
