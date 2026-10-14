"""Keeps `seahorse run` alive in the background, and starts it again after a reboot.

- Linux: a systemd service. As root, a system service; as a normal user, a user service with "linger", so it
  runs without an open session.
- macOS: a launchd agent. The computer must stay on and awake.
Windows is supported through WSL, where it is Linux.
"""
import os
import platform
import shutil
import subprocess
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


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    cmd = ["systemctl"] if _is_root() else ["systemctl", "--user"]
    return subprocess.run([*cmd, *args], capture_output=True, text=True)


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
    if platform.system() == "Darwin":
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.seahorse.agent"], capture_output=True)
    else:
        _systemctl("restart", NAME)


def stop() -> None:
    if platform.system() == "Darwin":
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(PLIST)], capture_output=True)
    else:
        _systemctl("stop", NAME)


def uninstall() -> None:
    """Stops the service and removes it, so it does not start again after a reboot."""
    if platform.system() == "Darwin":
        stop()
        PLIST.unlink(missing_ok=True)
        return
    _systemctl("disable", "--now", NAME)
    unit = Path("/etc/systemd/system") if _is_root() else Path.home() / ".config/systemd/user"
    (unit / f"{NAME}.service").unlink(missing_ok=True)
    _systemctl("daemon-reload")


def status() -> str:
    if platform.system() == "Darwin":
        out = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/com.seahorse.agent"], capture_output=True, text=True)
        return "running" if "state = running" in out.stdout else "not running"
    return _systemctl("is-active", NAME).stdout.strip() or "not installed"


def logs_command() -> str:
    if platform.system() == "Darwin":
        return f"tail -f {config.HOME / 'seahorse.log'}"
    return "journalctl -u seahorse -f" if _is_root() else "journalctl --user -u seahorse -f"
