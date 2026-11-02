"""Small, inspectable behavior-tree engine with stateful sequencing and decorators."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from .model import Context, Status


class Node(ABC):
    @abstractmethod
    def tick(self, context: Context) -> Status: ...

    def reset(self) -> None:
        pass


class Sequence(Node):
    """Memory sequence: resumes the currently RUNNING child."""
    def __init__(self, *children: Node):
        self.children = children
        self.current = 0

    def tick(self, context: Context) -> Status:
        while self.current < len(self.children):
            result = self.children[self.current].tick(context)
            if result is Status.SUCCESS:
                self.current += 1
            else:
                return result
        return Status.SUCCESS

    def reset(self) -> None:
        self.current = 0
        for child in self.children:
            child.reset()


class Selector(Node):
    """Fallback sequence: returns on first success or running child."""
    def __init__(self, *children: Node):
        self.children = children
        self.current = 0

    def tick(self, context: Context) -> Status:
        while self.current < len(self.children):
            result = self.children[self.current].tick(context)
            if result is Status.FAILURE:
                self.current += 1
            else:
                return result
        return Status.FAILURE

    def reset(self) -> None:
        self.current = 0
        for child in self.children:
            child.reset()


class Condition(Node):
    def __init__(self, label: str, predicate: Callable[[Context], bool]):
        self.label, self.predicate = label, predicate

    def tick(self, context: Context) -> Status:
        passed = self.predicate(context)
        if not passed:
            context.last_error = f"condition failed: {self.label}"
            context.emit("CONDITION_FAILED", condition=self.label)
        return Status.SUCCESS if passed else Status.FAILURE


class Retry(Node):
    """Up to max_attempts, including the first; failure restarts on next tick."""
    def __init__(self, label: str, child: Node, max_attempts: int = 3):
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.label, self.child, self.max_attempts = label, child, max_attempts
        self.failures = 0

    def tick(self, context: Context) -> Status:
        result = self.child.tick(context)
        if result is not Status.FAILURE:
            return result
        self.failures += 1
        if self.failures >= self.max_attempts:
            context.emit("RETRIES_EXHAUSTED", action=self.label, attempts=self.failures)
            return Status.FAILURE
        context.recorder.increment("retries")
        context.emit("RETRY_SCHEDULED", action=self.label, next_attempt=self.failures + 1)
        self.child.reset()
        return Status.RUNNING

    def reset(self) -> None:
        self.failures = 0
        self.child.reset()


class Timeout(Node):
    """Tick-based deadline; no wall clock, so replay is deterministic."""
    def __init__(self, label: str, child: Node, max_ticks: int = 30):
        if max_ticks < 1:
            raise ValueError("max_ticks must be positive")
        self.label, self.child, self.max_ticks = label, child, max_ticks
        self.used = 0

    def tick(self, context: Context) -> Status:
        self.used += 1
        result = self.child.tick(context)
        if result is Status.RUNNING and self.used >= self.max_ticks:
            context.recorder.increment("timeouts")
            context.last_error = f"{self.label} timed out after {self.used} ticks"
            context.emit("SKILL_TIMEOUT", action=self.label, budget=self.max_ticks)
            self.child.reset()
            return Status.FAILURE
        return result

    def reset(self) -> None:
        self.used = 0
        self.child.reset()
