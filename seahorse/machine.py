"""The machine that runs Seahorse, as the setup sees it: this machine, or a server reached with ssh.

The setup always runs on the person's own computer. For a server, it sends each command through ssh. A login
runs on the server, but its links open in the browser of the computer and its one-time codes go to the
clipboard, so nobody copies a link out of a terminal.
"""
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

from . import config

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][0-9A-B]|\x1b[=>]")
LINK = re.compile(r"https://[^\s\"'<>]+")
CODE = re.compile(r"(?i)code\W[^\n]{0,40}?\b([A-Z0-9]{4,5}-[A-Z0-9]{4,5})\b")
# Only the links of a login open by themselves.
LOGIN_HOSTS = ("https://claude.ai/", "https://console.anthropic.com/", "https://github.com/login/",
               "https://auth.openai.com/", "https://chatgpt.com/")
PATH = 'export PATH="$HOME/.local/bin:$PATH"; '


def clean(text: str) -> str:
    return ANSI.sub("", text).replace("\r", "")


def links_and_codes(text: str) -> tuple[list[str], list[str]]:
    """The complete login links (followed by a space or a line end) and the one-time codes in a terminal output."""
    text = clean(text)
    links = [m.group().rstrip(".,)") for m in LINK.finditer(text) if m.end() < len(text) and m.group().startswith(LOGIN_HOSTS)]
    return links, CODE.findall(text)


def copy_to_clipboard(text: str) -> bool:
    for command in (["clip"], ["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(command[0]):
            return subprocess.run(command, input=text.encode(), capture_output=True).returncode == 0
    return False


def relay(argv: list[str], open_links: bool, notice) -> str:
    """Runs an interactive command in this terminal and returns what it printed. With `open_links`, each login link
    opens in the browser of this computer and each one-time code goes to the clipboard, once."""
    process = subprocess.Popen(argv, stdout=subprocess.PIPE)
    out = sys.stdout.buffer
    seen: set[str] = set()
    text = ""
    while True:
        chunk = os.read(process.stdout.fileno(), 4096)
        if not chunk:
            break
        out.write(chunk)
        out.flush()
        text += chunk.decode("utf-8", "replace")
        if not open_links:
            continue
        links, codes = links_and_codes(text)
        for code in codes:
            if code not in seen and copy_to_clipboard(code):
                seen.add(code)
                notice("code_copied", code=code)
        for link in links:
            if link not in seen:
                seen.add(link)
                webbrowser.open(link)
                notice("link_opened")
    process.wait()
    return clean(text)


class Local:
    """This machine. Its own tools open the browser, so the setup only watches what they print."""
    remote = False
    system = platform.system()
    sudo = "" if not hasattr(os, "geteuid") or os.geteuid() == 0 else "sudo "
    label = "this computer"

    def sh(self, command: str, capture: bool = True, input: str | None = None) -> subprocess.CompletedProcess:
        env = {**os.environ, "PATH": f"{Path.home() / '.local/bin'}{os.pathsep}{os.environ.get('PATH', '')}"}
        return subprocess.run(command, shell=True, capture_output=capture, text=True, encoding="utf-8",
                              errors="replace", input=input, env=env)

    def which(self, tool: str) -> bool:
        return bool(shutil.which(tool) or shutil.which(str(Path.home() / ".local/bin" / tool)))

    def login(self, command: str, notice) -> str:
        if sys.platform == "win32":
            subprocess.run(command, shell=True)
            return ""
        import pty
        captured = []

        def read(fd):
            data = os.read(fd, 4096)
            captured.append(data.decode("utf-8", "replace"))
            return data
        pty.spawn(["bash", "-lc", PATH + command], read)
        return clean("".join(captured))

    def read_file(self, name: str) -> str | None:
        path = config.HOME / name
        return path.read_text(encoding="utf-8") if path.exists() else None

    def write_file(self, name: str, text: str, private: bool = False) -> None:
        config.HOME.mkdir(parents=True, exist_ok=True)
        path = config.HOME / name
        path.write_text(text, encoding="utf-8")
        if private:
            os.chmod(path, 0o600)

    def seahorse(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "seahorse", *args], capture_output=True, text=True, encoding="utf-8")


class Server:
    """A Linux server reached with ssh, with a key that only the setup uses."""
    remote = True
    system = "Linux"

    def __init__(self, host: str, user: str = "root"):
        self.host, self.user = host, user
        self.sudo = "" if user == "root" else "sudo "
        self.label = f"{user}@{host}"
        self.key = config.HOME / "server_key"

    def _ssh(self, tty: str = "", batch: bool = True) -> list[str]:
        """`tty` is "" (no terminal), "-t" or "-tt" (a terminal even when the output goes to a pipe)."""
        options = ["-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=30", "-o", "ConnectTimeout=15"]
        if batch:
            options += ["-i", str(self.key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes"]
        return ["ssh", *([tty] if tty else []), *options, self.label]

    def reachable(self) -> bool:
        return subprocess.run(self._ssh() + ["true"], capture_output=True).returncode == 0

    def add_key(self) -> None:
        """Puts the setup key on the server. ssh asks for the password of the server here, once."""
        config.HOME.mkdir(parents=True, exist_ok=True)
        if not self.key.exists():
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "seahorse-setup", "-f", str(self.key)],
                           check=True)
        public = self.key.with_suffix(".pub").read_text(encoding="utf-8").strip()
        script = (f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && "
                  f"(grep -qxF {shlex.quote(public)} ~/.ssh/authorized_keys || echo {shlex.quote(public)} >> ~/.ssh/authorized_keys) && "
                  f"chmod 600 ~/.ssh/authorized_keys")
        # A terminal (-t): a new server can ask to change its password first.
        subprocess.run(self._ssh("-t", batch=False) + [script])

    def sh(self, command: str, capture: bool = True, input: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(self._ssh() + [PATH + command], capture_output=capture, text=True, encoding="utf-8",
                              errors="replace", input=input)

    def which(self, tool: str) -> bool:
        return self.sh(f"command -v {tool}").returncode == 0

    def login(self, command: str, notice) -> str:
        # A wide terminal on the server, so a long link stays on one line.
        return relay(self._ssh("-tt") + [f"stty cols 1000 rows 50 2>/dev/null; {PATH}{command}"], True, notice)

    def upload_source(self, folder: Path) -> None:
        """Copies a local copy of the code to ~/.seahorse/source on the server, to test a version before its
        release. Only `SEAHORSE_SOURCE` uses it."""
        import io
        import tarfile
        skip = {".git", ".venv", "__pycache__", "node_modules"}
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as archive:
            archive.add(folder, arcname=".", filter=lambda item: None if skip & set(Path(item.name).parts) else item)
        subprocess.run(self._ssh() + ["rm -rf ~/.seahorse/source && mkdir -p ~/.seahorse/source && "
                                      "tar -xzf - -C ~/.seahorse/source 2>/dev/null"], input=data.getvalue(), check=True)

    def interactive(self, command: str) -> int:
        """Runs a command on the server in this terminal, for a person to read and answer."""
        return subprocess.run(self._ssh("-t") + [PATH + command]).returncode

    def read_file(self, name: str) -> str | None:
        result = self.sh(f"cat ~/.seahorse/{name}")
        return result.stdout if result.returncode == 0 else None

    def write_file(self, name: str, text: str, private: bool = False) -> None:
        mode = "umask 077; " if private else ""
        self.sh(f"mkdir -p ~/.seahorse && {mode}cat > ~/.seahorse/{name}", input=text)

    def seahorse(self, *args: str) -> subprocess.CompletedProcess:
        return self.sh("seahorse " + shlex.join(args))
