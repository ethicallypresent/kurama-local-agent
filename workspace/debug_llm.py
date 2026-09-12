import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.llama_server import ensure_from_config, stop
from core.loop import build_user_packet, call_llm, load_system_prompt, load_config
from core.memory import Memory
from core.paths import AgentPaths
from core.skill_manager import SkillManager
from core.tool_registry import ToolRegistry

p = AgentPaths.discover()
c = load_config(p)
print("boot", ensure_from_config(p.root, c), flush=True)
brain = load_system_prompt(p)
mem = Memory(p.db)
tools = ToolRegistry(p)
skills = SkillManager(p)
pkt = build_user_packet(
    goal="say hello and list your tools",
    user_input="say hello and list your tools",
    memory=mem,
    tools=tools.list_filtered("tools", 8),
    skills=skills.list_skills("tools", 8, True),
    memory_topk=3,
    evolution={},
)
print("brain", len(brain), "pkt", len(pkt), flush=True)
try:
    raw = call_llm(brain, pkt, c)
    print("RAW_START")
    print(raw)
    print("RAW_END")
finally:
    mem.close()
    stop()
