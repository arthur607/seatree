# Where Seahorse runs

Seahorse must stay on: it answers your messages, and its agents work at night.

| | A server (VPS) | Your own computer |
|---|---|---|
| **On day and night** | Yes | Only while the computer is on and awake |
| **Cost** | About US$5 a month | Free |
| **Setup from** | Windows, macOS and Linux | macOS and Linux. On Windows, only inside WSL |

You run the setup on your own computer in both cases. For a server, the setup connects to it with ssh and does the work there. You never type commands on the server.

## Rent a server

Any provider with Ubuntu works, such as Hetzner, DigitalOcean or Oracle Cloud. These steps use Hetzner.

1. Make an account at <https://www.hetzner.com/cloud>, and open the Cloud Console at <https://console.hetzner.cloud>.
2. Open a project, and click **Add Server**.
3. Choose these options:
   - **Location**: the one nearest to you.
   - **Image**: **Ubuntu**, the newest version.
   - **Type**: the smallest. Seahorse needs little power.
   - **SSH key**: skip it. Hetzner then sends the password of the server to your email.
4. Click **Create & Buy now**. Copy the **IPv4 address** of the server, such as `203.0.113.10`.

The setup asks for the address, and for the password once.

## The picture of the bot

Telegram lets only @BotFather change the picture of a bot. To use the Seahorse picture, download [seahorse-bot.png](https://raw.githubusercontent.com/lorenzoa7/seahorse/main/assets/seahorse-bot.png), send `/setuserpic` to @BotFather, choose your bot, and send the picture.

## Two people, one server

Each person gets their own Linux user on the server, such as `seahorse-ana`. Each user has its own notes, tokens, Telegram bot and service.

To add a person, run `seahorse setup` again, type the same server, and choose **A new Seahorse**. Each person needs their own Telegram bot. Each person should use their own AI account: an account is personal, and two Seahorses share its limits.

The `seahorse` commands on a computer act on the last Seahorse that the computer set up.

## Your own computer

- **macOS and Linux**: choose **On this computer** in the setup. Keep the computer on and awake. On a Mac: *System Settings → Battery → Options*, turn on *Prevent automatic sleeping when the display is off*.
- **Windows**: Seahorse runs inside WSL (Windows Subsystem for Linux).
  1. Open PowerShell as administrator, run `wsl --install`, and restart the computer.
  2. Open **Ubuntu** from the Start menu, and make a Linux user.
  3. In the Ubuntu window, run the install command for macOS and Linux.

You can move to a server later: run `seahorse setup` and choose a server.

## Run the setup on the server itself

You can also connect with `ssh root@YOUR-IP` and run the macOS and Linux install command on the server. The setup cannot open your browser there, so it shows each link for you to open on your computer.
