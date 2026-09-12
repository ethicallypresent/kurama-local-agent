# Kurama training data

Generated from the debugging/coding session (loop fixes, llama.cpp, safety, skills).

| File | Purpose |
|------|---------|
| `trajectories.jsonl` | Goal → assistant think+JSON actions (coding-focused) |
| `kurama_sft.jsonl` | Chat SFT rows (system + state packet + assistant) for fine-tuning `pocket.gguf` |
| `session_lessons.json` | Lessons already seeded into `db/memory.sqlite` |

## Already applied (agent learning layer)

```bash
python scripts/train_from_session.py
```

This seeds verified memories, boosts action beliefs in `db/beliefs.json`, and rewards a synthetic 2-step tool→finish trace.

## Fine-tune the GGUF later (optional)

When you have GPU / Unsloth / llama.cpp finetune:

1. Use `kurama_sft.jsonl` (ShareGPT-style `messages`).
2. Keep sequences short — condensed brain is already in the system turn.
3. Prefer LoRA on top of the base instruct model that produced `pocket.gguf`, then re-export GGUF.
4. Replace `models/pocket.gguf` and restart Kurama Studio.

There is currently no `llama-finetune` binary in the bundled WinGet llama.cpp build, so weight updates are deferred; behavioral training is live via memory + beliefs.
