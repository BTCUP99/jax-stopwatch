"""Start/stop only this project's services; never replace other localhost apps."""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
URL = "http://127.0.0.1:18765"


def health():
    try:
        data = json.load(urllib.request.urlopen(URL + "/api/state", timeout=1))
        if data.get("service") == "stopwatch-mac-bridge":
            return data
        raise RuntimeError("端口 18765 被其他服务占用，未更改该服务")
    except (OSError, ValueError):
        return None


def start():
    if health():
        print("StopWatch 接入已运行：" + URL)
        return
    python = ROOT / ".venv/bin/python"
    if not python.exists():
        raise RuntimeError("缺少 Python 环境，请先在项目目录运行 uv sync")
    with (RUNTIME / "bridge.log").open("ab") as log:
        child = subprocess.Popen([str(python), str(ROOT / "src/bridge.py")], cwd=ROOT,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    for _ in range(40):
        if health():
            print("StopWatch 接入已启动：" + URL)
            print("设备 A 键：按住说话，松开发送。B 键：切换助手；双击 B 停止。")
            return
        if child.poll() is not None:
            raise RuntimeError("接入启动失败，请查看 runtime/bridge.log")
        time.sleep(.25)
    raise RuntimeError("接入启动超时，请查看 runtime/bridge.log")


def run():
    """Foreground entry for the login agent: exit 0 if already running (started by hand)."""
    if health():
        print("StopWatch 接入已运行，登录项不重复启动")
        return
    os.chdir(ROOT)
    os.execv(str(ROOT / ".venv/bin/python"), [str(ROOT / ".venv/bin/python"), str(ROOT / "src/bridge.py")])


LABEL = "io.github.jax-stopwatch.bridge"
AGENT = Path.home() / f"Library/LaunchAgents/{LABEL}.plist"


def autostart(enable):
    """Login agent: starts the bridge at login, restarts it after a crash, never after a clean stop."""
    import plistlib
    domain = f"gui/{os.getuid()}"
    subprocess.run(["/bin/launchctl", "bootout", domain, str(AGENT)], capture_output=True)
    if not enable:
        AGENT.unlink(missing_ok=True)
        print("已关闭开机自启动")
        return
    # Keep the session's PATH so tools installed via nvm, npm prefixes etc. (codex, cloudflared) resolve.
    base = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    path = ":".join(p for p in dict.fromkeys((os.environ.get("PATH", "") + ":" + base).split(":")) if p)
    env = {"PATH": path, "HOME": str(Path.home()), "LANG": "zh_CN.UTF-8"}
    # Carry the session's proxy settings, if any, into the login agent.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    AGENT.write_bytes(plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [str(ROOT / ".venv/bin/python"), str(ROOT / "src/manage.py"), "run"],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": env,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "ProcessType": "Interactive",
        "StandardOutPath": str(RUNTIME / "bridge.log"),
        "StandardErrorPath": str(RUNTIME / "bridge.log"),
    }))
    subprocess.run(["/bin/launchctl", "bootstrap", domain, str(AGENT)], check=True)
    print("已开启开机自启动：登录后自动运行，异常退出会自动重启，手动停止不会被拉起")


def stop():
    data = health()
    if not data:
        print("StopWatch 接入未运行")
        return
    pid = int(data["pid"])
    command = subprocess.check_output(["/bin/ps", "-p", str(pid), "-o", "command="], text=True)
    if "bridge.py" not in command:
        raise RuntimeError("进程身份不符合预期，未停止")
    os.kill(pid, signal.SIGTERM)
    for _ in range(40):
        if not health():
            print("StopWatch 接入已停止")
            return
        time.sleep(.25)
    raise RuntimeError("停止尚未完成，请检查接入日志")


if __name__ == "__main__":
    os.umask(0o077)
    action = sys.argv[1] if len(sys.argv) > 1 else "start"
    if action == "start":
        start()
    elif action == "stop":
        stop()
    elif action == "run":
        run()
    elif action == "autostart":
        autostart((sys.argv[2] if len(sys.argv) > 2 else "on") != "off")
    elif action == "status":
        data = health()
        print(json.dumps({k: data.get(k) for k in ["connected", "phase", "codexReady", "grokConfigured"]}, ensure_ascii=False) if data else "未运行")
    else:
        raise SystemExit("Usage: manage.py start|stop|status|run|autostart [on|off]")
