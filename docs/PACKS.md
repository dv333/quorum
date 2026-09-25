# Topic packs

A topic pack is a small JSON file that sets up a kind of debate. Choosing one on the home screen (or with
`quorum ask --pack <id>`) fills in the question starter and gives the council guidance for how to approach it.

Quorum's own packs live in `backend/packs/`. Yours go in `data/packs/` (or the folder in `QUORUM_PACKS_DIR`). A file
with the same name as a built-in pack replaces it. Packs are read each time the home screen loads, so no restart is
needed.

## Format

The file name is the pack's id: lowercase letters, digits and dashes, up to 40 characters (`code-review.json`).

| Field | Required | Max | What it does |
|---|---|---|---|
| `name` | yes | 40 | The chip label |
| `description` | yes | 200 | Shown under the question box when the pack is chosen |
| `guidance` | yes | 800 | Added to every agent's instructions and the chair's, as "How to approach this conundrum: …" |
| `emoji` | no | 8 | Shown on the chip |
| `prompt` | no | 300 | A question starter placed in the question box (keep a trailing space) |
| `focus` | no | 200 | What the user cares most about, when they haven't set their own priorities in Customize |
| `models` | no | | Which models suit the pack (see below) |

## Models for a pack

A pack can build its council around suitable models:

```json
"models": {
  "prefer": ["coder", "devstral"],
  "suggest": ["qwen3-coder:30b", "qwen2.5-coder:14b"],
  "seats": 4
}
```

- `prefer`: parts of model names (up to 12). Installed models whose name contains one of them are the pack's
  specialists. When at least one is installed, the council is the specialists plus the strongest other models, up to
  `seats` (2 to 8, default 4). Without specialists, the council is every installed model as usual.
- `suggest`: Ollama models (up to 5) to offer when no specialist is installed. Each must be in `backend/catalog.json`
  so Quorum can check its size against your memory and free disk before offering it. Nothing is downloaded without a
  click, and models are never removed.

A pack that doesn't match this format is skipped, and the backend log says why.

## Writing good guidance

- Say how to debate, not what to conclude: "At least one agent argues against the decision in every round" works
  better than "Decide whether it's a good idea."
- Keep it short. Local models have small context windows, and the guidance is sent with every turn.
- Don't score or rank models. Quorum compares ideas, not models.

## Example

```json
{
  "name": "Stress-test a decision",
  "emoji": "🧪",
  "description": "Argue against a decision to find its weak spots before you commit.",
  "prompt": "I've decided to ",
  "focus": "hidden assumptions, downside risk, what would change the decision",
  "guidance": "Treat this as a pre-mortem. At least one agent should argue against the decision in every round. Surface hidden assumptions, the worst realistic outcome and how to limit it, and the evidence that would change the decision. Only agree once the strongest objection has been answered."
}
```
