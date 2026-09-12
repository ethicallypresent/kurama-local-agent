import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
trace_path = root / "db" / "last_trace.json"
beliefs_path = root / "db" / "beliefs.json"
log_path = root / "workspace" / "last_run.log"

print("=== LAST TRACE ===")
if not trace_path.exists():
    print("no last_trace.json")
else:
    d = json.loads(trace_path.read_text(encoding="utf-8"))
    print("goal:", d.get("goal"))
    print("ok:", d.get("ok"), "| rewarded:", d.get("rewarded"))
    print("saved_at:", d.get("saved_at"))
    print("result:", json.dumps(d.get("result"), indent=2))
    print("steps:", len(d.get("trace") or []))
    for t in d.get("trace") or []:
        print(
            f"  step {t['step']}: action={t['action']} ok={t['result_ok']} "
            f"err={t.get('result_error')!r}"
        )
        print(f"    rationale: {(t.get('rationale') or '')[:160]}")
        print(f"    think: {(t.get('think_snippet') or '')[:160]}")

print("\n=== BELIEFS ===")
if beliefs_path.exists():
    b = json.loads(beliefs_path.read_text(encoding="utf-8"))
    for k, v in b.items():
        print(f"  {k}: p={v.get('p_success')} n={v.get('observations')} a={v.get('alpha')} b={v.get('beta')}")

print("\n=== LOG HIGHLIGHTS ===")
if log_path.exists():
    text = log_path.read_text(encoding="utf-8", errors="replace")
    keys = ("ok", "error", "simple-chat", "finish", "use_tool", "Warmup", "reflection", "Salvaged", "normaliz")
    for line in text.splitlines():
        if any(k.lower() in line.lower() for k in keys):
            print(line[:220])
else:
    print("no last_run.log")
