# Anthropic plugin directory listing

echook was submitted to Anthropic's plugin directory on **2026-10-04**. This page records what was submitted, where it stands, and the rules the listing now puts on every change to the plugin folder. It is written for the agent or maintainer who makes the next change.

Evidence tags follow `docs/EVENT_BEHAVIOR_NOTES.md`: **[PORTAL]** was read in the developer portal on the date given, **[DOC]** is quoted from Anthropic's documentation, **[LIVE]** was run and observed, **[INFER]** is inference.

## Current state

| | |
|---|---|
| Portal | <https://claude.ai/directory/manage> (sign in with the maintainer's claude.ai account; submitting needs a paid plan) |
| Plugin page | `https://claude.ai/directory/manage/plugins/91aae6d2-b71c-4285-82a5-b9122674cd60` |
| Source | `ChanMeng666/echook`, branch `master`, plugin path `plugins/audio-hooks` |
| First version submitted | v6.7.2 at commit `f692417` |
| Status **[PORTAL 2026-10-04]** | Security scan passed. **In review**: held for content policy review, with Anthropic. Nothing is live. |
| Listed on | **Claude Code only** (see "Why only Claude Code") |
| Updates | GitHub push webhook connected; the directory also polls the branch about every 6 hours |
| Auto-publish | Not applicable while a reviewer publishes each version |

**What happens next.** A reviewer decides; the decision appears on the plugin page and is sent to the account's contact email. Approval is not guaranteed. After approval the maintainer selects **Publish** on the plugin page; until then selecting it only records a request **[DOC]**. Nobody but the account owner can do that step.

This table goes stale. Re-check the portal before relying on it.

## What the portal's validation said

Validation of `f692417` **passed with no blocking finding**: 4 warnings and 4 policy holds **[PORTAL 2026-10-04]**. A policy hold sends the version to a human reviewer; it is not a rejection, and the scan can raise the same hold again on every new version **[DOC]**.

| Result | Finding | What it refers to |
|---|---|---|
| Policy hold | Files or downloads the validator couldn't inspect | The 174 `.mp3` files. Only text, PNG/JPEG/GIF/WebP and fonts pass uninspected. |
| Policy hold | Scripts the validator couldn't follow | `runner/run.py`. When the plugin is a subfolder of its repository the validator follows only plain shell scripts, and every hook runs `python "${CLAUDE_PLUGIN_ROOT}/runner/run.py" …`. |
| Policy hold (2 findings) | Uses a credential from the user's machine | A heuristic: `bin/audio-hooks.py` contains the URL `git-scm.com` (in the `WINDOWS_NO_GIT_BASH` hint) and reads the environment, and the shell wrapper `bin/audio-hooks` builds its command from `${BASH_SOURCE[0]:-$0}`. Read together the validator treats that as a credential that could leave the machine. echook reads no credential and sends nothing except to a webhook the user configures. |
| Warning (3) | Unrecognized field in plugin.json | `documentationUrl`, `privacyPolicyUrl`, `supportUrl`. The portal's own text: the directory reads them for the listing, Claude Code ignores them, "No action needed". |
| Warning | Contains a download-and-run command | Reported for `bin/audio-hooks.py`. **Not located**: a search of the file for `curl`, `wget`, `| sh`, `iex` and similar found nothing. Shown to reviewers and users as an install-time risk. |
| Note | Images and fonts passed without a code check | `.claude-plugin/icon.png`. |

After submission the plugin page gave the reasons for the review hold as "Files or downloads the validator couldn't inspect" and "Ships executable files".

The three holds describe what echook is — a Python hook runner with bundled sounds — and are not expected to go away. Do not restructure the plugin to clear them without the maintainer deciding that; "leaving the scripts as they are and waiting for the review is also fine" is the portal's own wording.

## Why only Claude Code

The listing step reported Cowork and the Claude apps (web, desktop, mobile) as **not available: "Plugins with a top-level bin/ directory can't be installed here."** **[PORTAL]** The `bin/` directory is the `audio-hooks` CLI, which is the project's only interface, so this is by design. Hooks are also ignored in chat, and the status line is a Claude Code setting **[DOC]**.

## What was declared

These answers were given on the submission form and are attestations by the maintainer. If the plugin's behaviour changes so that one of them stops being true, the listing has to be corrected, not just the code.

**Data handling**

| Question | Answer | Basis |
|---|---|---|
| Does the plugin read or store personal data? | Reads only | It reads the event payload the editor passes to hooks, which can contain such data (for example Cursor's `user_email`); it does not store personal data. |
| Does any skill send data to a service other than the declared connectors? | Yes — listed in README | The user-configured webhook, described in `plugins/audio-hooks/README.md` and `PRIVACY.md`. |
| How long does your service retain data received from Claude? | Not retained | There is no service; the maintainer receives nothing. |
| Is the plugin intended for users under 18? | No | |

**Compliance acknowledgements** (all four ticked): agreement to the Software Directory Terms and the Directory Policy on behalf of the account's organization; "The plugin's privacy policy accurately describes the data it handles"; "The plugin does not exfiltrate credentials or execute code outside its declared MCP servers"; consent to be contacted about the submission.

The third statement was accepted by the maintainer knowing that echook declares no MCP server and that its whole function is a hook that runs a Python program, all of it disclosed in the plugin README.

**Listing links** (read from `plugins/audio-hooks/.claude-plugin/plugin.json`): privacy policy → `PRIVACY.md`, support → `SUPPORT.md`, documentation and homepage → the repository. `termsOfServiceUrl` is not set. The listing icon is `.claude-plugin/icon.png`, the project logo at 1024×1024; the plugin page shows it, marked "Waiting for review".

## Rules this puts on future changes

1. **Every push to `master` is now a candidate version.** The push webhook tells the directory within minutes, and it scans the newest commit on the tracked branch **[DOC]**. Keep `master` releasable; do work on a branch and merge when it is ready.
2. **Raise `version` in `plugin.json` with every release** that changes the plugin folder **[DOC]**. `scripts/bump-version.sh` does this.
3. **A later version that fails the security scan blocks the ones after it** until a reviewer clears the plugin, and the listing keeps serving the last published version meanwhile **[DOC]**.
4. **`bin/audio-hooks.py` is 228.6 KiB and the directory holds any non-image file over 256 KiB for a reviewer.** There are about 27 KiB of headroom. If the CLI must grow past that, split it rather than accept another hold. Other limits: 512 files in the plugin folder (223 today), 5 MiB per file, no `.DS_Store` / `Thumbs.db` / `desktop.ini` (blocking), no symlinks, submodules or LFS pointers where the plugin loads them (blocking).
5. **Hook commands must spell every path in full from `${CLAUDE_PLUGIN_ROOT}`** with no other variable, command substitution, wildcard or inline program such as `python -c`. For a plugin in a repository subfolder, breaking this **blocks** submission **[DOC]**. Today's `hooks.json` complies; keep it that way when editing it.
6. **No credentials in any file**, examples and docs included, and do not read a credential that is already in the user's environment and send it anywhere. A value the user must supply goes through a `userConfig` entry with `sensitive: true`, as `webhook_url` does.
7. **The README in the plugin folder is the listing's description**, and the security scan looks for behaviour it does not disclose. Change `plugins/audio-hooks/README.md` and `PRIVACY.md` in the same commit as any change to what the plugin runs, sends or writes.
8. **The listing icon was taken at the first submission and cannot be changed by editing the file later** **[PORTAL]**. The plugin name and short description follow the live version.
9. **The repository and plugin folder of a submission cannot be changed**, and the tracked branch cannot be changed while the plugin is with a reviewer **[DOC]**. Moving the plugin to the repository root, or renaming `plugins/audio-hooks/`, would mean a new submission.

## The push webhook

- GitHub repository webhook id `691758974`: `push` events only, content type `application/json`, delivered to an endpoint under `https://api.anthropic.com/directory-webhooks/github/…`. Created 2026-10-04 with `gh api repos/ChanMeng666/echook/hooks`; GitHub's test delivery returned 200 and the portal showed "Webhook connected" **[LIVE]**.
- The signing secret is generated by the portal and shown once. It is stored only in the GitHub webhook. To replace it: plugin page → **Settings** → **Rotate secret**, then update the secret on the GitHub webhook; the old one stops working immediately **[PORTAL]**.
- Check deliveries: `gh api repos/ChanMeng666/echook/hooks/691758974/deliveries --jq '.[] | "\(.event) \(.status_code) \(.delivered_at)"'`.
- Force a re-scan without pushing: plugin page → **Check for new commits**.

## What is not verified

- Whether a commit that changes nothing inside `plugins/audio-hooks/` registers as a new version, and what a new commit does to a version that is already with a reviewer. Neither was observed.
- What the reviewer will decide, and how long review takes. "Plugins that run code locally can take longer to review" is the portal's wording.
- What the "download-and-run" warning matched.
- The listing as users will see it. Only the developer-side preview was seen.

## Sources

- Submit: <https://claude.com/docs/plugins/submit>
- Pre-submission checklist: <https://claude.com/docs/plugins/pre-submission-checklist>
- Who can submit and what review involves: <https://claude.com/docs/directory/publish>
- Manifest fields the directory reads: <https://code.claude.com/docs/en/plugins/manifest-reference#directory-listing-fields>
- Policy: <https://support.claude.com/en/articles/13145358-anthropic-software-directory-policy>
