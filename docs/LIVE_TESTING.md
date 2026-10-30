# Driving a real Claude Code session

How to measure what Claude Code does in an **interactive** session from a script. `claude -p` draws no agent list, no status line and no view switching, so anything about the terminal UI has to be measured this way. Read this before claiming a UI behaviour is verified. `docs/EVENT_BEHAVIOR_NOTES.md` says what was found; this page says how.

First used for the view sensor in 7.2.0, on Claude Code 2.1.314, Windows 11. An earlier interactive run at 2.1.288 is mentioned in `docs/EVENT_BEHAVIOR_NOTES.md`; its driver was not kept.

## The rig

`pywinpty` runs the process in a pseudo-terminal and `pyte` turns its output into a screen you can read. Install both into a scratch folder with `pip install --target <scratch>/py pywinpty pyte`, not into the system Python. On Linux or macOS use `pty` from the standard library; that has not been tried here.

```python
import os, sys, threading, time
sys.path.insert(0, "<scratch>/py")
import pyte, winpty

screen = pyte.Screen(150, 45)
stream = pyte.Stream(screen)

# Without this the child inherits CLAUDE_CODE_CHILD_SESSION and turns transcript
# saving off, and anything read from a transcript silently finds nothing.
env = {k: v for k, v in os.environ.items()
       if not k.startswith("CLAUDE_CODE_") and k not in ("CLAUDECODE", "CLAUDE_PLUGIN_DATA")}
env["TERM"] = "xterm-256color"

proc = winpty.PtyProcess.spawn(
    ["claude.exe", "--plugin-dir", "<repo>/plugins/audio-hooks",
     "--settings", "<scratch>/settings.json", "--model", "sonnet",
     "--dangerously-skip-permissions"],
    cwd="<scratch>", dimensions=(45, 150), env=env)

def reader():
    while True:
        try:
            stream.feed(proc.read(65536))
        except Exception:
            break
threading.Thread(target=reader, daemon=True).start()

def frame(tag):
    lines = [line.rstrip() for line in screen.display if line.strip()]
    print(f"=== {tag}", *lines, sep="\n")
```

Run it with a working directory in scratch, so the test session's transcripts land under a project folder you can delete afterwards. Do not filter `env` by printing it: the shell can hold credentials.

`--settings` adds to the user's settings, so a test can register its own `statusLine`. Omitting `--plugin-dir` tests whatever the marketplace points at. On a directory-source marketplace that is still the checkout, whatever `installPath` says; to see which file runs, watch `python.exe` command lines while a session starts.

## Keys

| Action | Keys |
|---|---|
| Accept the trust prompt on a new folder | `\r` |
| Submit a prompt | Write the text, wait a second, then `\r` on its own. Sent together, the newline can land in the input |
| Select an agent in the list under the prompt | `\x1b[B` twice, with a pause between |
| Open its view | `\r`; the input then reads `Message @<agent>…` |
| Return to the main conversation | `\x1b[A` to select `main`, then `\r` |
| Leave | `\x03` twice |

**`Esc` does not leave an agent's view.** It only clears the selection in the list. A run that used it looked like the status line had failed to switch back.

## A prompt that gives a subagent to look at

Ask for a background agent on a model that differs from the main one, doing something slow enough to switch to:

> Use the Agent tool once with run_in_background=true, subagent_type general-purpose, model haiku, prompt: run this Bash command in the foreground with a 120000ms timeout: `python -c "import time;time.sleep(70)"` and then reply done. After launching it say started and end your turn.

A plain `sleep 60` is refused by this machine's harness, so the agent wastes its first turn. Give the agent about 30 seconds to reach its first response before opening the view.

## Probing what a mod sees

A throwaway plugin in scratch can log what a site receives. A helper that takes `$` must be a top-level `function` declaration, or `claude plugin validate` refuses it (`$ is passed to "log", which is not a function declared at the top of this file`). `$.fs.read` then `$.fs.write` to append is not atomic, and concurrent renders drop lines, so a gap in a counter is not a missed render.

## Mistakes this cost

- **The suite and the rig must not run together.** At 100% CPU the frames came back empty, which looks like a failed session.
- **Timing is load-dependent.** Bare Python took about 70 ms idle and up to 1.3 s under load. Measure a timing twice before writing it down.
- **Loading with `--plugin-dir` writes into the plugin folder.** Claude Code creates `plugins/audio-hooks/tsconfig.json` and `.claude-plugin/types/`. Both are in `.gitignore`.
- **Keep what cannot be regenerated.** See "After the release" in `docs/RELEASING.md`. The 7.2.0 run is in `echook-7.2.0-view-evidence.zip` on the maintainer's machine, outside the repository.
