# Where Seahorse runs

Seahorse must stay on: it answers your messages, and its agents work at night.

| | A server (VPS) | Your own computer |
|---|---|---|
| **On day and night** | Yes | Only while the computer is on and awake |
| **Cost** | About US$5 a month | Free |
| **Setup from** | Windows, macOS and Linux | macOS and Linux. On Windows, only inside WSL |

You run the setup on your own computer in both cases. For a server, the setup connects to it with ssh and does the work there. You never type commands on the server.

## What the server needs

- **Ubuntu or Debian.** Seahorse does not work on other Linux systems. Use the newest Ubuntu LTS version.
- **The root user, or a user that can use sudo with no password**, such as `ubuntu` on Oracle Cloud and AWS.
- **The password of the server, or its key file.** Most providers send a password by email. Oracle Cloud, AWS and Google Cloud give you a key file.
- **ssh on port 22**, open to the internet. This is the default on all providers.
- **1 GB of memory.** The smallest server of most providers is sufficient.

Rent the server before you start the setup. The setup asks for its IP at step 1.

## Rent a server

Any provider with Ubuntu works, such as Hetzner, DigitalOcean, Oracle Cloud or AWS. These steps use Hetzner.

1. Make an account at <https://www.hetzner.com/cloud>, and open the Cloud Console at <https://console.hetzner.cloud>.
2. Open a project, and click **Add Server**.
3. Choose these options:
   - **Location**: the one nearest to you.
   - **Image**: **Ubuntu**, the newest version.
   - **Type**: the smallest. Seahorse needs little power.
   - **SSH key**: skip it. Hetzner then sends the password of the server to your email.
4. Click **Create & Buy now**. Copy the **IPv4 address** of the server, such as `203.0.113.10`.

The setup asks for the address, and for the password once.

### The first login asks for a new password

On a new Hetzner server, the password in the email works only once. At the first login, the server asks you to change it:

1. At **Current password**, type the password from the email again.
2. At **New password** and **Retype new password**, type a new password. Keep it in a safe place.

Then the setup asks for the password again. Type the new password.

### A server that accepts only a key file

Oracle Cloud, AWS and Google Cloud do not accept a password. When you make the server, they give you a key file, such as `ssh-key-2026-10-21.key` or `my-server.pem`. When the setup sees that the server accepts only a key, it asks for:

- **The path of the key file**, such as `~/Downloads/ssh-key-2026-10-21.key`. You can drag the file into the terminal.
- **The user name**: `ubuntu` on Oracle Cloud and on AWS, with an Ubuntu image.

You can also type the user and the IP together at the first question, such as `ubuntu@203.0.113.10`.

The setup uses your key file only once, to put its own key on the server. It does not keep a copy of your key file.

### When the setup cannot connect

The setup shows the error of ssh, and tells you what to do:

| The setup says | What to do |
|---|---|
| Could not reach the server | Check the IP. In the panel of your provider, check that the server is on. |
| The server did not accept the password | Check the password, and type the IP again. |
| The identity of the server changed | This is normal after you rebuild the server, or when a new server gets the same IP. Answer **Yes** only if you did this. |
| Seahorse needs Ubuntu or Debian | In the panel of your provider, rebuild the server with Ubuntu. |

The setup keeps its own key (`~/.seahorse/server_key`) and its own list of known servers (`~/.seahorse/known_hosts`). It does not change your files in `~/.ssh`.

## The picture of the bot

Telegram lets only @BotFather change the picture of a bot. To use the Seahorse picture, download [seahorse-bot.png](https://raw.githubusercontent.com/lorenzoa7/seahorse/main/assets/seahorse-bot.png), send `/setuserpic` to @BotFather, choose your bot, and send the picture.

## Two people, one server

Each person gets their own Linux user on the server, such as `seahorse-ana`. Each user has its own notes, tokens, Telegram bot and service. A name with accents is correct: "João" gets the user `seahorse-joao`.

To add a person, run `seahorse setup` again, type the same server, and choose **A new Seahorse**. Each person needs their own Telegram bot. Each person should use their own AI account: an account is personal, and two Seahorses share its limits.

The `seahorse` commands on a computer act on the last Seahorse that the computer set up.

## Your own computer

- **macOS**: choose **On this computer** in the setup. Keep the computer on and awake: in *System Settings → Battery → Options*, turn on *Prevent automatic sleeping when the display is off*.
  - Seahorse needs the Command Line Tools of Apple, which include git. If they are not installed, the setup opens the window that installs them. After the install, run `seahorse setup` again.
  - Put the notes in a folder in your home folder, such as `~/seahorse-vault`. macOS does not let a background program use *Documents*, *Desktop*, *Downloads* or *iCloud Drive*.
- **Linux**: choose **On this computer** in the setup. Keep the computer on.
- **Windows**: Seahorse runs inside WSL (Windows Subsystem for Linux).
  1. Open PowerShell as administrator, run `wsl --install`, and restart the computer.
  2. Open **Ubuntu** from the Start menu, and make a Linux user.
  3. In the Ubuntu window, run the install command for macOS and Linux.
  4. **Keep an Ubuntu window open.** WSL stops Linux a short time after you close the last Ubuntu window, and Seahorse stops with it. You can minimize the window.

  In WSL, the links of the setup open in the browser of Windows. To open your notes in Obsidian on Windows, choose *Open folder as vault* in Obsidian, and paste the path that the setup shows, such as `\\wsl.localhost\Ubuntu\home\ana\seahorse-vault`.

## Move to a server later

Run `seahorse setup` on the computer that runs Seahorse now, and choose **On a server**. The setup asks before it moves Seahorse. Then:

- Seahorse stops on this computer, so two copies never answer the same bot.
- The settings, the tokens and the notes go to the server. With GitHub sync, the server gets the notes from GitHub. Without GitHub sync, the setup copies the folder of the notes to the server.
- The `seahorse` commands on this computer now act on the server.

## Run the setup on the server itself

You can also connect with `ssh root@YOUR-IP` and run the macOS and Linux install command on the server. The setup cannot open your browser there, so it shows each link for you to open on your computer.
