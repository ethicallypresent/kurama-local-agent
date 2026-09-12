# Kurama

Self-contained local agent: it owns its prompt, memory, tools, and skill lifecycle. The product UI is a **looping terminal GUI** (`Kurama.exe` / `python main.py`). WPF Studio is no longer shipped.

## Run

```bash
pip install -r requirements.txt
python main.py
```

Double-click `dist/Kurama-Portable/Kurama.exe` after a portable build. The console stays open: you type a goal, Kurama streams **thinking**, then the **answer**, and **asks before every tool**.

| Command | What |
| --- | --- |
| `python main.py` | Terminal GUI loop (asks which llama-server / GGUF model to use) |
| `python main.py --dry-run` | GUI with deterministic policy (no LLM) |
| `python main.py --model path-or-id` | Skip the picker and use that GGUF or server id |
| `python main.py --once "List the workspace"` | One-shot JSON run (scripts/CI) |
| `python main.py --once --dry-run "List the workspace"` | One-shot dry-run |
| `python tests/test_scaffold.py` | Stdlib scaffold tests |

Slash commands inside the GUI: `/help` `/models` `/config` `/preset` `/dry-run` `/reward` `/clear` `/quit`. F2 models, F3 config, Esc stops a run. Permission dialog: `y` allow, `n` deny, `a` allow this tool for the rest of the run.

## Portable exe

```bash
python scripts/publish_portable.py
```

Writes `dist/Kurama-Portable/Kurama.exe` (console, Python bundled) plus `brain/`, `models/`, `llama-server`, and the rest of the runtime data. Python on PATH is not required to *run* the portable folder.

On Windows the publisher also drops **Kurama.lnk** on the Desktop (working directory = the portable folder). The frozen exe refreshes that shortcut the first time you open the GUI, so copying the folder to another machine still gets a one-click launch.

## Layout

```
agent/
  brain/          system prompt, constitution, reasoning config
  core/           loop, memory, tools, skills
  tui/            terminal GUI (Textual)
  tools/_core/    read-only tools
  skills/         drafts → approved → archived
  db/             SQLite + chat history
  workspace/      writable area besides skills/drafts
  tests/
  main.py
```

## Loop contract

Every model turn must emit:

1. `<think>…</think>`
2. One JSON action: `use_tool | create_skill | update_plan | save_memory | request_confirmation | finish`

The TUI streams the think block live, hides the JSON action, and shows `finish.summary` as the answer. `use_tool` and `create_skill` pause for a human decision. `request_confirmation` uses the same dialog and **continues the loop** instead of exiting.

The runtime injects: goal, last result, working memory, top-k tools, top-k skills, recalled memories.

## Skill protocol

New capability → write draft with `SKILL MANIFEST` + `def run(...) -> dict` + optional `TESTS` → static + unit gate → promote on pass. Cap: 3 drafts per run.

## Honest limits

- Embeddings are hash-bags unless you plug in a real model and pgvector.
- Web search needs `TAVILY_API_KEY`.
- Skill execution loads Python in-process after an AST gate. That is convenience, not isolation.
- `code_runner` and skill import are not a security boundary against a hostile tenant.
