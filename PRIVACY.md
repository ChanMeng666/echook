# Privacy Policy

**echook** (the `audio-hooks` plugin for Claude Code, Cursor and Codex)

Effective date: 2026-10-04

echook is open-source software that runs entirely on your own computer. Its maintainer operates no server, no account system and no analytics service for it, and never receives any data from your use of it. This page explains what the software handles on your machine, the one case in which data leaves your machine, and how to control both.

The source code is the reference for everything stated here. Where this page and the code disagree, please [open an issue](https://github.com/ChanMeng666/echook/issues) so it can be corrected.

## Summary

- echook collects nothing for its maintainer. There is no telemetry, no analytics, no crash reporting and no update check.
- By default, nothing echook handles leaves your computer.
- Data leaves your computer in exactly one case: you configure a **webhook**, and echook then sends event messages to the URL you chose.
- Everything echook stores is in local files that you can read, change and delete.

## What echook processes on your computer

Your editor (Claude Code, Cursor or Codex) starts echook's hook runner when a lifecycle event occurs and passes it a description of that event. Depending on the event, that description can include the event name, a session identifier, the working directory, the name and input of a tool, an error message, and the text of the assistant's last message.

echook uses this information to decide whether to play a sound, show a desktop notification, speak a phrase, or send a webhook message. It does not read your conversation transcript or history, and it does not keep the event description after the event has been handled, except for what is listed under "What echook stores on your computer".

If you install the optional status line, your editor also passes echook a summary of the session (for example the model name, context usage and rate-limit percentages) each time the status line is redrawn. echook formats it for display and does not send it anywhere.

## What echook stores on your computer

| What | Where | Contents |
|---|---|---|
| Preferences | `user_preferences.json` in the plugin data directory (normally `~/.claude/plugins/data/audio-hooks-chanmeng-audio-hooks/`) | Your settings, including a webhook URL if you set one through the `audio-hooks` command line |
| Event log | `logs/events.ndjson` in the same directory, rotated by size | Event names, outcomes and errors. If text-to-speech is on, the first 100 characters of what was spoken |
| Short-lived state | `queue/` in the same directory | Snooze and debounce markers and status line caches |
| Preference backups | `~/.claude-audio-hooks-backups/` | Earlier copies of your preferences, made when you change a setting |
| Uninstall backups | `~/.claude/backups/` | Copies of files that `audio-hooks uninstall` is about to change |

When the webhook URL is set as a plugin option in Claude Code, Claude Code stores it in its own credential store; that storage is governed by Claude Code, not by echook.

Setting the environment variable `CLAUDE_HOOKS_DEBUG=1` additionally writes the most recent status line input to the data directory for troubleshooting.

## When data leaves your computer

**Webhooks (off by default).** If you configure a webhook URL, echook sends an HTTP POST to that URL, and to no other destination, for the event types you select. By default these are `stop`, `notification`, `permission_request`, `posttoolusefailure` and `stop_failure`.

- The Slack, Discord, Teams and ntfy formats send one line of text. For a finished turn this includes the first characters of the assistant's last message.
- The `raw` format sends the event as JSON: the assistant's last message, the tool name and tool input, the session identifier and the remaining fields of the event description. It does not include the transcript path. It includes the Cursor user email only if you turn on `webhook_settings.include_user_email`.

Whoever operates the destination you chose receives that content and handles it under their own terms and privacy policy. echook's maintainer does not receive it and cannot access it. Choose a destination you trust, and prefer one of the one-line formats if the content of your sessions is sensitive.

**Nothing else.** Sounds, desktop notifications and text-to-speech are produced by programs already on your computer (your operating system's audio player, notification centre and speech engine). echook makes no other network connection: it does not download code, check for updates, or contact any service of its maintainer.

The sound files shipped with echook were generated in advance with a third-party text-to-speech service. That happened once, when the files were made; using echook does not contact that service.

## Children

echook is a developer tool. It is not directed at children and does not knowingly process information about them.

## Your choices

- **See what is configured:** `audio-hooks status` and `audio-hooks get webhook_settings`.
- **Turn the webhook off:** `audio-hooks webhook clear`.
- **Turn text-to-speech of message content off:** `audio-hooks tts set --speak-assistant-message false` (it is off unless you turned it on).
- **Clear the event log:** `audio-hooks logs clear`.
- **Remove everything:** uninstall the plugin (`claude plugin uninstall audio-hooks@chanmeng-audio-hooks`), then delete the plugin data directory and `~/.claude-audio-hooks-backups/`.

Because the maintainer holds no data about you, there is nothing to request, export or delete on the maintainer's side.

## Third parties

echook runs inside Claude Code, Cursor or Codex. How those products handle your data is described in their own privacy policies and is outside echook's control. The same applies to any webhook destination you configure.

## Changes to this policy

Changes are made by editing this file in the public repository, so its full history is visible at <https://github.com/ChanMeng666/echook/commits/master/PRIVACY.md>. A change that affects what leaves your computer will also be noted in the [changelog](CHANGELOG.md).

## Contact

Questions about this policy: [open an issue](https://github.com/ChanMeng666/echook/issues) or start a [discussion](https://github.com/ChanMeng666/echook/discussions). To report a security problem privately, follow [SECURITY.md](SECURITY.md).
