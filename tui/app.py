"""Kurama looping terminal GUI (Textual)."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from core.llama_server import (
    current_model_path,
    is_healthy,
    list_models,
    stop as stop_llama,
)
from core.loop import AgentLoop, load_config
from core.model import ModelChoice, apply_choice, catalog, choice_from_spec
from core.paths import AgentPaths
from core.permission import DENY
from core.reflection import reward_last_run
from tui.history import Message, load_history, save_history
from tui.runtime import (
    apply_preset,
    create_loop,
    merge_config,
    preset_names,
    read_config,
)
from tui.stream import StreamSplitter, StreamView

log = logging.getLogger("kurama.tui")

HELP_TEXT = """\
Commands
  /help              this list
  /models            pick a llama-server model or GGUF
  /config            resources + agent settings
  /load [path-or-id] load a GGUF path or a server model id
  /stop              cancel the current generation
  /stop-server       stop llama-server
  /preset NAME       auto | cpu_optimal | balanced | max_context | fast_draft
  /dry-run           toggle deterministic policy (no LLM)
  /reward            reinforce the last run
  /clear             wipe chat history on this machine
  /quit              exit

Keys: F2 models · F3 config · Esc stop · y/n/a in the permission dialog
"""


def format_args(args: Any) -> str:
    if not args:
        return "(none)"
    try:
        return json.dumps(args, indent=2, default=str)
    except TypeError:
        return str(args)


def finish_summary(result: dict[str, Any] | None) -> str:
    if not isinstance(result, dict):
        return ""
    finish = result.get("finish")
    if isinstance(finish, dict) and finish.get("summary"):
        return str(finish["summary"]).strip()
    err = result.get("error")
    if err and not result.get("ok"):
        return str(err)
    return ""


# ---------------------------------------------------------------------------
# Textual widgets / screens
# ---------------------------------------------------------------------------

def run_app(*, dry_run: bool = False, max_steps: int | None = None, model: str | None = None) -> int:
    try:
        app = build_app(dry_run=dry_run, max_steps=max_steps, model=model)
    except ImportError:
        print("The terminal GUI needs textual. Install with:  pip install textual", flush=True)
        return 2
    app.run()
    return 0


def build_app(*, dry_run: bool = False, max_steps: int | None = None, model: str | None = None):
    from rich.markup import escape
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.screen import ModalScreen, Screen
    from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, ListItem, ListView, Static

    class ChatMessage(Static):
        def __init__(self, message: Message, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.message = message
            self.add_class("agent" if message.is_agent else "user")

        def on_mount(self) -> None:
            self.refresh_body()

        def refresh_body(self) -> None:
            b = self.message
            who = "[bold #F59E0B]Kurama[/]" if b.is_agent else "[bold #93c5fd]You[/]"
            parts = [who]
            thinking = (b.thinking or "").rstrip()
            answer = (b.text or "").rstrip()
            if thinking or (b.is_agent and not answer):
                label = "Thinking…" if b.is_agent and not answer else "Thought"
                parts.append(f"[dim]{label}[/]")
                if thinking:
                    parts.append(f"[italic dim]{escape(thinking)}[/]")
            if answer:
                parts.append(escape(answer))
            self.update("\n".join(parts))

    class PermissionModal(ModalScreen[str]):
        BINDINGS = [
            Binding("y", "allow", "Allow", show=True),
            Binding("n", "deny", "Deny", show=True),
            Binding("a", "allow_run", "Allow for this run", show=True),
            Binding("escape", "deny", "Deny", show=False),
        ]

        def __init__(self, request: dict[str, Any]) -> None:
            super().__init__()
            self.request = request

        def compose(self) -> ComposeResult:
            req = self.request
            kind = str(req.get("kind") or "use_tool")
            name = str(req.get("name") or "")
            desc = str(req.get("description") or "")
            target = str(req.get("target") or "")
            body = (
                f"[bold #F59E0B]Permission needed[/]\n\n"
                f"[dim]kind[/]  {kind}\n"
                f"[dim]name[/]  {name or '—'}\n"
            )
            if desc:
                body += f"[dim]what[/]  {desc}\n"
            if target and target != format_args(req.get("args")):
                body += f"[dim]target[/]  {target}\n"
            body += f"\n[dim]args[/]\n{format_args(req.get('args'))}"
            with Vertical(id="perm-dialog"):
                yield Static(body, id="perm-body")
                with Horizontal(id="perm-buttons"):
                    yield Button("Allow  (y)", id="allow", variant="success")
                    yield Button("Deny  (n)", id="deny", variant="error")
                    yield Button("Allow for this run  (a)", id="allow_run", variant="primary")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            self.dismiss(str(event.button.id))

        def action_allow(self) -> None:
            self.dismiss("allow")

        def action_deny(self) -> None:
            self.dismiss("deny")

        def action_allow_run(self) -> None:
            self.dismiss("allow_run")

    class ModelPickerScreen(Screen):
        """First screen: pick a llama-server id or a GGUF before chat starts."""

        BINDINGS = [Binding("enter", "confirm", "Use selected", show=True)]

        def compose(self) -> ComposeResult:
            yield Header()
            with Vertical(id="models-wrap"):
                yield Static(
                    "[bold]Which model?[/]  llama-server ids first, then GGUFs on disk. "
                    "Type a path if it is not listed.",
                    classes="hint",
                )
                yield ListView(id="model-list")
                yield Input(placeholder="or type a GGUF path / model id", id="model-spec")
                with Horizontal(classes="row"):
                    yield Button("Use selected", id="use-model", variant="primary")
                    yield Button("Refresh", id="refresh-models")
                yield Static("", id="models-status")
            yield Footer()

        def on_mount(self) -> None:
            self._refill()
            self.query_one("#model-list", ListView).focus()

        def _refill(self) -> None:
            app: KuramaApp = self.app  # type: ignore[assignment]
            lv = self.query_one("#model-list", ListView)
            lv.clear()
            cfg = read_config(app.paths)
            choices = catalog(app.paths.root, cfg)
            for choice in choices:
                item = ListItem(Label(choice.label))
                item.choice = choice  # type: ignore[attr-defined]
                lv.append(item)
            if choices:
                lv.index = 0
            n_server = sum(1 for c in choices if c.kind == "server")
            n_disk = sum(1 for c in choices if c.kind == "gguf")
            self.query_one("#models-status", Static).update(
                f"llama-server: {n_server}  ·  GGUF on disk: {n_disk}  ·  pick one, then Use selected"
            )

        def _selected_choice(self) -> ModelChoice | None:
            app: KuramaApp = self.app  # type: ignore[assignment]
            typed = self.query_one("#model-spec", Input).value.strip()
            if typed:
                return choice_from_spec(typed, app.paths.root, read_config(app.paths))
            lv = self.query_one("#model-list", ListView)
            highlighted = lv.highlighted_child
            return getattr(highlighted, "choice", None) if highlighted else None

        def action_confirm(self) -> None:
            self._submit()

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "refresh-models":
                self._refill()
                return
            if event.button.id == "use-model":
                self._submit()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            if event.input.id == "model-spec":
                self._submit()

        def _submit(self) -> None:
            choice = self._selected_choice()
            if choice is None:
                self.query_one("#models-status", Static).update("Select a model or type a GGUF path.")
                return
            self.dismiss(choice)

    class ModelsScreen(Screen):
        BINDINGS = [Binding("escape", "app.pop_screen", "Back", show=True)]

        def compose(self) -> ComposeResult:
            yield Header()
            with Vertical(id="models-wrap"):
                yield Static(
                    "[bold]Models[/]  —  llama-server ids and GGUFs. Load to switch. Esc back.",
                    classes="hint",
                )
                yield ListView(id="gguf-list")
                yield Input(placeholder="or type a GGUF path / model id", id="switch-spec")
                with Horizontal(classes="row"):
                    yield Button("Load selected", id="load-gguf", variant="primary")
                    yield Button("Refresh", id="scan-gguf")
                    yield Button("Stop server", id="stop-server")
                    yield Button("Back", id="back")
                yield Static("", id="models-status")
            yield Footer()

        def on_mount(self) -> None:
            self._refill()

        def _refill(self) -> None:
            app: KuramaApp = self.app  # type: ignore[assignment]
            lv = self.query_one("#gguf-list", ListView)
            lv.clear()
            cfg = read_config(app.paths)
            choices = catalog(app.paths.root, cfg)
            for choice in choices:
                item = ListItem(Label(choice.label))
                item.choice = choice  # type: ignore[attr-defined]
                lv.append(item)
            n_server = sum(1 for c in choices if c.kind == "server")
            loaded = current_model_path() or "(none)"
            ids = ", ".join(list_models()) or "—"
            self.query_one("#models-status", Static).update(
                f"llama-server ids: {ids}  ·  listed {n_server} live / {len(choices)} total  ·  file: {loaded}"
            )

        def _selected_choice(self) -> ModelChoice | None:
            app: KuramaApp = self.app  # type: ignore[assignment]
            typed = self.query_one("#switch-spec", Input).value.strip()
            if typed:
                return choice_from_spec(typed, app.paths.root, read_config(app.paths))
            lv = self.query_one("#gguf-list", ListView)
            highlighted = lv.highlighted_child
            return getattr(highlighted, "choice", None) if highlighted else None

        def on_button_pressed(self, event: Button.Pressed) -> None:
            app: KuramaApp = self.app  # type: ignore[assignment]
            bid = event.button.id
            if bid == "back":
                self.app.pop_screen()
            elif bid == "scan-gguf":
                self._refill()
            elif bid == "stop-server":
                app.stop_server()
                self._refill()
            elif bid == "load-gguf":
                choice = self._selected_choice()
                if choice is None:
                    self.query_one("#models-status", Static).update("Select a model first.")
                    return
                app.apply_model(choice)
                self.app.pop_screen()

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            choice = getattr(event.item, "choice", None)
            if choice is not None:
                self.app.apply_model(choice)  # type: ignore[attr-defined]
                self.app.pop_screen()

    class ConfigScreen(Screen):
        BINDINGS = [Binding("escape", "app.pop_screen", "Back", show=True)]

        def compose(self) -> ComposeResult:
            yield Header()
            with VerticalScroll(id="config-wrap"):
                yield Static("[bold]Resources & agent settings[/]", classes="hint")
                yield Static("Load preset", classes="field-label")
                yield Input(placeholder="auto | cpu_optimal | balanced | max_context | fast_draft", id="cfg-preset")
                yield Static("num_ctx", classes="field-label")
                yield Input(id="cfg-num_ctx")
                yield Static("num_batch", classes="field-label")
                yield Input(id="cfg-num_batch")
                yield Static("num_gpu  (−1 = all layers)", classes="field-label")
                yield Input(id="cfg-num_gpu")
                yield Static("num_thread  (0 = auto)", classes="field-label")
                yield Input(id="cfg-num_thread")
                yield Static("max_tokens / step", classes="field-label")
                yield Input(id="cfg-max_tokens")
                yield Static("timeout_sec", classes="field-label")
                yield Input(id="cfg-timeout_sec")
                yield Static("temperature", classes="field-label")
                yield Input(id="cfg-temperature")
                yield Static("max_steps", classes="field-label")
                yield Input(id="cfg-max_steps")
                yield Checkbox("Warm up model before runs", id="cfg-warmup")
                yield Checkbox("Dry-run if server unreachable", id="cfg-dry-fallback")
                yield Checkbox("Require <think> block", id="cfg-require-think")
                yield Checkbox("Think must cite observed/inferred/speculative", id="cfg-think-cite")
                yield Checkbox("Skill tests required before use", id="cfg-skill-test")
                with Horizontal(classes="row"):
                    yield Button("Save", id="cfg-save", variant="primary")
                    yield Button("Auto-allocate this PC", id="cfg-auto")
                    yield Button("Back", id="cfg-back")
                yield Static("", id="cfg-status")
            yield Footer()

        def on_mount(self) -> None:
            self._load_fields()

        def _load_fields(self) -> None:
            app: KuramaApp = self.app  # type: ignore[assignment]
            data = read_config(app.paths)
            llm = data.get("llm") or {}
            self.query_one("#cfg-preset", Input).value = str(llm.get("load_preset") or "")
            self.query_one("#cfg-num_ctx", Input).value = str(llm.get("num_ctx") or "")
            self.query_one("#cfg-num_batch", Input).value = str(llm.get("num_batch") or "")
            self.query_one("#cfg-num_gpu", Input).value = str(llm.get("num_gpu") if llm.get("num_gpu") is not None else "")
            self.query_one("#cfg-num_thread", Input).value = str(llm.get("num_thread") if llm.get("num_thread") is not None else "")
            self.query_one("#cfg-max_tokens", Input).value = str(llm.get("max_tokens") or "")
            self.query_one("#cfg-timeout_sec", Input).value = str(llm.get("timeout_sec") or "")
            self.query_one("#cfg-temperature", Input).value = str(llm.get("temperature") or "")
            self.query_one("#cfg-max_steps", Input).value = str(data.get("max_steps") or "")
            self.query_one("#cfg-warmup", Checkbox).value = bool(llm.get("warmup", True))
            self.query_one("#cfg-dry-fallback", Checkbox).value = bool(llm.get("dry_run_on_no_server", True))
            self.query_one("#cfg-require-think", Checkbox).value = bool(data.get("require_think_block", True))
            self.query_one("#cfg-think-cite", Checkbox).value = bool(data.get("think_block_must_cite_source", True))
            self.query_one("#cfg-skill-test", Checkbox).value = bool(data.get("skill_test_required_before_use", True))

        def _int(self, widget_id: str) -> int | None:
            raw = self.query_one(widget_id, Input).value.strip()
            if raw == "":
                return None
            return int(float(raw))

        def _float(self, widget_id: str) -> float | None:
            raw = self.query_one(widget_id, Input).value.strip()
            if raw == "":
                return None
            return float(raw)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            app: KuramaApp = self.app  # type: ignore[assignment]
            bid = event.button.id
            status = self.query_one("#cfg-status", Static)
            if bid == "cfg-back":
                self.app.pop_screen()
                return
            if bid == "cfg-auto":
                try:
                    apply_preset(app.paths, "auto")
                    app.reload_loop("Applied auto resource profile")
                    self._load_fields()
                    status.update("Saved auto-allocation for this PC.")
                except Exception as exc:  # noqa: BLE001
                    status.update(f"Auto-allocate failed: {exc}")
                return
            if bid != "cfg-save":
                return
            try:
                llm: dict[str, Any] = {}
                preset = self.query_one("#cfg-preset", Input).value.strip()
                if preset:
                    llm["load_preset"] = preset
                for key, wid in (
                    ("num_ctx", "#cfg-num_ctx"),
                    ("num_batch", "#cfg-num_batch"),
                    ("num_gpu", "#cfg-num_gpu"),
                    ("num_thread", "#cfg-num_thread"),
                    ("max_tokens", "#cfg-max_tokens"),
                    ("timeout_sec", "#cfg-timeout_sec"),
                ):
                    val = self._int(wid)
                    if val is not None:
                        llm[key] = val
                temp = self._float("#cfg-temperature")
                if temp is not None:
                    llm["temperature"] = temp
                llm["warmup"] = self.query_one("#cfg-warmup", Checkbox).value
                llm["dry_run_on_no_server"] = self.query_one("#cfg-dry-fallback", Checkbox).value
                top: dict[str, Any] = {
                    "require_think_block": self.query_one("#cfg-require-think", Checkbox).value,
                    "think_block_must_cite_source": self.query_one("#cfg-think-cite", Checkbox).value,
                    "skill_test_required_before_use": self.query_one("#cfg-skill-test", Checkbox).value,
                }
                steps = self._int("#cfg-max_steps")
                if steps is not None:
                    top["max_steps"] = steps
                merge_config(app.paths, top=top, llm=llm)
                app.reload_loop("Config saved")
                status.update("Saved reasoning_config.json")
            except Exception as exc:  # noqa: BLE001
                status.update(f"Save failed: {exc}")

    class KuramaApp(App):
        TITLE = "Kurama"
        CSS = """
        Screen { background: #120e0a; color: #f5efe6; }
        Header { background: #1a1410; color: #F59E0B; }
        Footer { background: #1a1410; }
        #status {
            dock: top;
            height: 1;
            padding: 0 1;
            color: #c4b5a0;
            background: #1a1410;
        }
        #chat {
            height: 1fr;
            padding: 0 1;
            scrollbar-gutter: stable;
        }
        ChatMessage {
            margin: 0 0 1 0;
            padding: 1;
            background: #1c1612;
            border: tall #2a2118;
        }
        ChatMessage.user { border: tall #1e3a5f; }
        ChatMessage.agent { border: tall #5c3d12; }
        #composer {
            dock: bottom;
            height: auto;
            padding: 0 1 1 1;
        }
        #prompt {
            width: 1fr;
            background: #1c1612;
            border: tall #F59E0B;
        }
        PermissionModal { align: center middle; }
        #perm-dialog {
            width: 72;
            height: auto;
            max-height: 28;
            padding: 1 2;
            background: #1c1612;
            border: thick #F59E0B;
        }
        #perm-buttons { height: auto; padding-top: 1; }
        #perm-buttons Button { margin-right: 1; }
        #models-wrap, #config-wrap { padding: 1 2; }
        .hint { color: #c4b5a0; margin-bottom: 1; }
        .field-label { color: #F59E0B; margin-top: 1; }
        .row { height: auto; padding: 1 0; }
        .row Button { margin-right: 1; }
        ListView { height: 14; background: #1c1612; border: tall #2a2118; }
        """
        BINDINGS = [
            Binding("f2", "show_models", "Models"),
            Binding("f3", "show_config", "Config"),
            Binding("escape", "cancel_run", "Stop"),
        ]

        def __init__(
            self,
            paths: AgentPaths,
            *,
            dry_run: bool = False,
            max_steps: int | None = None,
            model: str | None = None,
        ) -> None:
            super().__init__()
            self.paths = paths
            self.dry_run = dry_run
            self.max_steps = max_steps
            self.preset_model = (model or "").strip() or None
            self.loop: AgentLoop | None = None
            self.messages: list[Message] = []
            self._live: ChatMessage | None = None
            self._splitter = StreamSplitter()
            self._busy = False
            self._status = "Starting…"
            self._model_ready = False
            self._perm_done: threading.Event | None = None
            self._perm_box: dict[str, str] = {}

        def compose(self) -> ComposeResult:
            yield Header()
            yield Static("Starting…", id="status")
            yield VerticalScroll(id="chat")
            with Horizontal(id="composer"):
                yield Input(placeholder="Ask Kurama  ·  /help for commands", id="prompt")
            yield Footer()

        def on_mount(self) -> None:
            self.messages = load_history(self.paths.db)
            chat = self.query_one("#chat", VerticalScroll)
            if not self.messages:
                intro = Message(
                    who="Kurama",
                    text="Hey — I'm Kurama. Give me a goal and I'll loop perceive → think → act. I'll ask before any tool.",
                    is_agent=True,
                )
                self.messages.append(intro)
            for b in self.messages:
                chat.mount(ChatMessage(b))
            self.query_one("#prompt", Input).focus()
            if self.dry_run:
                self.set_status("Dry-run  ·  no llama-server")
                self.run_worker(self._bootstrap, thread=True, exclusive=True, group="boot")
                return
            if self.preset_model:
                self.set_status(f"Loading {self.preset_model}…")
                self.run_worker(
                    lambda: self._apply_then_boot(
                        choice_from_spec(self.preset_model, self.paths.root, read_config(self.paths))
                    ),
                    thread=True,
                    exclusive=True,
                    group="boot",
                )
                return
            self.set_status("Pick a model to start")
            self.push_screen(ModelPickerScreen(), self._on_model_picked)

        def set_status(self, text: str) -> None:
            self._status = text
            try:
                self.query_one("#status", Static).update(text)
            except Exception:  # noqa: BLE001
                pass
            self.sub_title = text

        def _on_model_picked(self, choice: ModelChoice | None) -> None:
            if choice is None:
                self.set_status("No model selected — F2 to pick one")
                return
            self.set_status(f"Loading {choice.label}…")
            self.run_worker(lambda: self._apply_then_boot(choice), thread=True, exclusive=True, group="boot")

        def apply_model(self, choice: ModelChoice) -> None:
            if self._busy:
                self.set_status("Wait for the current run to finish")
                return
            self.set_status(f"Loading {choice.label}…")
            self.run_worker(lambda: self._apply_then_boot(choice), thread=True, exclusive=True, group="boot")

        def _apply_then_boot(self, choice: ModelChoice | None) -> None:
            if choice is None:
                self.call_from_thread(self.set_status, "No model selected")
                return
            try:
                cfg = load_config(self.paths)
                boot = apply_choice(self.paths, choice, cfg)
                if not boot.get("ok"):
                    tail = str(boot.get("log") or "").replace("\n", " ")[-160:]
                    msg = f"Model load failed: {boot.get('error')}"
                    if tail:
                        msg = f"{msg} | {tail}"
                    self.call_from_thread(self.set_status, msg)
                    return
                self._model_ready = True
                self.call_from_thread(self.reload_loop, self._ready_note(boot, choice))
            except Exception as exc:  # noqa: BLE001
                log.exception("model apply failed")
                self.call_from_thread(self.set_status, f"Model load failed: {exc}")

        def _ready_note(self, boot: dict, choice: ModelChoice) -> str:
            mid = str(boot.get("model_id") or choice.model_id)
            where = "llama-server" if boot.get("started") or boot.get("choice") == "server" else "ready"
            return f"{where}  ·  {mid}"

        def _bootstrap(self) -> None:
            try:
                self.loop = create_loop(
                    self.paths,
                    on_token=self._on_token,
                    on_permission=self._ask_permission,
                )
                if self.dry_run:
                    self.call_from_thread(self.set_status, "Dry-run  ·  no llama-server")
                    return
                llm = (self.loop.cfg.get("llm") or {}) if self.loop else {}
                model = Path(current_model_path() or str(llm.get("model") or "")).name or "idle"
                ctx = llm.get("num_ctx")
                self.call_from_thread(self.set_status, f"ready  ·  {model}  ·  ctx {ctx}")
            except Exception as exc:  # noqa: BLE001
                log.exception("bootstrap failed")
                self.call_from_thread(self.set_status, f"Startup error: {exc}")

        def reload_loop(self, note: str) -> None:
            self.loop = create_loop(
                self.paths,
                on_token=self._on_token,
                on_permission=self._ask_permission,
                previous=self.loop,
            )
            self.set_status(note)

        def _on_token(self, tok: str) -> None:
            view = self._splitter.feed(tok)
            self.call_from_thread(self.render_stream, view)

        def render_stream(self, view: StreamView) -> None:
            if self._live is None:
                return
            self._live.message.thinking = view.thinking
            self._live.message.text = view.answer
            self._live.refresh_body()
            self.query_one("#chat", VerticalScroll).scroll_end(animate=False)

        def _ask_permission(self, request: dict[str, Any]) -> str:
            self._perm_box = {}
            self._perm_done = threading.Event()
            done = self._perm_done
            box = self._perm_box

            def on_done(decision: str | None) -> None:
                box["v"] = decision or DENY
                done.set()

            def open_modal() -> None:
                self.push_screen(PermissionModal(request), on_done)

            self.call_from_thread(open_modal)
            if not done.wait(timeout=3600):
                return DENY
            return box.get("v") or DENY

        def save_chat(self) -> None:
            try:
                save_history(self.paths.db, self.messages)
            except OSError as exc:
                log.warning("chat history save failed: %s", exc)

        def append_message(self, message: Message) -> ChatMessage:
            self.messages.append(message)
            widget = ChatMessage(message)
            self.query_one("#chat", VerticalScroll).mount(widget)
            self.query_one("#chat", VerticalScroll).scroll_end(animate=False)
            return widget

        def on_input_submitted(self, event: Input.Submitted) -> None:
            text = (event.value or "").strip()
            event.input.value = ""
            if not text:
                return
            if text.startswith("/"):
                self._command(text)
                return
            if self._busy:
                self.set_status("Already running — Esc to stop")
                return
            self._start_turn(text)

        def _command(self, raw: str) -> None:
            parts = raw.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""
            if cmd in ("/help", "/?"):
                self.append_message(Message(who="Kurama", text=HELP_TEXT, is_agent=True))
            elif cmd in ("/quit", "/exit", "/q"):
                self.exit()
            elif cmd == "/clear":
                self.messages.clear()
                chat = self.query_one("#chat", VerticalScroll)
                chat.remove_children()
                self.append_message(
                    Message(who="Kurama", text="Chat cleared. History wiped for this machine only.", is_agent=True)
                )
                self.save_chat()
                self.set_status("Chat cleared")
            elif cmd == "/models":
                self.push_screen(ModelsScreen())
            elif cmd == "/config":
                self.push_screen(ConfigScreen())
            elif cmd == "/stop":
                self.action_cancel_run()
            elif cmd == "/stop-server":
                self.stop_server()
            elif cmd == "/dry-run":
                self.dry_run = not self.dry_run
                self.set_status("Dry-run ON" if self.dry_run else "Dry-run OFF")
            elif cmd == "/preset":
                name = arg or "cpu_optimal"
                if name not in preset_names():
                    self.append_message(
                        Message(
                            who="Kurama",
                            text=f"Unknown preset {name!r}. Try: {', '.join(preset_names())}",
                            is_agent=True,
                        )
                    )
                    return
                try:
                    apply_preset(self.paths, name)
                    self.reload_loop(f"Applied preset {name}")
                    self.append_message(Message(who="Kurama", text=f"Applied preset `{name}`.", is_agent=True))
                except Exception as exc:  # noqa: BLE001
                    self.append_message(Message(who="Kurama", text=f"Preset failed: {exc}", is_agent=True))
            elif cmd == "/load":
                if arg:
                    choice = choice_from_spec(arg, self.paths.root, read_config(self.paths))
                    if choice is None:
                        self.append_message(
                            Message(who="Kurama", text=f"No model matched {arg!r}.", is_agent=True)
                        )
                    else:
                        self.apply_model(choice)
                else:
                    self.push_screen(ModelsScreen())
            elif cmd == "/reward":
                self._reward()
            else:
                self.append_message(Message(who="Kurama", text=f"Unknown command {cmd}. /help for the list.", is_agent=True))
            self.save_chat()

        def _reward(self) -> None:
            if self.loop is None:
                self.set_status("Not ready")
                return
            try:
                out = reward_last_run(self.loop.cfg, self.paths.db, note="tui /reward", memory=self.loop.memory)
                msg = str(out.get("message") or out)
                self.append_message(Message(who="Kurama", text=msg, is_agent=True))
                self.set_status("Reward saved" if out.get("ok") else "Reward failed")
            except Exception as exc:  # noqa: BLE001
                self.append_message(Message(who="Kurama", text=f"Reward error: {exc}", is_agent=True))

        def _unlock_prompt(self) -> None:
            self._busy = False
            try:
                prompt = self.query_one("#prompt", Input)
                prompt.disabled = False
                prompt.focus()
            except Exception:  # noqa: BLE001
                pass

        def _start_turn(self, goal: str) -> None:
            self._busy = True
            self.append_message(Message(who="You", text=goal, is_agent=False))
            live = Message(who="Kurama", text="", thinking="", is_agent=True)
            self._live = self.append_message(live)
            self._splitter = StreamSplitter()
            self.save_chat()
            self.set_status("Thinking…" if not self.dry_run else "Dry-run…")
            self.query_one("#prompt", Input).focus()
            self.run_worker(lambda: self._run_turn(goal), thread=True, exclusive=True, group="run")

        def _run_turn(self, goal: str) -> None:
            result: dict[str, Any] = {"ok": False, "error": "run_failed"}
            try:
                if self.loop is None:
                    result = {"ok": False, "error": "Not ready — still starting."}
                else:
                    self.loop.on_token = self._on_token
                    self.loop.on_permission = self._ask_permission
                    try:
                        raw = self.loop.run(goal, dry_run=self.dry_run, max_steps=self.max_steps)
                        result = raw if isinstance(raw, dict) else {"ok": False, "error": str(raw)}
                    except Exception as exc:  # noqa: BLE001
                        log.exception("run failed")
                        result = {"ok": False, "error": str(exc)}
                view = self._splitter.finalize()
                summary = finish_summary(result)
                if summary:
                    view = StreamView(view.thinking, summary, False, "Thought")
                elif result.get("error") == "cancelled":
                    view = StreamView(view.thinking, view.answer or "Generation stopped.", False, "Thought")
                elif not result.get("ok") and result.get("error"):
                    view = StreamView(view.thinking, view.answer or str(result.get("error")), False, "Thought")
                self.call_from_thread(self._finish_turn, view, result)
            except Exception as exc:  # noqa: BLE001
                log.exception("turn worker crashed")
                self.call_from_thread(
                    self._finish_turn,
                    StreamView("", str(exc), False, "Thought"),
                    {"ok": False, "error": str(exc)},
                )

        def _finish_turn(self, view: StreamView, result: dict[str, Any]) -> None:
            try:
                self.render_stream(view)
                if self._live is not None:
                    self._live.message.thinking = view.thinking
                    self._live.message.text = view.answer or (
                        "Done." if result.get("ok") else str(result.get("error") or "Failed")
                    )
                    self._live.refresh_body()
            except Exception:  # noqa: BLE001
                log.exception("failed to render turn")
            self._live = None
            self._unlock_prompt()
            self.save_chat()
            llm = {}
            if self.loop is not None:
                llm = self.loop.cfg.get("llm") or {}
            model = Path(current_model_path() or str(llm.get("model") or "")).name or "idle"
            ok = result.get("ok")
            steps = result.get("steps")
            suffix = f"  ·  {steps} steps" if steps is not None else ""
            if self.dry_run:
                self.set_status(f"Idle  ·  dry-run{suffix}")
            elif ok:
                self.set_status(f"Idle  ·  {model}{suffix}")
            else:
                self.set_status(f"{result.get('error') or 'error'}{suffix}")

        def action_cancel_run(self) -> None:
            if self.loop is not None:
                self.loop.cancel_requested = True
            if self._perm_done is not None:
                self._perm_box["v"] = DENY
                self._perm_done.set()
                try:
                    if isinstance(self.screen, ModalScreen):
                        self.pop_screen()
                except Exception:  # noqa: BLE001
                    pass
            try:
                self.workers.cancel_group("run")
            except Exception:  # noqa: BLE001
                pass
            self._unlock_prompt()
            if self._live is not None:
                self.set_status("Stopped — type to continue")

        def action_show_models(self) -> None:
            self.push_screen(ModelsScreen())

        def action_show_config(self) -> None:
            self.push_screen(ConfigScreen())

        def stop_server(self) -> None:
            stop_llama()
            cfg = read_config(self.paths)
            ls = cfg.get("llama_server") or {}
            port = int(ls.get("port") or 8080)
            host = str(ls.get("host") or "127.0.0.1")
            if is_healthy(f"http://{host}:{port}/v1"):
                self.set_status("Stop incomplete — port still in use")
            else:
                self.set_status("llama.cpp stopped")

        def load_gguf(self, path: Path) -> None:
            choice = choice_from_spec(str(path), self.paths.root, read_config(self.paths))
            if choice is None:
                self.set_status(f"GGUF not found: {path}")
                return
            self.apply_model(choice)

        def on_unmount(self) -> None:
            if self.loop is not None:
                self.loop.cancel_requested = True
                try:
                    self.loop.close()
                except Exception:  # noqa: BLE001
                    pass
            if not self.dry_run:
                stop_llama()

    paths = AgentPaths.discover()
    return KuramaApp(paths, dry_run=dry_run, max_steps=max_steps, model=model)
