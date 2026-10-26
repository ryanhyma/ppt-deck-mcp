# Prompts

Three files, three jobs. Keep them in this order when changing behaviour:

| File | Audience | What it is |
|---|---|---|
| [`main-prompt.md`](main-prompt.md) | **model + operator** | The complete prompt: core rules, how to behave towards the user, 11 worked workflows (first deck, chart, table, SVG, restyle, content edit, import, template choice, French, long decks), failure playbook, copy-paste example asks |
| [`agent-deck-builder.md`](agent-deck-builder.md) | model | The terse production prompt that ships in the agent config, plus the defect each line prevents |
| [`server-instructions.md`](server-instructions.md) | model | The MCP `serverInstructions` that go into `librechat.yaml` (the floor when Skills are disabled) |

`main-prompt.md` contains the agent prompt's core block **verbatim**; `tests/test_deployment_assets.py`
fails if the two drift, if a workflow calls a tool or parameter that does not exist, or if the example
asks section disappears. The machine-readable copy of the agent config is
[`../deploy/librechat/agent-deck-builder.json`](../deploy/librechat/agent-deck-builder.json).

Which one do I paste where?

- **LibreChat agent → Instructions**: `agent-deck-builder.md` (tested default) or `main-prompt.md` when
  the model is stronger and you want it to guide the user rather than just execute.
- **`librechat.yaml` → `mcpServers.ppt.serverInstructions`**: `server-instructions.md`.
- **Skills** stay in [`../skills/`](../skills) — they are loaded from disk, not pasted.
