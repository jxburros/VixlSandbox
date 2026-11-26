# VixlSandbox: notes for agent contributors

A Windows desktop app (`src/vixl_sandbox`) that runs one brief through the same model in separate sessions, with no
tools, with code tools, or with any installed Vixl version, and compares the results. README.md explains it for
users.

- **Vixl is installed, never imported.** Each version lives in its own environment (`versions.py`, made with uv) and
  runs as its own `python -m vixl mcp` process (`mcp_client.py`). The app must not import `vixl`; tests register the
  test environment's interpreter as a stand-in version.
- **Arms** are `bare`, `code` and `vixl@<version>` (`arms.py`). Every arm gets `BASE_SYSTEM`; only the tool paragraph
  differs. Keep it that way: the comparison is only fair if nothing else changes between arms.
- **Keys** stay in memory or in the OS credential store (`system.KeyStore`), never in files, logs, events or exports.
  Tests check that no key reaches disk.
- **Windows first.** Subprocesses use `CREATE_NO_WINDOW`; the packaged app has no console (`sys.stdout` may be None);
  close file handles before deleting files.
- Run `ruff check src tests packaging` and `pytest -n auto`. `pytest -m network` installs a real Vixl release.
- The Windows executable is built by `.github/workflows/build.yml` (PyInstaller, `packaging/vixl_sandbox.spec`) and
  self-tested with `--self-test`.
- Do not put model names, signatures or authorship trailers in code, docs or commit/PR text.
