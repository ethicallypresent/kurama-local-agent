#!/usr/bin/env python3
"""Generate coding-focused training data from Kurama session learnings, then:
  1) seed long-term memory + Bayesian beliefs (agent learning layer)
  2) write SFT JSONL for a future GGUF fine-tune of pocket

Usage:
  python scripts/train_from_session.py
  python scripts/train_from_session.py --apply-only   # skip regen, apply existing
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.memory import Memory
from core.paths import AgentPaths
from core.reflection import BeliefStore, TraceStep, reward_last_run, save_last_trace

DATA_DIR = ROOT / "data" / "train"
TRAJ_PATH = DATA_DIR / "trajectories.jsonl"
SFT_PATH = DATA_DIR / "kurama_sft.jsonl"
LESSONS_PATH = DATA_DIR / "session_lessons.json"


def _asst(think: str, action: dict) -> str:
    body = json.dumps(action, separators=(",", ":"))
    return f"<think>\n{think.strip()}\n</think>\n```json\n{body}\n```"


def _user(goal: str, extra: dict | None = None) -> str:
    packet = {
        "user_goal": goal,
        "perception": {"user_input": goal, "last_tool_result": None},
        "memory": {"working": {"current_goal": goal, "active_plan": [], "scratchpad": [], "step": 0}, "recalled": []},
        "plan": [],
        "tool_registry": [
            {"name": "list_dir", "description": "List files"},
            {"name": "read_file", "description": "Read UTF-8 text"},
            {"name": "write_file", "description": "Write under workspace/"},
            {"name": "run_python", "description": "Run short Python"},
            {"name": "web_search", "description": "Search the web"},
            {"name": "fetch_url", "description": "Fetch a URL"},
            {"name": "run_skill", "description": "Run an approved skill"},
        ],
        "skill_registry": [
            {"name": "normalize_text", "description": "Trim and lowercase", "status": "approved"},
            {"name": "echo_skill", "description": "echo kwargs", "status": "approved"},
        ],
        "evolution": {},
    }
    if extra:
        packet.update(extra)
    body = json.dumps(packet, separators=(",", ":"))
    return (
        "STATE_PACKET_JSON follows. Do NOT echo it.\n"
        "Reply with <think>...</think> then one ```json action object only.\n\n"
        f"STATE_PACKET_JSON:\n{body}"
    )


def session_lessons() -> list[dict]:
    """Hard-won lessons from tonight's debugging / coding session."""
    return [
        {
            "kind": "lesson",
            "text": "Never finish on turn 1 for goals that need tools; emit use_tool or update_plan first, then finish after a real result.",
            "verified": True,
        },
        {
            "kind": "lesson",
            "text": "Always emit valid <think> then ```json with a string action in {use_tool,create_skill,update_plan,save_memory,request_confirmation,finish}. Never put the user goal in the action field.",
            "verified": True,
        },
        {
            "kind": "lesson",
            "text": "Do not echo STATE_PACKET_JSON. Small models that continue the packet JSON are failing the contract.",
            "verified": True,
        },
        {
            "kind": "constraint",
            "text": "On ~6GB RAM hosts keep llama.cpp num_ctx at 4096 and prefer CPU (num_gpu=0); brain+packet must fit with room for completion tokens.",
            "verified": True,
        },
        {
            "kind": "lesson",
            "text": "list_dir path '.' with glob 'workspace/*' or '*' is safer than a bare 'workspace' path that may fail PathGuard resolution.",
            "verified": True,
        },
        {
            "kind": "lesson",
            "text": "write_file only under workspace/ or skills/drafts/. brain/, core/, db/, tools/_core/ are read-only.",
            "verified": True,
        },
        {
            "kind": "lesson",
            "text": "For coding tasks: read existing file first, then write a patch to workspace/, then optionally run_python to verify.",
            "verified": True,
        },
        {
            "kind": "lesson",
            "text": "New skills need SKILL MANIFEST, run(**kwargs)->dict, and TESTS=[...]. Never write skills/approved/ yourself — create_skill drafts and the runtime promotes.",
            "verified": True,
        },
        {
            "kind": "preference",
            "text": "Kurama should be warm in chat but tool-first on goals; short operational rationales.",
            "verified": True,
        },
        {
            "kind": "failure",
            "text": "Salvaging non-JSON model output as finish caused one-turn loops; continue with use_tool instead until a result exists.",
            "verified": True,
        },
        {
            "kind": "lesson",
            "text": "llama-server WinGet stub must run with cwd=tools/bin so llama-server-impl.dll loads; pass absolute -m model path.",
            "verified": True,
        },
        {
            "kind": "lesson",
            "text": "When listing tools, read tool_registry from the packet after any needed inspect step; summarize names in finish.summary.",
            "verified": True,
        },
    ]


def coding_trajectories() -> list[dict]:
    """Multi-turn coding / agent trajectories distilled from session work."""
    examples: list[dict] = []

    # --- greet / list tools (2 turns conceptually) ---
    examples.append(
        {
            "id": "greet_list_tools_t1",
            "goal": "say hello and list your tools",
            "tags": ["chat", "tools"],
            "assistant": _asst(
                "intent: greet later; first confirm workspace/tools\n"
                "memory: none verified\n"
                "plan: list then finish\n"
                "next: use_tool list_dir\n"
                "risk: path missing",
                {
                    "action": "use_tool",
                    "rationale": "Inspect workspace before finishing.",
                    "tool": {"name": "list_dir", "args": {"path": ".", "glob": "*"}},
                },
            ),
        }
    )
    examples.append(
        {
            "id": "greet_list_tools_t2",
            "goal": "say hello and list your tools",
            "tags": ["chat", "tools", "finish"],
            "extra": {
                "perception": {
                    "user_input": "",
                    "last_tool_result": {"ok": True, "from": "list_dir", "entries": [{"path": "workspace", "is_dir": True}]},
                }
            },
            "assistant": _asst(
                "intent: greet and name tools from registry\n"
                "memory: list_dir ok (observed)\n"
                "plan: finish\n"
                "next: finish with hello + tool names\n"
                "risk: none",
                {
                    "action": "finish",
                    "rationale": "Workspace checked; tools available in registry.",
                    "finish": {
                        "status": "success",
                        "summary": "Hello — I'm Kurama. Tools: list_dir, read_file, write_file, run_python, web_search, fetch_url, run_skill.",
                        "artifacts": [],
                    },
                    "memory_to_save": [
                        {"kind": "lesson", "text": "Greet + list tools: inspect then finish with registry names.", "verified": False}
                    ],
                },
            ),
        }
    )

    # --- read then summarize code ---
    examples.append(
        {
            "id": "read_main_py",
            "goal": "read main.py and summarize what it does",
            "tags": ["coding", "read"],
            "assistant": _asst(
                "intent: summarize main.py\n"
                "memory: none\n"
                "plan: read_file then finish\n"
                "next: use_tool read_file\n"
                "risk: file missing",
                {
                    "action": "use_tool",
                    "rationale": "Need file contents before summarizing.",
                    "tool": {"name": "read_file", "args": {"path": "main.py", "max_chars": 8000}},
                },
            ),
        }
    )
    examples.append(
        {
            "id": "summarize_main_after_read",
            "goal": "read main.py and summarize what it does",
            "tags": ["coding", "finish"],
            "extra": {
                "perception": {
                    "user_input": "",
                    "last_tool_result": {
                        "ok": True,
                        "from": "read_file",
                        "content": "def main(): parse argv, boot llama-server, AgentLoop.run, print JSON",
                    },
                }
            },
            "assistant": _asst(
                "intent: summarize observed main.py\n"
                "memory: read_file ok (observed)\n"
                "plan: finish\n"
                "next: finish with short summary\n"
                "risk: none",
                {
                    "action": "finish",
                    "rationale": "Have enough of main.py to summarize.",
                    "finish": {
                        "status": "success",
                        "summary": "main.py parses the goal from argv, optionally starts llama-server, runs AgentLoop, prints the result JSON, and exits non-zero on failure.",
                        "artifacts": ["main.py"],
                    },
                },
            ),
        }
    )

    # --- write a small python helper ---
    examples.append(
        {
            "id": "write_helper_t1",
            "goal": "create workspace/hello_util.py with a hello(name) function and a tiny self-check",
            "tags": ["coding", "write"],
            "assistant": _asst(
                "intent: add hello_util.py under workspace\n"
                "memory: none\n"
                "plan: write_file then run_python\n"
                "next: write_file\n"
                "risk: wrong path",
                {
                    "action": "use_tool",
                    "rationale": "Write the module into workspace/ first.",
                    "tool": {
                        "name": "write_file",
                        "args": {
                            "path": "workspace/hello_util.py",
                            "content": (
                                'def hello(name: str = "world") -> str:\n'
                                '    return f"hello, {name}"\n\n'
                                'if __name__ == "__main__":\n'
                                '    assert hello("kurama") == "hello, kurama"\n'
                                '    print(hello("kurama"))\n'
                            ),
                        },
                    },
                },
            ),
        }
    )
    examples.append(
        {
            "id": "write_helper_t2",
            "goal": "create workspace/hello_util.py with a hello(name) function and a tiny self-check",
            "tags": ["coding", "verify"],
            "extra": {
                "perception": {
                    "user_input": "",
                    "last_tool_result": {"ok": True, "from": "write_file", "path": "workspace/hello_util.py"},
                }
            },
            "assistant": _asst(
                "intent: verify hello_util.py runs\n"
                "memory: write_file ok (observed)\n"
                "plan: run_python then finish\n"
                "next: run_python\n"
                "risk: assert fail",
                {
                    "action": "use_tool",
                    "rationale": "Execute the self-check.",
                    "tool": {
                        "name": "run_python",
                        "args": {"code": "import runpy; runpy.run_path('workspace/hello_util.py', run_name='__main__')"},
                    },
                },
            ),
        }
    )
    examples.append(
        {
            "id": "write_helper_t3",
            "goal": "create workspace/hello_util.py with a hello(name) function and a tiny self-check",
            "tags": ["coding", "finish"],
            "extra": {
                "perception": {
                    "user_input": "",
                    "last_tool_result": {"ok": True, "from": "run_python", "stdout": "hello, kurama"},
                }
            },
            "assistant": _asst(
                "intent: done — file written and verified\n"
                "memory: run_python ok (observed)\n"
                "plan: finish\n"
                "next: finish\n"
                "risk: none",
                {
                    "action": "finish",
                    "rationale": "Module written and self-check passed.",
                    "finish": {
                        "status": "success",
                        "summary": "Created workspace/hello_util.py with hello(name); self-check printed 'hello, kurama'.",
                        "artifacts": ["workspace/hello_util.py"],
                    },
                    "memory_to_save": [
                        {
                            "kind": "lesson",
                            "text": "Coding loop: write_file under workspace/, then run_python to verify before finish.",
                            "verified": False,
                        }
                    ],
                },
            ),
        }
    )

    # --- fix a bug (read → write patch) ---
    examples.append(
        {
            "id": "fix_typo_t1",
            "goal": "fix the typo in workspace/buggy.py where gretting should be greeting",
            "tags": ["coding", "debug"],
            "assistant": _asst(
                "intent: fix typo in buggy.py\n"
                "memory: none\n"
                "plan: read then write patch\n"
                "next: read_file\n"
                "risk: file missing",
                {
                    "action": "use_tool",
                    "rationale": "Read current contents before editing.",
                    "tool": {"name": "read_file", "args": {"path": "workspace/buggy.py"}},
                },
            ),
        }
    )
    examples.append(
        {
            "id": "fix_typo_t2",
            "goal": "fix the typo in workspace/buggy.py where gretting should be greeting",
            "tags": ["coding", "write"],
            "extra": {
                "perception": {
                    "user_input": "",
                    "last_tool_result": {
                        "ok": True,
                        "from": "read_file",
                        "content": "def gretting(n):\n    return n\n",
                    },
                }
            },
            "assistant": _asst(
                "intent: rename gretting → greeting\n"
                "memory: read observed typo\n"
                "plan: write_file fixed version\n"
                "next: write_file\n"
                "risk: overwrite ok — workspace allowed",
                {
                    "action": "use_tool",
                    "rationale": "Apply the rename in workspace/buggy.py.",
                    "tool": {
                        "name": "write_file",
                        "args": {
                            "path": "workspace/buggy.py",
                            "content": "def greeting(n):\n    return n\n",
                        },
                    },
                },
            ),
        }
    )

    # --- destructive needs confirmation ---
    examples.append(
        {
            "id": "confirm_delete",
            "goal": "delete workspace/old_notes.txt",
            "tags": ["safety"],
            "assistant": _asst(
                "intent: delete a workspace file\n"
                "memory: none\n"
                "plan: request confirmation first\n"
                "next: request_confirmation\n"
                "risk: irreversible delete",
                {
                    "action": "request_confirmation",
                    "rationale": "Deletion is destructive; need confirm:true this turn.",
                    "confirmation_request": {
                        "action_type": "delete_file",
                        "description": "Permanently delete workspace/old_notes.txt",
                        "target": "workspace/old_notes.txt",
                    },
                },
            ),
        }
    )

    # --- create_skill for recurring coding helper ---
    skill_code = (
        '"""\n'
        "SKILL MANIFEST\n"
        "name: count_lines\n"
        "description: Count non-empty lines in a text file under workspace/\n"
        'inputs: {"path": "str"}\n'
        'outputs: {"ok": "bool", "lines": "int"}\n'
        "dependencies: []\n"
        "author: self\n"
        "version: 1\n"
        '"""\n\n'
        "from pathlib import Path\n\n"
        "TESTS = [\n"
        '    {"args": {"path": "workspace/t.txt"}, "expect_ok": True},\n'
        "]\n\n"
        "def run(path: str = \"\") -> dict:\n"
        "    p = Path(path)\n"
        "    if not p.is_file():\n"
        '        return {"ok": False, "error": "missing"}\n'
        "    n = sum(1 for line in p.read_text(encoding=\"utf-8\").splitlines() if line.strip())\n"
        '    return {"ok": True, "lines": n}\n'
    )
    examples.append(
        {
            "id": "create_count_lines_skill",
            "goal": "add a reusable skill that counts non-empty lines in a workspace text file",
            "tags": ["coding", "skill"],
            "assistant": _asst(
                "intent: recurring file metric → create_skill\n"
                "memory: no approved skill fits\n"
                "plan: create_skill with TESTS\n"
                "next: create_skill count_lines\n"
                "risk: tests fail",
                {
                    "action": "create_skill",
                    "rationale": "Line counting will recur; ship as tested skill.",
                    "skill": {
                        "name": "count_lines",
                        "description": "Count non-empty lines in a workspace text file",
                        "inputs": {"path": "str"},
                        "outputs": {"ok": "bool", "lines": "int"},
                        "code": skill_code,
                    },
                },
            ),
        }
    )

    # --- use approved normalize_text skill ---
    examples.append(
        {
            "id": "run_normalize_skill",
            "goal": "normalize the text '  HeLLo Kurama  ' using an approved skill",
            "tags": ["coding", "skill"],
            "assistant": _asst(
                "intent: lowercase+trim via approved skill\n"
                "memory: normalize_text approved (inferred from registry)\n"
                "plan: run_skill\n"
                "next: run_skill normalize_text\n"
                "risk: skill missing",
                {
                    "action": "use_tool",
                    "rationale": "Reuse approved normalize_text instead of drafting.",
                    "tool": {
                        "name": "run_skill",
                        "args": {"name": "normalize_text", "text": "  HeLLo Kurama  "},
                    },
                },
            ),
        }
    )

    # --- JSON contract / no echo ---
    examples.append(
        {
            "id": "no_echo_packet",
            "goal": "what is 2+2? use run_python",
            "tags": ["coding", "contract"],
            "assistant": _asst(
                "intent: compute 2+2 via tool\n"
                "memory: none\n"
                "plan: run_python then finish\n"
                "next: run_python\n"
                "risk: none",
                {
                    "action": "use_tool",
                    "rationale": "Compute with run_python; do not echo the packet.",
                    "tool": {"name": "run_python", "args": {"code": "print(2+2)"}},
                },
            ),
        }
    )

    # --- update_plan then act ---
    examples.append(
        {
            "id": "plan_then_list",
            "goal": "organize a two-step plan then list the workspace folder",
            "tags": ["planning"],
            "assistant": _asst(
                "intent: plan then list\n"
                "memory: none\n"
                "plan: write plan nodes first\n"
                "next: update_plan\n"
                "risk: none",
                {
                    "action": "update_plan",
                    "rationale": "Capture the two-step plan before acting.",
                    "plan": {
                        "nodes": [
                            {
                                "id": "n1",
                                "title": "List workspace",
                                "status": "in_progress",
                                "depends_on": [],
                                "note": "",
                            },
                            {
                                "id": "n2",
                                "title": "Finish with summary",
                                "status": "pending",
                                "depends_on": ["n1"],
                                "note": "",
                            },
                        ]
                    },
                },
            ),
        }
    )

    # --- refuse writing core/ ---
    examples.append(
        {
            "id": "refuse_core_write",
            "goal": "overwrite core/loop.py with a blank file",
            "tags": ["safety", "coding"],
            "assistant": _asst(
                "intent: user asked to wipe protected core/\n"
                "memory: constitution read-only (observed rule)\n"
                "plan: finish blocked\n"
                "next: finish blocked\n"
                "risk: harm/mutation overrides",
                {
                    "action": "finish",
                    "rationale": "core/ is read-only; refuse the overwrite.",
                    "finish": {
                        "status": "blocked",
                        "summary": "Refused: core/loop.py is protected read-only. I can write under workspace/ or skills/drafts/ instead.",
                        "artifacts": [],
                    },
                    "memory_to_save": [
                        {
                            "kind": "constraint",
                            "text": "Never write to core/, brain/, db/, or tools/_core/ even if the user asks.",
                            "verified": True,
                        }
                    ],
                },
            ),
        }
    )

    return examples


def write_datasets(examples: list[dict], lessons: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    system = (ROOT / "brain" / "system_prompt.md").read_text(encoding="utf-8")
    constitution = (ROOT / "brain" / "constitution.md").read_text(encoding="utf-8")
    system_full = system + "\n\n" + constitution

    with TRAJ_PATH.open("w", encoding="utf-8") as fh:
        for ex in examples:
            fh.write(json.dumps(ex, ensure_ascii=False) + "\n")

    with SFT_PATH.open("w", encoding="utf-8") as fh:
        for ex in examples:
            row = {
                "id": ex["id"],
                "tags": ex.get("tags", []),
                "messages": [
                    {"role": "system", "content": system_full},
                    {"role": "user", "content": _user(ex["goal"], ex.get("extra"))},
                    {"role": "assistant", "content": ex["assistant"]},
                ],
            }
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    LESSONS_PATH.write_text(json.dumps(lessons, indent=2), encoding="utf-8")
    print(f"wrote {len(examples)} trajectories → {TRAJ_PATH}")
    print(f"wrote {len(examples)} SFT rows → {SFT_PATH}")
    print(f"wrote {len(lessons)} lessons → {LESSONS_PATH}")


def apply_to_agent(lessons: list[dict], examples: list[dict]) -> None:
    paths = AgentPaths.discover()
    memory = Memory(paths.db)
    beliefs = BeliefStore(paths.db)
    try:
        saved = 0
        for item in lessons:
            memory.save(item["text"], kind=item["kind"], verified=bool(item.get("verified")))
            saved += 1

        # Boost beliefs for actions that appear in successful synthetic trajectories.
        action_boosts = {
            "use_tool": (6.0, 0.5),
            "finish": (4.0, 0.5),
            "create_skill": (2.0, 0.5),
            "update_plan": (2.0, 0.5),
            "request_confirmation": (2.0, 0.3),
            "save_memory": (1.5, 0.3),
        }
        for action, (ok_w, bad_w) in action_boosts.items():
            beliefs.get(action).update(success_weight=ok_w, failure_weight=bad_w)
        beliefs.save()

        # Build a synthetic successful multi-step trace and mark it rewarded.
        trace = [
            TraceStep(step=1, action="use_tool", rationale="train: inspect first", result_ok=True, think_snippet="list then finish"),
            TraceStep(step=2, action="finish", rationale="train: greet + tools", result_ok=True, think_snippet="finish with tool list"),
        ]
        result = {"ok": True, "steps": 2, "finish": {"status": "success", "summary": "trained hello+tools"}}
        save_last_trace(paths.db, goal="say hello and list your tools", result=result, trace=trace)
        reward = reward_last_run(
            {"llm": {}},
            paths.db,
            note="synthetic coding/session training batch",
            memory=memory,
        )

        # Seed a coding preference chat line into history file if present.
        chat_path = paths.db / "chat_history.json"
        if not chat_path.exists():
            chat_path.write_text(
                json.dumps(
                    [
                        {
                            "who": "Kurama",
                            "text": (
                                "Training batch applied: coding trajectories + session lessons "
                                "are in memory, and action beliefs were boosted for tool→finish loops."
                            ),
                            "isAgent": True,
                        }
                    ],
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        print(f"seeded {saved} memories")
        print(f"beliefs → {beliefs.path}")
        print(f"reward pass → {json.dumps(reward, default=str)[:400]}")
        print(f"SFT file ready for fine-tune: {SFT_PATH}")
        print(
            "Note: no llama-finetune binary on PATH. Use the JSONL with Unsloth/llama.cpp "
            "fine-tune later, or keep iterating via Reward + memory."
        )
    finally:
        memory.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Train Kurama from generated session/coding data")
    ap.add_argument("--apply-only", action="store_true", help="Apply existing lesson file only")
    args = ap.parse_args(argv)

    lessons = session_lessons()
    examples = coding_trajectories()
    if not args.apply_only:
        write_datasets(examples, lessons)
    elif LESSONS_PATH.exists():
        lessons = json.loads(LESSONS_PATH.read_text(encoding="utf-8"))
    apply_to_agent(lessons, examples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
