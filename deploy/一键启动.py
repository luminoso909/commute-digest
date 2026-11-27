"""在 AutoDL 项目目录运行 python3 deploy/一键启动.py；相同配置可重复启动。"""

import argparse
from contextlib import ExitStack, closing, contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from email.errors import HeaderParseError
from email.headerregistry import Address
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".autodl"
ENV = ROOT / ".env"
PYTHON = ROOT / ".venv/bin/python"
SERVICES = {
    "preview": ("网页", "web.log"),
    "ngrok": ("HTTPS 入口", "ngrok.log"),
    "daily": ("共享早晚报", "daily.log"),
    "accounts": ("账号订阅", "accounts.log"),
}


CONFIG_KEYS = ("PUBLIC_BASE_URL", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "NGROK_AUTHTOKEN",
               "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM",
               "RECIPIENT_EMAIL", "COMMUTE_DB", "COMMUTE_DEBUG_DELIVERY")
HEALTH = "/_stcore/health"
LOCAL = "http://127.0.0.1:8501"
WORKERS = {"daily", "accounts"}
DRAIN_TIMEOUT = 15 * 60


class DeliveryBusy(ValueError):
    pass


def environment():
    if not ENV.exists():
        return dict(os.environ)
    data = subprocess.check_output(
        ["bash", "-c", 'set -a; source "$1" || exit 1; env -0', "commute-digest", str(ENV)]
    )
    return dict(os.fsdecode(item).split("=", 1) for item in data.split(b"\0") if item)


def ngrok_config(env):
    project_config = STATE / "ngrok.yml"
    default_config = Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ngrok/ngrok.yml"
    return next((path for path in (project_config, default_config) if path.is_file()), None)


def is_mailbox(value):
    try:
        address = Address(addr_spec=value)
    except (HeaderParseError, ValueError):
        return False
    return bool(address.username and address.domain and address.addr_spec == value and len(value) <= 254)


def configure():
    env = environment()
    additions = {}

    def field(name, prompt, default="", hidden=False):
        if env.get(name):
            return
        if not sys.stdin.isatty():
            raise ValueError(f"请先在云端终端运行一次启动向导，或在 .env 填写 {name}。")
        question = prompt + (f" [{default}]" if default else "") + "："
        value = (getpass.getpass(question) if hidden else input(question)).strip() or default
        if not value:
            raise ValueError(f"请填写 {prompt}。")
        additions[name] = env[name] = value

    field("PUBLIC_BASE_URL", "ngrok 控制台分配的 HTTPS 网址")
    origin = urlsplit(env["PUBLIC_BASE_URL"])
    if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password
            or origin.port not in (None, 443) or origin.query or origin.fragment or origin.path not in ("", "/")):
        raise ValueError("请使用 https://域名 形式的公开网址。")
    if ngrok_config(env) is None:
        field("NGROK_AUTHTOKEN", "ngrok Authtoken", hidden=True)
    field("DEEPSEEK_API_KEY", "共享日报的 DeepSeek API Key", hidden=True)
    field("SMTP_HOST", "SMTP 服务器", "smtp.gmail.com")
    field("SMTP_PORT", "SMTP TLS 端口", "465")
    field("SMTP_USER", "你自己的 SMTP 登录账号（Gmail 填完整发件邮箱）")
    login = env["SMTP_USER"].strip()
    infer_sender = not env.get("SMTP_FROM") and is_mailbox(login)
    if infer_sender:
        env["SMTP_FROM"] = login
    else:
        field("SMTP_FROM", "该 SMTP 账号已获准使用的发件邮箱")
    sender = env["SMTP_FROM"].strip()
    if not is_mailbox(sender):
        raise ValueError("请在 SMTP_FROM 填写有效的发件邮箱。")
    field("SMTP_PASSWORD", "SMTP 授权码", hidden=True)
    if not env.get("RECIPIENT_EMAIL") and not sys.stdin.isatty():
        additions["RECIPIENT_EMAIL"] = env["RECIPIENT_EMAIL"] = sender
    else:
        field("RECIPIENT_EMAIL", "共享早晚报收件邮箱", sender)
    if not is_mailbox(env["RECIPIENT_EMAIL"].strip()):
        raise ValueError("请在 RECIPIENT_EMAIL 填写有效的共享收件邮箱。")
    if additions and infer_sender:
        additions["SMTP_FROM"] = sender
    if not ENV.exists():
        # The existing shared scheduler sources this file for each slot, including on restarts.
        additions = {name: env[name] for name in CONFIG_KEYS if env.get(name)}
    if additions:
        original = ENV.read_bytes() if ENV.exists() else b""
        with tempfile.NamedTemporaryFile("wb", dir=ROOT, delete=False) as handle:
            handle.write(original)
            if original and not original.endswith(b"\n"):
                handle.write(b"\n")
            for name, value in additions.items():
                handle.write(f"{name}={shlex.quote(value)}\n".encode())
        os.replace(handle.name, ENV)
    env["COMMUTE_DELIVERY_LOCK"] = str(STATE / "delivery.lock")
    return env


def executable(name):
    for path in (STATE / name, STATE / "tools/bin" / name):
        if os.access(path, os.X_OK):
            return str(path)
    return shutil.which(name)


def install_tools():
    if not executable("uv"):
        subprocess.run([sys.executable, "-m", "venv", str(STATE / "tools")], check=True)
        subprocess.run([str(STATE / "tools/bin/pip"), "install", "uv"], check=True)
    if not executable("ngrok"):
        arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())
        if platform.system() != "Linux" or not arch:
            raise ValueError("请先将 ngrok 安装到 PATH 或 .autodl/ngrok。")
        url = f"https://bin.ngrok.com/c/bNyj1mQVY4c/ngrok-v3-stable-linux-{arch}.tgz"
        with tempfile.TemporaryDirectory(dir=STATE) as tmp, urlopen(url, timeout=60) as response:
            binary = Path(tmp) / "ngrok"
            with tarfile.open(fileobj=response, mode="r|gz") as archive:
                for member in archive:
                    if member.name.lstrip("./") == "ngrok" and member.isfile():
                        with binary.open("wb") as handle:
                            shutil.copyfileobj(archive.extractfile(member), handle)
                        break
            if not binary.is_file():
                raise ValueError("ngrok 安装包中未找到可执行文件。")
            binary.chmod(0o700)
            binary.replace(STATE / "ngrok")
    subprocess.run([executable("uv"), "sync", "--locked"], cwd=ROOT, check=True)


def sessions():
    result = subprocess.run(["screen", "-ls"], capture_output=True, text=True)
    active = {}
    for line in result.stdout.splitlines():
        match = re.match(r"\s*(\d+\.commute-digest-(\w+))\s", line)
        if match and match[2] in SERVICES and "Dead" not in line:
            active.setdefault(match[2], []).append(match[1])
    return active


def commands(env):
    tunnel = [executable("ngrok"), "http", "http://127.0.0.1:8501",
              "--url", env["PUBLIC_BASE_URL"].rstrip("/"), "--log=stdout"]
    config = ngrok_config(env)
    if config:
        tunnel.extend(["--config", str(config)])
    return {
        "preview": [str(PYTHON), "-m", "streamlit", "run", "src/commute_digest/web.py",
                    "--server.address=127.0.0.1", "--server.port=8501", "--server.headless=true"],
        "ngrok": tunnel,
        "daily": [str(PYTHON), "-u", "-m", "commute_digest.autodl_scheduler"],
        "accounts": [str(PYTHON), "-u", "-m", "commute_digest.subscription_delivery"],
    }


def worker_processes(screen_name):
    """Linux screen descendants and launch times; None if the process tree is unavailable."""
    boot = Path("/proc/stat")
    if not boot.is_file():
        return None
    try:
        boot_time = int(next(line[6:] for line in boot.read_text().splitlines() if line.startswith("btime ")))
        ticks_per_second = os.sysconf("SC_CLK_TCK")
        pending = [int(screen_name.split(".", 1)[0])]
        processes = []
        while pending:
            pid = pending.pop()
            proc = Path("/proc") / str(pid)
            try:
                fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
                started = boot_time + int(fields[19]) / ticks_per_second
                argv = (proc / "cmdline").read_bytes().split(b"\0")
                environment_items = (proc / "environ").read_bytes().split(b"\0")
                processes.append((argv, environment_items, started))
                pending.extend(map(int, (proc / f"task/{pid}/children").read_text().split()))
            except (FileNotFoundError, ProcessLookupError):
                continue
        return processes or None
    except (OSError, ValueError, StopIteration):
        return None


def legacy_job_active(connection, workers, now):
    """First upgrade from screen scripts predating delivery.lock, with the ledger write-locked."""
    if not workers:
        return False
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "daily" in workers:
        processes = workers["daily"]
        if processes is not None:
            if any(b"deliver" in argv for argv, _, _ in processes):
                return True
        elif "deliveries" in tables:
            rows = connection.execute("SELECT day FROM deliveries WHERE status IN ('building','sending')")
            for day, in rows:
                try:
                    slot = datetime.fromisoformat(day.partition(":")[2] if day.startswith("preview:") else day)
                except ValueError:
                    continue
                if slot.tzinfo and now - timedelta(hours=12) <= slot <= now:
                    return True
    if "accounts" in workers and "subscription_deliveries" in tables:
        processes = workers["accounts"]
        # Old workers use a 30-minute build lease and a 30-second SMTP timeout. Ancient
        # ambiguous 'sending' rows remain intact for dedup, but no longer hold up upgrades.
        threshold = now.timestamp() - 2 * 3600
        if processes:
            threshold = max(threshold, min(start for _, _, start in processes) - 1)
        rows = connection.execute(
            "SELECT claimed_at FROM subscription_deliveries WHERE status IN ('building','sending')"
        )
        return any(datetime.fromisoformat(stamp).timestamp() >= threshold for stamp, in rows)
    return False


@contextmanager
def drain_workers(env, active):
    if not active:
        yield
        return
    lock_path = env["COMMUTE_DELIVERY_LOCK"]
    expected = os.fsencode("COMMUTE_DELIVERY_LOCK=" + lock_path)
    path = Path(env.get("COMMUTE_DB") or ROOT / "data/morning.sqlite3").resolve()
    with open(lock_path, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DeliveryBusy("当前投递任务仍在运行。") from None
        db_context = closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=2)) \
            if path.is_file() else nullcontext(None)
        with db_context as db:
            if db is not None:
                # Reserve the database before inspecting old workers: no new mail can
                # be claimed between the process/ledger check and quitting their screen.
                db.execute("BEGIN IMMEDIATE")
                legacy = {}
                for key, names in active.items():
                    for name in names:
                        processes = worker_processes(name)
                        if processes and any(expected in variables for _, variables, _ in processes):
                            continue
                        if key not in legacy:
                            legacy[key] = processes
                        elif legacy[key] is None or processes is None:
                            legacy[key] = None
                        else:
                            legacy[key].extend(processes)
                if legacy_job_active(db, legacy, datetime.now(timezone.utc)):
                    raise DeliveryBusy("旧版投递任务仍在运行。")
            yield


def healthy(url):
    try:
        req = Request(url, headers={"ngrok-skip-browser-warning": "true"})
        with urlopen(req, timeout=3) as response:
            return response.read(8).strip() == b"ok"
    except (OSError, URLError):
        return False


def signature(env):
    digest = hashlib.sha256()
    files = [Path(__file__), ROOT / "uv.lock", ROOT / "pyproject.toml", ROOT / ".streamlit/config.toml"]
    files += sorted((ROOT / "src/commute_digest").glob("*.py"))
    for file in files:
        digest.update(file.read_bytes())
    for file in (ENV, ngrok_config(env)):
        if file and file.exists():
            digest.update(file.read_bytes())
    for key in CONFIG_KEYS:
        digest.update(json.dumps([key, env.get(key)]).encode())
    return digest.hexdigest()


def stop_services(active):
    for names in active.values():
        for name in names:
            subprocess.run(["screen", "-S", name, "-X", "quit"], check=True)
    for _ in range(30):
        if not set(sessions()).intersection(active):
            return
        time.sleep(.2)
    raise ValueError("旧会话尚未退出，请检查 screen -ls。")


def launch(key, command, env):
    name = "commute-digest-" + key
    subprocess.run(["screen", "-wipe", name], capture_output=True)
    subprocess.run(["screen", "-dmS", name, "bash", "-c",
                    'exec >>"$1" 2>&1; shift; exec "$@"', name,
                    str(STATE / SERVICES[key][1]), *command], cwd=ROOT, env=env, check=True)


def start(force=False):
    env = configure()
    release = signature(env)
    marker = STATE / "running-release"
    public = env["PUBLIC_BASE_URL"].rstrip("/")
    active = sessions()
    replace = force or not marker.exists() or marker.read_text().strip() != release
    if replace:
        restart = set(active)
    else:
        restart = {key for key, names in active.items() if len(names) != 1}
        local_ok = healthy(LOCAL + HEALTH)
        if "preview" in active and not local_ok:
            restart.add("preview")
        if "ngrok" in active and local_ok and not healthy(public + HEALTH):
            restart.add("ngrok")

    install_needed = replace or not PYTHON.is_file() or not executable("ngrok")
    worker_sessions = {key: active[key] for key in active if key in WORKERS and (key in restart or install_needed)}
    with ExitStack() as upgrade:
        deadline = time.monotonic() + DRAIN_TIMEOUT
        announced = False
        while True:
            try:
                upgrade.enter_context(drain_workers(env, worker_sessions))
                break
            except DeliveryBusy as error:
                if not announced:
                    print(f"{error} 等待本期发送结束后自动继续升级。", flush=True)
                    announced = True
                if time.monotonic() >= deadline:
                    raise ValueError("等待投递超过 15 分钟，已保留运行中的服务。请查看投递日志。") from error
                time.sleep(2)
        if install_needed:
            install_tools()
        if restart:
            stop_services({key: active[key] for key in restart})

    active = sessions()
    service_commands = commands(env)
    for key, command in service_commands.items():
        if key not in active:
            launch(key, command, env)

    # This marks the local release loaded by the launched commands, not external
    # tunnel health. A later retry can repair just the endpoint during an outage.
    if replace:
        with tempfile.NamedTemporaryFile("w", dir=STATE, delete=False) as handle:
            handle.write(release)
        os.replace(handle.name, marker)

    for attempt in range(20):
        current = sessions()
        local_ok = healthy(LOCAL + HEALTH)
        public_ok = local_ok and healthy(public + HEALTH)
        if all(len(current.get(key, [])) == 1 for key in SERVICES) and local_ok and public_ok:
            print(f"通勤日报已启动　{public}")
            return
        # A broken Web/tunnel pair can only be distinguished after the local app is ready.
        if attempt == 4 and local_ok and not public_ok and "ngrok" in current:
            stop_services({"ngrok": current["ngrok"]})
            launch("ngrok", service_commands["ngrok"], env)
        time.sleep(1)
    raise ValueError("服务尚未全部就绪，可运行 status 查看具体入口与日志路径。")


def status():
    active = sessions()
    for key, (label, log) in SERVICES.items():
        count = len(active.get(key, []))
        state = {0: "已停止", 1: "运行中"}.get(count, f"{count} 个运行实例")
        print(f"{label}　{state}　.autodl/{log}")
    local_ok = healthy(LOCAL + HEALTH)
    print(f"本机网页　{'可访问' if local_ok else '无法访问'}　{LOCAL}")
    origin = environment().get("PUBLIC_BASE_URL", "").rstrip("/")
    public_ok = bool(origin) and healthy(origin + HEALTH)
    print(f"公网 HTTPS　{'可访问' if public_ok else '无法访问'}　{origin or '尚未配置'}")
    return local_ok and public_ok and all(len(active.get(key, [])) == 1 for key in SERVICES)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="start", choices=["start", "status", "restart"])
    args = parser.parse_args()
    os.chdir(ROOT)
    if not shutil.which("screen"):
        parser.exit(1, "请安装 GNU screen，例如 apt-get install screen。\n")
    if args.action == "status":
        sys.exit(0 if status() else 1)
    os.umask(0o077)
    STATE.mkdir(mode=0o700, exist_ok=True)
    with (STATE / "start.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        start(force=args.action == "restart")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, sqlite3.Error, tarfile.TarError, subprocess.CalledProcessError, EOFError) as error:
        sys.exit(f"启动失败：{error}")
