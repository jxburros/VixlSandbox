# PyInstaller spec for the Windows executable: `pyinstaller packaging/vixl_sandbox.spec` from the repository root,
# after `python packaging/make_icon.py`. Produces dist/VixlSandbox.exe: one windowed file with the app, its page,
# and the uv executable that installs Vixl versions.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules
from uv import find_uv_bin

root = Path(SPECPATH).parent
datas = [(str(root / "src" / "vixl_sandbox" / "static"), "vixl_sandbox/static")]
binaries = [(find_uv_bin(), "uv")]
hiddenimports = (collect_submodules("uvicorn") + collect_submodules("keyring.backends")
                 + collect_submodules("mcp.client") + ["webview"])
for package in ("pypdfium2", "pypdfium2_raw", "anthropic", "webview"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

analysis = Analysis([str(root / "packaging" / "launch.py")], pathex=[str(root / "src")], binaries=binaries,
                    datas=datas, hiddenimports=hiddenimports, excludes=["tkinter", "matplotlib", "numpy"])
pyz = PYZ(analysis.pure)
icon = root / "packaging" / "icon.ico"
exe = EXE(pyz, analysis.scripts, analysis.binaries, analysis.datas, [], name="VixlSandbox", console=False,
          icon=str(icon) if icon.is_file() else None, upx=False)
