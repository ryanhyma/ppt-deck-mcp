"""Deployment assets stay in sync with the code.

These are not unit tests of the server's behaviour; they pin the things that rot silently when the
tool surface changes: the agent's tool list, the MCP `serverInstructions`, the deployment skills, and
the Windows service script's hardening flags. Every failure here has already happened once on this
project ("the agent says style_slide isn't available in my toolkit"; skills that were never in git).
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

_TMP = Path(tempfile.mkdtemp(prefix="deploy-tests-"))
os.environ.setdefault("PPT_TEMPLATE_PATH", str(ROOT / "templates"))
os.environ.setdefault("PPT_OUTPUT_DIR", str(_TMP / "out"))
os.environ.setdefault("PPT_PUBLIC_BASE", "http://test.local:8931")

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "upstream"))

import server  # noqa: E402

REGISTERED = set(server.app._tool_manager._tools)

# Tools this deployment promises to expose to the agent. Order is irrelevant; membership is not.
CURATED = {
    "list_templates", "build_deck", "verify_deck", "preview_deck", "get_last_deck",
    "get_deck_outline", "list_decks", "style_slide", "style_text", "import_deck", "render_svg",
}


class PromptAssets(unittest.TestCase):
    def setUp(self):
        self.agent = json.loads((ROOT / "deploy/librechat/agent-deck-builder.json").read_text("utf-8"))
        self.instructions = (ROOT / "prompts/server-instructions.md").read_text("utf-8")
        self.agent_prompt = (ROOT / "prompts/agent-deck-builder.md").read_text("utf-8")
        self.yaml = (ROOT / "deploy/librechat/mcpServers.ppt.yaml").read_text("utf-8")

    def test_curated_tools_are_registered_and_wired_to_the_agent(self):
        """A renamed tool must fail here, not in a chat where the model invents a workaround."""
        for tool in sorted(CURATED):
            self.assertIn(tool, REGISTERED, f"{tool} is documented but not registered on the server")
            self.assertIn(f"{tool}_mcp_ppt", self.agent["tools"], f"{tool} is missing from the agent")

    def test_agent_tool_keys_match_the_server_name(self):
        """LibreChat builds '<tool>_mcp_<server>'; renaming the server in yaml breaks every key."""
        self.assertIn("ppt:", self.yaml)
        self.assertNotIn("_mcp_ppt2", self.yaml)
        for key in self.agent["tools"]:
            self.assertTrue(key.endswith("_mcp_ppt"), f"{key} does not use the 'ppt' server name")
            self.assertIn(key[: -len("_mcp_ppt")], REGISTERED, f"{key} names a tool that does not exist")

    def test_agent_skills_are_enabled_and_use_placeholders(self):
        """LibreChat silently drops skill NAMES; ids are deployment-specific, so they cannot be committed."""
        self.assertTrue(self.agent["skills_enabled"])
        self.assertEqual(len(self.agent["skills"]), 2)
        for skill_id in self.agent["skills"]:
            self.assertRegex(skill_id, r"^\{\{SKILL_ID_[A-Z_]+\}\}$",
                             "committed skill ids would be wrong on every other deployment")

    def test_agent_prompt_matches_the_versioned_copy(self):
        self.assertEqual(self.agent["instructions"].strip(),
                         self.agent_prompt.split("```text\n", 1)[1].split("\n```", 1)[0].strip())

    def test_server_instructions_name_every_curated_tool(self):
        """Instructions must stand alone: they reach the model even with Skills disabled."""
        for tool in sorted(CURATED):
            self.assertIn(tool, self.instructions, f"{tool} is not mentioned in serverInstructions")

    def test_agent_prompt_names_the_workflow_tools(self):
        """The agent prompt stays terse on purpose; render_svg is documented in the svg skill instead."""
        for tool in ("list_templates", "build_deck", "verify_deck", "preview_deck",
                     "get_deck_outline", "style_slide", "style_text", "import_deck"):
            self.assertIn(tool, self.agent_prompt)

    def test_instructions_carry_the_rules_that_fixed_real_defects(self):
        lowered = self.instructions.lower()
        for phrase in ("total", "one visual per slide", "base=", "rebuild once"):
            self.assertIn(phrase, lowered)


class SkillAssets(unittest.TestCase):
    def test_skills_live_in_the_repo_with_front_matter(self):
        for name in ("ppt-deck-builder", "svg-graphics"):
            path = ROOT / "skills" / name / "SKILL.md"
            self.assertTrue(path.is_file(), f"{path} missing — deployment skills must be versioned")
            text = path.read_text("utf-8")
            self.assertTrue(text.startswith("---\n"), f"{name}: YAML front matter required")
            head = text.split("---", 2)[1]
            self.assertIn(f"name: {name}", head)
            self.assertIn("description:", head)
            # The description is what the model sees when choosing a skill: it must say WHEN.
            self.assertRegex(head, r"description: .*\buse when\b|\bwhenever\b|\bUse when\b",
                             f"{name}: the description should say when to use the skill")

    def test_skills_only_reference_tools_that_exist(self):
        text = (ROOT / "skills/ppt-deck-builder/SKILL.md").read_text("utf-8")
        text += (ROOT / "skills/svg-graphics/SKILL.md").read_text("utf-8")
        mentioned = set(re.findall(r"`([a-z]+(?:_[a-z]+)+)\(", text))  # `tool_name(`
        self.assertTrue(mentioned, "expected backticked tool calls in the skill files")
        for tool in sorted(mentioned):
            self.assertIn(tool, REGISTERED, f"skill references unknown tool {tool}()")


class WindowsServiceAssets(unittest.TestCase):
    """The installer is never run in CI, so its hardening flags are pinned here instead."""

    def setUp(self):
        self.script = (ROOT / "deploy/windows/install-service.ps1").read_text("utf-8")
        self.env = (ROOT / "deploy/windows/ppt-mcp.env.example").read_text("utf-8")

    def test_service_is_registered_with_paths_logs_and_restart_policy(self):
        for flag in ("nssm install", "AppDirectory", "AppStdout", "AppStderr",
                     "AppRotateFiles", "AppRotateOnline", "AppTimestampLog",
                     "AppExit Default Restart", "AppRestartDelay", "AppStopMethodConsole"):
            self.assertIn(flag, self.script, f"install script lost {flag}")

    def test_service_runs_as_an_account_and_restricts_the_port(self):
        self.assertIn("ObjectName", self.script, "service account is optional here, never anonymous")
        self.assertIn("New-NetFirewallRule", self.script)
        self.assertIn("-RemoteAddress $LibreChatHost", self.script,
                      "the MCP port must be reachable only from the LibreChat host")
        self.assertIn("block soffice egress", self.script,
                      "untrusted decks must not let LibreOffice reach the network")

    def test_installer_detects_pyuno_and_sets_unbuffered_output(self):
        self.assertIn("python.exe", self.script)  # LibreOffice's interpreter, not the venv
        self.assertIn("import uno", self.script)
        self.assertIn("PYTHONUNBUFFERED=1", self.script)
        self.assertIn("PPT_UNO_PYTHON", self.script)

    def test_env_template_carries_the_production_switches(self):
        for key in ("PPT_AUTH_MODE", "PPT_ENV", "PPT_ENTRA_TENANT_ID", "PPT_ENTRA_AUDIENCE",
                    "PPT_ENTRA_REQUIRED_SCOPES", "PPT_PUBLIC_BASE", "PPT_PREVIEW_WIDTH"):
            self.assertIn(key, self.env, f"env template is missing {key}")
        self.assertIn("hostname, not an IP literal", self.env,
                      "PPT_PUBLIC_BASE must be a hostname (LibreChat blocks IP literals)")


def tearDownModule():
    import shutil

    shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
