import json
import urllib.request

from core.llama_server import ensure_from_config, stop
from core.loop import load_system_prompt, load_config
from core.paths import AgentPaths

p = AgentPaths.discover()
c = load_config(p)
print("boot", ensure_from_config(p.root, c), flush=True)
brain = load_system_prompt(p)
payload = {
    "model": "pocket",
    "messages": [
        {
            "role": "system",
            "content": (
                "You are Kurama. Always begin with <think> then </think>, "
                "then a normal answer. Never skip think tags."
            ),
        },
        {"role": "user", "content": "Say hello and explain what you can do in 3 sentences."},
    ],
    "temperature": 0.25,
    "max_tokens": 250,
    "stream": False,
}
req = urllib.request.Request(
    "http://127.0.0.1:8080/v1/chat/completions",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=300) as resp:
    data = json.loads(resp.read().decode())
text = data["choices"][0]["message"]["content"] or ""
print("RAW_START")
print(text)
print("RAW_END")
low = text.lower()
print("has_open", "<think>" in low)
print("has_close", "</think>" in low)
stop()
