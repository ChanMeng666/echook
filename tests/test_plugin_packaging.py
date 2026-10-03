"""Directory-readiness facts about the plugin folder that are cheap to keep true.

The Claude plugin directory's pre-submission checklist blocks a plugin with no
README (40+ words outside code blocks) or no license, and its security scan
looks for undisclosed data flows. ``claude plugin validate`` checks none of
that, so these assertions stand in for the parts that are decidable offline.
They also pin two compatibility decisions made for v6.7:

* ``userConfig.webhook_url`` is ``sensitive``. Claude Code still exports it to
  hook processes as ``CLAUDE_PLUGIN_OPTION_WEBHOOK_URL`` (measured on 2.1.288
  with a shell-form SessionStart hook, and documented: "exported to hook
  processes for every option"), and the field has been available since
  ``userConfig`` itself (2.1.83).
* No ``userConfig`` field declares ``options``: the docs say that blocks every
  Claude Code before 2.1.271 from loading the plugin.
"""

from __future__ import annotations

try:
    import _isolation  # noqa: F401  (suite-level isolation, tests/_isolation.py)
except ImportError:  # python -m unittest tests.test_x from the repo root
    from tests import _isolation  # noqa: F401

import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "plugins" / "audio-hooks"
MANIFEST = PLUGIN / ".claude-plugin" / "plugin.json"
README = PLUGIN / "README.md"


def _prose_words(markdown: str) -> int:
    without_code = re.sub(r"```.*?```", " ", markdown, flags=re.S)
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'_-]*", without_code))


class TestPluginPackaging(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        cls.readme = README.read_text(encoding="utf-8") if README.exists() else ""

    def test_readme_meets_the_directory_minimum(self) -> None:
        self.assertTrue(README.exists(), "plugins/audio-hooks/README.md is required by the directory")
        self.assertGreaterEqual(_prose_words(self.readme), 40)

    def test_readme_discloses_the_data_flows_a_reviewer_looks_for(self) -> None:
        text = self.readme.lower()
        for needle in ("webhook", "last message", "text-to-speech", "settings.json",
                       "data directory", "python", "never", "telemetry"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_readme_names_every_default_the_runner_actually_has(self) -> None:
        """The README states what is on by default; keep it tied to the template."""
        prefs = json.loads((REPO / "config" / "default_preferences.json").read_text(encoding="utf-8"))
        on = sorted(k for k, v in prefs["enabled_hooks"].items() if v is True)
        self.assertEqual(on, ["notification", "permission_request", "stop"])
        self.assertFalse(prefs["webhook_settings"]["enabled"])
        self.assertFalse(prefs["tts_settings"]["enabled"])
        self.assertFalse(prefs["tts_settings"]["speak_assistant_message"])
        self.assertEqual(prefs["webhook_settings"]["hook_types"],
                         ["stop", "notification", "permission_request", "posttoolusefailure", "stop_failure"])
        for name in on:
            self.assertIn(f"`{name}`", self.readme)
        for hook in prefs["webhook_settings"]["hook_types"]:
            self.assertIn(f"`{hook}`", self.readme)

    def test_license_is_declared_in_the_plugin_folder(self) -> None:
        self.assertTrue(self.manifest.get("license") or (PLUGIN / "LICENSE").exists())

    def test_the_webhook_url_is_sensitive(self) -> None:
        self.assertIs(self.manifest["userConfig"]["webhook_url"].get("sensitive"), True)

    def test_the_runner_still_reads_the_webhook_option_from_the_environment(self) -> None:
        """Sensitive options are delivered as CLAUDE_PLUGIN_OPTION_<KEY>, not substituted
        into hook commands; the overlay must keep reading exactly that variable."""
        src = (REPO / "hooks" / "user_preferences.py").read_text(encoding="utf-8")
        self.assertIn("CLAUDE_PLUGIN_OPTION_WEBHOOK_URL", src)
        hooks = (PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8")
        self.assertNotIn("user_config", hooks, "shell-form hooks reject ${user_config.*}")

    def test_no_user_config_option_raises_the_minimum_claude_code_version(self) -> None:
        for key, spec in self.manifest["userConfig"].items():
            with self.subTest(option=key):
                self.assertNotIn("options", spec)

    def test_display_name_and_required_fields_are_present(self) -> None:
        for field in ("name", "version", "description", "author", "license", "displayName"):
            self.assertTrue(self.manifest.get(field), field)

    def test_directory_listing_links_are_https_and_point_at_real_files(self) -> None:
        """The directory reads these from plugin.json only. The policy link must
        resolve to a file that exists in the repository it names."""
        for field in ("documentationUrl", "supportUrl", "privacyPolicyUrl"):
            with self.subTest(field=field):
                self.assertTrue(str(self.manifest.get(field, "")).startswith("https://"), field)
        prefix = "https://github.com/ChanMeng666/echook/blob/master/"
        for field in ("supportUrl", "privacyPolicyUrl"):
            with self.subTest(field=field):
                url = self.manifest[field]
                self.assertTrue(url.startswith(prefix), url)
                self.assertTrue((REPO / url[len(prefix):]).is_file(), url)

    def test_privacy_policy_states_the_one_way_data_leaves_the_machine(self) -> None:
        text = (REPO / "PRIVACY.md").read_text(encoding="utf-8").lower()
        for needle in ("webhook", "no telemetry", "update check", "last message",
                       "user_preferences.json", "events.ndjson"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        self.assertIn("PRIVACY.md", self.readme, "the plugin README should link the policy")

    def test_listing_icon_meets_the_directory_requirements(self) -> None:
        """The directory wants a square PNG of 512-2048 px under 2 MB at this path."""
        import struct
        icon = PLUGIN / ".claude-plugin" / "icon.png"
        self.assertTrue(icon.is_file())
        data = icon.read_bytes()
        self.assertLess(len(data), 2 * 1024 * 1024)
        self.assertEqual(data[:8], bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]))
        width, height = struct.unpack(">II", data[16:24])
        self.assertEqual(width, height)
        self.assertTrue(512 <= width <= 2048, width)

    def test_marketplace_entry_carries_category_and_tags(self) -> None:
        market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        entry = market["plugins"][0]
        self.assertTrue(entry.get("category"))
        self.assertTrue(entry.get("tags"))


EVALS = PLUGIN / "evals"
EXPECTED_CASES = {
    "snooze-30-minutes", "too-loud", "unrelated-request", "pomodoro-out-of-scope",
    "statusline-context-only", "away-notification", "cursor-install-duplicate-bridge",
}
GRADER_TYPES = {"regex", "tool_used", "tool_order", "file_exists", "llm", "baseline"}


def _front_matter(path: Path):
    text = path.read_text(encoding="utf-8")
    match = re.match(r"---\r?\n(.*?)\r?\n---\r?\n?(.*)", text, flags=re.S)
    assert match, f"{path} has no frontmatter"
    fields = {}
    for line in match.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields, match.group(2)


class TestSkillEvalSuite(unittest.TestCase):
    """`claude plugin eval` has no dry run, so the structure is checked here."""

    def test_the_expected_cases_exist(self) -> None:
        cases = {p.name for p in EVALS.iterdir() if p.is_dir() and p.name != "results"}
        self.assertEqual(cases, EXPECTED_CASES)

    def test_every_case_has_a_prompt_and_graders_and_no_shell(self) -> None:
        for name in sorted(EXPECTED_CASES):
            with self.subTest(case=name):
                case = EVALS / name
                fields, body = _front_matter(case / "prompt.md")
                self.assertTrue(body.strip(), "empty prompt")
                tools = fields.get("allowed_tools", "")
                for gated in ("Bash", "Write", "Edit", "WebFetch", "PowerShell"):
                    self.assertNotIn(gated, tools, "native Windows has no sandbox backend for shell-granting suites")
                graders = sorted((case / "graders").glob("*.md"))
                self.assertGreaterEqual(len(graders), 1)
                for grader in graders:
                    gfields, _ = _front_matter(grader)
                    self.assertIn(gfields.get("type"), GRADER_TYPES, grader.name)

    def test_cases_do_not_discourage_the_skill_from_running(self) -> None:
        """In the first eval run, prompts with that phrase saw Haiku skip the skill in 3 of 7 cases (small sample)."""
        for name in sorted(EXPECTED_CASES):
            with self.subTest(case=name):
                _, body = _front_matter(EVALS / name / "prompt.md")
                self.assertNotRegex(body.lower(), r"don.t run anything")

    def test_results_are_ignored_and_not_shipped(self) -> None:
        self.assertIn("results/", (EVALS / ".gitignore").read_text(encoding="utf-8"))
        self.assertFalse((EVALS / "results").exists())


if __name__ == "__main__":
    unittest.main()
