# Your notes on other devices

Your notes are a folder of Markdown files: the vault. You read and write it in [Obsidian](https://obsidian.md). With the GitHub sync (step 7 of the setup), the vault is in a private GitHub repository, and each device keeps a copy.

- **The computer where you ran the setup**: step 9 of the setup copies the vault, adds the Git plugin, and opens the vault in Obsidian. You do nothing more.
- **The computer that runs Seahorse**: Seahorse saves this folder itself. You need no plugin there.

## On your phone

1. **Make a key for the phone.** Open <https://github.com/settings/personal-access-tokens/new>.
   - **Token name**: `Obsidian phone`.
   - **Expiration**: the longest. When it ends, make a new key.
   - **Repository access**: *Only select repositories*, and choose your vault.
   - **Permissions → Contents**: *Read and write*.
   - Click **Generate token**, and copy the token.
2. **Install Obsidian**, and make a new empty vault, such as `Seahorse`.
3. **Install the Git plugin**: *Settings → Community plugins*, turn them on, *Browse*, search for **Git** (by Vinzent), install it, and enable it.
4. **Log in**: in *Settings → Git*:
   - *Authentication*: your GitHub user name, and the token as the password.
   - *Commit author*: your name and your email.
5. **Get your notes**: open the command palette and run *Git: Clone an existing remote repo*.
   - URL: `https://github.com/YOUR-USER/YOUR-VAULT.git`.
   - Folder: leave it empty.
   - When it asks if the repository has a `.obsidian` folder, answer *Yes*.
   - Close Obsidian and open it again. If it asks if you trust the plugins, answer yes.

The sync settings come with the vault: Obsidian gets and sends the changes every minute, and when it opens.

## On another computer

1. Install Git from <https://git-scm.com>.
2. Run `git clone https://github.com/YOUR-USER/YOUR-VAULT.git`. On Windows, a browser window opens for the GitHub login. On macOS and Linux, use your user name, and a token as the password (step 1 above).
3. In Obsidian, choose *Open folder as vault*, and choose the new folder.
4. Install the **Git** plugin as in step 3 above.

## Keep the vault small

Keep big files out of the vault. On a phone, the Git plugin loads the repository into memory, and large images can make Obsidian close.
