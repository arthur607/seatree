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
from pathlib import Path

from . import config

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][0-9A-B]|\x1b[=>]")
LINK = re.compile(r"https://[^\s\"'<>]+")
CODE = re.compile(r"(?i)code\W[^\n]{0,40}?\b([A-Z0-9]{4,5}-[A-Z0-9]{4,5})\b")
# Only the links of a login open by themselves.
LOGIN_HOSTS = ("https://claude.ai/", "https://console.anthropic.com/", "https://github.com/login/",
               "https://auth.openai.com/", "https://chatgpt.com/")
PATH = 'export PATH="$HOME/.local/bin:$PATH"; '
NO_SETUP_KEY = "no setup key yet"

# What ssh says, and what it means for the person. The first match wins.
SSH_PROBLEMS = [
    ("host_changed", re.compile(r"REMOTE HOST IDENTIFICATION HAS CHANGED|Host key verification failed", re.I)),
    ("password_change", re.compile(r"must change your password|password has expired|required to change your password|"
                                   r"password change required|password updated successfully", re.I)),
    ("other_user", re.compile(r"Please login as the user", re.I)),
    ("unknown_host", re.compile(r"Could not resolve hostname", re.I)),
    # Permission denied, and the server offers no password: it takes only keys.
    ("key_only", re.compile(r"Permission denied \((?![^)]*(?:password|keyboard-interactive))[a-z0-9,-]+\)", re.I)),
    ("password", re.compile(r"Permission denied|Too many authentication failures", re.I)),
    ("unreachable", re.compile(r"timed out|No route to host|Network is unreachable|Connection refused|"
                               r"Connection reset|Connection closed by|banner exchange|kex_exchange_identification", re.I)),
]


def clean(text: str) -> str:
    return ANSI.sub("", text).replace("\r", "")


def links_and_codes(text: str) -> tuple[list[str], list[str]]:
    """The complete login links (followed by a space or a line end) and the one-time codes in a terminal output."""
    text = clean(text)
    links = [m.group().rstrip(".,)") for m in LINK.finditer(text) if m.end() < len(text) and m.group().startswith(LOGIN_HOSTS)]
    return links, CODE.findall(text)


def ssh_problem(text: str) -> str:
    """What went wrong in an ssh output: a name from SSH_PROBLEMS, or "" when nothing known matches."""
    for problem, pattern in SSH_PROBLEMS:
        if pattern.search(text or ""):
            return problem
    return ""


def asked_user(text: str) -> str:
    """The user that a server asks for, such as AWS: 'Please login as the user "ubuntu" rather than "root"'."""
    found = re.search(r"Please login as the user \W?([a-z_][a-z0-9_.-]*)", text or "", re.I)
    return found[1] if found else ""


def ssh_detail(text: str) -> str:
    """The last lines of an ssh output, which say what is wrong, without the box of warnings and the notes."""
    lines = [line.strip() for line in clean(text or "").splitlines()]
    useful = [line for line in lines if line and not line.startswith(("@", "Warning: Permanently", "Connection to "))]
    return " ".join(useful[-3:])[-400:]


def wsl() -> bool:
    """True inside WSL, Linux on Windows: links open in the browser of Windows."""
    return sys.platform.startswith("linux") and ("microsoft" in platform.release().lower() or bool(os.environ.get("WSL_DISTRO_NAME")))


def launch(url: str) -> bool:
    """Opens a link (https or obsidian://) in its app, in a process with no console. An app opened straight from
    the setup can write its own logs into the terminal, as Obsidian does on Windows. False when this computer has
    no way to open it: then the caller shows the link."""
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    try:
        if sys.platform == "win32":
            subprocess.Popen(["rundll32", "url.dll,FileProtocolHandler", url], creationflags=subprocess.DETACHED_PROCESS, **quiet)
            return True
        if sys.platform == "darwin":
            subprocess.Popen(["open", url], **quiet)
            return True
        if wsl():  # wslview comes with the wslu tools; explorer.exe opens a link in the browser of Windows
            opener = shutil.which("wslview") or shutil.which("explorer.exe")
        else:
            screen = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
            opener = shutil.which("xdg-open") if screen else None
        if not opener:
            return False
        subprocess.Popen([opener, url], start_new_session=True, **quiet)
        return True
    except OSError:
        return False


def copy_to_clipboard(text: str) -> bool:
    for command in (["clip"], ["pbcopy"], ["clip.exe"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(command[0]):
            try:
                return subprocess.run(command, input=text.encode(), capture_output=True, timeout=10).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                return False
    return False


def relay(argv: list[str], open_links: bool, notice, errors: bool = False) -> tuple[str, int]:
    """Runs an interactive command in this terminal. Returns what it printed and its exit code. With `open_links`,
    each login link opens in the browser of this computer and each one-time code goes to the clipboard, once.
    With `errors`, what the command writes to stderr is in the text too."""
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT if errors else None)
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
                if launch(link):
                    notice("link_opened")
    process.wait()
    return clean(text), process.returncode


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

    def root(self, command: str) -> subprocess.CompletedProcess:
        """A command that changes the system, such as apt-get."""
        return self.sh(self.sudo + command)

    def login(self, command: str, notice, typing: bool = True) -> str:
        """The tool opens the browser itself. Its one-time codes go to the clipboard, as on a server."""
        if sys.platform == "win32":
            subprocess.run(command, shell=True)
            return ""
        import pty
        captured: list[str] = []
        seen: set[str] = set()

        def read(fd):
            data = os.read(fd, 4096)
            captured.append(data.decode("utf-8", "replace"))
            for code in links_and_codes("".join(captured))[1]:
                if code not in seen and copy_to_clipboard(code):
                    seen.add(code)
                    notice("code_copied", code=code)
            return data
        pty.spawn(["bash", "-lc", PATH + command], read)
        return clean("".join(captured))

    def read_file(self, name: str) -> str | None:
        path = config.HOME / name
        return path.read_text(encoding="utf-8") if path.exists() else None

    def write_file(self, name: str, text: str, private: bool = False) -> None:
        """A private file is readable only by its owner from the first byte: it is made with mode 600."""
        config.HOME.mkdir(parents=True, exist_ok=True)
        path = config.HOME / name
        if not private:
            path.write_text(text, encoding="utf-8")
            return
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        if hasattr(os, "fchmod"):
            os.fchmod(handle, 0o600)  # os.open keeps the old mode of a file that was there before
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(text)

    def seahorse(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "seahorse", *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace")


class Server:
    """A Linux server reached with ssh, with a key that only the setup uses, and a list of known servers that only
    the setup uses."""
    remote = True
    system = "Linux"

    def __init__(self, host: str, user: str = "root", admin: "Server | None" = None):
        """`user` runs Seahorse. `admin` (root, or a user with sudo) makes the Linux user of each person and
        installs system tools."""
        self.host, self.user, self.admin = host, user, admin
        self.label = f"{user}@{host}"
        self.key = config.HOME / "server_key"
        self.known_hosts = config.HOME / "known_hosts"

    def _ssh(self, tty: str = "", batch: bool = True, quiet: bool = True, identity: Path | None = None) -> list[str]:
        """`tty` is "" (no terminal), "-t" or "-tt" (a terminal even when the output goes to a pipe). `quiet` shows
        only the errors of ssh. Without `batch`, ssh uses the keys of the person, the key file `identity`, or asks
        for the password."""
        options = ["-o", f'UserKnownHostsFile="{self.known_hosts.as_posix()}"', "-o", "StrictHostKeyChecking=accept-new",
                   "-o", "ServerAliveInterval=30", "-o", "ConnectTimeout=15"]
        if quiet:
            options += ["-o", "LogLevel=ERROR"]
        if batch:
            options += ["-i", str(self.key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes"]
        elif identity:
            options += ["-i", str(identity), "-o", "IdentitiesOnly=yes"]
        return ["ssh", *([tty] if tty else []), *options, self.label]

    def check(self) -> str:
        """"" when the setup key opens the server. Else what ssh said."""
        if not self.key.exists():
            return NO_SETUP_KEY
        result = subprocess.run(self._ssh(quiet=False) + ["true"], capture_output=True, text=True, encoding="utf-8",
                                errors="replace")
        return "" if result.returncode == 0 else (clean(result.stderr).strip() or f"ssh exit code {result.returncode}")

    def reachable(self) -> bool:
        """True when the setup key opens the server. Before the key exists, the answer is no."""
        return not self.check()

    def add_key(self, identity: Path | None = None) -> str:
        """Puts the setup key on the server, and returns what ssh printed. ssh uses the keys of the person, the key
        file `identity`, or asks for the password, once."""
        config.HOME.mkdir(parents=True, exist_ok=True)
        if not self.key.exists():
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "seahorse-setup", "-f", str(self.key)],
                           check=True)
        public = self.key.with_suffix(".pub").read_text(encoding="utf-8").strip()
        script = (f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && "
                  f"(grep -qxF {shlex.quote(public)} ~/.ssh/authorized_keys || echo {shlex.quote(public)} >> ~/.ssh/authorized_keys) && "
                  f"chmod 600 ~/.ssh/authorized_keys")
        # A terminal (-tt): a new server can ask to change its password first. The output stays on the screen,
        # and the setup reads it to say what went wrong in plain words.
        return relay(self._ssh("-tt", batch=False, quiet=False, identity=identity) + [script], False, None, errors=True)[0]

    def forget_host(self) -> None:
        """Forgets the identity of this server, after it changed (such as a server rebuilt with the same IP)."""
        if self.known_hosts.exists():
            subprocess.run(["ssh-keygen", "-R", self.host, "-f", str(self.known_hosts)], capture_output=True)

    def sh(self, command: str, capture: bool = True, input: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(self._ssh() + [PATH + command], capture_output=capture, text=True, encoding="utf-8",
                              errors="replace", input=input)

    def which(self, tool: str) -> bool:
        return self.sh(f"command -v {tool}").returncode == 0

    def as_root(self, command: str) -> subprocess.CompletedProcess:
        """A command as root: straight for root, through sudo for a user that has it (such as ubuntu on AWS)."""
        return self.sh(command if self.user == "root" else f"sudo -n bash -c {shlex.quote(command)}")

    def can_admin(self) -> bool:
        """True for root, and for a user that can use sudo with no password."""
        return self.user == "root" or self.sh("sudo -n true").returncode == 0

    def root(self, command: str) -> subprocess.CompletedProcess:
        """A command that changes the system, such as apt-get: as root, through the admin connection if needed."""
        if self.user == "root":
            return self.sh(command)
        return self.admin.as_root(command) if self.admin else self.as_root(command)

    def system_name(self) -> tuple[set[str], str]:
        """The ids of the Linux system (such as {"ubuntu", "debian"}) and its name, from /etc/os-release."""
        out = self.sh('. /etc/os-release 2>/dev/null; echo "$ID $ID_LIKE|$PRETTY_NAME"').stdout.strip()
        ids, _, name = out.partition("|")
        return set(ids.lower().split()), name or "?"

    def make_person(self, user: str) -> str:
        """As root: makes the Linux user that runs one person's Seahorse, lets the setup key in, and keeps its
        services running without a login (linger). Each person has their own folder, service and tokens.
        Returns "" when each step worked, or the error."""
        public = shlex.quote(self.key.with_suffix(".pub").read_text(encoding="utf-8").strip())
        script = (f"set -e; id {user} >/dev/null 2>&1 || useradd -m -s /bin/bash {user}; loginctl enable-linger {user}; "
                  f"install -d -m 700 -o {user} -g {user} /home/{user}/.ssh; "
                  f"touch /home/{user}/.ssh/authorized_keys; "
                  f"grep -qxF {public} /home/{user}/.ssh/authorized_keys || echo {public} >> /home/{user}/.ssh/authorized_keys; "
                  f"chown {user}:{user} /home/{user}/.ssh/authorized_keys; chmod 600 /home/{user}/.ssh/authorized_keys; "
                  "command -v git >/dev/null || (apt-get update -qq && apt-get install -y -qq git curl)")
        result = self.as_root(script)
        return "" if result.returncode == 0 else (ssh_detail(result.stderr) or f"exit code {result.returncode}")

    def people(self) -> list[str]:
        """The Linux users that already run a Seahorse on this server."""
        out = self.as_root("ls -d /root/.seahorse/config.toml /home/*/.seahorse/config.toml 2>/dev/null").stdout
        return ["root" if line.startswith("/root/") else line.split("/")[2] for line in out.split()]

    def login(self, command: str, notice, typing: bool = True) -> str:
        """`typing`: the person types in the login (such as a code). Without it, the login gets no input, so the
        tool asks no questions and does not wait for Enter."""
        # A wide terminal on the server, so a long link stays on one line.
        quiet = "" if typing else " </dev/null"
        return relay(self._ssh("-tt") + [f"stty cols 1000 rows 50 2>/dev/null; {PATH}{command}{quiet}"], True, notice)[0]

    def upload(self, folder: Path, target: str, skip: frozenset[str] = frozenset()) -> subprocess.CompletedProcess:
        """Copies a folder of this computer into `target` on the server (such as ~/seahorse-vault), through ssh."""
        import io
        import tarfile
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as archive:
            archive.add(folder, arcname=".", filter=lambda item: None if skip & set(Path(item.name).parts) else item)
        return subprocess.run(self._ssh() + [f"mkdir -p {target} && tar -xzf - -C {target}"], input=data.getvalue(),
                              capture_output=True)

    def upload_source(self, folder: Path) -> None:
        """Copies a local copy of the code to ~/.seahorse/source on the server, to test a version before its
        release. Only `SEAHORSE_SOURCE` uses it."""
        self.sh("rm -rf ~/.seahorse/source")
        result = self.upload(folder, "~/.seahorse/source", frozenset({".git", ".venv", "__pycache__", "node_modules"}))
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, "upload", result.stdout, result.stderr)

    def interactive(self, command: str) -> int:
        """Runs a command on the server in this terminal, for a person to read and answer. ssh exits with 255 when
        it cannot reach the server."""
        return subprocess.run(self._ssh("-t") + [PATH + command]).returncode

    def read_file(self, name: str) -> str | None:
        result = self.sh(f"cat ~/.seahorse/{name}")
        return result.stdout if result.returncode == 0 else None

    def write_file(self, name: str, text: str, private: bool = False) -> bool:
        """Sends the text over stdin, as bytes: no secret in the command line, and no Windows line ends."""
        mode = "umask 077; " if private else ""
        command = self._ssh() + [f"mkdir -p ~/.seahorse && {mode}cat > ~/.seahorse/{name}"]
        return subprocess.run(command, input=text.encode("utf-8"), capture_output=True).returncode == 0

    def seahorse(self, *args: str) -> subprocess.CompletedProcess:
        return self.sh("seahorse " + shlex.join(args))
