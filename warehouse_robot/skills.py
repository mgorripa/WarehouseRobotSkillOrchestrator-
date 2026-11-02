"""Robot-skill API: navigate, grasp, place, and delivery verification.

All operations act on discrete simulated state, not actual robot hardware.
"""
from __future__ import annotations
from .model import Cell, Context, Status
from .navigation import route
from .tree import Node


class Navigate(Node):
    def __init__(self, label: str, destination: str):
        assert destination in ("pickup", "dropoff")
        self.label, self.destination = label, destination
        self.path: list[Cell] | None = None
        self.started = False

    def tick(self, context: Context) -> Status:
        target: Cell = getattr(context.mission, self.destination)
        if not self.started:
            self.started = True
            context.emit("SKILL_START", action=self.label, target=list(target))
            if context.attempt(self.label):
                return Status.FAILURE
        if context.robot.location == target:
            context.emit("SKILL_SUCCESS", action=self.label)
            return Status.SUCCESS
        if self.path is None or not self.path or not context.world.open(self.path[0]):
            if self.path is not None:
                context.recorder.increment("replans")
                context.emit("ROUTE_REPLAN", action=self.label)
            self.path = route(context.world, context.robot.location, target)
            if self.path is None:
                context.last_error = f"unreachable {self.destination}: {target}"
                context.emit("SKILL_FAILURE", action=self.label, reason=context.last_error)
                return Status.FAILURE
        if context.robot.battery < 1:
            context.recorder.increment("safety_stops")
            context.last_error = "battery depleted; safe stop"
            context.emit("SAFETY_STOP", reason=context.last_error)
            return Status.FAILURE
        step = self.path.pop(0)
        context.robot.location = step
        context.robot.battery -= 1
        context.robot.steps += 1
        context.recorder.increment("robot_steps")
        context.emit("NAV_STEP", action=self.label, location=list(step), battery=context.robot.battery)
        if step == target:
            context.emit("SKILL_SUCCESS", action=self.label)
            return Status.SUCCESS
        return Status.RUNNING

    def reset(self) -> None:
        self.path = None
        self.started = False


class Grasp(Node):
    def tick(self, context: Context) -> Status:
        context.emit("SKILL_START", action="grasp")
        if context.robot.location != context.mission.pickup:
            context.last_error = "not at pickup location"
        elif context.robot.carrying is not None:
            context.last_error = "already carrying an object"
        elif context.world.objects.get(context.mission.object_id) != context.mission.pickup:
            context.last_error = "object missing at pickup location"
        elif context.attempt("grasp"):
            return Status.FAILURE
        else:
            del context.world.objects[context.mission.object_id]
            context.robot.carrying = context.mission.object_id
            context.emit("GRASP_OK", object_id=context.mission.object_id)
            return Status.SUCCESS
        context.emit("SKILL_FAILURE", action="grasp", reason=context.last_error)
        return Status.FAILURE


class Place(Node):
    def tick(self, context: Context) -> Status:
        context.emit("SKILL_START", action="place")
        if context.robot.location != context.mission.dropoff:
            context.last_error = "not at dropoff location"
        elif context.robot.carrying != context.mission.object_id:
            context.last_error = "not carrying requested object"
        elif context.attempt("place"):
            return Status.FAILURE
        else:
            context.robot.carrying = None
            context.world.delivered[context.mission.object_id] = context.mission.dropoff
            context.emit("PLACE_OK", object_id=context.mission.object_id)
            return Status.SUCCESS
        context.emit("SKILL_FAILURE", action="place", reason=context.last_error)
        return Status.FAILURE


class Verify(Node):
    def tick(self, context: Context) -> Status:
        correct = (
            context.world.delivered.get(context.mission.object_id) == context.mission.dropoff
            and context.robot.carrying is None
        )
        if correct:
            context.emit("VERIFICATION_PASSED", object_id=context.mission.object_id)
            return Status.SUCCESS
        context.last_error = "post-placement verification failed"
        context.emit("VERIFICATION_FAILED", reason=context.last_error)
        return Status.FAILURE
