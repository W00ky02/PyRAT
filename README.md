# PyRAT

PyRAT is a Discord RAT (Remote Access Trojan) made with Python.

# Disclaimer!!!

I'm not responsible for any damage. This tool is for educational purpose only!

# Tutorial

You will need a Discord Bot for this tool. Generate one on the Discord Developler Portal and copy the Bot Token.

First you need to install the requirements.

```bash
pip3 install -r requirements.txt
```

Then paste the Bot Token in the Python Script.

If you want to compile it into an .exe, run the `compiler.bat´ and the .exe will be created.

Or else you cound just run 

```bash
pyinstaller --onefile --noconsole --copy-metadata imageio --copy-metadata imageio-ffmpeg --hidden-import=imageio --hidden-import=imageio_ffmpeg PyRAT.py
```

# Functions

.ss - take a screenshot
.sc - record screen for 5 seconds
.cmd <command> - run cmd commands
.dir - list files in current directory
.chdir <directory> - change current directory
.download <url> - download file from url
.delete <filename> - delete file from target computer
.upload <filename> - upload file from target computer
.geo - get maps location of target computer
.restart - restart computer
.run <filename> - run any file on target computer
.join - join voice channel and listen live mic audio 
.leave - leave voice channel
.shutdown - shut down computer
.logoff - log current user off
.bluescreen - bluescreen target computer
.notepad - open notepad
.message <text> - show message on target computer
.moveto - move mouse to specific coordinates
.click - click mouse at current position
.whoami - show user of computer
.website - open website
.write - write anything on target computer
.wc - take a picture with webcam
.wcr - record webcam for 5 second
.help - show these commands

# Warning

The live stream mic does't work in the .exe. I don't know why but everything else works.
