<div align="center"><a name="readme-top"></a>

[![Project Banner](./public/echook-logo.svg)](#)

# echook

**AI-operated audio notification system for Claude Code, Cursor IDE, and Codex CLI.**<br/>
You type one slash command at install time. Then natural language forever.<br/>
26 hook events, 2 audio themes, rate-limit alerts, webhooks, TTS, context monitor — all operated by your AI agent on your behalf.<br/>
**🆕 5.2.0 — Codex CLI compatibility.** New native install path for OpenAI's Codex CLI: `audio-hooks install --codex` writes `~/.codex/hooks.json` registering all 6 events Codex supports (per [developers.openai.com/codex/hooks](https://developers.openai.com/codex/hooks)). AI-first feature-flag handling: install authors a fresh `~/.codex/config.toml` when none exists, emits machine-readable `next_steps` for the calling AI agent to follow up when an existing one needs editing. New `--invoker codex` CLI flag baked into the template, new `editor_targets.codex` block in status, new `codex: {...}` sub-object in webhook payloads, +33 bridge-contract tests. See [CHANGELOG](./CHANGELOG.md#520---2026-05-04). All 5.1.x fixes are still active.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Version](https://img.shields.io/badge/version-5.2.0-blue.svg)](https://github.com/ChanMeng666/echook)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-green.svg)](https://github.com/ChanMeng666/echook)
[![Claude Code](https://img.shields.io/badge/Claude_Code-v2.1.80%2B-brightgreen.svg)](https://claude.ai/download)
[![Plugin](https://img.shields.io/badge/install-just_talk_to_Claude-purple.svg)](#-install-in-60-seconds)

**Share This Project**

[![][share-x-shield]][share-x-link]
[![][share-linkedin-shield]][share-linkedin-link]
[![][share-reddit-shield]][share-reddit-link]
[![][share-telegram-shield]][share-telegram-link]
[![][share-whatsapp-shield]][share-whatsapp-link]

---

### Promotional Video

https://github.com/user-attachments/assets/3504d214-efac-4e01-84c0-426430b842d6

<sup>Built with Remotion, Claude Code, ElevenLabs & Suno. Source: <a href="https://github.com/ChanMeng666/claude-code-audio-hooks-promo-video">claude-code-audio-hooks-promo-video</a></sup>

</div>

<details>
<summary><kbd>Table of Contents</kbd></summary>

- [What's New in v5.0](#-whats-new-in-v50)
- [Pick Your Platform](#-pick-your-platform)
- [Install in 60 Seconds](#-install-in-60-seconds) — Claude Code
- [Cursor IDE — Two Install Paths](#-cursor-ide--same-project-two-install-paths)
- [Codex CLI — Native Install](#-codex-cli--native-install)
- [Just Say It — Natural Language Control](#-just-say-it--natural-language-control)
- [How It Works](#-how-it-works)
- [Key Features](#-key-features)
- [Technical Reference](#-technical-reference)
- [Platform Support](#-platform-support)
- [Troubleshooting](#-troubleshooting)
- [Uninstall](#-uninstall)
- [For Developers](#-for-developers)
- [Documentation](#-documentation)
- [Contributing](#-contributing)
- [License](#-license)
- [Author](#-author)

</details>

---

## What's New in v5.0

> *Renamed from `claude-code-audio-hooks` to `echook` in 5.2.1 — see [CHANGELOG](CHANGELOG.md#521---2026-05-05) for migration notes if you have an existing install. Door-only rename: the `audio-hooks` CLI, the `chanmeng-audio-hooks` marketplace name, and all state directories are unchanged.*

<details>
<summary><kbd>v5.0 — AI-first redesign (click to expand)</kbd></summary>
<br>

v5.0 is an **AI-first redesign**. Every project surface is now machine-operable end-to-end so Claude Code can install, configure, snooze, troubleshoot, and upgrade the project on a human's behalf without any clicks, prompts, or doc reading.

| Highlight | Effect |
|---|---|
| **`audio-hooks` JSON CLI** | Single binary with 27 subcommands. JSON to stdout, stable error codes + suggested commands. |
| **`/audio-hooks` SKILL** | Natural-language activation: "snooze audio for an hour" — Claude runs it for you. |
| **NDJSON structured logging** | Schema `audio-hooks.v1` with stable error code enums. |
| **Plugin-native install** | Two commands and you're done. |
| **4 new hook events** | `PermissionDenied`, `CwdChanged`, `FileChanged`, `TaskCreated` — 26 total. |
| **Native matcher routing** | `hooks.json` registers per-matcher handlers with unique audio per variant. |
| **Rate-limit alerts** | One-shot warning at 80%/95% of your 5h or 7d quota. |
| **TTS speak Claude's reply** | Instead of "Task completed", TTS speaks Claude's actual final message. |
| **Status line with context monitor** | Color-coded Context and API Quota bars with `/compact` reminders. |
| **ElevenLabs audio generator** | One-line manifest edit + one-command rebuild for new audio. |

### v5.0 in Action

<div align="center">
  <table>
    <tr>
      <td width="50%" align="center">
        <img src="docs/images/plugin-installed.png" alt="Plugin Installed" width="100%"/>
        <br/><em>Plugin Installed — 26 hooks registered</em>
      </td>
      <td width="50%" align="center">
        <img src="docs/images/skill-registration.png" alt="SKILL Registration" width="100%"/>
        <br/><em>/audio-hooks SKILL active</em>
      </td>
    </tr>
    <tr>
      <td width="50%" align="center">
        <img src="docs/images/statusline-context-monitor.png" alt="Status Line" width="100%"/>
        <br/><em>Context monitor status line</em>
      </td>
      <td width="50%" align="center">
        <img src="docs/images/plugin-marketplace-detail.png" alt="Marketplace" width="100%"/>
        <br/><em>Marketplace registration</em>
      </td>
    </tr>
  </table>
</div>

See [`CHANGELOG.md`](CHANGELOG.md) for full details.

</details>

---

## Pick Your Platform

This is an **AI-first** project. You don't follow install steps yourself — you tell your AI agent (Claude Code, Cursor's agent, or Codex CLI) what to do, in plain English, and it runs every shell command, reads every JSON output, and reports back. Pick your platform:

| Your editor / CLI | Tell your AI agent | Jump to |
|---|---|---|
| **Claude Code** | *"Install the audio-hooks plugin from `github.com/ChanMeng666/echook`."* | [Install in 60 Seconds](#-install-in-60-seconds) |
| **Cursor IDE** (with Claude Code installed too — most users) | *"Run `audio-hooks status` and confirm `editor_targets.cursor.state` is `bridged-via-claude-code`."* (Cursor 3.2.16+ auto-bridges Claude Code plugins.) | [Cursor IDE — Path A](#-cursor-ide--same-project-two-install-paths) |
| **Cursor IDE** (without Claude Code) | *"Clone `github.com/ChanMeng666/echook` into `~/audio-hooks`, run `python ~/audio-hooks/bin/audio-hooks install --cursor`, then verify with `audio-hooks status` + `audio-hooks test all`."* | [Cursor IDE — Path B](#-cursor-ide--same-project-two-install-paths) |
| **Codex CLI** | *"Clone `github.com/ChanMeng666/echook` into `~/audio-hooks`, run `python ~/audio-hooks/bin/audio-hooks install --codex`, read the JSON output, and follow `next_steps` if `feature_flag_state` is `section_missing` or `flag_missing_or_false`."* | [Codex CLI — Native Install](#-codex-cli--native-install) |

Once installed (any path), every natural-language prompt in [Just Say It](#-just-say-it--natural-language-control) below works identically — same `audio-hooks` CLI, same JSON, same skill, regardless of which AI agent is driving.

---

## Install in 60 Seconds

**For 99% of operations, you just talk to Claude Code in plain English.** The exception is the one-time install: `/reload-plugins` has no CLI equivalent, so the human types it exactly once. Everything else — configure, snooze, theme, webhook, troubleshoot, uninstall — is pure natural language.

### Step 1 — Open Claude Code

```bash
claude
```

### Step 2 — Install with one prompt

Paste this into Claude Code:

> **Please install the audio-hooks plugin from `github.com/ChanMeng666/echook`. Use the Bash tool to run `claude plugin marketplace add ChanMeng666/echook` then `claude plugin install audio-hooks@chanmeng-audio-hooks`. After both commands complete, tell me to type `/reload-plugins` to activate the plugin.**

### Step 3 — Type `/reload-plugins` (the one manual slash command)

```text
/reload-plugins
```

### Step 4 — Verify and configure with one prompt

> **Verify audio-hooks works by running `audio-hooks diagnose` and `audio-hooks test all`. Then switch to the chime audio theme.**

**Total:** 1 shell command + 2 natural-language prompts + 1 slash command = **4 things**. From here on: natural language forever.

<details>
<summary><kbd>Install flow diagram</kbd></summary>
<br>

```mermaid
sequenceDiagram
    participant Human
    participant Terminal
    participant Claude as Claude Code
    participant Bash as Bash tool
    participant Skill as /audio-hooks SKILL
    participant CLI as audio-hooks CLI

    Human->>Terminal: type `claude` to start a session
    Human->>Claude: "install audio-hooks plugin from<br/>github.com/ChanMeng666/echook via<br/>`claude plugin marketplace add` and `claude plugin install`"
    Claude->>Bash: claude plugin marketplace add ChanMeng666/echook
    Bash-->>Claude: "Successfully added marketplace"
    Claude->>Bash: claude plugin install audio-hooks@chanmeng-audio-hooks
    Bash-->>Claude: "Installed audio-hooks. Run /reload-plugins to apply."
    Claude-->>Human: "Plugin installed.<br/>Type /reload-plugins to activate it."

    rect rgb(255, 230, 200)
        Note over Human: The one manual slash command in the whole flow
        Human->>Claude: /reload-plugins
        Claude-->>Human: "Reloaded: 8 plugins, 37 hooks, 5 skills"
    end

    Human->>Claude: "verify audio-hooks works and switch to chime audio"
    Claude->>Skill: SKILL activates on intent
    Claude->>Bash: audio-hooks diagnose
    Bash-->>Claude: {"ok":true,"errors":[],"warnings":[]}
    Claude->>Bash: audio-hooks test all
    Bash-->>Claude: {"ok":true,"passed":6,"failed":0}
    Claude->>Bash: audio-hooks theme set custom
    Bash-->>Claude: {"ok":true,"theme":"custom"}
    Claude-->>Human: "Done. 26 hooks active, all tests passed,<br/>audio theme set to chimes."

    Note over Human,CLI: From here on: pure natural language forever.
```

</details>

---

## Cursor IDE — Same Project, Two Install Paths

The project ships AI-first install paths for Cursor IDE 3.2.16+ users. Pick the one that matches your setup — both end at the same place: every `audio-hooks` subcommand below works identically on Cursor.

### Path A — Cursor + Claude Code (most users)

You already get audio-hooks for free. Cursor 3.2.16+ auto-bridges any Claude Code plugin you have installed (per [cursor.com/docs/reference/third-party-hooks](https://cursor.com/docs/reference/third-party-hooks)). Just install via the Claude Code flow above, enable Cursor Settings → "Third-party skills", then ask your agent:

> **Run `audio-hooks status` and confirm `editor_targets.cursor.state` is `bridged-via-claude-code`.**

8 of 10 hook events bridge to Cursor. `Notification` and `PermissionRequest` are absent because Cursor has no equivalent — that is by Cursor's design, not a project bug. Stop, SubagentStop, PreToolUse, PostToolUse, SessionStart, SessionEnd, PreCompact, and UserPromptSubmit all fire identically across both editors.

To disable bridging entirely, turn off Cursor Settings → "Third-party skills" (this is Cursor's global toggle, not per-plugin).

### Path B — Cursor without Claude Code

Paste this single prompt into Cursor's agent chat:

> **Clone `https://github.com/ChanMeng666/echook` into `~/audio-hooks`, then run `python ~/audio-hooks/bin/audio-hooks install --cursor`. After it succeeds, restart Cursor and run `audio-hooks status` followed by `audio-hooks test all`.**

Cursor's agent runs `git clone`, runs the install (which writes `~/.cursor/hooks.json` and seeds `~/.cursor/audio-hooks-data/user_preferences.json`), then verifies. The install is non-interactive end-to-end — no prompts, no menus, no human in the loop.

`audio-hooks install --cursor` registers 11 Cursor-native event types — the 8 bridgeable ones above plus `subagentStart`, `postToolUseFailure`, and `afterFileEdit` (Cursor-only events that have no Claude Code equivalent and so cannot be bridged from a Claude Code plugin). Each entry is tagged `_managed_by: "audio-hooks"` so uninstall removes only ours and leaves any other Cursor hooks alone.

**Already have Claude Code?** Don't run Path B — `install --cursor` aborts with `DUPLICATE_BRIDGE` to prevent double-firing. Use Path A instead. (`--force` overrides the check if you really want both paths active.)

### Upgrading a Cursor-only install

> **`cd ~/audio-hooks && git pull && python bin/audio-hooks install --cursor`**

Re-running the install is idempotent and preserves `~/.cursor/audio-hooks-data/user_preferences.json` — no data loss, no re-configuration.

### Uninstalling

| Setup | Tell your agent |
|---|---|
| Path A (auto-bridge) | *"Uninstall the audio-hooks plugin from Claude Code."* — Cursor stops bridging automatically once the plugin is gone. |
| Path B (native install) | *"Run `python ~/audio-hooks/bin/audio-hooks uninstall --cursor`."* Pass `--purge` to also delete `~/.cursor/audio-hooks-data/`. |

### Cursor + audio-hooks: natural-language control still works

Once installed (either path), every prompt in *Just Say It* below works on Cursor too. *"Switch the audio theme to chimes"*, *"Snooze audio for an hour"*, *"Enable rate-limit alerts at 80% and 95%"* — same CLI, same JSON, same skill.

---

## Codex CLI — Native Install

OpenAI's Codex CLI does NOT auto-bridge Claude Code plugins (unlike Cursor), so there's exactly one install path: a native registration at `~/.codex/hooks.json`. The install is fully AI-first — paste this single prompt into Codex (or any AI agent that can run shell commands):

> **Clone `https://github.com/ChanMeng666/echook` into `~/audio-hooks`, then run `python ~/audio-hooks/bin/audio-hooks install --codex`. Read the JSON output: if `feature_flag_state` is `section_missing` or `flag_missing_or_false`, follow the `next_steps` instruction (use your Edit tool to add `[features]\ncodex_hooks = true` to `~/.codex/config.toml`). Then restart Codex and run `audio-hooks status` to confirm `editor_targets.codex.state == "active"`.**

What the install does:

1. Writes `~/.codex/hooks.json` registering the 6 hook events Codex supports (`SessionStart`, `PreToolUse`, `PostToolUse`, `PermissionRequest`, `UserPromptSubmit`, `Stop`) — see [developers.openai.com/codex/hooks](https://developers.openai.com/codex/hooks). Each entry is tagged `_managed_by: "audio-hooks"` so uninstall removes only ours.
2. Seeds `~/.codex/audio-hooks-data/user_preferences.json` from the project default. Subsequent re-installs preserve this file.
3. **Handles the feature flag without touching user TOML.** Codex requires `[features]\ncodex_hooks = true` in `~/.codex/config.toml`. If the file doesn't exist, install authors a fresh one with the flag enabled. If the file exists but the flag is missing, install emits a machine-readable `next_steps` instruction so the calling AI agent can add the flag with its Edit tool — we never round-trip the user's TOML, because that destroys comments and formatting.
4. Writes an install marker at `~/.codex/audio-hooks-data/install_marker.json` so `audio-hooks status` can render `editor_targets.codex.state`.

The 18 audio-hooks canonical events with no Codex equivalent (`Notification`, `SubagentStop`, `PreCompact`, `WorktreeCreate`, `Elicitation`, etc.) no-op cleanly under the codex invoker with a debug NDJSON event — they never block, error, or interfere with Codex's session.

### Uninstalling Codex audio-hooks

> **Run `python ~/audio-hooks/bin/audio-hooks uninstall --codex`.** Pass `--purge` to also delete `~/.codex/audio-hooks-data/`. The uninstall **never touches `~/.codex/config.toml`** — the `codex_hooks` feature flag may benefit other Codex hook plugins.

### Codex + audio-hooks: natural-language control still works

Once installed, every prompt in *Just Say It* below works under Codex too. *"Switch the audio theme to chimes"*, *"Snooze audio for an hour"* — same CLI, same JSON output. Just point your Codex agent at the project root or use `audio-hooks` from PATH.

---

## Just Say It — Natural Language Control

Once installed (Claude Code, Cursor, or Codex — same CLI everywhere), you operate the project the same way: talk to your AI agent in plain English, it runs the right `audio-hooks` subcommand, reads the JSON output, and reports back. Every configuration is one message. The diagrams below show "Your AI Agent" generically — substitute Claude Code, Cursor's agent, or Codex CLI as appropriate.

```mermaid
sequenceDiagram
    actor You as You
    participant CC as Your AI Agent

    rect rgb(219, 234, 254)
    Note over You,CC: Audio Theme
    You->>CC: Switch audio-hooks to the chime theme.
    CC-->>You: audio-hooks theme set custom — switched to chimes.
    You->>CC: Switch audio-hooks to the voice theme.
    CC-->>You: audio-hooks theme set default — switched to ElevenLabs Jessica.
    end

    rect rgb(220, 252, 231)
    Note over You,CC: Snooze & Mute
    You->>CC: Snooze audio for 30 minutes.
    CC-->>You: audio-hooks snooze 30m — muted until 3:45 PM.
    You->>CC: Snooze audio for 8 hours.
    CC-->>You: audio-hooks snooze 8h — quiet for the rest of the day.
    You->>CC: Unmute audio.
    CC-->>You: audio-hooks snooze off — audio resumed.
    You->>CC: Is audio currently muted?
    CC-->>You: audio-hooks snooze status — not snoozed.
    end

    rect rgb(254, 243, 199)
    Note over You,CC: Hook Selection & Notification Mode
    You->>CC: Configure audio-hooks to only fire on stop,<br/>notification, and permission_request —<br/>disable everything else.
    CC-->>You: audio-hooks hooks enable-only stop notification<br/>permission_request — 3 hooks active, rest disabled.
    You->>CC: Enable session_start and session_end hooks<br/>so I hear when sessions begin and end.
    CC-->>You: session_start and session_end enabled.
    You->>CC: Switch audio-hooks to audio-only mode,<br/>no desktop popups.
    CC-->>You: Notification mode set to audio_only.
    You->>CC: Switch to notification-only mode —<br/>desktop popups but no audio.
    CC-->>You: Notification mode set to notification_only.
    You->>CC: For the stop hook only, use desktop<br/>notification without audio.
    CC-->>You: Per-hook override: stop → notification_only.
    You->>CC: Make notifications more detailed.
    CC-->>You: Detail level set to verbose.
    end
```

```mermaid
sequenceDiagram
    actor You as You
    participant CC as Your AI Agent

    rect rgb(237, 233, 254)
    Note over You,CC: Webhooks & Integrations
    You->>CC: Send audio-hooks alerts to my Slack webhook<br/>at https://hooks.slack.com/services/... and test it.
    CC-->>You: Webhook set to slack format. Test delivered.
    You->>CC: Send alerts to my Discord webhook instead.
    CC-->>You: Webhook set to discord format. Test delivered.
    You->>CC: Send alerts to ntfy at<br/>https://ntfy.sh/my-channel. Test it.
    CC-->>You: Webhook set to ntfy format. Test delivered.
    You->>CC: Only send stop and stop_failure events<br/>to the webhook, nothing else.
    CC-->>You: Webhook hook_types set to [stop, stop_failure].
    end

    rect rgb(254, 226, 226)
    Note over You,CC: TTS & Rate Limits
    You->>CC: Enable audio-hooks TTS and have it speak<br/>Claude's actual final message instead of<br/>a generic announcement.
    CC-->>You: TTS enabled with speak_assistant_message = true.
    You->>CC: Set the stop hook TTS message to<br/>"Build finished" instead of the default.
    CC-->>You: Custom TTS message for stop set.
    You->>CC: Make sure audio-hooks rate-limit alerts are<br/>enabled with 80% and 95% thresholds for both<br/>5-hour and 7-day windows.
    CC-->>You: Rate-limit alerts on — 80%, 95% for both windows.
    end

    rect rgb(207, 250, 254)
    Note over You,CC: Status Line & Context Monitor
    You->>CC: Install the audio-hooks status line<br/>in my Claude Code settings.
    CC-->>You: audio-hooks statusline install — restart to see it.
    You->>CC: Configure the status line to only show<br/>context usage.
    CC-->>You: Visible segments set to [context].
    You->>CC: Show only context and API quota<br/>in the status line.
    CC-->>You: Visible segments set to [context, api_quota].
    You->>CC: Reset the status line to show all segments.
    CC-->>You: Visible segments reset to all.
    end

    rect rgb(232, 245, 233)
    Note over You,CC: Focus Flow
    You->>CC: Enable the audio-hooks focus flow<br/>with breathing exercises.
    CC-->>You: Focus flow enabled — 4-7-8 breathing.
    You->>CC: Switch focus flow to hydration reminders.
    CC-->>You: Focus flow mode set to hydration.
    You->>CC: Only show focus flow if Claude thinks<br/>for more than 30 seconds.
    CC-->>You: min_thinking_seconds set to 30.
    end

    rect rgb(229, 231, 235)
    Note over You,CC: Monitor, Debug & Uninstall
    You->>CC: Enable the audio-hooks file_changed hook and<br/>configure it to watch .env and .envrc.
    CC-->>You: file_changed enabled, watching [.env, .envrc].
    You->>CC: Test all my audio-hooks hooks and tell me<br/>if any failed.
    CC-->>You: audio-hooks test all — 26/26 passed.
    You->>CC: What's the current state of audio-hooks?
    CC-->>You: audio-hooks status — theme: default,<br/>18 hooks enabled, 0 errors.
    You->>CC: Show me the last 20 errors and clear the log.
    CC-->>You: 2 errors found (WEBHOOK_TIMEOUT). Log cleared.
    You->>CC: What version of audio-hooks am I running?
    CC-->>You: v5.1.3, plugin install.
    You->>CC: Please uninstall audio-hooks completely.
    CC-->>You: Plugin uninstalled. All hooks removed.
    end
```

Each prompt is one message. Your AI agent (Claude Code, Cursor, or Codex) parses it, runs the right `audio-hooks` subcommand(s), and reports back. **You don't memorise anything.**

<details>
<summary><kbd>Plain-text prompt reference — copy-friendly table</kbd></summary>
<br>

| Goal | Paste this into your AI agent (Claude Code / Cursor / Codex) |
|---|---|
| **Audio Theme** | |
| Switch to chime sounds | *"Switch audio-hooks to the chime theme."* |
| Switch to voice sounds | *"Switch audio-hooks to the voice theme."* |
| **Snooze & Mute** | |
| Mute for 30 minutes | *"Snooze audio for 30 minutes."* |
| Mute for the rest of the day | *"Snooze audio for 8 hours."* |
| Unmute | *"Unmute audio."* |
| Check mute status | *"Is audio-hooks currently muted?"* |
| **Hook Selection** | |
| Only keep critical alerts | *"Only fire audio-hooks on stop, notification, and permission_request. Disable everything else."* |
| Enable session start/end sounds | *"Enable the session_start and session_end hooks."* |
| Enable tool execution sounds | *"Enable pretooluse and posttooluse audio."* |
| **Notification Mode** | |
| Audio only, no desktop popups | *"Switch audio-hooks to audio-only mode."* |
| Desktop popups only, no audio | *"Switch audio-hooks to notification-only mode."* |
| Per-hook override | *"For the stop hook, use desktop notification without audio."* |
| Make notifications verbose | *"Make audio-hooks notifications more detailed."* |
| Disable all notifications | *"Disable all audio-hooks notifications entirely."* |
| **Focus Flow** | |
| Breathing exercises | *"Enable the audio-hooks focus flow with breathing exercises."* |
| Hydration reminders | *"Switch audio-hooks focus flow to hydration reminders."* |
| Custom URL during thinking | *"Set audio-hooks focus flow to open `https://example.com` during thinking."* |
| Longer thinking delay | *"Only show focus flow if Claude thinks for more than 30 seconds."* |
| **Webhooks** | |
| Send alerts to Slack | *"Send audio-hooks alerts to my Slack webhook at `https://hooks.slack.com/services/...` and test it."* |
| Send alerts to Discord | *"Send audio-hooks alerts to my Discord webhook at `https://discord.com/api/webhooks/...` and test it."* |
| Send alerts to Teams | *"Send audio-hooks alerts to my Teams webhook. Test it."* |
| Send alerts to ntfy | *"Send audio-hooks alerts to `https://ntfy.sh/my-topic` in ntfy format. Test it."* |
| Only webhook certain events | *"Only send stop and stop_failure events to the webhook."* |
| Disable webhook | *"Disable the audio-hooks webhook."* |
| **TTS (Text-to-Speech)** | |
| Speak Claude's reply out loud | *"Enable audio-hooks TTS and speak Claude's actual final message."* |
| Custom TTS message for a hook | *"Set the audio-hooks stop TTS message to 'Build finished'."* |
| Limit spoken message length | *"Limit audio-hooks TTS to 300 characters."* |
| **Rate-limit Alerts** | |
| Enable with custom thresholds | *"Enable audio-hooks rate-limit alerts at 80% and 95% for both windows."* |
| Adjust 5-hour thresholds | *"Set audio-hooks 5-hour rate-limit thresholds to 75% and 90%."* |
| **Status Line** | |
| Add a status bar | *"Install the audio-hooks status line."* |
| Status bar: context only | *"Only show context usage in the audio-hooks status line."* |
| Status bar: context + API quota | *"Show context and API quota in the audio-hooks status line."* |
| Status bar: show everything | *"Reset the audio-hooks status line to show all segments."* |
| Remove status bar | *"Uninstall the audio-hooks status line."* |
| **File Watching** | |
| Watch .env for changes | *"Enable the audio-hooks file_changed hook and watch `.env` and `.envrc`."* |
| **Monitor & Debug** | |
| Test all hooks | *"Test all audio-hooks and tell me if any failed."* |
| Show current state | *"Show the current audio-hooks status — enabled hooks, theme, and recent errors."* |
| Why no sound? | *"Audio-hooks isn't playing sounds. Diagnose and fix it."* |
| Show recent errors | *"Show me the last 20 audio-hooks errors."* |
| Clear the log | *"Clear the audio-hooks event log."* |
| Check version | *"What version of audio-hooks am I running?"* |
| Adjust debounce timing | *"Set audio-hooks debounce to 1000ms."* |
| **Uninstall** | |
| Uninstall | *"Please uninstall audio-hooks completely."* |

</details>

---

## How It Works

```mermaid
flowchart LR
    CC[Claude Code event] -->|stdin JSON| MR{native matcher<br/>routing}
    MR -->|session_start_resume| HR[hook_runner.py]
    MR -->|stop_failure_rate_limit| HR
    MR -->|notification_idle_prompt| HR
    MR -->|...| HR

    HR -->|reads| RL[rate-limit pre-check<br/>marker debounce]
    HR -->|reads| CFG[user_preferences.json]
    HR -->|reads| MARK[snooze + focus-flow markers]

    HR -->|fires| AUDIO[Audio playback<br/>26 MP3s, 2 themes]
    HR -->|fires| NOTIF[Desktop notification]
    HR -->|fires| TTS[TTS announcement]
    HR -->|fires| WH[Webhook subprocess<br/>fire-and-forget]
    HR -->|writes| LOG[(NDJSON event log<br/>schema audio-hooks.v1)]

    style CC fill:#4A90E2,color:#fff
    style HR fill:#7ED321,color:#000
    style RL fill:#F5A623,color:#000
    style AUDIO fill:#F5A623,color:#000
    style WH fill:#9013FE,color:#fff
    style LOG fill:#50E3C2,color:#000
```

Claude Code fires hook events as JSON on stdin. Native matchers in `hooks.json` route each event to `hook_runner.py` with a synthetic event name. The runner checks snooze state, rate-limit thresholds, debounce, and user filters — then fires audio playback, desktop notifications, TTS, and webhooks as configured.

<table><tbody>
<tr></tr>
<tr><td width="10000">
<details>
<summary>&nbsp;&nbsp;<strong>AI Control Surface</strong></summary><br>

```mermaid
flowchart TB
    USER[Human] -->|natural language| CLAUDE[Claude Code]
    CLAUDE -->|activates| SKILL[/audio-hooks SKILL/]
    SKILL -->|invokes via Bash tool| BIN[bin/audio-hooks]

    BIN -->|reads| CONFIG[(user_preferences.json<br/>in plugin data dir)]
    BIN -->|reads| MARKERS[(snooze + focus-flow markers)]
    BIN -->|reads| LOGS[(NDJSON event log)]
    BIN -->|writes| CONFIG
    BIN -->|writes| MARKERS

    BIN -->|invokes| HR[hook_runner.run_hook<br/>for `audio-hooks test`]

    BIN -->|JSON to stdout| CLAUDE
    CLAUDE -->|reports back| USER

    style USER fill:#4A90E2,color:#fff
    style CLAUDE fill:#9013FE,color:#fff
    style SKILL fill:#7ED321,color:#000
    style BIN fill:#F5A623,color:#000
```

</details>
</td></tr>
<tr></tr>
<tr><td width="10000">
<details>
<summary>&nbsp;&nbsp;<strong>Hook Lifecycle</strong></summary><br>

```mermaid
flowchart TD
    EVT[Hook event fires<br/>e.g. Stop] --> SNOOZE{snooze<br/>active?}
    SNOOZE -->|yes| EXIT0[exit 0 silent]
    SNOOZE -->|no| RL{rate_limits<br/>in stdin?}
    RL -->|yes| RLCHK{crossed<br/>threshold?}
    RLCHK -->|yes| RLAUDIO[play warning audio<br/>+ write debounce marker]
    RLCHK -->|no| DEB
    RL -->|no| DEB{debounced<br/>recently?}
    RLAUDIO --> DEB
    DEB -->|yes| EXIT0
    DEB -->|no| FILTER{user filter<br/>matches?}
    FILTER -->|yes, exclude| EXIT0
    FILTER -->|no| ENABLED{hook<br/>enabled?}
    ENABLED -->|no| EXIT0
    ENABLED -->|yes| FIRE[play audio + notif + TTS + webhook]
    FIRE --> LOG[write NDJSON event]

    style EVT fill:#4A90E2,color:#fff
    style FIRE fill:#7ED321,color:#000
    style RLAUDIO fill:#F5A623,color:#000
    style EXIT0 fill:#999,color:#fff
```

</details>
</td></tr>
<tr></tr>
<tr><td width="10000">
<details>
<summary>&nbsp;&nbsp;<strong>Plugin Layout</strong></summary><br>

```mermaid
flowchart TB
    REPO[echook/]

    REPO --> CP[.claude-plugin/marketplace.json]
    REPO --> PLUGINS[plugins/audio-hooks/]
    REPO --> CANON[CANONICAL SOURCE]

    PLUGINS --> P_MANIFEST[.claude-plugin/plugin.json]
    PLUGINS --> P_HOOKS[hooks/hooks.json<br/>matcher-scoped]
    PLUGINS --> P_RUNNER[runner/run.py]
    PLUGINS --> P_SKILL[skills/audio-hooks/SKILL.md]
    PLUGINS --> P_BIN[bin/ — audio-hooks + statusline]
    PLUGINS --> P_AUDIO[audio/ — bundled MP3s]
    PLUGINS --> P_CONFIG[config/default_preferences.json]

    CANON --> C_HOOKS[hooks/hook_runner.py]
    CANON --> C_BIN[bin/audio-hooks + audio-hooks.py + .cmd]
    CANON --> C_AUDIO[audio/default + audio/custom]
    CANON --> C_CONFIG[config/]
    CANON --> C_SCRIPTS[scripts/ — install + build-plugin + uninstall]

    C_HOOKS -.->|build-plugin.sh syncs| P_HOOKS
    C_BIN -.->|build-plugin.sh syncs| P_BIN
    C_AUDIO -.->|build-plugin.sh syncs| P_AUDIO

    style REPO fill:#4A90E2,color:#fff
    style PLUGINS fill:#7ED321,color:#000
    style CANON fill:#F5A623,color:#000
```

</details>
</td></tr>
</tbody></table>

---

## Key Features

### Status Line with Context Monitor

Real-time context window and API quota bars — color-coded warnings before Claude enters the "agent dumb zone".

<p align="center">
<img src="docs/images/statusline-context-monitor.png" alt="Status Line — context window monitor" width="720">
</p>

```text
[Opus] echook v5.1.3 | 6/26 Sounds | Webhook: ntfy | Theme: Voice
[MUTED 23m]  feat/audio-v5  API Quota: 78%  Context: 65% (130K/200K)  /compact
```

| Color | Range | Meaning | Action |
|---|---|---|---|
| Green | < 50% | Safe — agent performs well | Keep working |
| Yellow | 50-80% | Caution — entering the "dumb zone" | Type `/compact` or `/clear` |
| Red | > 80% | Danger — agent makes frequent errors | Type `/compact` immediately |

**Absolute counts (v5.1.3+).** When Claude Code reports the context window size, the segment also shows current/max tokens, e.g. `Context: 83% (166K/200K)`. This makes the math obvious when you `/model`-switch between context-window variants — switching from Opus 4.7 (1M) to Sonnet 4.6 (200K) keeps your tokens identical but shrinks the denominator 5×, so a sudden jump from `17%` to `83%` is **expected**, not a bug. See `docs/TROUBLESHOOTING.md#context-97-or-any-sudden-jump-right-after-switching-models` for the full explanation.

<details>
<summary><kbd>10 customisable segments</kbd></summary>
<br>

| Segment | Shows |
|---|---|
| `model` | Model name (e.g. `[Opus]`) |
| `version` | echook version |
| `sounds` | Enabled sound count |
| `webhook` | Webhook status |
| `theme` | Audio theme |
| `snooze` | Mute countdown (when active) |
| `focus` | Focus Flow mode (when active) |
| `branch` | Git branch name |
| `api_quota` | API usage quota bar |
| `context` | Context window usage bar |

</details>

### Audio Themes

| Theme | Style | Source |
|---|---|---|
| `default` | ElevenLabs **Jessica** voice — short spoken phrases like *"Task completed"* | `audio/default/*.mp3` |
| `custom` | Modern UI sound effects (chimes, beeps) | `audio/custom/chime-*.mp3` |

Say *"switch to chimes"* or *"switch to voice"* — Claude Code handles the rest.

### 26 Hook Events

26 events covering the full Claude Code lifecycle — from session start to file changes, permission requests to rate-limit warnings. 6 are enabled by default; toggle any with natural language.

<details>
<summary><kbd>Full hook events table</kbd></summary>
<br>

| Hook | Default | Audio file | Native matchers |
|---|:-:|---|---|
| `notification` | on | notification-urgent.mp3 | `permission_prompt` / `idle_prompt` / `auth_success` / `elicitation_dialog` |
| `stop` | on | task-complete.mp3 | |
| `subagent_stop` | on | subagent-complete.mp3 | agent type |
| `permission_request` | on | permission-request.mp3 | tool name |
| `permission_denied` | on | permission-denied.mp3 | |
| `task_created` | on | task-created.mp3 | |
| `task_completed` | | team-task-done.mp3 | |
| `session_start` | | session-start.mp3 | `startup` / `resume` / `clear` / `compact` |
| `session_end` | | session-end.mp3 | `clear` / `resume` / `logout` / `prompt_input_exit` |
| `pretooluse` | | task-starting.mp3 | tool name |
| `posttooluse` | | task-progress.mp3 | tool name |
| `posttoolusefailure` | | tool-failed.mp3 | tool name |
| `userpromptsubmit` | | prompt-received.mp3 | |
| `subagent_start` | | subagent-start.mp3 | agent type |
| `precompact` / `postcompact` | | notification-info.mp3 / post-compact.mp3 | `manual` / `auto` |
| `stop_failure` | | stop-failure.mp3 | `rate_limit` / `authentication_failed` / `billing_error` / `server_error` / `unknown` |
| `teammate_idle` | | teammate-idle.mp3 | |
| `config_change` | | config-change.mp3 | |
| `instructions_loaded` | | instructions-loaded.mp3 | |
| `worktree_create` / `worktree_remove` | | worktree-create.mp3 / worktree-remove.mp3 | |
| `elicitation` / `elicitation_result` | | elicitation.mp3 / elicitation-result.mp3 | |
| `cwd_changed` | | cwd-changed.mp3 | |
| `file_changed` | | file-changed.mp3 | literal filenames |

</details>

### Webhooks

Fan out hook events to Slack, Discord, Teams, ntfy, or any HTTP endpoint. Versioned `audio-hooks.webhook.v1` payload. Fire-and-forget via subprocess — never blocks the hook. Say *"send alerts to my Slack"* and Claude Code sets it up.

### Rate-limit Alerts

Watches every hook's stdin for `rate_limits` and plays a one-shot warning at configurable thresholds (default 80%/95%). Each `(window, threshold, resets_at)` fires exactly once — warned at 80%, again at 95%, never spammed.

### TTS — Speak Claude's Reply

Instead of a static "Task completed", TTS speaks Claude's actual final message (truncated to 200 chars). Off by default — privacy-conscious. Say *"speak Claude's actual reply when done"* to enable.

### Focus Flow

Anti-distraction micro-task during Claude's thinking time: guided breathing exercise, hydration reminder, custom URL, or shell command. Auto-closes when Claude finishes. Say *"enable focus flow with breathing exercises"*.

---

## Technical Reference

<details>
<summary><kbd>CLI, configuration, environment variables, error codes, logging, manual install (click to expand)</kbd></summary>

### `audio-hooks` CLI

Single Python binary on PATH. JSON output, no prompts, no spinners.

| Subcommand | Purpose |
|---|---|
| `audio-hooks manifest` | Canonical introspection — every subcommand, hook, config key, error code, env var |
| `audio-hooks manifest --schema` | JSON Schema for `user_preferences.json` |
| `audio-hooks status` | Full state snapshot |
| `audio-hooks version` | Version + install mode detection |
| `audio-hooks get <dotted.key>` | Read any config key |
| `audio-hooks set <dotted.key> <value>` | Write any config key (auto-coerces) |
| `audio-hooks hooks list` | All 26 hooks with current state |
| `audio-hooks hooks enable/disable <name>` | Toggle a hook |
| `audio-hooks hooks enable-only <a> <b>` | Exclusive enable |
| `audio-hooks theme list/set <name>` | Audio theme |
| `audio-hooks snooze [duration]/off/status` | Mute hooks (default 30m) |
| `audio-hooks webhook/set/clear/test` | Webhook config + test |
| `audio-hooks tts set ...` | TTS config |
| `audio-hooks rate-limits set ...` | Rate-limit alert thresholds |
| `audio-hooks test <hook\|all>` | Smoke-test hooks |
| `audio-hooks diagnose` | System check |
| `audio-hooks logs tail/clear` | NDJSON event log |
| `audio-hooks install/uninstall` | Non-interactive install/uninstall |
| `audio-hooks statusline show/install/uninstall` | Status line management |

### Configuration Keys

| Key | Type | Default | Effect |
|---|---|---|---|
| `audio_theme` | `default` \| `custom` | `default` | Voice recordings vs chimes |
| `enabled_hooks.<hook>` | bool | varies | Per-hook toggle |
| `playback_settings.debounce_ms` | int | 500 | Min ms between same hook firing |
| `notification_settings.mode` | enum | `audio_and_notification` | `audio_only` / `notification_only` / `audio_and_notification` / `disabled` |
| `notification_settings.detail_level` | enum | `standard` | `minimal` / `standard` / `verbose` |
| `webhook_settings.enabled` | bool | `false` | Webhook fan-out |
| `webhook_settings.url` | string | `""` | Target URL |
| `webhook_settings.format` | enum | `raw` | `slack` / `discord` / `teams` / `ntfy` / `raw` |
| `webhook_settings.hook_types` | array | `["stop","notification",...]` | Which hooks fire the webhook |
| `tts_settings.enabled` | bool | `false` | TTS announcements |
| `tts_settings.speak_assistant_message` | bool | `false` | TTS Claude's actual reply on stop |
| `tts_settings.assistant_message_max_chars` | int | 200 | Truncation cap |
| `rate_limit_alerts.enabled` | bool | `true` | Watch stdin rate_limits |
| `rate_limit_alerts.five_hour_thresholds` | int[] | `[80, 95]` | 5h window thresholds |
| `rate_limit_alerts.seven_day_thresholds` | int[] | `[80, 95]` | 7d window thresholds |
| `focus_flow.enabled` / `mode` / `min_thinking_seconds` / `breathing_pattern` | mixed | off / `breathing` / 15 / `4-7-8` | Anti-distraction micro-task |
| `statusline_settings.visible_segments` | string[] | `[]` (all) | Status line segments to show |

### Environment Variables

| Variable | Purpose |
|---|---|
| `CLAUDE_PLUGIN_DATA` | Plugin install state directory (auto-set by Claude Code) |
| `CLAUDE_PLUGIN_ROOT` | Plugin install root (auto-set) |
| `CLAUDE_AUDIO_HOOKS_DATA` | Explicit override for state directory |
| `CLAUDE_AUDIO_HOOKS_PROJECT` | Explicit override for project root |
| `CLAUDE_HOOKS_DEBUG` | `1` to write debug-level events to NDJSON log |
| `CLAUDE_NONINTERACTIVE` | `1` to force scripts into non-interactive mode |
| `ELEVENLABS_API_KEY` | Used by `scripts/generate-audio.py` (never logged) |

### Stable Error Codes

| Code | When | Suggested fix |
|---|---|---|
| `AUDIO_FILE_MISSING` | Audio file doesn't exist | `audio-hooks diagnose` |
| `AUDIO_PLAYER_NOT_FOUND` | No audio player binary | `audio-hooks diagnose` |
| `AUDIO_PLAY_FAILED` | Player exited with error | `audio-hooks test` |
| `INVALID_CONFIG` | `user_preferences.json` malformed | `audio-hooks manifest --schema` |
| `CONFIG_READ_ERROR` | Can't read config | `audio-hooks status` |
| `WEBHOOK_HTTP_ERROR` | Webhook returned non-2xx | `audio-hooks webhook test` |
| `WEBHOOK_TIMEOUT` | Webhook timed out | `audio-hooks webhook test` |
| `NOTIFICATION_FAILED` | Desktop notification failed | `audio-hooks diagnose` |
| `TTS_FAILED` | TTS engine failed | `audio-hooks tts set --enabled false` |
| `SETTINGS_DISABLE_ALL_HOOKS` | `disableAllHooks: true` in settings | `audio-hooks diagnose` |
| `DUAL_INSTALL_DETECTED` | Both install methods active | `bash scripts/uninstall.sh --yes` |
| `PROJECT_DIR_NOT_FOUND` | Can't locate project | `audio-hooks status` |
| `UNKNOWN_HOOK_TYPE` | Unrecognised hook name | `audio-hooks hooks list` |
| `INTERNAL_ERROR` | Unexpected error | `audio-hooks logs tail` |

### NDJSON Event Log

Every event is one JSON object per line at `${CLAUDE_PLUGIN_DATA}/logs/events.ndjson`. Schema `audio-hooks.v1`.

```json
{"ts":"2026-04-11T10:23:45.123Z","schema":"audio-hooks.v1","level":"info","hook":"stop","session_id":"abc","action":"play_audio","audio_file":"chime-task-complete.mp3","duration_ms":42}
```

Levels: `debug`, `info`, `warn`, `error`. Log rotation: 5 MB cap, 3 files kept.

### Manual Install Reference

**Plugin install** (two slash commands inside Claude Code):

```text
/plugin marketplace add ChanMeng666/echook
/plugin install audio-hooks@chanmeng-audio-hooks
/reload-plugins
```

**Legacy script install** (pre-v5.0, still works):

```bash
git clone https://github.com/ChanMeng666/echook.git
cd echook
bash scripts/install-complete.sh    # auto non-interactive on non-TTY
```

Both paths share the same `hook_runner.py` and `audio-hooks` CLI. They are mutually exclusive — `audio-hooks diagnose` reports `DUAL_INSTALL_DETECTED` if both are active.

### ElevenLabs Audio Generator

`scripts/generate-audio.py` reads `config/audio_manifest.json` and regenerates audio via the ElevenLabs API:

```bash
ELEVENLABS_API_KEY=sk_... python scripts/generate-audio.py           # generate missing
ELEVENLABS_API_KEY=sk_... python scripts/generate-audio.py --force   # regenerate all
python scripts/generate-audio.py --dry-run                           # preview
```

To add a new audio file: edit `config/audio_manifest.json`, run the generator, then `bash scripts/build-plugin.sh`.

</details>

---

## Platform Support

| Platform | Audio player | Status |
|---|---|---|
| **Windows** (PowerShell / Git Bash / WSL2) | PowerShell MediaPlayer | Fully supported |
| **macOS** | `afplay` | Fully supported |
| **Linux** | `mpg123` / `ffplay` / `paplay` / `aplay` (auto-detected) | Fully supported |

Python 3.6+ is the only runtime requirement.

---

## Troubleshooting

<table><tbody>
<tr></tr>
<tr><td width="10000">
<details>
<summary>&nbsp;&nbsp;<strong>No sound at all</strong></summary><br>

Run `audio-hooks diagnose`, look for any error code, and run its `suggested_command`. Or just say: *"Audio-hooks isn't playing sounds. Diagnose and fix it."*

</details>
</td></tr>
<tr></tr>
<tr><td width="10000">
<details>
<summary>&nbsp;&nbsp;<strong>Hearing double sounds</strong></summary><br>

Both legacy script install and plugin install are active. Diagnose reports `DUAL_INSTALL_DETECTED`. Fix: `bash scripts/uninstall.sh --yes` (removes legacy, preserves config).

</details>
</td></tr>
<tr></tr>
<tr><td width="10000">
<details>
<summary>&nbsp;&nbsp;<strong>Plugin won't install</strong></summary><br>

Run `claude plugin validate plugins/audio-hooks` from the project root — surfaces manifest schema errors. v5.0.1+ verified clean on Claude Code v2.1.101.

</details>
</td></tr>
<tr></tr>
<tr><td width="10000">
<details>
<summary>&nbsp;&nbsp;<strong>pretooluse / posttooluse too noisy</strong></summary><br>

They fire on every tool execution (Read, Glob, Grep, etc.) — disabled by default for this reason. Enable explicitly with *"enable pretooluse and posttooluse audio"*.

</details>
</td></tr>
</tbody></table>

---

## Uninstall

**Plugin install:** say *"uninstall audio-hooks"* or manually:

```text
/plugin uninstall audio-hooks@chanmeng-audio-hooks
```

**Legacy script install:**

```bash
bash scripts/uninstall.sh --yes              # preserve config + audio
bash scripts/uninstall.sh --yes --purge      # remove everything
```

---

## For Developers

<details>
<summary><kbd>Repository layout, workflow, and contribution guide (click to expand)</kbd></summary>

### Repository Layout

```
echook/
├── .claude-plugin/marketplace.json
├── plugins/audio-hooks/              # plugin layout (populated by build-plugin.sh)
│   ├── .claude-plugin/plugin.json
│   ├── hooks/hooks.json
│   ├── runner/run.py
│   ├── skills/audio-hooks/SKILL.md
│   ├── bin/
│   ├── audio/
│   ├── cursor-hooks/                 # mirror of canonical cursor-hooks/
│   ├── codex-hooks/                  # mirror of canonical codex-hooks/
│   └── config/default_preferences.json
├── hooks/                            # CANONICAL
│   ├── hook_runner.py
│   ├── invoker.py                    # detect_invoker() — Claude Code / Cursor / Codex
│   └── user_preferences.py
├── bin/                              # CANONICAL
│   ├── audio-hooks / audio-hooks.py / audio-hooks.cmd
│   └── audio-hooks-statusline / .py / .cmd
├── audio/                            # CANONICAL: 26 default + 26 custom
├── config/
│   ├── default_preferences.json
│   ├── user_preferences.schema.json
│   └── audio_manifest.json
├── cursor-hooks/hooks.json           # CANONICAL: Cursor IDE install template
├── codex-hooks/hooks.json            # CANONICAL: Codex CLI install template
├── scripts/
│   ├── install-complete.sh / install-windows.ps1
│   ├── uninstall.sh / build-plugin.sh
│   ├── bump-version.sh               # atomic version bump across 6 files
│   ├── generate-audio.py
│   └── ...
├── tests/                            # 139 unittest cases (Cursor + Codex bridge contracts)
├── CLAUDE.md
├── README.md
└── CHANGELOG.md
```

### Workflow

1. Edit canonical files (`/hooks/`, `/bin/`, `/audio/`, `/config/`, `/cursor-hooks/`, `/codex-hooks/`)
2. Run `bash scripts/build-plugin.sh` to sync into plugin layout
3. CI verifies in-sync via `bash scripts/build-plugin.sh --check`
4. Validate: `claude plugin validate plugins/audio-hooks`
5. Test: `python -m unittest discover -v tests` (139 tests; Ubuntu/Windows/macOS × Python 3.9/3.12/3.13 in CI)
6. Bump version (when releasing): `bash scripts/bump-version.sh <new_version>` — atomically updates 6 canonical version locations and re-runs `build-plugin.sh`

### Contributing

Pull requests welcome. Fork, clone, make changes to canonical files, run `build-plugin.sh`, validate, test end-to-end, and submit with a conventional commit message.

</details>

---

## Documentation

| Document | Purpose |
|---|---|
| [**CLAUDE.md**](CLAUDE.md) | Canonical AI-facing operating guide (~40 lines, points everywhere else) |
| [**docs/INSTALLATION_GUIDE.md**](docs/INSTALLATION_GUIDE.md) | Step-by-step install for Claude Code / Cursor / Codex |
| [**docs/TROUBLESHOOTING.md**](docs/TROUBLESHOOTING.md) | Diagnostic recipes for common issues |
| [**docs/ARCHITECTURE.md**](docs/ARCHITECTURE.md) | System architecture and design decisions |
| [**CHANGELOG.md**](CHANGELOG.md) | Detailed version history |
| `audio-hooks manifest` | Live source of truth — includes `subcommands`, `hooks`, `config_keys`, `error_codes`, `env_vars`, `editor_targets`, `supported_editors`, and `pointers` (paths to every doc above). Always up to date. |

---

<table>
<tr>
<td>

**Design Philosophy** — This project is **AI-operated**, not AI-assisted. A typical CLI tool: the human learns the tool. **echook**: the human says what they want, Claude Code learns the tool and does the work. The human is **upstream** of Claude Code, not downstream of the CLI.

</td>
</tr>
</table>

---

## License

This project is licensed under the **MIT License** — see [LICENSE](LICENSE) for details.

- Commercial use allowed
- Modification allowed
- Distribution allowed
- Private use allowed

---

## Author

<div align="center">
  <table>
    <tr>
      <td align="center">
        <a href="https://github.com/ChanMeng666">
          <img src="https://github.com/ChanMeng666.png?size=100" width="100px;" alt="Chan Meng"/>
          <br />
          <sub><b>Chan Meng</b></sub>
        </a>
        <br />
        <small>Creator & Lead Developer</small>
      </td>
    </tr>
  </table>
</div>

<p align="center">
  <a href="https://github.com/ChanMeng666">
    <img src="https://img.shields.io/badge/GitHub-ChanMeng666-181717?style=flat&logo=github&logoColor=white" alt="GitHub"/>
  </a>
  <a href="https://www.linkedin.com/in/chanmeng666/">
    <img src="https://img.shields.io/badge/LinkedIn-chanmeng666-0A66C2?style=flat&logo=linkedin&logoColor=white" alt="LinkedIn"/>
  </a>
  <a href="https://chanmeng.org/">
    <img src="https://img.shields.io/badge/Website-chanmeng.org-4285F4?style=flat&logo=googlechrome&logoColor=white" alt="Website"/>
  </a>
</p>

<p align="center">
  <a href="https://buymeacoffee.com/chanmeng66u" target="_blank">
    <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="50">
  </a>
</p>

---

<div align="right">

[![][back-to-top]](#readme-top)

</div>

<!-- LINK DEFINITIONS -->

[back-to-top]: https://img.shields.io/badge/-BACK_TO_TOP-black?style=flat-square

[share-x-shield]: https://img.shields.io/badge/-Share%20on%20X-black?labelColor=black&logo=x&logoColor=white&style=flat-square
[share-x-link]: https://x.com/intent/tweet?text=Check%20out%20echook%20-%20AI-operated%20audio%20notifications%20for%20Claude%20Code&url=https%3A%2F%2Fgithub.com%2FChanMeng666%2Fechook

[share-linkedin-shield]: https://img.shields.io/badge/-Share%20on%20LinkedIn-blue?labelColor=blue&logo=linkedin&logoColor=white&style=flat-square
[share-linkedin-link]: https://www.linkedin.com/sharing/share-offsite/?url=https%3A%2F%2Fgithub.com%2FChanMeng666%2Fechook

[share-reddit-shield]: https://img.shields.io/badge/-Share%20on%20Reddit-orange?labelColor=black&logo=reddit&logoColor=white&style=flat-square
[share-reddit-link]: https://www.reddit.com/submit?title=echook%20-%20AI-operated%20audio%20notifications&url=https%3A%2F%2Fgithub.com%2FChanMeng666%2Fechook

[share-telegram-shield]: https://img.shields.io/badge/-Share%20on%20Telegram-blue?labelColor=blue&logo=telegram&logoColor=white&style=flat-square
[share-telegram-link]: https://t.me/share/url?text=echook%20-%20AI-operated%20audio%20notifications&url=https%3A%2F%2Fgithub.com%2FChanMeng666%2Fechook

[share-whatsapp-shield]: https://img.shields.io/badge/-Share%20on%20WhatsApp-green?labelColor=green&logo=whatsapp&logoColor=white&style=flat-square
[share-whatsapp-link]: https://api.whatsapp.com/send?text=Check%20out%20echook%20-%20AI-operated%20audio%20notifications%20for%20Claude%20Code%20https%3A%2F%2Fgithub.com%2FChanMeng666%2Fechook
