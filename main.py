#!/usr/bin/env python3
"""Start SushrutAI: local AI models (Ollama), database (if Postgres is configured), API and web app.

    python main.py                 # start everything, open the browser
    python main.py --pull          # also download any missing AI models first
    python main.py --prod          # production build of the web app instead of the dev server
    python main.py --no-web        # API only (or --no-api for the web app only)
    python main.py --check         # smoke test: start everything, confirm healthy, stop
    python main.py --local-only    # only this PC can open it (by default phones on the home network can too)

Uses only the Python standard library, so any Python 3.10+ can run it. Services that are already running are
reused, not restarted. Ctrl+C stops only what this script started (Ollama keeps running if it was already up;
the Postgres container is left running so data stays available).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API_DIR = ROOT / "services" / "api"
WEB_DIR = ROOT / "apps" / "web"
COMPOSE = ROOT / "infra" / "docker-compose.yml"
IS_WIN = os.name == "nt"

API_PORT, WEB_PORT = 8787, 3000  # overridable with --api-port / --web-port
DEFAULT_MODELS = {"LLM_MODEL": "medgemma:4b", "RX_VLM_MODEL": "qwen3-vl:4b-instruct",
                  "EMBED_MODEL": "bge-m3", "GUARD_MODEL": "llama-guard3:1b",
                  "FAST_LLM_MODEL": "llama3.2:3b",  # Settings → Faster / Instant chat (non-symptom questions)
                  "SUGGEST_MODEL": "qwen2.5:0.5b", "SUGGEST_MODEL_INDIC": "gemma3:1b"}  # follow-up suggestion chips (CPU)

COLORS = {"ollama": "35", "db": "34", "api": "36", "web": "32", "main": "33"}
_print_lock = threading.Lock()


# ---------------------------------------------------------------- output helpers
def say(tag: str, msg: str) -> None:
    with _print_lock:
        print(f"\033[{COLORS.get(tag, '0')}m[{tag:>6}]\033[0m {msg}", flush=True)


def fail(msg: str) -> None:
    say("main", f"\033[31m{msg}\033[0m")


def stream(tag: str, proc: subprocess.Popen, quiet: bool = False) -> None:
    for raw in iter(proc.stdout.readline, b""):
        line = raw.decode("utf-8", "replace").rstrip()
        if line and not quiet:
            say(tag, line)


# ---------------------------------------------------------------- environment
def load_env() -> dict[str, str]:
    """`.env` at the repo root (same file the API reads), overridden by the real environment."""
    env: dict[str, str] = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k in env or k in DEFAULT_MODELS or k in
                ("DATABASE_URL", "OLLAMA_BASE_URL")})
    return env


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def lan_ips() -> list[str]:
    """This PC's addresses on the home network (private IPv4 ranges only), best first."""
    found: list[str] = []
    try:  # the address used for the default route (no packet is sent for a UDP connect)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        found += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass

    def private(ip: str) -> bool:
        a, b = (int(x) for x in ip.split(".")[:2])
        return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)

    return [ip for ip in dict.fromkeys(found) if private(ip)]


def http_ok(url: str, timeout: float = 2.0) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read() if r.status < 500 else None
    except Exception:  # noqa: BLE001
        return None


def wait_for(check, seconds: float, what: str, proc: subprocess.Popen | None = None) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        if proc is not None and proc.poll() is not None:
            time.sleep(0.5)  # let its last output lines print
            fail(f"{what} exited during startup (exit code {proc.returncode}) — see its output above")
            return False
        time.sleep(1)
    fail(f"{what} did not come up within {int(seconds)} s")
    return False


def find_exe(name: str, *fallbacks: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for f in fallbacks:
        p = Path(os.path.expandvars(f))
        if p.exists():
            return str(p)
    return None


# ---------------------------------------------------------------- process management
class Procs:
    """Children this script started; stopped (with their child processes) on exit."""

    def __init__(self) -> None:
        self.items: list[tuple[str, subprocess.Popen]] = []

    def start(self, tag: str, cmd: list[str], cwd: Path, env: dict | None = None, quiet: bool = False) -> subprocess.Popen:
        kw: dict = {}
        if IS_WIN:  # own process group: Ctrl+C reaches only this script, which then stops the whole tree
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        p = subprocess.Popen(cmd, cwd=cwd, env={**os.environ, **(env or {})}, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)
        threading.Thread(target=stream, args=(tag, p, quiet), daemon=True).start()
        self.items.append((tag, p))
        return p

    def died(self) -> str | None:
        return next((tag for tag, p in self.items if p.poll() is not None), None)

    def stop_all(self) -> None:
        for tag, p in reversed(self.items):
            if p.poll() is not None:
                continue
            say("main", f"stopping {tag}")
            if IS_WIN:
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
            else:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                    p.wait(10)
                except Exception:  # noqa: BLE001
                    os.killpg(p.pid, signal.SIGKILL)


# ---------------------------------------------------------------- services
def start_ollama(procs: Procs, env: dict, pull: bool) -> bool:
    base = env.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    if http_ok(f"{base}/api/version"):
        say("ollama", f"already running at {base}")
    else:
        exe = find_exe("ollama", r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe", "/usr/local/bin/ollama")
        if not exe:
            fail("Ollama is not installed — get it from https://ollama.com, or run with --no-ai")
            return False
        say("ollama", "starting ollama serve")
        p = procs.start("ollama", [exe, "serve"], ROOT, quiet=True)
        if not wait_for(lambda: http_ok(f"{base}/api/version"), 40, "Ollama", p):
            return False
    tags = json.loads(http_ok(f"{base}/api/tags") or b'{"models": []}')
    have = {m["name"] for m in tags.get("models", [])} | {m["name"].split(":")[0] for m in tags.get("models", [])}
    wanted = sorted({env.get(k, v) for k, v in DEFAULT_MODELS.items()})
    missing = [m for m in wanted if m not in have and f"{m}:latest" not in have]
    if missing and pull:
        exe = find_exe("ollama", r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
        for m in missing:
            say("ollama", f"pulling {m} (this can take a while)")
            subprocess.run([exe, "pull", m], check=False)
    elif missing:
        say("ollama", "\033[33mmissing models: " + ", ".join(missing) + " — run `python main.py --pull` "
                      "(chat and prescription reading need them)\033[0m")
    else:
        say("ollama", "models ready: " + ", ".join(wanted))
    return True


def start_database(env: dict) -> bool:
    url = env.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        say("db", f"SQLite ({ROOT / 'data' / 'sushrut.db'}) — no database server needed")
        return True
    if port_open(5432):
        say("db", "Postgres already running on :5432")
        return True
    docker = find_exe("docker", r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
    if not docker:
        fail("DATABASE_URL points to Postgres but Docker is not installed")
        return False
    if subprocess.run([docker, "info"], capture_output=True).returncode != 0:
        desktop = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
        if IS_WIN and desktop.exists():
            say("db", "starting Docker Desktop")
            subprocess.Popen([str(desktop)], creationflags=subprocess.DETACHED_PROCESS)
        if not wait_for(lambda: subprocess.run([docker, "info"], capture_output=True).returncode == 0, 150,
                        "Docker"):
            return False
    say("db", "starting Postgres container")
    r = subprocess.run([docker, "compose", "-f", str(COMPOSE), "up", "-d", "postgres"], capture_output=True, text=True)
    if r.returncode != 0:
        fail(r.stderr.strip()[-400:])
        return False
    return wait_for(lambda: port_open(5432), 60, "Postgres")


def start_api(procs: Procs, reload: bool, lan: list[str]) -> bool:
    if http_ok(f"http://127.0.0.1:{API_PORT}/healthz"):
        say("api", f"already running on :{API_PORT}")
        if lan and not http_ok(f"http://{lan[0]}:{API_PORT}/healthz"):
            say("api", "\033[33mit was started for this PC only, so phones can't reach it — stop it (or the old "
                       "launcher) and run this again to open it to the home network\033[0m")
        return True
    if port_open(API_PORT):
        fail(f"port {API_PORT} is used by another program — stop it, then run this again")
        return False
    py = API_DIR / ".venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")
    if not py.exists():
        fail(f"API environment missing — in {API_DIR} run: uv venv --python 3.12 .venv && "
             'uv pip install -p .venv -e ".[dev]"')
        return False
    cmd = [str(py), "-m", "uvicorn", "app.main:app", "--port", str(API_PORT), "--log-level", "warning",
           "--host", "0.0.0.0" if lan else "127.0.0.1"]
    origins = ",".join(dict.fromkeys([f"http://localhost:{WEB_PORT}", f"http://127.0.0.1:{WEB_PORT}", "http://localhost:3000",
                                      *(f"http://{ip}:{WEB_PORT}" for ip in lan)]))
    if reload:
        cmd += ["--reload", "--reload-dir", "app"]
    say("api", "starting API (first start loads speech models, ~20 s)")
    p = procs.start("api", cmd, API_DIR, env={"HF_HUB_OFFLINE": "1", "PYTHONIOENCODING": "utf-8", "CORS_ORIGINS": origins})
    return wait_for(lambda: http_ok(f"http://127.0.0.1:{API_PORT}/healthz"), 120, "API", p)


def start_web(procs: Procs, prod: bool, lan: list[str]) -> bool:
    if port_open(WEB_PORT):
        if http_ok(f"http://127.0.0.1:{WEB_PORT}/", timeout=20) is not None:
            say("web", f"already running on :{WEB_PORT}")
            if lan:
                say("web", "\033[33mif phones can't open it, it was started without home-network access — stop it "
                           "and run this again\033[0m")
            return True
        fail(f"port {WEB_PORT} is used by another program — stop it, then run this again")
        return False
    npm = find_exe("npm.cmd" if IS_WIN else "npm", r"C:\Program Files\nodejs\npm.cmd")
    if not npm:
        fail("Node.js is not installed — get Node 20+ from https://nodejs.org")
        return False
    # The browser reaches the API on the same host it opened the app from (localhost here, 192.168.x.y on a
    # phone), so only the port is passed. SUSHRUT_DEV_ORIGINS lets Next's dev server answer other devices.
    env = {"NEXT_PUBLIC_API_PORT": str(API_PORT), "SUSHRUT_DEV_ORIGINS": ",".join(lan)}
    if not (WEB_DIR / "node_modules").exists():
        say("web", "installing web dependencies (npm install)")
        if subprocess.run([npm, "install"], cwd=WEB_DIR).returncode != 0:
            fail("npm install failed")
            return False
    if prod:
        say("web", "building the web app (npm run build)")
        if subprocess.run([npm, "run", "build"], cwd=WEB_DIR, env={**os.environ, **env}).returncode != 0:
            fail("web build failed")
            return False
    say("web", "starting web app")
    host = "0.0.0.0" if lan else "127.0.0.1"
    cmd = [npm, "run", "start" if prod else "dev", "--", "-H", host] + (["-p", str(WEB_PORT)] if WEB_PORT != 3000 else [])
    p = procs.start("web", cmd, WEB_DIR, env=env)
    return wait_for(lambda: http_ok(f"http://127.0.0.1:{WEB_PORT}/", timeout=30) is not None, 180, "Web app", p)


# ---------------------------------------------------------------- main
def main() -> int:
    global API_PORT, WEB_PORT
    ap = argparse.ArgumentParser(description="Start SushrutAI (Ollama, database, API, web app).")
    ap.add_argument("--pull", action="store_true", help="download missing Ollama models before starting")
    ap.add_argument("--prod", action="store_true", help="build and serve the production web app")
    ap.add_argument("--reload", action="store_true", help="restart the API when its code changes")
    ap.add_argument("--no-api", action="store_true", help="do not start the API")
    ap.add_argument("--no-web", action="store_true", help="do not start the web app")
    ap.add_argument("--no-ai", action="store_true", help="skip Ollama (app runs, AI features will not)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    ap.add_argument("--check", action="store_true", help="start everything, confirm it is healthy, then stop and exit")
    ap.add_argument("--local-only", action="store_true",
                    help="only this PC can open the app (default: phones and other devices on the home network too)")
    ap.add_argument("--api-port", type=int, default=API_PORT, help=f"API port (default {API_PORT})")
    ap.add_argument("--web-port", type=int, default=WEB_PORT, help=f"web app port (default {WEB_PORT})")
    args = ap.parse_args()
    API_PORT, WEB_PORT = args.api_port, args.web_port

    if IS_WIN:
        os.system("")  # enable ANSI colours in the Windows console
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    env = load_env()
    procs = Procs()
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stop.set())

    try:
        ok = True
        lan = [] if args.local_only else lan_ips()
        if not args.local_only and not lan:
            say("main", "\033[33mno home-network address found (not connected to Wi-Fi/LAN?) — running for this PC only\033[0m")
        if not args.no_ai:
            ok = start_ollama(procs, env, args.pull)  # a missing model is a warning, not a failure
        ok = ok and start_database(env)
        if ok and not args.no_api:
            ok = start_api(procs, args.reload, lan)
        if ok and not args.no_web:
            ok = start_web(procs, args.prod, lan)
        if not ok:
            fail("startup failed — stopping what was started")
            return 1

        say("main", "\033[1mSushrutAI is running\033[0m")
        if not args.no_web:
            say("main", f"  App      http://localhost:{WEB_PORT}")
            if lan:
                say("main", f"  \033[1mPhone    http://{lan[0]}:{WEB_PORT}\033[0m   (same Wi-Fi; type it in the phone's browser)")
            if len(lan) > 1:  # virtual adapters (WSL, hotspot, VPN): only needed if the first address doesn't work
                say("main", "           other addresses of this PC: " + ", ".join(f"http://{ip}:{WEB_PORT}" for ip in lan[1:]))
        if not args.no_api:
            say("main", f"  API docs http://localhost:{API_PORT}/docs")
        if lan and not args.no_web:
            say("main", "  Notes for phones:")
            say("main", "   - First time only: if Windows asks, allow Python and Node.js on Private networks; and set "
                        "this Wi-Fi to 'Private' in Windows settings, or phones can't connect.")
            say("main", "   - Over plain http:// phone browsers block the microphone, so on a phone type your "
                        "questions (voice works on this PC).")
        say("main", "  Press Ctrl+C to stop" + ("" if procs.items else " (nothing to stop: all were already running)"))
        if args.check:
            say("main", "check passed — stopping")
            return 0
        if not args.no_web and not args.no_browser:
            webbrowser.open(f"http://localhost:{WEB_PORT}")
        if not procs.items:
            return 0

        while not stop.is_set():
            dead = procs.died()
            if dead:
                fail(f"{dead} stopped unexpectedly (see its output above)")
                return 1
            stop.wait(1)
        return 0
    finally:
        procs.stop_all()


if __name__ == "__main__":
    sys.exit(main())
