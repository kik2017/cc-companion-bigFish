<div align="center">

# cc-companion 🐾

**A little companion who lives on your desktop and keeps you company while you work with Claude Code.**

[中文](README.md) · [Code license: MIT](LICENSE) · [Character art license](companion/assets/character/LICENSE.txt)

[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE) [![Art: CC BY-NC-SA 4.0](https://img.shields.io/badge/art-CC%20BY--NC--SA%204.0-lightgrey)](companion/assets/character/LICENSE.txt)

</div>

She sits in a corner of your screen, above your other windows, and shows what Claude Code is doing: thinking, editing, running a command, waiting for you, done. Watching costs nothing: it uses no tokens. If you like, she can also talk: she reacts to your day in her own words, chats with you, and remembers you from one day to the next.

<img src="docs/images/preview.png" width="400" alt="The status bubble above the whale-tail maid character, showing the current action, project, model, effort, context use and quota bars">

*The bundled character is the whale-tail maid, animated by [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet) (non-commercial art, see [Credits and licensing](#credits-and-licensing)).*

## What she does

- **Shows Claude Code's state:** thinking, reading and searching, editing files, running a command, delegating to subagents, waiting for you, done, errors and compacting.
- **A status bubble** with the project, model, reasoning effort, subagents, task progress, how full the context is, and your 5-hour and 7-day usage.
- **Reacts to you:** drag her anywhere, pat her head, poke her, touch her tail.
- **Can talk (optional):** with your own Anthropic API key she says short lines at moments that matter, answers when you chat with her, and sleeps each night: she writes a diary, sorts out what she has learned about you, and sometimes dreams.

## Get started (Windows 10 / 11)

1. **Unzip** `cc-companion-<version>-windows-x64.zip` anywhere you like. A folder without spaces in its path, such as `C:\Apps\cc-companion`, is best (see [the status line](#the-status-line)).
2. **Double-click `cc-companion.exe`.** The app is not signed, so Windows may say "Windows protected your PC": click **More info**, then **Run anyway**.
3. **She appears** in the bottom-right corner of your screen, and her face appears in the system tray (the notification area; it may be under the **^** arrow).
4. **Connect her to Claude Code:** right-click her tray icon, choose **Connect to Claude Code…**, and confirm. From now on every new Claude Code session wakes her up, in the terminal and in the desktop app alike.
5. **Optional, let her talk:** right-click the tray icon, choose **Set API key…**, and paste a key from [console.anthropic.com](https://console.anthropic.com). Then right-click her and choose **Persona → On**.

That's it. If she is ever not running, the next Claude Code session or prompt starts her again.

## Using her

Right-click her, or her tray icon, for the menu. Left-click the tray icon to show or hide her. Changes apply at once and are remembered.

| Menu | What it does |
| --- | --- |
| Show her / Hide her | (tray) Show or hide her |
| Set API key… | (tray) Save the key she needs to talk |
| Connect to Claude Code | (tray) Add or remove her hooks in Claude Code's settings |
| Size | Small, medium or large |
| Bubble | Always, only when it needs you (waiting, errors, done), or never |
| Idle moves | How often she stretches, yawns and plays while nothing happens |
| Persona | Her voice and memory: see below |
| Reduce motion | Hold still; the bubble keeps updating |
| Show subagent count | Show how many subagents are working |
| Sounds | A sound when Claude finishes or hits an error |
| Hide until all sessions end | Hide her now, and quit when every Claude Code session has ended |
| Quit | Hide her, and quit once no Claude Code session is running; a new session brings her back |

### Her voice and memory (Persona)

Off by default. It needs an API key; see step 5 above.

| Persona menu | What it does |
| --- | --- |
| Off / On / Quiet | On: she speaks and shows her mood. Quiet: moods only, no words and no cost. |
| Today: … | How many lines she has said today and what they cost |
| Talk to her… | A small chat box beside her; she answers in her bubble, and the box keeps today's conversation |
| What she knows… | What she has learned about you. Archive anything that is wrong and she stops using it |
| Private today | Nothing from today is remembered (until 5 am) |
| No pause | Lets her speak whenever something happens, with no gaps between lines (this can cost more) |
| Limit | Normal (30 lines an hour, $0.50 a day), Unlimited, or your own number of lines a day |
| Chattiness | Quiet, Normal or Chatty: how often she gets a chance to speak |
| Open her folder | Her identity, diary, memories and settings, as plain files |

**Her day ends at 5 am.** After it ends, once you have been away for half an hour, she sleeps: she writes a diary entry, updates what she knows about you, rewrites a little of who she is becoming, and sometimes dreams. She may tell you her dream the next morning.

**Make her your own.** Her name is 汐 (Shio) and she speaks Simplified Chinese by default. Open her folder and edit `identity.md` to change her name, personality, language, or what she calls you.

### What it costs

- **Watching Claude Code is free.** It uses Claude Code's hooks and spends no tokens.
- **Her voice uses your API key** with Claude Haiku, the cheapest model: about $0.0001 to $0.0003 per line or chat reply, and a few tenths of a cent for each night's sleep. A typical day is around one or two cents.
- By default she stops at 30 lines an hour and $0.50 a day, then shows moods only. Change this under **Persona → Limit**; the menu always shows today's total.

## The status line

In Claude Code in a terminal, the companion also adds a status line under your prompt, such as `Opus high | ctx 12% | 5h 23% 2h14m`, and it fills her bubble's context and usage details. The desktop app does not run status lines, so there the bubble shows a little less. If you already have a status line of your own, Connect keeps yours.

The status line needs a folder path without spaces or symbols. If yours has them and no short form is available, she connects without the status line; move the folder and connect again.

## Privacy

- **Watching stays on your computer.** She listens only on your own machine (127.0.0.1) and keeps only event names, tool names, counts, the project folder's name and similar labels. Your prompts, code, files and Claude's replies are dropped on arrival and never stored.
- **When she talks**, she sends Anthropic only those labels (what Claude Code is doing, times, counts, project folder names) and anything you type to her in the chat box. She never sees your code, prompts or Claude's replies.
- **Her memories are plain files** in `%LOCALAPPDATA%\cc-companion\persona\`, on your computer only. Read them, edit them, or delete them.
- **Your API key** is stored only as your Windows user environment variable `CC_COMPANION_API_KEY`. The app never writes it to a file or a log.
- **Connect to Claude Code** adds only her own entries to `~/.claude/settings.json`, keeps everything else, and saves a backup first. Her hooks only watch: they never approve, block or change anything Claude does.

## Update or remove

**Update:** right-click the tray icon and choose **Quit**, replace the folder with the new version, and start `cc-companion.exe` again. If you put the new version in a different folder, choose **Connect to Claude Code…** again.

**Remove:**

1. Right-click the tray icon and click **Connected to Claude Code** to disconnect.
2. Choose **Quit**, then delete the folder.
3. To also remove her settings and memories, delete `%LOCALAPPDATA%\cc-companion`.
4. To remove the API key, run `reg delete HKCU\Environment /v CC_COMPANION_API_KEY /f` in a terminal.

## If something is off

- **Claude Code shows hook errors mentioning `127.0.0.1:47621`.** She is not running. Your next prompt or a new session starts her; or double-click `cc-companion.exe`.
- **She doesn't appear.** Look for her icon in the tray, and choose **Show her**. If the icon isn't there either, double-click `cc-companion.exe`.
- **Nothing happens when Claude Code works.** Check that the tray menu says **Connected to Claude Code**, then start a new session.
- **She doesn't talk.** Check that **Persona** is **On**, that an API key is set, and the **Today** line in the Persona menu: past her limit she only shows moods. Between midnight and 7 am she keeps quiet except for late-night company, pats and chat.
- **Everything happens twice.** She is connected twice, for example from this build and from a source install. Disconnect one of them.

Her logs are in `%LOCALAPPDATA%\cc-companion` (`companion.log`, `mind.log`). They contain event names, timings and costs, never your conversations or what she says, so you can share them when asking for help.

## Your own character

The whale-tail maid is used unless you choose a skin pack. A skin pack is a folder with a `manifest.json`, one folder of PNG or WebP frames per animation, and the art's license, placed in `%LOCALAPPDATA%\cc-companion\skins\<name>\`. Then set `"skin": "<name>"` in `%LOCALAPPDATA%\cc-companion\settings.json`. Please respect the license of any art you use.

## For developers

The source is here under the MIT license. A developer guide is coming. Until then, `pip install -e ".[dev,mind]"` and `python -m pytest` get you started.

## Credits and licensing

The bundled whale-tail maid is animated by [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet). The character design is by Bilibili @ZipZipPipe, based on the original character 溟月 by Bilibili @上善无形. It is a community fan creation, not an official character of any company. Thanks to all of them.

**The character art is not MIT.** It is shared under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/): non-commercial use only, with credit, and shared alike. Its terms and credits are in [`companion/assets/character/LICENSE.txt`](companion/assets/character/LICENSE.txt), which travels with the frames. The code is released under the [MIT license](LICENSE), Copyright © 2026 kik2017. To use this project commercially, replace the bundled character first.
