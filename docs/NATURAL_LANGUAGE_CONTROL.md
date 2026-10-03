# Natural-Language Control

echook is configured **in natural language**: you never memorise CLI flags. You tell your AI agent (Claude Code, Cursor's agent, or Codex CLI) what you want in plain English, and it runs the right `audio-hooks` subcommand, reads the JSON output, and reports back. Every configuration is one message.

This page is the **complete reference of example prompts**. You don't need to copy them verbatim — paraphrase freely. Not sure what's possible? Just ask your agent **"what can I configure in echook?"** and it will list every option.

> The diagrams below show "Your AI Agent" generically — substitute Claude Code, Cursor's agent, or Codex CLI as appropriate. The CLI, JSON, and skill are identical on all three.

## What it looks like in practice

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

    rect rgb(229, 231, 235)
    Note over You,CC: Monitor, Debug & Uninstall
    You->>CC: Enable the audio-hooks file_changed hook and<br/>configure it to watch .env and .envrc.
    CC-->>You: file_changed enabled, watching [.env, .envrc].
    You->>CC: Test all my audio-hooks hooks and tell me<br/>if any failed.
    CC-->>You: audio-hooks test all — every hook passed.
    You->>CC: What's the current state of audio-hooks?
    CC-->>You: audio-hooks status — theme: default,<br/>stop, notification and permission_request on, 0 errors.
    You->>CC: Show me the last 20 errors and clear the log.
    CC-->>You: 2 errors found (WEBHOOK_TIMEOUT). Log cleared.
    You->>CC: What version of audio-hooks am I running?
    CC-->>You: audio-hooks version — plugin install, running the latest release.
    You->>CC: Please uninstall audio-hooks completely.
    CC-->>You: Plugin uninstalled. All hooks removed.
    end
```

## Copy-friendly prompt reference

Each row is one message you can paste into your AI agent (Claude Code / Cursor / Codex).

| Goal | Paste this into your AI agent |
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
| Different sound for shell vs MCP (Cursor) | *"Enable the shell_before and mcp_before hooks so I hear shell commands and MCP calls separately."* |
| Ping me when setup/init finishes | *"Enable the setup hook."* |
| **Notification Mode** | |
| Audio only, no desktop popups | *"Switch audio-hooks to audio-only mode."* |
| Desktop popups only, no audio | *"Switch audio-hooks to notification-only mode."* |
| Per-hook override | *"For the stop hook, use desktop notification without audio."* |
| Make notifications verbose | *"Make audio-hooks notifications more detailed."* |
| Disable all notifications | *"Disable all audio-hooks notifications entirely."* |
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
| **Status Line (Claude Code)** | |
| Add a status bar | *"Install the audio-hooks status line."* |
| What can it show? | *"List all the audio-hooks status line segments."* (runs `statusline segments`) |
| Status bar: context only | *"Only show context usage in the audio-hooks status line."* |
| Status bar: weekly limit only | *"Show only my weekly limit in the audio-hooks status line."* |
| Status bar: cost + model + effort | *"Show session cost, the model, and effort in the status line."* |
| Status bar: session duration / burn rate / git changes | *"Show how long I've been running, my cost per hour, and uncommitted git changes."* |
| Status bar: drop a few segments | *"Hide burn rate and api time but keep everything else in the status line."* (sets `hidden_segments`) |
| Status bar: show everything | *"Reset the audio-hooks status line to show all segments."* |
| Status bar: too many rows / truncated | *"Pin the audio-hooks status line width to 120 columns."* |
| Remove status bar | *"Uninstall the audio-hooks status line."* |
| **Status Line (Codex — curation only)** | |
| Codex bar is cut off / shows `…` | *"Fix my Codex status bar — it's truncated."* (runs `statusline codex apply --preset balanced`) |
| Codex bar + tab title | *"Curate both my Codex status line and terminal title."* (`--target both`) |
| Preview before applying | *"Preview the full Codex status line preset."* |
| Inspect current Codex bar | *"Show my current Codex status line."* |
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

## See also

- [CLI & Configuration Reference](CLI_REFERENCE.md) — the exact subcommands and config keys behind these prompts.
- [Installation Guide](INSTALLATION_GUIDE.md) — install/uninstall for Claude Code, Cursor, and Codex.
- `audio-hooks manifest` — the live, always-current source of truth for every option.
