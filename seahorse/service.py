"""Keeps `seahorse run` alive in the background, and starts it again after a reboot.

- Linux: a systemd service. As root, a system service; as a normal user, a user service with "linger", so it
  runs without an open session.
- macOS: a launchd agent. The computer must stay on and awake.
Windows is supported through WSL, where it is Linux.
"""
import os
import platform
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from . import config

NAME = "seahorse"
PLIST = Path.home() / "Library/LaunchAgents/com.seahorse.agent.plist"


def _binary() -> str:
    return shutil.which("seahorse") or str(Path.home() / ".local/bin/seahorse")


def _path() -> str:
    extra = [str(Path.home() / ".local/bin"), "/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin"]
    return ":".join(dict.fromkeys(extra + os.environ.get("PATH", "").split(":")))


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _systemd_unit() -> str:
    target = "multi-user.target" if _is_root() else "default.target"
    return f"""[Unit]
Description=Seahorse: second brain bot and agents
After=network-online.target
[Service]
ExecStart={_binary()} run
Environment=SEAHORSE_HOME={config.HOME}
Environment=PATH={_path()}
Restart=always
RestartSec=10
[Install]
WantedBy={target}
"""


def _supported() -> bool:
    """Linux has systemd and macOS has launchd. Windows runs Seahorse only inside WSL, where it is Linux."""
    return platform.system() in {"Linux", "Darwin"}


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    cmd = ["systemctl"] if _is_root() else ["systemctl", "--user"]
    try:
        return subprocess.run([*cmd, *args], capture_output=True, text=True)
    except FileNotFoundError:  # no systemd here
        return subprocess.CompletedProcess(cmd, 1, "", "systemctl was not found")


def install() -> str:
    system = platform.system()
    if system == "Linux":
        unit = Path("/etc/systemd/system") if _is_root() else Path.home() / ".config/systemd/user"
        unit.mkdir(parents=True, exist_ok=True)
        (unit / f"{NAME}.service").write_text(_systemd_unit(), encoding="utf-8")
        if not _is_root():
            linger = subprocess.run(["loginctl", "enable-linger", os.environ.get("USER", "")], capture_output=True, text=True)
            if linger.returncode != 0:
                print("To keep Seahorse running after you log out, run: sudo loginctl enable-linger $USER")
        _systemctl("daemon-reload")
        result = _systemctl("enable", "--now", NAME)
        _systemctl("restart", NAME)
        return "systemd service installed" if result.returncode == 0 else f"systemd failed: {result.stderr.strip()}"
    if system == "Darwin":
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        log = config.HOME / "seahorse.log"
        PLIST.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.seahorse.agent</string>
<key>ProgramArguments</key><array><string>{_binary()}</string><string>run</string></array>
<key>EnvironmentVariables</key><dict><key>SEAHORSE_HOME</key><string>{config.HOME}</string><key>PATH</key><string>{_path()}</string></dict>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>{log}</string><key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
""", encoding="utf-8")
        uid = str(os.getuid())
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(PLIST)], capture_output=True)
        result = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(PLIST)], capture_output=True, text=True)
        return "launchd agent installed" if result.returncode == 0 else f"launchd failed: {result.stderr.strip()}"
    return "This system is not supported. On Windows, install Seahorse inside WSL."


def restart() -> None:
    if not _supported():
        return
    if platform.system() == "Darwin":
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.seahorse.agent"], capture_output=True)
    else:
        _systemctl("restart", NAME)


def stop() -> None:
    if not _supported():
        return
    if platform.system() == "Darwin":
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(PLIST)], capture_output=True)
    else:
        _systemctl("stop", NAME)


def uninstall() -> None:
    """Stops the service and removes it, so it does not start again after a reboot."""
    if not _supported():
        return
    if platform.system() == "Darwin":
        stop()
        PLIST.unlink(missing_ok=True)
        return
    _systemctl("disable", "--now", NAME)
    unit = Path("/etc/systemd/system") if _is_root() else Path.home() / ".config/systemd/user"
    (unit / f"{NAME}.service").unlink(missing_ok=True)
    _systemctl("daemon-reload")


def _launchd() -> str:
    return subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/com.seahorse.agent"], capture_output=True, text=True).stdout


def status() -> str:
    if not _supported():
        return "not installed"
    if platform.system() == "Darwin":
        return "running" if "state = running" in _launchd() else "not running"
    return _systemctl("is-active", NAME).stdout.strip() or "not installed"


def restarts() -> int | None:
    """How many times the service started again by itself (systemd), or started at all (launchd)."""
    if not _supported():
        return None
    if platform.system() == "Darwin":
        found = re.search(r"\bruns = (\d+)", _launchd())
    else:
        found = re.fullmatch(r"\s*(\d+)\s*", _systemctl("show", "-p", "NRestarts", "--value", NAME).stdout)
    return int(found[1]) if found else None


def stays_up(seconds: int) -> bool:
    """True when the service is still running after `seconds`, and did not crash and start again in that time.
    Call it right after a start: a version that breaks at start fails here."""
    time.sleep(2)  # launchd starts the agent again a moment after a restart: count from then
    before = restarts()
    time.sleep(seconds)
    after = restarts()
    return status() in {"active", "running"} and (before is None or after is None or after == before)


def logs_command() -> str:
    if platform.system() == "Darwin":
        return f"tail -n 50 -f {shlex.quote(str(config.HOME / 'seahorse.log'))}"
    return "journalctl -u seahorse -f -n 50" if _is_root() else "journalctl --user -u seahorse -f -n 50"
