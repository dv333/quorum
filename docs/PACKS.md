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
