@echo off
pyinstaller --onefile --noconsole --copy-metadata imageio --copy-metadata imageio-ffmpeg --hidden-import=imageio --hidden-import=imageio_ffmpeg PyRAT.py
pause
