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
- **Reacts to you:** drag her anywhere, pat her head, poke her, touch her tail, each with a few reactions she picks from; wake her from a nap.
- **Lives through the day:** over 40 animations, from humming and stretching to breakfast, lunch, dinner and a nap after lunch, and a purse that empties as your 5-hour usage climbs.
- **Can talk (optional):** with your own Anthropic API key she says short lines at moments that matter, answers when you chat with her (and can look things up on the web, if you let her), and sleeps each night: she writes a diary, sorts out what she has learned about you, and sometimes dreams.

## See her move

**While Claude Code works**

<table>
<tr>
<td align="center"><img src="docs/images/anim/idle.webp" width="190" alt="Idle: standing calmly"><br><sub>Idle</sub></td>
<td align="center"><img src="docs/images/anim/thinking.webp" width="190" alt="Thinking"><br><sub>Thinking</sub></td>
<td align="center"><img src="docs/images/anim/deep_thought.webp" width="190" alt="Thinking hard"><br><sub>Thinking</sub></td>
<td align="center"><img src="docs/images/anim/look_around.webp" width="190" alt="Looking around"><br><sub>Searching</sub></td>
</tr>
<tr>
<td align="center"><img src="docs/images/anim/take_notes.webp" width="190" alt="Taking notes"><br><sub>Searching</sub></td>
<td align="center"><img src="docs/images/anim/write_code.webp" width="190" alt="Writing code"><br><sub>Editing</sub></td>
<td align="center"><img src="docs/images/anim/working.webp" width="190" alt="Busy tapping"><br><sub>Editing · running</sub></td>
<td align="center"><img src="docs/images/anim/desk_tap.webp" width="190" alt="Tapping the desk"><br><sub>Running a command</sub></td>
</tr>
<tr>
<td align="center"><img src="docs/images/anim/working_command.webp" width="190" alt="Sorting papers"><br><sub>Subagents · compacting</sub></td>
<td align="center"><img src="docs/images/anim/waiting.webp" width="190" alt="Pacing"><br><sub>Waiting for you</sub></td>
<td align="center"><img src="docs/images/anim/success.webp" width="190" alt="Done"><br><sub>Done</sub></td>
<td align="center"><img src="docs/images/anim/error.webp" width="190" alt="Stopped on an error"><br><sub>Error</sub></td>
</tr>
</table>

**When you play with her**

<table>
<tr>
<td align="center"><img src="docs/images/anim/head_pat.webp" width="190" alt="A head pat: giggling"><br><sub>Pat her head</sub></td>
<td align="center"><img src="docs/images/anim/shy.webp" width="190" alt="A head pat: shy"><br><sub>Pat her head</sub></td>
<td align="center"><img src="docs/images/anim/poke.webp" width="190" alt="A poke: waving"><br><sub>Poke her</sub></td>
<td align="center"><img src="docs/images/anim/huff.webp" width="190" alt="A poke: huffy"><br><sub>Poke her</sub></td>
</tr>
<tr>
<td align="center"><img src="docs/images/anim/happy_hop.webp" width="190" alt="A poke: a happy hop"><br><sub>Poke her</sub></td>
<td align="center"><img src="docs/images/anim/tail.webp" width="190" alt="Touching her tail: bubbles"><br><sub>Touch her tail</sub></td>
<td align="center"><img src="docs/images/anim/tail_slap.webp" width="190" alt="Touching her tail: a tail slap"><br><sub>Touch her tail</sub></td>
<td align="center"><img src="docs/images/anim/drag.webp" width="190" alt="Dragged and set down"><br><sub>Drag her, then let go</sub></td>
</tr>
</table>

**When she talks (Persona on)**

<table>
<tr>
<td align="center"><img src="docs/images/anim/chatter_screen.webp" width="190" alt="Talking at the screen"><br><sub>Saying a line</sub></td>
<td align="center"><img src="docs/images/anim/chatter_daydream.webp" width="190" alt="Talking, daydreaming"><br><sub>Saying a line</sub></td>
<td align="center"><img src="docs/images/anim/chatter_wipe.webp" width="190" alt="Talking while tidying"><br><sub>Saying a line</sub></td>
<td align="center"><img src="docs/images/anim/curtsy.webp" width="190" alt="A curtsy"><br><sub>Greeting you</sub></td>
</tr>
</table>

**While nothing happens**

<table>
<tr>
<td align="center"><img src="docs/images/anim/idle_stretch.webp" width="190" alt="Stretching"><br><sub>A stretch</sub></td>
<td align="center"><img src="docs/images/anim/idle_yawn.webp" width="190" alt="Yawning"><br><sub>A yawn</sub></td>
<td align="center"><img src="docs/images/anim/eat_token.webp" width="190" alt="Eating a token"><br><sub>Snacking on a token</sub></td>
<td align="center"><img src="docs/images/anim/hum.webp" width="190" alt="Humming"><br><sub>Humming</sub></td>
</tr>
</table>

**By the clock**

<table>
<tr>
<td align="center"><img src="docs/images/anim/brush_teeth.webp" width="190" alt="Brushing her teeth"><br><sub>Morning</sub></td>
<td align="center"><img src="docs/images/anim/breakfast.webp" width="190" alt="Breakfast"><br><sub>Breakfast</sub></td>
<td align="center"><img src="docs/images/anim/lunch.webp" width="190" alt="Lunch"><br><sub>Lunch</sub></td>
<td align="center"><img src="docs/images/anim/dinner.webp" width="190" alt="Dinner"><br><sub>Dinner</sub></td>
</tr>
<tr>
<td align="center"><img src="docs/images/anim/nap.webp" width="190" alt="A nap"><br><sub>A nap after lunch · falling asleep</sub></td>
<td align="center"><img src="docs/images/anim/doze.webp" width="190" alt="Dozing off, then startled awake"><br><sub>Dozing late at night</sub></td>
<td align="center"><img src="docs/images/anim/startled.webp" width="190" alt="Startled awake"><br><sub>Clicked awake</sub></td>
<td></td>
</tr>
</table>

**Your 5-hour usage**

<table>
<tr>
<td align="center"><img src="docs/images/anim/purse_full.webp" width="190" alt="A purse full to the brim"><br><sub>Under 20%</sub></td>
<td align="center"><img src="docs/images/anim/purse_jingle.webp" width="190" alt="A jingling purse"><br><sub>20-40%</sub></td>
<td align="center"><img src="docs/images/anim/purse_steady.webp" width="190" alt="A steady purse"><br><sub>40-60%</sub></td>
</tr>
<tr>
<td align="center"><img src="docs/images/anim/purse_frown.webp" width="190" alt="Counting coins with a frown"><br><sub>60-80%</sub></td>
<td align="center"><img src="docs/images/anim/purse_empty.webp" width="190" alt="An empty purse"><br><sub>80-99%</sub></td>
<td align="center"><img src="docs/images/anim/purse_broke.webp" width="190" alt="Not a coin left"><br><sub>Used up</sub></td>
</tr>
</table>

**Each touch picks one of her reactions at random**, and while Claude Code works she switches between the clips for what it is doing. By the clock she brushes her teeth in the morning (6:30-8:00), has breakfast (7:30-9:30), lunch (11:30-13:30), a nap after lunch (13:30-15:00) and dinner (17:30-19:30), and dozes off late at night: each once a day, at her first idle moment in it. Click her while she naps and she wakes with a start. Each time your 5-hour usage climbs into a new step, she shows you her purse once.

With her voice on she talks while she says a line, curtsies or waves when she greets you, and naps when she goes to sleep. Some clips also show her mood: happy (a happy hop), proud (done), worried (sighing), sleepy (a yawn) and curious (looking around). Previews are shown at 36% size and 8 fps; on your desktop she moves at full size and 24 fps.

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
| Today: … | How many lines she has said today (and web searches, once you use them) and what they cost |
| Talk to her… | A small chat box beside her; she answers in her bubble, and the box keeps today's conversation |
| What she knows… | What she has learned about you. Archive anything that is wrong and she stops using it |
| Private today | Nothing from today is remembered (until 5 am) |
| No pause | Lets her speak whenever something happens, with no gaps between lines (this can cost more) |
| Limit | Normal (30 lines an hour, $0.50 a day), Unlimited, or your own number of lines a day |
| Web search | Off (the default), Normal (10 searches a day), Unlimited, or your own number a day. She searches only to answer what you type in Talk to her |
| Chattiness | Quiet, Normal or Chatty: how often she gets a chance to speak |
| Open her folder | Her identity, diary, memories and settings, as plain files |

**She notices when you are around.** While nobody is at the keyboard she keeps her lines to herself; when you come back she may greet you and tell you what finished while you were gone. She notices the afternoon, evening and night coming on, too. Pat her while she is mid-sentence and she answers once she has finished.

**She can look things up.** Turn on **Persona → Web search** and, when you ask her something current in the chat box (a new release, the weather, today's news), she may search the web, at most twice an answer, and tell you the gist in her bubble with the site it came from. She never searches on her own, and nothing she reads becomes something she "knows" about you.

**Her day ends at 5 am.** After it ends, once you have been away for half an hour, she sleeps: she writes a diary entry, updates what she knows about you, rewrites a little of who she is becoming, and sometimes dreams. She may tell you her dream the next morning.

**She grows closer to you.** Pats and chats, day after day, slowly make her more at ease with you: shy at first, then familiar and teasing, then warm, with running jokes of your own. It cannot be rushed; it takes both the attention and the days.

**Make her your own.** Her name is 汐 (Shio) and she speaks Simplified Chinese by default. Open her folder and edit `identity.md` to change her name, personality, language, or what she calls you.

### What it costs

- **Watching Claude Code is free.** It uses Claude Code's hooks and spends no tokens.
- **Her voice uses your API key** with Claude Haiku, the cheapest model: about $0.0001 to $0.0003 per line or chat reply, and a few tenths of a cent for each night's sleep. A typical day is around one or two cents.
- **Web search costs more:** about $0.01 a search, plus a tenth of a cent or so for the pages she reads, so an answer that searches costs about a cent. It is off until you turn it on, and Normal allows 10 searches a day.
- By default she stops at 30 lines an hour and $0.50 a day (searches included), then shows moods only. Change this under **Persona → Limit**; the menu always shows today's total.

## The status line

In Claude Code in a terminal, the companion also adds a status line under your prompt, such as `Opus high | ctx 12% | 5h 23% 2h14m`, and it fills her bubble's context and usage details. The desktop app does not run status lines, so there the bubble shows a little less. If you already have a status line of your own, Connect keeps yours.

The status line needs a folder path without spaces or symbols. If yours has them and no short form is available, she connects without the status line; move the folder and connect again.

## Privacy

- **Watching stays on your computer.** She listens only on your own machine (127.0.0.1) and keeps only event names, tool names, counts, the project folder's name and similar labels. Your prompts, code, files and Claude's replies are dropped on arrival and never stored.
- **When she talks**, she sends Anthropic only those labels (what Claude Code is doing, times, counts, project folder names) and anything you type to her in the chat box. She never sees your code, prompts or Claude's replies.
- **With web search on**, what you type in the chat box can become a search query. The search runs at Anthropic, through Anthropic's web search tool, as part of the same request; the app itself still talks only to Anthropic. The search results are never logged or saved; only her answer is kept, like any chat reply.
- **To know whether you are at your desk**, she reads only how long it has been since you last used the keyboard or mouse: never what you typed or where you clicked.
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

## Changelog

**2026-10-30**
- She can look things up on the web, if you let her. Turn on **Persona → Web search** (Normal: 10 searches a day; Unlimited or your own number too), and she may search to answer what you ask in the chat box. Searches show in the Today line and count toward her daily limit.
- The chat box waits up to a minute for her answer, so a search has time to finish.
- She grows closer to you over the days, with your pats and chats, and it shows in how she talks to you.
- Who she is becoming is kept in a few sections, holding on to what lasts about the two of you and letting one-off details go.
- Fixed: something that happened just as she greeted you (a day-together milestone, say) was dropped; now she can mention both.
- **Over 40 animations**, up from 16: several reactions for each touch, more clips for each kind of work, a talking clip while she speaks, a curtsy when she greets you, breakfast, lunch, dinner, a nap after lunch (click to wake her), and a purse for your 5-hour usage. Her window is a little wider to fit them, and she is drawn a touch smaller.

**2026-10-29**
- Fixed: when your computer was off as her day ended, she now sleeps as soon as she starts again, even if she greets you first.

**2026-10-28 (0.2.0)**
- She notices when you are at your desk: no lines while you are away, and a greeting or a "welcome back" with what finished meanwhile when you return. She notices the afternoon, evening and night coming on.
- A pat while she is mid-sentence is answered once she finishes.
- A tray icon with **Set API key…** and **Connect to Claude Code**, and a portable Windows build that needs no Python.
- **Talk to her:** a chat box that keeps today's conversation. What you tell her about yourself, she knows after a night's sleep.
- **Private today**, **No pause**, **Limit**, **Chattiness**, **What she knows…**, and today's spend in the Persona menu.
- Her voice and her sleep: short lines at moments that matter, and each night a diary, what she has learned about you, a little of who she is becoming, and sometimes a dream.
- The bundled character's license is corrected to CC BY-NC-SA 4.0 (non-commercial).

**2026-10-27**
- First version: she watches Claude Code through its hooks and shows what it is doing, with a status bubble, done and error sounds, click and drag reactions, skin packs, and a status line for Claude Code in a terminal.

## Credits and licensing

The bundled whale-tail maid is animated by [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet). The character design is by Bilibili @ZipZipPipe, based on the original character 溟月 by Bilibili @上善无形. It is a community fan creation, not an official character of any company. Thanks to all of them.

**The character art is not MIT.** It is shared under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/): non-commercial use only, with credit, and shared alike. Its terms and credits are in [`companion/assets/character/LICENSE.txt`](companion/assets/character/LICENSE.txt), which travels with the frames. The code is released under the [MIT license](LICENSE), Copyright © 2026 kik2017. To use this project commercially, replace the bundled character first.
