"""Runtime-discoverable action types, each with a JSON Schema for its options.

The reference API's ``options`` payload is untyped at the transport level; here
each factory carries a JSON Schema, and ``ActionRegistry.validate_params``
reports the offending field by path.
"""

from __future__ import annotations

import collections.abc
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Type

from g1_api.errors import NotFoundError, ValidationError
from g1_api.models.action import (
    ACTION_NAME_BY_KIND,
    CANONICAL_ACTION_KINDS,
    PARAMS_BY_KIND,
    REQUIRED_SAFETY_FLAG_BY_KIND,
    ActionKind,
    ActionParams,
    params_from_dict,
)
from g1_api.core.safety import CommandClass, command_class_for_action_kind

__all__ = [
    "ActionTypeInfo",
    "ActionRegistry",
    "build_default_action_registry",
    "AVOIDS_OBSTACLES_BY_KIND",
    "ACTION_DESCRIPTIONS",
]

AVOIDS_OBSTACLES_BY_KIND: Mapping[ActionKind, Optional[bool]] = {
    ActionKind.MOVE: False,        # teleop -- the reference warns it does not avoid obstacles
    ActionKind.MOVE_TO: True,
    ActionKind.ROTATE: False,
    ActionKind.ROTATE_TO: False,
}

ACTION_DESCRIPTIONS: Mapping[ActionKind, str] = {
    ActionKind.MOVE: (
        "Velocity teleop for a bounded duration. DOES NOT AVOID OBSTACLES -- the "
        "most dangerous action. Deadzone vx/vy 0.2, omega 0.3."
    ),
    ActionKind.MOVE_TO: (
        "Navigate to a target pose with obstacle avoidance. with_yaw is a required "
        "explicit boolean: False ignores the yaw (Slamtec semantics, no flag trap)."
    ),
    ActionKind.ROTATE: "Rotate in place by a RELATIVE angle (CCW positive).",
    ActionKind.ROTATE_TO: "Rotate in place to an ABSOLUTE heading (yaw).",
}


def _move_schema() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "description": (
            "TELEOPERATION. DOES NOT AVOID OBSTACLES. Duration is bounded (default "
            "ceiling 3000 ms); if every commanded velocity is below its deadzone "
            "(vx/vy 0.2, omega 0.3) the command would produce no motion and is "
            "rejected with 400."
        ),
        "properties": {
            "vx": {"type": "number", "description": "Forward velocity, m/s."},
            "vy": {"type": "number", "description": "Lateral velocity, m/s."},
            "omega": {"type": "number", "description": "Yaw rate, rad/s, CCW positive."},
            "duration_ms": {
                "type": "integer",
                "exclusiveMinimum": 0,
                "maximum": 3000,
                "default": 500,
                "description": "How long to drive, ms. Bounded and short by design.",
            },
        },
        "required": ["vx", "vy", "omega", "duration_ms"],
    }


def _move_to_schema() -> Dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "description": "Navigate to a target point. with_yaw is REQUIRED and explicit.",
        "properties": {
            "target": {
                "type": "object",
                "additionalProperties": False,
                "required": ["x", "y"],
                "properties": {
                    "x": {"type": "number", "description": "X in map frame, metres."},
                    "y": {"type": "number", "description": "Y in map frame, metres."},
                },
            },
            "yaw": {"type": "number", "description": "Goal heading, radians. Ignored if with_yaw=false."},
            "with_yaw": {
                "type": "boolean",
                "description": "REQUIRED. false => ignore yaw (Slamtec semantics, no flag trap).",
            },
            "reach_threshold": {"type": "number", "exclusiveMinimum": 0, "default": 0.35},
            "yaw_threshold": {"type": "number", "exclusiveMinimum": 0, "default": 0.5,
                               "description": "Arrival heading tolerance, radians (rotate-align phase)."},
            "timeout_s": {"type": "number", "exclusiveMinimum": 0, "default": 120.0},
        },
        "required": ["target", "with_yaw"],
    }


def _rotate_schema(absolute: bool) -> Dict[str, Any]:
    key = "yaw" if absolute else "angle"
    meaning = (
        "ABSOLUTE target heading, radians, CCW positive."
        if absolute
        else "RELATIVE rotation angle, radians, CCW positive."
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "description": (
            "%s Rotate is RELATIVE, RotateTo is ABSOLUTE -- the same field, "
            "opposite meaning in the reference API." % (meaning,)
        ),
        "properties": {key: {"type": "number", "description": meaning}},
        "required": [key],
    }


def _params_schema_for(kind: ActionKind) -> Dict[str, Any]:
    if kind is ActionKind.MOVE:
        return _move_schema()
    if kind is ActionKind.MOVE_TO:
        return _move_to_schema()
    if kind is ActionKind.ROTATE:
        return _rotate_schema(False)
    if kind is ActionKind.ROTATE_TO:
        return _rotate_schema(True)
    return {"type": "object", "description": "This action takes no parameters."}


# -- a minimal, dependency-free JSON Schema subset validator -----------------

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, (list, tuple)),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
}


def _fail(path: str, message: str, got: Any = None, expected: Optional[str] = None) -> None:
    raise ValidationError(
        "%s: %s" % (path, message),
        field=path,
        expected=expected,
        got=got,
        source="core.registry",
    )


def _validate(value: Any, schema: Dict[str, Any], path: str) -> None:
    declared = schema.get("type")
    if declared is not None:
        options = declared if isinstance(declared, list) else [declared]
        if not any(_TYPE_CHECKS.get(name, lambda _v: True)(value) for name in options):
            _fail(path, "must be %s" % (" or ".join(options),), got=value)
    if "enum" in schema and value is not None and value not in schema["enum"]:
        _fail(path, "must be one of %s" % (", ".join(str(a) for a in schema["enum"]),), got=value)
    if value is None:
        return
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        for name in schema.get("required", ()):
            if name not in value or value[name] is None:
                _fail("%s.%s" % (path, name), "is required")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                _fail(path, "has unknown field(s) %s" % (", ".join(unknown),), got=unknown)
        for name, sub_schema in properties.items():
            if name in value:
                _validate(value[name], sub_schema, "%s.%s" % (path, name))
        return
    if isinstance(value, (list, tuple)):
        if "minItems" in schema and len(value) < schema["minItems"]:
            _fail(path, "needs at least %d item(s)" % (schema["minItems"],), got=len(value))
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                _validate(item, item_schema, "%s[%d]" % (path, index))
        return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            _fail(path, "must be >= %s" % (schema["minimum"],), got=value)
        if "maximum" in schema and value > schema["maximum"]:
            _fail(path, "must be <= %s" % (schema["maximum"],), got=value)
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            _fail(path, "must be > %s" % (schema["exclusiveMinimum"],), got=value)


@dataclass(frozen=True)
class ActionTypeInfo:
    kind: ActionKind
    name: str
    compat_action_name: str
    params_schema: Dict[str, Any]
    command_class: CommandClass
    required_flag: str
    moves_robot: bool
    avoids_obstacles: Optional[bool]
    params_class: Type[ActionParams]
    description: str = ""

    def to_dict(self, include_schema: bool = True) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "action_name": self.compat_action_name,
            "kind": self.name,
            "description": self.description,
            "command_class": str(self.command_class),
            "required_capability_flag": self.required_flag,
            "moves_robot": self.moves_robot,
            "avoids_obstacles": self.avoids_obstacles,
        }
        if include_schema:
            out["options_schema"] = self.params_schema
        return out


class ActionRegistry(object):
    def __init__(self, entries: Optional[Iterable[ActionTypeInfo]] = None) -> None:
        self._by_kind: Dict[ActionKind, ActionTypeInfo] = {}
        self._by_name: Dict[str, ActionTypeInfo] = {}
        for entry in entries or ():
            self.register(entry)

    def register(self, entry: ActionTypeInfo) -> ActionTypeInfo:
        if entry.kind in self._by_kind:
            raise ValidationError(
                "action kind %r is already registered" % (str(entry.kind),),
                field="kind",
                source="core.registry",
            )
        self._by_kind[entry.kind] = entry
        self._by_name[entry.compat_action_name] = entry
        self._by_name[entry.name] = entry
        return entry

    def __len__(self) -> int:
        return len(self._by_kind)

    def kinds(self) -> Tuple[ActionKind, ...]:
        return tuple(self._by_kind)

    def get(self, kind: ActionKind) -> ActionTypeInfo:
        entry = self._by_kind.get(ActionKind(kind))
        if entry is None:
            raise NotFoundError(
                "unknown action type %r" % (getattr(kind, "value", kind),),
                resource="action_type",
                identifier=getattr(kind, "value", kind),
                source="core.registry",
                details={"available": sorted(self._by_name)},
            )
        return entry

    def get_by_action_name(self, action_name: str) -> ActionTypeInfo:
        entry = self._by_name.get(str(action_name))
        if entry is None:
            raise NotFoundError(
                "unknown action factory %r" % (action_name,),
                resource="action_factory",
                identifier=action_name,
                source="core.registry",
                details={"available": sorted(set(self._by_name))},
            )
        return entry

    def list_factories(self, include_schema: bool = True) -> List[Dict[str, Any]]:
        return [
            entry.to_dict(include_schema)
            for entry in self._by_kind.values()
        ]

    def validate_params(self, kind: ActionKind, params: Any = None) -> Dict[str, Any]:
        entry = self.get(kind)
        if isinstance(params, ActionParams):
            # Already materialised and typed; the wire schema was validated when
            # the raw options were first seen. Re-validating ``to_dict()`` would
            # reject canonical-only keys (e.g. ``absolute`` on rotate).
            return params.to_dict()
        if params is None:
            data = {}
        elif isinstance(params, collections.abc.Mapping):
            data = dict(params)
        else:
            raise ValidationError(
                "options must be an object, got %s" % (type(params).__name__,),
                field="options",
                source="core.registry",
            )
        _validate(data, entry.params_schema, "options")
        typed = params_from_dict(entry.kind, data)
        return typed.to_dict()

    def parse_params(self, kind: ActionKind, params: Any = None) -> ActionParams:
        entry = self.get(kind)
        normalised = self.validate_params(kind, params)
        return params_from_dict(entry.kind, normalised)


def build_default_action_registry() -> ActionRegistry:
    registry = ActionRegistry()
    for kind in CANONICAL_ACTION_KINDS:
        registry.register(
            ActionTypeInfo(
                kind=kind,
                name=str(kind),
                compat_action_name=ACTION_NAME_BY_KIND[kind],
                params_schema=_params_schema_for(kind),
                command_class=command_class_for_action_kind(kind),
                required_flag=REQUIRED_SAFETY_FLAG_BY_KIND.get(kind, "allow_navigation"),
                moves_robot=True,
                avoids_obstacles=AVOIDS_OBSTACLES_BY_KIND.get(kind),
                params_class=PARAMS_BY_KIND[kind],
                description=ACTION_DESCRIPTIONS.get(kind, ""),
            )
        )
    return registry
