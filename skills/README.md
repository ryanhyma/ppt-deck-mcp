# Deployment Skills

These are the **LibreChat deployment skills** for this MCP server. They live in the repo so a
deployment does not depend on somebody's home directory, and so changes to the tool surface can be
reviewed with the code that changes it.

```text
skills/
  ppt-deck-builder/SKILL.md    # how to build/style/verify a deck (the main one)
  svg-graphics/SKILL.md        # SVG → PNG for icons and simple diagrams
```

## How LibreChat consumes them

1. Copy (or bind-mount) this directory to the path LibreChat reads at startup — `/app/skill` in the
   container image, `<deploy>\skill` for a native install — and restart LibreChat. The startup log must
   say `[deploymentSkills] Loaded N deployment skill(s) from …`.
2. **Skills are loaded read-only and are not Skill documents in MongoDB.** An agent can only use them
   if it carries the MongoDB **ObjectIds** of the `Skill` documents that were created from them
   (`{{SKILL_ID_PPT_DECK_BUILDER}}` / `{{SKILL_ID_SVG_GRAPHICS}}` in
   [`../deploy/librechat/agent-deck-builder.json`](../deploy/librechat/agent-deck-builder.json)) with
   `skills_enabled: true`. Names are silently dropped — a skill list that looks right but resolves to
   ObjectIds that do not exist leaves the agent with no skills.
3. The MCP server's own `serverInstructions`
   ([`../prompts/server-instructions.md`](../prompts/server-instructions.md)) also carry the workflow.
   That is deliberate redundancy: instructions reach the model even when Skills are disabled, and the
   skill file carries the detail (chart/table shapes, styling vocabulary, import caveats).

## Changing a skill

Edit it here, re-deploy the directory, restart LibreChat. Keep the YAML front matter (`name`,
`description`) — the `description` is what the model sees when choosing a skill, so say *when* to use
it, not just what it does.

The names of the tools referenced in these files must match what the server registers; that is
asserted by `tests/test_deployment_assets.py`, so a renamed tool fails CI instead of silently rotting
the skill.
