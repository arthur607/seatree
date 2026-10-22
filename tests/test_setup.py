from test_seahorse import Base, write, task, message, remove  # sets up the throwaway vault
"""Tests of the install side: the settings file, ssh errors in plain words, the GitHub and Obsidian copies, the
migrations, the update and its rollback, and the commands before a setup. They never use the network, a server
or Telegram.

Usage: python -m unittest discover -s tests
"""
import contextlib
import copy
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from seahorse import __version__, config, i18n, machine, migrations, obsidian, provision, service, setup, updates, vault


def _git(folder: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(folder), "-c", "user.name=Test", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def _quiet(function, *args):
    """Runs a provision step and returns what it printed."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = function(*args)
    return code, out.getvalue().strip().splitlines()[-1] if out.getvalue().strip() else ""


class Folder(unittest.TestCase):
    """A temporary folder for each test, outside the throwaway vault."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        remove(self.tmp)


class TestSettingsFile(Folder):
    def test_values_are_escaped_and_nested_tables_stay(self):
        settings = config._merge(copy.deepcopy(config.DEFAULTS), {
            "user": {"name": 'Ana "Bia" \\ O\'Neil\tx'},
            "vault": {"path": "/Users/a/My\\ Notes"},
            "ai": {"models": {"fast": "haiku", "deep": "opus"}},
        })
        text = config.dumps(settings)
        self.assertEqual(tomllib.loads(text), settings)
        self.assertIn("[ai.models]", text)  # a second run of the setup once deleted it

    def test_a_broken_file_names_the_file_and_the_line(self):
        path = self.tmp / "config.toml"
        path.write_text('[user]\nname = "Ana\n', encoding="utf-8")
        data, error = config.read_toml(path)
        self.assertEqual(data, {})
        self.assertIn(str(path), error)
        self.assertIn("line 2", error)

    def test_every_command_starts_with_a_broken_file(self):
        path = self.tmp / "config.toml"
        path.write_text("name = \n", encoding="utf-8")
        try:
            with mock.patch.object(config, "CONFIG_FILE", path):
                settings = config.load()
            self.assertTrue(config.ERROR)
            self.assertEqual(settings["user"]["timezone"], "UTC")
        finally:
            config.ERROR = ""

    def test_new_settings_and_their_defaults(self):
        self.assertEqual(config.DAILY_MESSAGES, 5)
        self.assertEqual(config.DEFAULTS["messenger"], {"daily_messages": 5})
        self.assertEqual(config.OBSIDIAN_VAULT, config.VAULT.name)  # no name saved: the folder name


class TestSsh(Folder):
    HETZNER = ("root@203.0.113.10's password: \r\nYou are required to change your password immediately (administrator "
               "enforced).\r\nWARNING: Your password has expired.\r\nYou must change your password now and login again!\r\n"
               "Current password: \r\nNew password: \r\nRetype new password: \r\npasswd: password updated successfully\r\n"
               "Connection to 203.0.113.10 closed.\r\n")
    CHANGED = ("@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
               "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n"
               "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\nHost key verification failed.\n")

    def test_what_ssh_says_in_plain_words(self):
        cases = {
            self.HETZNER: "password_change",
            self.CHANGED: "host_changed",
            "root@203.0.113.10: Permission denied (publickey).": "key_only",
            "ubuntu@203.0.113.10: Permission denied (publickey,gssapi-keyex,gssapi-with-mic).": "key_only",
            "root@203.0.113.10: Permission denied (publickey,password).": "password",
            "Permission denied, please try again.\r\nroot@x: Permission denied (publickey,password).": "password",
            "root@x: Permission denied (keyboard-interactive).": "password",
            "Received disconnect from 203.0.113.10 port 22:2: Too many authentication failures": "password",
            "ssh: Could not resolve hostname x: Name or service not known": "unknown_host",
            "ssh: connect to host 203.0.113.10 port 22: Connection timed out": "unreachable",
            "banner exchange: Connection to UNKNOWN port -1: Connection refused": "unreachable",
            'Please login as the user "ubuntu" rather than the user "root".': "other_user",
            "": "",
        }
        for text, problem in cases.items():
            self.assertEqual(machine.ssh_problem(text), problem, text)
        self.assertEqual(machine.asked_user('Please login as the user "ubuntu" rather than the user "root".'), "ubuntu")

    def test_the_detail_leaves_out_the_notes_of_ssh(self):
        text = ("Warning: Permanently added '203.0.113.10' (ED25519) to the list of known hosts.\r\n"
                "root@203.0.113.10: Permission denied (publickey).\r\n")
        self.assertEqual(machine.ssh_detail(text), "root@203.0.113.10: Permission denied (publickey).")
        self.assertEqual(machine.ssh_detail(self.CHANGED), "Host key verification failed.")

    def test_ssh_shows_its_errors_and_has_its_own_list_of_servers(self):
        with mock.patch.object(config, "HOME", self.tmp / "my home"):
            server = machine.Server("203.0.113.10", "ubuntu")
            quiet, login = server._ssh(), server._ssh("-tt", batch=False, quiet=False, identity=Path("key file"))
        self.assertNotIn("-q", quiet)  # -q hid "Permission denied" and a changed host key
        self.assertIn("LogLevel=ERROR", quiet)
        self.assertIn(f'UserKnownHostsFile="{(self.tmp / "my home" / "known_hosts").as_posix()}"', quiet)
        self.assertEqual(quiet[-1], "ubuntu@203.0.113.10")
        self.assertNotIn("BatchMode=yes", login)
        self.assertNotIn("LogLevel=ERROR", login)
        self.assertEqual(login[login.index("-i") + 1], "key file")

    @unittest.skipUnless(shutil.which("ssh"), "no ssh here")
    def test_ssh_reads_a_list_of_servers_in_a_folder_with_a_space(self):
        with mock.patch.object(config, "HOME", self.tmp / "my home"):
            argv = machine.Server("203.0.113.10")._ssh()
        out = subprocess.run([argv[0], "-G", *argv[1:]], capture_output=True, text=True).stdout
        self.assertIn(f"userknownhostsfile {(self.tmp / 'my home' / 'known_hosts').as_posix()}", out)


class TestConnectToServer(unittest.TestCase):
    """The way from the IP to a server that the setup key opens, with a fake server and fake answers."""

    def connect(self, address: str, checks: list[str], logins: list[str], answers: dict | None = None):
        said, keys, forgot = [], [], []

        class FakeServer:
            def __init__(self, host, user="root", admin=None):
                self.host, self.user = host, user

            def check(self):
                return checks.pop(0)

            def add_key(self, identity=None):
                keys.append((self.user, identity))
                return logins.pop(0)

            def forget_host(self):
                forgot.append(self.host)

        answers = answers or {}
        with mock.patch.object(setup, "Server", FakeServer), \
                mock.patch.object(setup, "_working", lambda *a, **k: contextlib.nullcontext()), \
                mock.patch.object(setup, "_say", said.append), \
                mock.patch.object(setup, "_t", lambda name, **values: name), \
                mock.patch.object(setup, "_confirm", lambda name, **values: answers.get(name, True)), \
                mock.patch.object(setup, "_ask", lambda name, *a, **k: answers[name]), \
                mock.patch.object(setup, "_key_file", lambda: Path("login_key")):
            server = setup._open_server(address)
        return server, said, keys, forgot

    def test_a_new_hetzner_server_asks_for_a_new_password_first(self):
        server, said, keys, _ = self.connect("203.0.113.10", [machine.NO_SETUP_KEY, machine.NO_SETUP_KEY, ""],
                                             [TestSsh.HETZNER, ""])
        self.assertEqual((server.host, server.user), ("203.0.113.10", "root"))
        self.assertIn("server_password_changed", said)
        self.assertEqual(len(keys), 2)  # the old password, then the new one

    def test_a_server_that_takes_only_a_key_file(self):
        server, said, keys, _ = self.connect("203.0.113.10", [machine.NO_SETUP_KEY, machine.NO_SETUP_KEY, ""],
                                             ["root@203.0.113.10: Permission denied (publickey).", ""],
                                             {"server_user": "ubuntu"})
        self.assertEqual(server.user, "ubuntu")
        self.assertIn("server_key_only", said)
        self.assertEqual(keys, [("root", None), ("ubuntu", Path("login_key"))])

    def test_a_server_that_asks_for_another_user(self):
        server, said, _, _ = self.connect("203.0.113.10", [machine.NO_SETUP_KEY, ""],
                                          ['Please login as the user "ubuntu" rather than the user "root".'])
        self.assertEqual(server.user, "ubuntu")

    def test_a_rebuilt_server_asks_before_it_trusts_the_new_identity(self):
        server, _, _, forgot = self.connect("ubuntu@203.0.113.10", [TestSsh.CHANGED, ""], [])
        self.assertEqual((server.user, forgot), ("ubuntu", ["203.0.113.10"]))
        server, _, _, _ = self.connect("203.0.113.10", [TestSsh.CHANGED], [], {"server_host_changed": False})
        self.assertIsNone(server)

    def test_errors_that_end_the_try_in_plain_words(self):
        cases = [
            (["ssh: connect to host 203.0.113.10 port 22: Connection timed out"], [], "server_unreachable"),
            ([machine.NO_SETUP_KEY], ["root@x: Permission denied (publickey,password)."], "server_password_wrong"),
            ([machine.NO_SETUP_KEY, "root@x: Permission denied (publickey)."], [""], "server_failed"),
        ]
        for checks, logins, text in cases:
            server, said, _, _ = self.connect("203.0.113.10", checks, logins)
            self.assertIsNone(server)
            self.assertEqual(said[-1], text)


class TestSetupHelpers(unittest.TestCase):
    def test_paths_for_the_shell_of_the_server(self):
        self.assertEqual(setup.shell_path("~/seahorse-vault"), '"$HOME"/seahorse-vault')
        self.assertEqual(setup.shell_path("~/my notes"), '"$HOME"/\'my notes\'')
        self.assertEqual(setup.shell_path("/srv/notes"), "/srv/notes")

    def test_a_broken_config_on_the_server_keeps_the_vault_on_the_server(self):
        class Machine:
            remote, label = True, "root@203.0.113.10"

            def read_file(self, name):
                return 'name = "Ana\n' if name == "config.toml" else None
        said = []
        with mock.patch.object(setup, "M", Machine()), mock.patch.object(setup, "_say", said.append), \
                mock.patch.object(setup, "_working", lambda *a, **k: contextlib.nullcontext()):
            settings = setup._load()
        self.assertEqual(settings["vault"]["path"], "~/seahorse-vault")  # never the folder of this computer
        self.assertEqual(len(said), 1)  # the error of the file, with its line

    def test_linux_names_without_accents(self):
        self.assertEqual(setup.linux_name("João"), "joao")
        self.assertEqual(setup.linux_name(" Zoë "), "zoe")
        self.assertEqual(setup.linux_name("ana2"), "ana2")
        for bad in ("Ana Paula", "1ana", "", "李", "a" * 21):
            self.assertEqual(setup.linux_name(bad), "", bad)

    def test_paths_as_people_paste_them(self):
        self.assertEqual(setup.clean_path("  '/Users/a/My Notes' "), "/Users/a/My Notes")
        self.assertEqual(setup.clean_path('"/Users/a/My Notes"'), "/Users/a/My Notes")
        if sys.platform != "win32":  # a folder dragged into the Terminal of macOS
            self.assertEqual(setup.clean_path("/Users/a/My\\ Notes"), "/Users/a/My Notes")

    def test_a_morning_summary_in_the_quiet_hours(self):
        self.assertTrue(setup.in_quiet("07:30", "23:00", "08:00"))  # across midnight
        self.assertFalse(setup.in_quiet("08:00", "23:00", "08:00"))  # the quiet hours end at 08:00
        self.assertTrue(setup.in_quiet("23:00", "23:00", "08:00"))
        self.assertTrue(setup.in_quiet("09:00", "08:00", "10:00"))
        self.assertFalse(setup.in_quiet("07:00", "08:00", "10:00"))
        self.assertFalse(setup.in_quiet("08:00", "00:00", "00:00"))  # no quiet hours

    def test_folders_that_macos_protects(self):
        home = Path("/Users/a")
        for folder in ("Documents", "Documents/vault", "Desktop/x", "Downloads",
                       "Library/Mobile Documents/com~apple~CloudDocs/vault"):
            self.assertTrue(setup.mac_blocked(home / folder, home), folder)
        for folder in ("seahorse-vault", "DocumentsX", "Notes/Documents"):
            self.assertFalse(setup.mac_blocked(home / folder, home), folder)

    def test_texts_with_the_paste_keys_of_each_system(self):
        self.assertIn(setup.PASTE, setup._t("code_copied", code="AB12-CD34"))
        self.assertIn("{guide}", i18n.t("setup.welcome", "en"))
        for language in i18n.LANGUAGES:  # the first message comes right before the first question
            self.assertNotIn("anything", i18n.t("setup.connected_new", language).lower())


@unittest.skipUnless(shutil.which("git"), "no git here")
class TestObsidianCopy(Folder):
    def setUp(self):
        super().setUp()
        self.source = self.tmp / "source"
        _git(self.tmp, "init", "-q", "-b", "main", str(self.source))
        write(self.source / "Note.md", "one")
        _git(self.source, "add", "-A")
        _git(self.source, "commit", "-q", "-m", "one")
        self.url = self.source.as_uri()

    def test_repository_names(self):
        for url in ("git@github.com:Ana/My.Vault.git", "https://github.com/ana/my.vault", "ssh://git@github.com/Ana/My.Vault/",
                    "https://x-access-token:abc@github.com/Ana/My.Vault.git"):
            self.assertEqual(obsidian.repository(url), "ana/my.vault", url)
        self.assertEqual(obsidian.repository("https://gitlab.com/a/b"), "")

    def test_names_that_windows_refuses(self):
        for bad in ("Notes/Why?.md", "a:b.md", "Notes/con.md", "Ends with a dot./x.md", 'Say "hi".md', "a\\b.md"):
            self.assertTrue(obsidian.windows_bad(bad), bad)
        for good in ("Notes/Console.md", "Café é bom.md", "1. 🎯 Intention/Tasks.base", ".obsidian/app.json"):
            self.assertFalse(obsidian.windows_bad(good), good)

    def test_a_second_run_brings_the_newest_notes(self):
        folder = self.tmp / "copy"
        self.assertEqual(obsidian.copy_vault("ana/vault", folder, url=self.url), ("", ""))
        write(self.source / "New.md", "two")
        _git(self.source, "add", "-A")
        _git(self.source, "commit", "-q", "-m", "two")
        self.assertEqual(obsidian.copy_vault("ana/vault", folder, url=self.url), ("", ""))  # once, git clone failed here
        self.assertTrue((folder / "New.md").exists())

    def test_another_repository_or_a_full_folder(self):
        folder = self.tmp / "copy"
        obsidian.copy_vault("ana/vault", folder, url=self.url)
        self.assertEqual(obsidian.copy_vault("ana/other", folder, url=(self.tmp / "other").as_uri())[0], "other_repo")
        write(self.tmp / "full" / "mine.md", "mine")
        self.assertEqual(obsidian.copy_vault("ana/vault", self.tmp / "full", url=self.url)[0], "not_empty")
        self.assertTrue((self.tmp / "full" / "mine.md").exists())

    def test_a_failed_copy_says_why_and_leaves_no_half_folder(self):
        problem, detail = obsidian.copy_vault("ana/vault", self.tmp / "copy", token="secret-token",
                                              url=(self.tmp / "missing").as_uri())
        self.assertEqual(problem, "failed")
        self.assertNotIn("secret-token", detail)
        self.assertFalse((self.tmp / "copy").exists())

    @unittest.skipUnless(sys.platform == "win32", "only Windows refuses these names")
    def test_notes_with_names_that_windows_refuses(self):
        blob = subprocess.run(["git", "-C", str(self.source), "hash-object", "-w", "--stdin"], input="x",
                              capture_output=True, text=True).stdout.strip()
        for name in ("Notes/Why?.md", "Notes/a:b.md"):
            _git(self.source, "-c", "core.protectNTFS=false", "update-index", "--add", "--cacheinfo", f"100644,{blob},{name}")
        _git(self.source, "commit", "-q", "-m", "bad names")
        (self.tmp / "empty").mkdir()
        problem, detail = obsidian.copy_vault("ana/vault", self.tmp / "empty", url=self.url)
        self.assertEqual(problem, "bad_names")
        self.assertIn("Notes/Why?.md", detail)
        self.assertIn("Notes/a:b.md", detail)
        self.assertEqual(list((self.tmp / "empty").iterdir()), [])  # ready for the next run

    def test_the_token_is_never_in_the_command_line(self):
        with mock.patch("seahorse.obsidian.subprocess.run") as run:
            obsidian._run(["clone", "-q", "https://github.com/ana/vault.git", "x"], token="secret-token")
        argv, options = run.call_args
        self.assertNotIn("secret-token", " ".join(argv[0]))
        self.assertEqual(options["env"]["SEAHORSE_GITHUB_TOKEN"], "secret-token")

    def test_the_token_never_goes_to_a_plain_file(self):
        folder = self.tmp / "copy"
        obsidian.copy_vault("ana/vault", folder, url=self.url)
        _git(folder, "config", "credential.helper", "store")
        with mock.patch("seahorse.obsidian._run", wraps=obsidian._run) as run:
            self.assertFalse(obsidian.keep_login(folder, "ana", "secret-token"))
        self.assertFalse(any("approve" in call.args[0] for call in run.call_args_list))
        name = subprocess.run(["git", "-C", str(folder), "config", "user.name"], capture_output=True, text=True).stdout
        self.assertEqual(name.strip(), "ana")


class TestVaultSetup(Base):
    def test_a_new_vault_gets_this_version_at_once(self):
        code, last = _quiet(provision.vault)
        self.assertEqual(code, 0)
        self.assertTrue(last.startswith("created "))
        self.assertEqual((config.VAULT / updates.VAULT_VERSION).read_text(encoding="utf-8").strip(), __version__)
        listed = (config.VAULT / updates.TEMPLATE_LIST).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(listed), len(updates.template_files()))
        self.assertEqual(updates.migrate_vault(self.today), [])  # no old migration runs on a new vault

    def test_a_deleted_template_file_stays_deleted(self):
        _quiet(provision.vault)
        gone = config.VAULT / "1. 🎯 Intention/Wishlist/Games.base"
        gone.unlink()
        vault.publish("delete")
        self.assertEqual(_quiet(provision.vault)[1], "exists")
        updates.migrate_vault(self.today)
        self.assertFalse(gone.exists())

    def test_an_update_adds_the_template_files_of_the_new_version(self):
        _quiet(provision.vault)
        new, deleted = "1. 🎯 Intention/Wishlist/Books.base", "1. 🎯 Intention/Wishlist/Games.base"
        listed = config.VAULT / updates.TEMPLATE_LIST
        listed.write_text("\n".join(n for n in listed.read_text(encoding="utf-8").splitlines() if n != new), encoding="utf-8")
        (config.VAULT / new).unlink()
        (config.VAULT / deleted).unlink()
        self.assertIn(f"added {new}", updates.migrate_vault(self.today))
        self.assertTrue((config.VAULT / new).exists())
        self.assertFalse((config.VAULT / deleted).exists())

    def test_an_old_vault_knows_the_template_of_1_2_0(self):
        vault.create(self.today)  # made by 1.2.0: no list of template files
        self.assertIn("6. ⚙️ System/_AGENTS.md", updates.known_template_files())
        gone = config.VAULT / "1. 🎯 Intention/Wishlist/Games.base"
        gone.unlink()
        updates.migrate_vault(self.today)
        self.assertFalse(gone.exists())
        self.assertTrue((config.VAULT / updates.TEMPLATE_LIST).exists())

    def test_steps_run_by_version_and_a_failed_step_runs_again(self):
        vault.create(self.today)
        marker = config.VAULT / updates.VAULT_VERSION
        write(marker, "1.2.0\n")
        ran = []
        broken = {"yes": True}

        def failing():
            ran.append("1.3.0 b")
            if broken["yes"]:
                raise RuntimeError("boom")
        steps = [("1.2.0", lambda: ran.append("1.2.0")), ("1.3.0", lambda: ran.append("1.3.0 a")), ("1.3.0", failing),
                 ("1.4.0", lambda: ran.append("1.4.0"))]
        with mock.patch.object(migrations, "MIGRATIONS", steps), mock.patch.object(updates, "__version__", "1.3.0"):
            with self.assertRaises(RuntimeError):
                updates.migrate_vault(self.today)
            self.assertEqual(marker.read_text(encoding="utf-8").strip(), "1.2.0")
            broken["yes"] = False
            self.assertEqual(updates.migrate_vault(self.today), ["migrated to 1.3.0"])
        self.assertEqual(ran, ["1.3.0 a", "1.3.0 b", "1.3.0 a", "1.3.0 b"])
        self.assertEqual(marker.read_text(encoding="utf-8").strip(), "1.3.0")

    def test_no_vault_no_migration(self):
        self.assertEqual(updates.migrate_vault(self.today), [])
        self.assertFalse((config.SYSTEM / "_AGENTS.md").exists())


@unittest.skipUnless(shutil.which("git"), "no git here")
class TestGitHubConnect(Base):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        remove(self.tmp)

    def test_a_failed_push_leaves_no_remote_so_the_setup_asks_again(self):
        _quiet(provision.vault)
        code, _ = _quiet(provision.connect, (self.tmp / "missing.git").as_posix())
        self.assertNotEqual(code, 0)
        self.assertFalse(vault.has_remote())
        self.assertEqual(_quiet(provision.vault)[1], "exists")

    def test_a_good_push_keeps_the_remote(self):
        bare = self.tmp / "vault.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        _quiet(provision.vault)
        self.assertEqual(_quiet(provision.connect, bare.as_posix()), (0, "OK"))
        self.assertEqual(_quiet(provision.vault)[1], f"remote {bare.as_posix()}")

    def test_a_move_clones_the_vault(self):
        bare = self.tmp / "vault.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        _quiet(provision.vault)
        _quiet(provision.connect, bare.as_posix())
        remove(config.VAULT)
        self.assertEqual(_quiet(provision.clone, bare.as_posix()), (0, "OK"))
        self.assertTrue((config.SYSTEM / "_AGENTS.md").exists())


class TestUpdate(Base):
    def _update(self, migrate_code: int = 0, stays_up: bool = True):
        migrated = subprocess.CompletedProcess([], migrate_code, "", "Traceback\nValueError: boom\n")
        with mock.patch.object(updates, "latest", return_value={"version": "9.9.9", "notes": "- new"}), \
                mock.patch.object(updates, "install") as install, \
                mock.patch.object(updates.subprocess, "run", return_value=migrated), \
                mock.patch.object(service, "status", return_value="active"), \
                mock.patch.object(service, "restart") as restart, \
                mock.patch.object(service, "stays_up", return_value=stays_up), \
                mock.patch.object(updates.telegram_api, "send") as send:
            ok, text = updates.update(assume_yes=True)
        self.assertEqual(ok, migrate_code == 0 and stays_up)  # `seahorse update` exits with 1 when it fails
        return text, [call.args[0] for call in install.call_args_list], send.call_args.args[0], restart

    def test_a_good_update_says_so(self):
        text, installed, sent, _ = self._update()
        self.assertEqual(text, "Updated to 9.9.9.")
        self.assertEqual(installed, ["9.9.9"])
        self.assertEqual(sent, i18n.t("updates.done", version="9.9.9"))

    def test_a_version_that_does_not_stay_up_is_put_back(self):
        text, installed, sent, restart = self._update(stays_up=False)
        self.assertEqual(installed, ["9.9.9", __version__])
        self.assertIn(__version__, sent)
        self.assertEqual(text, sent)
        self.assertEqual(restart.call_count, 2)  # the new version, then the old one again

    def test_a_failed_migration_puts_the_old_version_back(self):
        text, installed, sent, restart = self._update(migrate_code=1)
        self.assertEqual(installed, ["9.9.9", __version__])
        self.assertIn("ValueError: boom", sent)
        self.assertNotIn("Traceback", sent)
        restart.assert_not_called()  # the old code still runs

    def test_release_notes_are_english_only_in_english(self):
        self.assertEqual(updates.release_notes("1.3.0", "- a\n- b", "en"), "- a\n- b")
        notes = updates.release_notes("1.3.0", "- a", "pt-BR")
        self.assertIn("releases/tag/v1.3.0", notes)
        self.assertNotIn("- a", notes)

    def test_the_release_is_a_zip_so_no_git_is_needed(self):
        self.assertEqual(updates.source("1.3.0"), "https://github.com/lorenzoa7/seahorse/archive/refs/tags/v1.3.0.zip")


class TestServiceAndFiles(Folder):
    def test_the_service_on_windows_says_it_is_not_installed(self):
        with mock.patch.object(service.platform, "system", return_value="Windows"):
            self.assertEqual(service.status(), "not installed")
            self.assertIsNone(service.restarts())
            service.restart()
            service.uninstall()

    @unittest.skipIf(sys.platform == "win32", "Windows has no file modes")
    def test_secrets_are_private_from_the_first_byte(self):
        with mock.patch.object(config, "HOME", self.tmp):
            (self.tmp / "secrets.env").write_text("old", encoding="utf-8")
            os.chmod(self.tmp / "secrets.env", 0o644)
            machine.Local().write_file("secrets.env", "TOKEN=x\n", private=True)
        self.assertEqual(stat.S_IMODE((self.tmp / "secrets.env").stat().st_mode), 0o600)
        self.assertEqual((self.tmp / "secrets.env").read_text(encoding="utf-8"), "TOKEN=x\n")


class TestCommands(Folder):
    def _seahorse(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "SEAHORSE_HOME": str(self.tmp), "PYTHONIOENCODING": "utf-8"}
        return subprocess.run([sys.executable, "-m", "seahorse", *args], capture_output=True, text=True,
                              encoding="utf-8", env=env, timeout=120)

    def test_commands_before_a_setup_say_what_to_do(self):
        result = self._seahorse("status")
        self.assertEqual(result.returncode, 1)
        self.assertIn(i18n.t("cli.not_set_up", "en"), result.stderr)

    def test_a_broken_settings_file_is_named_with_its_line(self):
        (self.tmp / "config.toml").write_text('[user]\nname = "Ana\n', encoding="utf-8")
        result = self._seahorse("run")
        self.assertEqual(result.returncode, 1)
        self.assertIn("config.toml", result.stderr)
        self.assertIn("line 2", result.stderr)


if __name__ == "__main__":
    unittest.main()
