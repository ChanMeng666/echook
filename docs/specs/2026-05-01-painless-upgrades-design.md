# Painless upgrades for audio-hooks — design

> **Status:** approved 2026-05-01 | **Target version:** 5.1.5

## Context

Today an existing audio-hooks user who upgrades is at risk of losing their configuration in three distinct ways:

1. **`claude plugin uninstall` deletes the plugin data dir by default.** `~/.claude/plugins/data/audio-hooks-chanmeng-audio-hooks/user_preferences.json` lives there. Users must remember the `--keep-data` flag; if they (or an AI agent) forget, every customization is gone.
2. **Default values silently flip across versions.** 5.1.4 changed `subagent_stop`, `permission_denied`, `task_created` from `false` → `true`. A user whose data dir was wiped (per #1) is reinitialised from the new template and starts hearing more audio than before, with no signal that anything changed.
3. **No backup, no recovery.** When config is lost there is no `.bak` to restore from.

This project is AI-first: the entire install / upgrade / configure / uninstall lifecycle is supposed to be operable via JSON-emitting CLI subcommands by Claude Code on the user's behalf, with no human menus. Today's upgrade UX violates that — it relies on the AI remembering an obscure flag (`--keep-data`) of an upstream tool (`claude plugin`) the project doesn't own.

This design eliminates that footgun by giving the project full control over its own upgrade lifecycle, with non-destructive migration, defensive backups, and a stable defaults policy.

---

## Goals

1. **Existing users never lose configuration on upgrade.** Even if `claude plugin uninstall` runs without `--keep-data`, recovery is one CLI command.
2. **New keys auto-add with sensible defaults; existing keys never get overwritten.** A user upgrading from 5.1.3 → 5.1.5 keeps their `audio_theme: "custom"`, snooze schedule, and per-hook overrides. New v5.1.5 keys appear with new-installer defaults.
3. **Default flips become a CI-enforced policy violation.** No more silent behavior changes between versions for users who never touched their config.
4. **AI-first throughout.** Everything new is a JSON CLI subcommand, ISO-timestamped, machine-parseable. Zero human menus, zero prompts.

## Non-goals

- A general-purpose plugin migration framework. Scope is this project's own state.
- A schema validator. Existing `audio-hooks manifest --schema` is sufficient.
- Backups for non-config state (audio files, logs, queue markers). Only `user_preferences.json`.
- Sandboxing the upstream `claude plugin` CLI. We wrap it; we don't replace it.

---

## Architecture

### New module

```
hooks/user_preferences.py     # NEW — single source of truth
```

Both `hooks/hook_runner.py` and `bin/audio-hooks.py` import `UserPreferences` and access it via a **lazy module-level singleton** `get_prefs()` (NOT instantiated at module top-level). This avoids import-time side effects (path probing, file IO) and makes test isolation trivial — tests can clear the singleton with `_reset_prefs()` to inject a tempdir-based instance. Three duplicated helper sets (`_resolve_plugin_data_dir`, `_is_running_from_plugin`, `_auto_init_user_prefs`, `_apply_plugin_option_overlay`, plus the diverged `_resolve_config_file` / `_config_path` pair) are deleted from both call sites.

```
bin/audio-hooks.py ──┐
                     ├──> hooks/user_preferences.py ──> config/default_preferences.json
hooks/hook_runner.py ┘                              ──> config/_defaults_baseline.json
```

The new module is intentionally light: ~250 lines, no side effects on import (unlike `hook_runner.py` which scans audio dirs etc. on import).

### Class interface

```python
class UserPreferences:
    """Single source of truth for user_preferences.json access.

    Owns path resolution, load (with auto-migration), save (with auto-backup),
    backup management, and diff-from-default reporting.
    """

    PLUGIN_ID = "audio-hooks-chanmeng-audio-hooks"
    EXTERNAL_BACKUP_DIRNAME = ".claude-audio-hooks-backups"
    EXTERNAL_BACKUP_KEEP = 10

    def __init__(self, project_dir: Path, *, script_path: Optional[Path] = None):
        ...

    # Path resolution (memoised)
    @property
    def data_dir(self) -> Path: ...        # CLAUDE_PLUGIN_DATA → ... → legacy temp
    @property
    def config_path(self) -> Path: ...     # data_dir / "user_preferences.json"
    @property
    def queue_dir(self) -> Path: ...
    @property
    def log_dir(self) -> Path: ...

    # Load + save
    def load(self) -> Dict[str, Any]: ...
    def save(self, cfg: Dict[str, Any]) -> None: ...

    # Convenience
    def get_dotted(self, key: str) -> Any: ...
    def set_dotted(self, key: str, value: Any) -> None: ...

    # Backup management
    def list_backups(self) -> List[BackupEntry]: ...
    def restore_from(self, backup_id: str) -> Dict[str, Any]: ...
    def prune_backups(self, keep: int = EXTERNAL_BACKUP_KEEP) -> int: ...

    # Diagnostics
    def diff_from_default(self) -> Dict[str, Any]: ...
```

### Path resolution priority chain (unchanged from 5.1.4 + this fix)

1. `CLAUDE_PLUGIN_DATA` env var (Claude Code injects in hook fire context)
2. `CLAUDE_AUDIO_HOOKS_DATA` env var (explicit override)
3. Plugin-cache layout detected via `script_path.parent.parent / .claude-plugin / plugin.json`
4. Shared dir `~/.claude/plugins/data/audio-hooks-chanmeng-audio-hooks/` if `user_preferences.json` exists there
5. Cursor-native dir `~/.cursor/audio-hooks-data/` if `user_preferences.json` exists there
6. Legacy temp dir `<TEMP>/claude_audio_hooks_queue/`

The 5.1.4 bug (CLI from project source wrote to wrong file) is fixed by this single chain being the *only* place path resolution happens.

### Migration semantics

`load()` runs `_migrate(cfg, template)` whenever `cfg["_version"] != template["_version"]`. The merge rules:

| Key type | Rule | Why |
|---|---|---|
| `_version` / `version` / `$schema` | Always overwrite from template | Metadata, not user data |
| `_comment*` / `_description` / `_usage_notes` | Always overwrite from template | Documentation; users edit these via CLI, not by hand |
| Any other top-level key (`audio_theme`, `webhook_settings`, ...) | If user has it → keep user's; if missing → adopt template default | User intent is the highest authority |
| Nested `dict` values | Recurse with same rules | Per-leaf decision |
| `list` values | Atomic — user's list wins entirely; template list never merged in | A user choosing `webhook_settings.hook_types: ["stop"]` has implicitly excluded everything else |
| Type mismatch — **scalar vs scalar** (user `int`, template `string`) | Keep user value, log NDJSON warning | User has a recoverable opinion |
| Type mismatch — **scalar vs container** (user `true`, template `{...}`) | **Reset to template default**, log NDJSON warning | Keeping a scalar where dict is expected breaks every downstream `.get(...)` call — user's value is unrecoverable |
| Key in user but not template | Keep user value | Schema deprecation tolerance |

After successful merge, bump `cfg["_version"]` and `cfg["version"]` to the template's. Persist via `save()` (which itself triggers a backup of pre-migration state).

### Backup mechanics

Every `save()` snapshots the *prior* file content (if any) to two locations before the atomic write:

- **Sibling**: `<data_dir>/user_preferences.json.bak` — last-good only, overwrites on each save. Survives nothing scary because it lives next to the main file.
- **External**: `~/.claude-audio-hooks-backups/audio-hooks-chanmeng-audio-hooks/<ISO_ts>.json` — the disaster recovery store. Kept outside `~/.claude/plugins/data/` so `claude plugin uninstall` can't touch it. Rotation: **keep the 20 most recent** (raised from 10 after design review — AI scripts that run `audio-hooks hooks enable-only ...` trigger 26 saves in seconds, leaving no headroom under keep=10).

#### Filename ↔ ID mapping

The filename uses ISO-8601 with `:` replaced by `-` for Windows compatibility AND a 3-digit millisecond suffix to disambiguate sub-second saves:

```
filename:  2026-05-01T07-42-13.041Z.json
ID:        2026-05-01T07:42:13.041Z          (in-memory + JSON output)
```

Conversion is a deterministic round-trippable pair of pure functions:

```python
def _id_to_filename(backup_id: str) -> str:
    return backup_id.replace(":", "-") + ".json"

def _filename_to_id(filename: str) -> str:
    stem = filename.removesuffix(".json")
    # Restore : at positions 13 and 16 (HH-MM-SS.mmmZ → HH:MM:SS.mmmZ)
    return stem[:13] + ":" + stem[14:16] + ":" + stem[17:]
```

Both forms are tested via round-trip. `restore_from(id)` first checks if `id` is a magic string (`latest`, `latest-sibling`, `latest-external`); else converts to filename and looks up.

#### Dedup before backup

Before writing an external backup, compare the about-to-be-saved content's bytes against the most recent existing external backup file. If byte-identical, **skip** the new backup write (don't bump the rotation counter either). This protects scripted scenarios from chewing through the 20-slot budget on no-ops.

#### Atomic write + concurrency

- Atomic write: temp file in same dir → fsync → `os.replace()`. Same pattern as `audio-hooks-statusline.py` v5.1.3.
- Same-dir constraint matters: `os.replace()` across drive letters fails with `EXDEV` on Windows. Test scope must include a temp dir whose Path is a different drive letter than `$HOME`.
- **File lock around the entire read-modify-write window**. Two `audio-hooks set` invocations from one AI session can race (AI agents commonly run `set` calls back-to-back in a loop). Without a lock, the later save backs up post-earlier-save state — silently losing what the second command thought it was editing.
  - Sentinel: `<data_dir>/.user_prefs.lock` (separate from main file so atomic-replace-of-main-file doesn't race with lock acquisition).
  - Mechanism: `fcntl.flock(LOCK_EX)` on POSIX, `msvcrt.locking(LK_LOCK)` on Windows. Block up to 5 seconds; emit `LOCK_TIMEOUT` error code if exceeded.
  - Lock covers: load → migrate → backup → atomic-write. The lock is released after `os.replace()`; readers (other processes) holding shared locks see either the pre-replace or post-replace file, never a torn state.

### Restore semantics

```python
def restore_from(self, backup_id: str) -> Dict[str, Any]:
    """
    backup_id forms accepted:
      - "latest"            → most recent across sibling + external (by mtime)
      - "latest-sibling"    → the .bak in data_dir
      - "latest-external"   → newest in ~/.claude-audio-hooks-backups/
      - "2026-05-01T07:42:13Z"  → exact timestamp (must exist as external)
    """
```

Restoration goes through `save()`, so the *current* state is itself snapshotted before being overwritten. Result: an AI that picks the wrong backup can recover by restoring the immediate prior backup.

#### list_backups() entry format

Each entry returned by `list_backups()` is a dict with stable schema:

```json
{
  "id": "2026-05-01T07:42:13Z",
  "location": "external",
  "path": "/home/user/.claude-audio-hooks-backups/audio-hooks-chanmeng-audio-hooks/2026-05-01T07-42-13Z.json",
  "size_bytes": 9024,
  "from_version": "5.1.4",
  "mtime_iso": "2026-05-01T07:42:13Z"
}
```

The sibling `.bak` appears in the list with `location: "sibling"` and a synthetic `id: "latest-sibling"`. External backups have `location: "external"` and `id` matching their filename timestamp. `restore_from` accepts either form by checking `id` exact match first, then magic strings (`latest*`).

### `audio-hooks upgrade` subcommand

```
audio-hooks upgrade [--check-only] [--force]
```

Replaces the current "uninstall + install" two-step incantation with one idempotent JSON command.

**Scope detection (corrected after design review)**: parse `claude plugin list --json` (verified to exist; outputs the array of installed plugins with `id`, `scope`, `version`, `installPath`). Find the entry where `id == "audio-hooks@chanmeng-audio-hooks"` and read its `scope`. Do NOT scrape `~/.claude/plugins/installed_plugins.json` directly — that file's schema is a Claude-Code internal and may change; `list --json` is the documented machine interface.

**Strategy: prefer `update`, fall back to `uninstall --keep-data + install`.** `claude plugin update <plugin> --scope <scope>` exists (verified) and is the right primitive — designed to refresh code without touching data. Use it as the first attempt; reinstall is a fallback for scenarios where `update` fails (e.g., plugin record corrupted).

Execution sequence:

1. Run `claude plugin list --json`; locate our entry; emit `NOT_INSTALLED` if absent.
2. If `--check-only`: emit `{ok, current_version, latest_version, would_upgrade, ...}` and exit.
3. Write upgrade-in-progress marker to `~/.claude-audio-hooks-backups/.upgrade_in_progress.json` containing the detected scope, current version, and recovery instructions in the JSON body.
4. **First attempt**: `claude plugin update <plugin> --scope <detected>`. If exits zero AND post-update `list --json` shows the new version → skip steps 5-6, jump to 7.
5. **Fallback**: `claude plugin uninstall <plugin> --keep-data --scope <detected> -y`.
6. `claude plugin install <plugin> --scope <detected>`.
7. Re-run `claude plugin list --json` to verify the new version is registered and enabled.
8. Delete the marker.
9. Call `prefs.load()` to trigger automatic migration of `user_preferences.json` (which survives step 5 because of `--keep-data`, and step 4 because `update` doesn't touch data).

Output JSON:

```json
{
  "ok": true,
  "from_version": "5.1.3",
  "to_version": "5.1.5",
  "scope": "local",
  "data_preserved": true,
  "config_migrated": {
    "from_version": "5.1.3",
    "to_version": "5.1.5",
    "added_keys": ["enabled_hooks.permission_denied", "..."]
  },
  "backup_before_upgrade": "2026-05-01T07:42:13Z",
  "warnings": []
}
```

**Failure modes:**

| Failure point | Code | Action |
|---|---|---|
| Plugin not installed | `NOT_INSTALLED` | Suggest `audio-hooks install --plugin` |
| `claude plugin update` non-zero | (silent, falls through to uninstall+install) | Logged at debug level only |
| `claude plugin uninstall` non-zero in fallback | `UPGRADE_UNINSTALL_FAILED` | Marker stays; manual recovery |
| `claude plugin install` non-zero in fallback | `UPGRADE_REINSTALL_FAILED` | Marker stays; suggest manual `claude plugin install` |
| Version verify fails after update OR install | `UPGRADE_VERIFY_FAILED` | Suggest marketplace sync issue |
| Migration IO failure | (warning, not error) | upgrade itself succeeds; old config intact |
| Marker present at start | `PRIOR_UPGRADE_INCOMPLETE` (warning) | Don't block; AI investigates and `--force`s if safe |

`--force` only matters when a stale marker is detected. Documented as "use only after manual verification of plugin state".

#### Recovery when upgrade left plugin missing

If step 6 (fallback install) fails, the plugin is gone and the user's `audio-hooks` binary on PATH may also be gone if it was sourced from the plugin cache. In that case, the user can still recover via the project source tree:

```
python <project>/bin/audio-hooks.py upgrade --force
```

The `bin/audio-hooks.py` in the project source has identical capability and discovers plugin state via `claude plugin list --json` (which is always available because it's bundled with `claude` itself). The marker file at `~/.claude-audio-hooks-backups/.upgrade_in_progress.json` includes a `recovery_command` field with this exact instruction so the AI can read it and act without further docs. The marker also includes the detected scope and pre-upgrade version so a fresh AI session has full context.

### Defaults stability test

```
config/_defaults_baseline.json     # NEW — pinned snapshot
tests/test_defaults_stability.py   # NEW — flip detector
```

The test asserts: every **scalar leaf** key path present in `_defaults_baseline.json` must have an identical value in `default_preferences.json`. Definition of leaf:

- **Scalar leaf** (bool / number / string / null): compared by `==`.
- **Array value**: compared by **set equality** of elements (order-independent). This avoids false positives when `webhook_settings.hook_types` gets re-alphabetised. Elements must themselves be scalar (no array-of-objects in our schema today; if that ever changes, the test must be revisited).
- **Object value**: recurse into it; the value-as-a-whole is never compared.

Newly added paths in `default_preferences.json` are allowed. Removed paths are allowed. Flipped scalars fail loudly with the path and the old/new values. Element changes in arrays (added or removed entries) also fail with a diff.

Metadata fields (`_version`, `version`, `$schema`, `_comment*`, `_description`, `_usage_notes`) are exempt.

When a flip is intentional (e.g., a security fix that needs a new default), the developer updates **both** files in the same commit and documents the flip in CHANGELOG. The test stays green.

### 5.1.4 default-flip rollback

Per Section 3 Part B Plan A:

1. Revert `enabled_hooks.subagent_stop` from `true` → `false` in `config/default_preferences.json`.
2. Revert `enabled_hooks.permission_denied` from `true` → `false`.
3. Revert `enabled_hooks.task_created` from `true` → `false`.
4. Sync into `plugins/audio-hooks/config/default_preferences.json` via `build-plugin.sh`.
5. Create `config/_defaults_baseline.json` by literally copying the *post-revert* `config/default_preferences.json` content (verbatim, including comment fields). This is the new clean baseline. Subsequent commits that intentionally flip a default must update this file in the same commit.
6. Add a CHANGELOG 5.1.5 entry documenting the rollback and the new policy.

Existing users whose `user_preferences.json` was reinitialised under 5.1.4 (and now have these keys explicitly set to `true`) keep that value across migration to 5.1.5 — the migration rule is "never overwrite an existing key". Their effective behavior is unchanged across 5.1.4 → 5.1.5. The 5.1.5 release notes add a one-line escape hatch:

> If 5.1.4 reinitialised your config and you don't want `subagent_stop` / `permission_denied` / `task_created` audio: `audio-hooks hooks disable subagent_stop permission_denied task_created`.

### CLI surface (new subcommands)

```
audio-hooks upgrade [--check-only] [--force]   # the AI-first upgrade flow
audio-hooks backup list                         # JSON array, newest first
audio-hooks backup show <id>                    # full content of one backup
audio-hooks backup restore <id|latest|latest-sibling|latest-external>
audio-hooks backup prune                        # idempotent rotation enforcement
```

No bare `audio-hooks backup` alias for `backup list` — keeps consistency with `audio-hooks hooks` (no alias; bare form errors with usage).

`audio-hooks status` and `audio-hooks manifest` gain a `customizations` block (output of `diff_from_default()`) and a `last_migration` block (latest `config_migrated` event from NDJSON, if any).

`audio-hooks manifest` also exposes `defaults_baseline_version` so AI can see which baseline this code enforces.

### Error codes (added to stable enum)

| Code | When | suggested_command |
|---|---|---|
| `BACKUP_FAILED` | Backup write failed (disk full, permissions) | `audio-hooks diagnose` |
| `BACKUP_NOT_FOUND` | Restore target backup_id doesn't exist | `audio-hooks backup list` |
| `RESTORE_FAILED` | Backup file corrupt / unparseable | `audio-hooks backup list` |
| `LOCK_TIMEOUT` | Could not acquire `.user_prefs.lock` within 5 s — another process is mid-save | Retry; if persists, check for stale lockfile via `audio-hooks diagnose` |
| `MIGRATION_FAILED` | Migration IO error (deep_merge itself can't fail) | `audio-hooks logs tail` |
| `UPGRADE_UNINSTALL_FAILED` | `claude plugin uninstall` non-zero | `claude plugin list` |
| `UPGRADE_REINSTALL_FAILED` | `claude plugin install` non-zero | `claude plugin install audio-hooks@chanmeng-audio-hooks` |
| `UPGRADE_VERIFY_FAILED` | post-install version is still stale | `claude plugin list` |
| `NOT_INSTALLED` | `upgrade` invoked but no record in `installed_plugins.json` | `audio-hooks install --plugin` |
| `PRIOR_UPGRADE_INCOMPLETE` | `.upgrade_in_progress.json` marker found at start | `audio-hooks status` |

### Module-level back-compat: NONE

Per Section 2 decision: rip out all `CONFIG_FILE = ...` / `QUEUE_DIR = ...` module-level globals from `hook_runner.py` and `bin/audio-hooks.py`. Every call site migrates to `_prefs.config_path` / `_prefs.queue_dir` / `_prefs.load()` etc. in the same commit. The diff is bigger; the resulting code has half the surface area and zero "two ways to do the same thing" confusion.

---

## Testing strategy

| Test file | Coverage |
|---|---|
| `tests/test_user_preferences.py` (NEW) | UserPreferences class: path resolution chain (including the 5.1.4 anti-stranding shared-dir fallback as a named regression test), load/save round-trip, atomic write across drive letters, get/set_dotted, diff_from_default, plugin overlay, `get_prefs()` lazy-singleton + `_reset_prefs()` test isolation |
| `tests/test_migration.py` (NEW) | `_deep_merge_missing` rules: every row in the migration semantics table; empty user file; new keys; scalar-vs-scalar type drift; scalar-vs-container reset; list atomicity (set-equality); comment field overwrite; missing `_version` field; downgrade attempt (newer user version reading older template) |
| `tests/test_backups.py` (NEW) | sibling .bak written on save; external dir created with proper permissions; rotation at 20; dedup-skip on byte-identical content; restore from each backup_id form; restore-of-restore reversibility; filename↔ID round-trip; sub-second collision avoidance via millisecond suffix; lock acquisition timeout; concurrent save races |
| `tests/test_upgrade_command.py` (NEW) | `--check-only` no-op; full upgrade with monkey-patched `claude plugin` subprocess; failure-mode error codes; `PRIOR_UPGRADE_INCOMPLETE` detection |
| `tests/test_defaults_stability.py` (NEW) | the policy enforcer; passes against the post-rollback baseline |
| `tests/test_cursor_bridge.py` (existing) | Should still pass after refactor; possibly some adjustments to use UserPreferences instead of internal helpers |

Target: ~30 new test cases, ~600 lines of test code. All stdlib-only (subprocess + tempfile + unittest), runnable on the existing CI matrix (Ubuntu/Windows/macOS × Python 3.9/3.12/3.13).

## Rollout

**Single feature branch, multiple commits**. The design review surfaced a real concern that one mega-commit is "review-by-bisect" — splitting into staged commits within one branch keeps PR atomicity (one merge to master, one tag) while making each commit individually bisectable. The branch ships as a single 5.1.5 release; commits inside are:

- **Commit 1**: New `hooks/user_preferences.py` + tests, NO call-site changes yet (class lives next to existing helpers, both work, all green)
- **Commit 2**: Migrate `hooks/hook_runner.py` and `bin/audio-hooks.py` call sites onto `UserPreferences`; delete duplicated helpers and module-level globals
- **Commit 3**: New CLI subcommands (`upgrade`, `backup *`); manifest output extensions
- **Commit 4**: 5.1.4 default flips reverted; `_defaults_baseline.json` created; stability test added
- **Commit 5**: Plugin layout sync via `build-plugin.sh`; CHANGELOG / README / SKILL / `plugin.json` version bumps; release tag

Each commit's tests are green at HEAD. If a Windows CI flake hits on master, `git bisect` zeroes in on the actual broken commit instead of the whole 5.1.5 release.

Sequence inside the branch:

1. New `hooks/user_preferences.py` with full class and tests.
2. `hooks/hook_runner.py` and `bin/audio-hooks.py` refactored to use `UserPreferences`. All duplicated helpers deleted. All module-level globals (`CONFIG_FILE`, `QUEUE_DIR`) deleted.
3. New CLI subcommands: `upgrade`, `backup *`. Manifest output extended.
4. `config/default_preferences.json` 5.1.4 default flips reverted.
5. `config/_defaults_baseline.json` created from post-revert state.
6. `tests/test_defaults_stability.py` passes against the new baseline.
7. `bash scripts/build-plugin.sh` syncs everything into `plugins/audio-hooks/`.
8. `bash scripts/build-plugin.sh --check` is part of CI smoke tests.
9. `CHANGELOG.md` 5.1.5 entry, `CLAUDE.md` decision-tree updates, `README.md` "What's new", `SKILL.md` upgrade section.
10. `plugins/audio-hooks/.claude-plugin/plugin.json` version bump.

The user must run `audio-hooks upgrade` exactly once after this commit ships to migrate from 5.1.4 to 5.1.5. The `audio-hooks upgrade` command itself works fine when invoked from 5.1.4's CLI surface (only depends on `claude plugin` external commands), so first-time users get the full benefit immediately.

## Out of scope (deliberately deferred)

- **Schema validation in load()**: today's manifest already exposes the JSON Schema, but `load()` doesn't validate user_preferences.json against it. Adding validation would make migration more rigorous but risks rejecting user-edited files that are slightly off-schema. Defer until we observe real schema-related bugs.
- **Multi-plugin generalisation**: `EXTERNAL_BACKUP_DIRNAME` is hardcoded to this plugin. If audio-hooks ever ships sister plugins, they'd want their own backup dirs. Trivial to extract; not needed today.
- **Backup encryption**: backups contain webhook URLs and possibly user_email. They're under `~/.claude-audio-hooks-backups/` with default file permissions. Anyone with $HOME read access can read them. Acceptable threat model for a dev-tools project; revisit if a paying enterprise asks.
- **`audio-hooks downgrade`**: rolling back to a prior plugin version. Possible (use `claude plugin install <ver>` if marketplace allows pinning), but not a 5.1.5 priority.
