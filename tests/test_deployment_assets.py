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

sys.path.insert(0, str(HERE))
from _env import TMP as _TMP  # noqa: E402

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

    def test_main_prompt_keeps_the_core_rules_verbatim(self):
        """The main prompt is the richer variant; its load-bearing block must not fork from the agent."""
        main = (ROOT / "prompts/main-prompt.md").read_text("utf-8")
        core = self.agent["instructions"].strip()
        block = main.split("```text\n", 1)[1].split("\n```", 1)[0].strip()
        self.assertEqual(block, core, "core rules drifted between main-prompt.md and the agent config")

    def test_main_prompt_documents_workflows_and_example_asks(self):
        """It has to guide a user, not just instruct a model: workflows + copy-paste asks."""
        main = (ROOT / "prompts/main-prompt.md").read_text("utf-8")
        self.assertIn("## Workflows", main)
        self.assertIn("## Example asks", main)
        self.assertIn("## Failure playbook", main)
        for workflow in ("list_templates → build_deck(", "style_slide(", "get_deck_outline(", "import_deck(", "render_svg("):
            self.assertIn(workflow, main)
        # Every example ask should be a single line a person could type.
        asks = [ln for ln in main.splitlines() if ln.startswith("- \"")]
        self.assertGreaterEqual(len(asks), 6, "the example asks section looks empty")

    def test_main_prompt_example_commands_use_real_apis(self):
        """A workflow that passes a parameter the tool does not accept teaches the model to fail."""
        main = (ROOT / "prompts/main-prompt.md").read_text("utf-8")
        for call in ("style_slide(file, slide_index=", "verify_deck(file=<deck_id>, expected_slides=",
                     "build_deck(…full outline…, base=<deck_id>)", 'import_deck(file="', "render_svg(svg="):
            self.assertIn(call, main)
        # Guard the vocabulary that has burned us: chart/table keys and the bullet limits.
        for phrase in ("categories", "series", "headers", "rows", "90 characters", "TOTAL"):
            self.assertIn(phrase, main)

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


class GatewayAssets(unittest.TestCase):
    """LibreChat v0.8.9 does not send `headers:` and misreads a 401/probe failure as "needs OAuth",
    so the credential is delivered by a token-broker gateway. These assertions keep the broker honest.
    """

    def setUp(self):
        self.dir = ROOT / "deploy/mcp-gateway"
        self.broker = (self.dir / "broker.py").read_text("utf-8")
        self.compose = (self.dir / "docker-compose.yml").read_text("utf-8")
        self.dockerfile = (self.dir / "Dockerfile").read_text("utf-8")

    def test_the_broker_obtains_and_refreshes_a_real_token(self):
        """A static bearer would expire; production tokens are short-lived, so it must fetch."""
        self.assertIn("client_credentials", self.broker)
        self.assertIn("expires_in", self.broker)
        self.assertIn('f"Bearer {BROKER.get()}"', self.broker)
        self.assertIn("invalidate", self.broker)          # 401 -> refresh once and retry

    def test_the_broker_speaks_mcp_over_http(self):
        """Streaming plus header normalisation are what make LibreChat's client work at all."""
        self.assertIn("chunked", self.broker)             # SSE responses are not buffered
        self.assertIn("text/event-stream", self.broker)    # Accept: */* must be normalised
        self.assertIn('"*/*" in accept', self.broker)
        self.assertIn("-32600", self.broker)              # empty probe answered as JSON-RPC error

    def test_the_gateway_publishes_no_host_port(self):
        self.assertIsNone(re.search(r"^\s+ports:\s*$", self.compose, re.MULTILINE),
                          "the gateway must not publish a host port")
        self.assertIn("librechat_default", self.compose)

    def test_the_gateway_runs_unprivileged_with_a_healthcheck(self):
        self.assertIn("USER 10001", self.dockerfile)
        self.assertIn("HEALTHCHECK", self.dockerfile)

    def test_the_credential_is_not_committed(self):
        gitignore = (ROOT / ".gitignore").read_text("utf-8")
        self.assertIn("deploy/mcp-gateway/.env", gitignore)


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
                    "PPT_ENTRA_REQUIRED_SCOPES", "PPT_PUBLIC_BASE", "PPT_PREVIEW_WIDTH",
                    "PPT_URL_SECRET", "PPT_ALLOWED_ROOTS", "PPT_MCP_TOKEN",
                    "PPT_ENTRA_JWKS_JSON"):
            self.assertIn(key, self.env, f"env template is missing {key}")
        self.assertIn("hostname, not an IP literal", self.env,
                      "PPT_PUBLIC_BASE must be a hostname (LibreChat blocks IP literals)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
