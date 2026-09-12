"""In-process event bus.

Components never read each other's private state. They send Events; the
agent loop is the clock that fires them each turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class Event:
    """One message on the bus.

    channel:
      input     — user, tool result, recalled memory
      output    — dispatch an action
      control   — bounds, energy, identity, confirmation
      internal  — packet, relay
      feedback  — reflection, observation update, halt
    """

    channel: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    step: int = 0
    energy_cost: float = 0.0
    source: str = ""
    target: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "kind": self.kind,
            "step": self.step,
            "source": self.source,
            "target": self.target,
            "energy_cost": self.energy_cost,
            "payload_keys": sorted(self.payload.keys()),
        }


@runtime_checkable
class Component(Protocol):
    name: str

    def receive(self, event: Event) -> list[Event]:
        """Handle one event. Return zero or more events to put back on the bus."""
        ...


class EventBus:
    """Every component on one bus; recent events kept as a short trace."""

    def __init__(self) -> None:
        self._components: dict[str, Component] = {}
        self.trace: list[Event] = []
        self._trace_cap = 240

    def attach(self, component: Component) -> None:
        self._components[component.name] = component

    def get(self, name: str) -> Component | None:
        return self._components.get(name)

    @property
    def component_names(self) -> list[str]:
        return sorted(self._components)

    def fire(self, event: Event, *, target: str | None = None) -> list[Event]:
        """Deliver an event. target=None broadcasts to every component."""
        dest = target or event.target or None
        self._record(event)
        replies: list[Event] = []
        if dest:
            component = self._components.get(dest)
            if component is None:
                return []
            for reply in component.receive(event) or []:
                self._record(reply)
                replies.append(reply)
            return replies
        for component in self._components.values():
            for reply in component.receive(event) or []:
                self._record(reply)
                replies.append(reply)
        return replies

    def last(self, kind: str | None = None, *, channel: str | None = None) -> Event | None:
        for item in reversed(self.trace):
            if kind is not None and item.kind != kind:
                continue
            if channel is not None and item.channel != channel:
                continue
            return item
        return None

    def _record(self, event: Event) -> None:
        self.trace.append(event)
        if len(self.trace) > self._trace_cap:
            self.trace = self.trace[-self._trace_cap :]
