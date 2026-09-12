"""
SKILL MANIFEST
name: normalize_text
description: Trim and lowercase text
kind: transform
inputs: {"text": "str"}
outputs: {"ok": "bool", "text": "str"}
dependencies: []
author: self
version: 1
"""

TESTS = [
    {"args": {"text": "  Hi "}, "expect_ok": True},
]

def run(text: str = "") -> dict:
    return {"ok": True, "name": "normalize_text", "text": str(text).strip().lower()}
