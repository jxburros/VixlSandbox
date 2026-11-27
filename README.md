# VixlSandbox

A Windows app for comparing how AI models make graphics and documents with different versions of
[Vixl](https://github.com/jxburros/Vixl), and without Vixl at all.

You give a brief once. The app sends it to the same model in separate sessions, one per **arm**:

| Arm | What the model gets |
| --- | --- |
| **Bare** | No tools. It answers in text; SVG, HTML or Markdown in its reply are saved as files. |
| **Code tools** | File tools, Python with drawing and document libraries, and a browser to see HTML and SVG. The fair control: what an agent without Vixl would use. |
| **Vixl 0.36.0**, **Vixl 0.35.0** … | One installed Vixl version's MCP tools plus the same file tools. Add as many versions as you like. |

Every arm gets the same opening instructions; only the description of its tools differs. Each session has its own
empty folder and its own conversation, so nothing leaks between arms.

## Install

1. Download `VixlSandbox.exe` from the latest [release](../../releases) (or, for an unreleased build, from the
   **Windows app** workflow run's artifacts in the [Actions](../../actions) tab).
2. Run it. Windows SmartScreen may say the publisher is unknown, because the file is not code-signed yet: choose
   **More info → Run anyway**.

The app opens in its own window. It needs no Python on your computer: the first time you add a Vixl version, it
downloads its own Python (about 30 MB) into the app's data folder.

## First launch: add Vixl versions

The app opens on the **Versions** tab until a Vixl version is installed.

- **Add a Vixl version**: pick any published release (every `vixl-engine` release on PyPI) and press **Install**.
  Each version goes into its own environment with that release's agent skill, and becomes its own arm. Install a
  few to compare them, for example the newest and the one before.
- **Check for new versions** looks for new releases; a banner offers each new one once it is published.
- **Code-tools environment**: press **Install** once to enable the Code tools arm (Pillow, matplotlib, reportlab,
  python-pptx and friends; Vixl is not in it).
- **Remove** deletes a version. Runs that used it keep their results, but cannot get follow-ups until you reinstall
  it.

## Running a comparison

1. **New run**: choose a provider (Anthropic, OpenAI, Meta (Muse), Moonshot (Kimi), local Ollama or LM Studio, or any
   OpenAI-compatible URL), paste the API key, and pick a model (**Load models** lists them).
   Tick **Remember on this computer** to keep the key in Windows Credential Manager; otherwise it is forgotten when the
   app closes. Keys are never written to the app's files or to exports.
2. Tick the arms, write the brief, choose the number of repeats, and **Start run**.
3. Each session's log updates live. When they finish, results show side by side, **blind** at first (Output A, B,
   C …). Pick the best, or Tie / None is good, and the labels appear.
4. **Follow-up change**: type a change (for example "make the title larger") and **Send to every session**. Each
   session continues its own conversation and files, so arms are compared change by change. Every round keeps its own
   snapshot; the round tabs switch between them.
5. **Judge…** scores the round on screen with a six-part design rubric, using any model (a different one from the
   model under test is fairer).
6. Click a file name to open it with its own app (a `.pptx` opens in PowerPoint).
7. **Scoreboard** totals every model and arm: how often it delivered, blind-vote wins, judge mean, time, tokens and
   cost.

## Export

**Export** on a run, or **Export selected / Export all runs** on the Runs tab, saves one ZIP in
`Downloads\VixlSandbox`:

- `index.html`: an offline report with every run, round and generation side by side;
- `GUIDE.md`: how the runs were made (arms, Vixl versions, settings), how to read them, and results by model and arm;
- `data.json`: everything as data;
- per generation and round: the preview, every file, and `how-it-was-made.md`, which lists what was asked, the tools,
  every model message and every tool call with its full arguments and result.

## Where things are kept

Everything lives in `%LOCALAPPDATA%\VixlSandbox`: `versions\` (one environment per Vixl version), `tools-env\`,
`python\` (the downloaded Python), `results\` (runs, rounds and `lab.db`) and `app.log`. Deleting the folder resets
the app.

## Good to know

- The Code tools arm runs Python the model wrote, as you, with a time limit, no API keys and no Vixl. It is a guard
  rail, not a sandbox: it can read other files on your computer, so use briefs you would run yourself.
- HTML and SVG previews use Microsoft Edge, which every Windows 10 and 11 has. PowerPoint and Word previews need
  LibreOffice; without it those files still open with their own apps.
- The window uses Edge WebView2. On a computer without it, the app opens in your browser instead; use **Quit** in the
  page to stop it.

## Building and developing

```bash
pip install -e ".[dev]" vixl-engine   # vixl-engine stands in for an installed version in the tests
pytest -n auto                        # pytest -m network also installs a real release with uv
python -m vixl_sandbox --browser      # run from source in your browser
```

The **Windows app** workflow builds `VixlSandbox.exe` with PyInstaller (`packaging/vixl_sandbox.spec`) on every push
and pull request, and attaches it to the release when a `v*` tag is pushed. To build locally on Windows:

```bash
pip install -e ".[build]"
python packaging/make_icon.py
pyinstaller --noconfirm packaging/vixl_sandbox.spec   # dist\VixlSandbox.exe
```
