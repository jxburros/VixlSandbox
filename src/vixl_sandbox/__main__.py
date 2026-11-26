"""Start VixlSandbox: the local server, then the app's window (or the default browser)."""

import argparse
import logging
import os
import socket
import sys
import threading
import time
import webbrowser

from . import __version__, system


def free_port(preferred=8765):
    for port in (preferred, 0):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return probe.getsockname()[1]
    raise RuntimeError("no free local port")


def start_server(app, port):
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            return server
        time.sleep(0.05)
    raise RuntimeError("the local server did not start")


def open_window(url):
    """Open the app in its own window (Edge WebView2 on Windows). Returns False when no window can be made."""
    try:
        import webview
    except ImportError:
        return False
    try:
        webview.create_window("VixlSandbox", url, width=1360, height=900, min_size=(900, 600))
        webview.start()
    except Exception:  # noqa: BLE001 - no WebView runtime: fall back to the browser
        logging.exception("could not open the app window")
        return False
    return True


def self_test(home, report):
    """Start the server on a free port, read /api/config, write "ok <version> <arms>" or the error to ``report``."""
    import json
    import urllib.request

    from .server import create_app

    try:
        app = create_app(home, token="self-test")
        port = free_port(0)
        server = start_server(app, port)
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/config", headers={"X-Lab-Token": "self-test"})
        with urllib.request.urlopen(request, timeout=30) as response:
            config = json.loads(response.read())
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=30) as response:
            page = response.read().decode("utf-8")
        assert "VixlSandbox" in page and config["app"] == __version__
        from .versions import find_uv

        find_uv()
        server.should_exit = True
        result, code = f"ok {__version__} arms={','.join(a['id'] for a in config['arms'])}", 0
    except Exception as exc:  # noqa: BLE001 - the report says what failed
        result, code = f"failed: {type(exc).__name__}: {exc}", 1
    with open(report, "w", encoding="utf-8") as out:
        out.write(result + "\n")
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(prog="VixlSandbox", description=__doc__)
    parser.add_argument("--browser", action="store_true", help="open in the default browser, not the app window")
    parser.add_argument("--port", type=int, default=8765, help="preferred local port (default 8765)")
    parser.add_argument("--home", help="data folder (default: the app's folder in your user profile)")
    parser.add_argument("--self-test", metavar="FILE",
                        help="start the server, call its API once, write the result to FILE and exit (for builds)")
    options = parser.parse_args(argv)
    if options.home:
        os.environ["VIXL_SANDBOX_HOME"] = options.home
    home = system.home()
    if sys.stderr is None or sys.stdout is None:
        # A windowed executable has no console: send anything printed to a log file instead of failing on it.
        console = open(home / "console.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - lives as long as the app
        sys.stdout = sys.stdout or console
        sys.stderr = sys.stderr or console
    logging.basicConfig(filename=home / "app.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.info("VixlSandbox %s starting; data in %s", __version__, home)

    from .server import create_app

    if options.self_test:
        return self_test(home, options.self_test)
    port = free_port(options.port)
    stop = threading.Event()
    app = create_app(home, on_quit=stop.set)
    server = start_server(app, port)
    url = f"http://127.0.0.1:{port}/"
    if options.browser or not open_window(url):
        # In a browser the page's Quit button stops the app (closing the tab does not).
        app.state.browser_mode = True
        webbrowser.open(url)
        print(f"VixlSandbox is running at {url}; use Quit in the page to stop it.")
        try:
            stop.wait()
        except KeyboardInterrupt:
            pass
    server.should_exit = True


if __name__ == "__main__":
    sys.exit(main())
