"""Application configuration: a frozen dataclass tree with three layers.

Precedence, lowest to highest:

1. built-in defaults (the dataclass field defaults in this module),
2. an optional YAML file -- the ``path`` argument to :func:`load_config`, or
   ``$G1_API_CONFIG`` when no path is given,
3. environment variables ``G1_API_<SECTION>_<FIELD>``.

Two aliases exist: ``G1_API_MODE`` (== ``G1_API_MODE_MODE``) and
``G1_API_LOG_LEVEL`` (== ``G1_API_LOGGING_LEVEL``).

**Safety posture.** Every ``ALLOW_*`` flag defaults to ``False`` and the default
mode is ``mock``. A fresh, unconfigured process cannot move a robot.
"""

from __future__ import annotations

import collections.abc
import copy
import dataclasses
import logging
import os
import typing
from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple

try:  # pragma: no cover
    import yaml as _yaml
except Exception:  # pragma: no cover
    _yaml = None

__all__ = [
    "ConfigError",
    "RobotMode",
    "ServerConfig",
    "ModeConfig",
    "SafetyConfig",
    "IdentityConfig",
    "MapConfig",
    "VoiceConfig",
    "HandConfig",
    "TourConfig",
    "LoggingConfig",
    "AppConfig",
    "load_config",
    "get_config",
    "set_config",
    "reset_config",
    "parse_bool",
    "ENV_PREFIX",
    "ENV_CONFIG_PATH",
]

_log = logging.getLogger(__name__)

ENV_PREFIX = "G1_API"
ENV_CONFIG_PATH = "G1_API_CONFIG"


class ConfigError(ValueError):
    """Raised for a malformed config file, value or environment override."""


class RobotMode:
    MOCK = "mock"
    REAL = "real"


MODES: Tuple[str, ...] = (RobotMode.MOCK, RobotMode.REAL)

_TRUE = frozenset({"1", "true", "yes", "y", "on", "t"})
_FALSE = frozenset({"0", "false", "no", "n", "off", "f"})
_NULL = frozenset({"", "none", "null", "nil", "~"})


def parse_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and not isinstance(raw, bool):
        if raw in (0, 1):
            return bool(raw)
        raise ConfigError("cannot read %r as a boolean" % (raw,))
    text = str(raw).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ConfigError("cannot read %r as a boolean" % (raw,))


def _split_sequence(raw: Any) -> Tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return tuple(part.strip() for part in raw.split(",") if part.strip())
    if isinstance(raw, (list, tuple)):
        return tuple(str(part).strip() for part in raw)
    raise ConfigError("cannot read %r as a list of strings" % (raw,))


def _coerce(value: Any, annotation: Any, where: str) -> Any:
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Union:
        non_none = [a for a in args if a is not type(None)]  # noqa: E721
        if isinstance(value, str) and value.strip().lower() in _NULL:
            return None
        if value is None:
            return None
        if len(non_none) == 1:
            return _coerce(value, non_none[0], where)
        return value
    if origin is tuple:
        return _split_sequence(value)
    if origin is list:
        return list(_split_sequence(value))
    if annotation is bool:
        return parse_bool(value)
    if annotation is int:
        try:
            return int(str(value).strip(), 0) if isinstance(value, str) else int(value)
        except (TypeError, ValueError):
            raise ConfigError("cannot read %r as an integer for %s" % (value, where))
    if annotation is float:
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ConfigError("cannot read %r as a number for %s" % (value, where))
    if annotation is str:
        return str(value)
    return value


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 1448
    cors_origins: Tuple[str, ...] = ("http://localhost:*",)
    enable_sdk_console: bool = True
    max_request_bytes: int = 32 * 1024 * 1024

    def __post_init__(self) -> None:
        object.__setattr__(self, "cors_origins", tuple(self.cors_origins))
        if not (0 < int(self.port) < 65536):
            raise ConfigError("server.port must be in 1..65535, got %r" % (self.port,))


@dataclass(frozen=True)
class ModeConfig:
    mode: str = RobotMode.MOCK
    #: DDS interface for the real adapter. **No default**: an explicit value is
    #: required (the course code ships eth0/eno1/enp4s0 defaults simultaneously,
    #: which is a known source of accidents).
    network_interface: Optional[str] = None
    adapter_startup_timeout_s: float = 10.0
    #: DDS layer backend for the real adapter: ``real`` uses unitree_sdk2py
    #: (LocoClient/AudioClient/ArmClient); ``stub`` replaces it with log-only
    #: stand-ins so the ROS side (navigation/localization/maps) can run against
    #: a simulator with no robot hardware present.
    dds_backend: str = "real"
    #: Parameterized navigation launch file managed by ``:select`` (map switch
    #: restarts it with the selected map's paths). Unset = the navigation stack
    #: is managed externally and ``:select`` only switches the map pointer.
    launch_file: Optional[str] = None
    #: How long ``:select`` waits for /move_base + /slam_reloc after a restart.
    launch_ready_timeout_s: float = 90.0
    #: Mapping (map recording) launch file managed by ``/mapping/:start``.
    #: Unset = the mapping endpoints answer 501 in real mode.
    mapping_launch_file: Optional[str] = None
    #: How MoveTo judges arrival. ``tf`` (default) keeps the course semantics:
    #: TF map->body distance + rotate + XY fine adjust, /move_base/result is
    #: ignored. ``planner`` trusts the planner's action result instead
    #: (SUCCEEDED -> reached, ABORTED -> failed) with a TF sanity check; meant
    #: for the source-built AeroMaze stack whose goal tolerances are tight
    #: enough (xy 0.05) that move_base itself achieves the requested precision.
    nav_feedback: str = "tf"
    #: Optional shell setup file sourced before *navigation* roslaunch (e.g. an
    #: overlay workspace's devel/setup.bash so a source-built nav stack shadows
    #: the apt one for this launch only). Mapping launch is not affected.
    nav_launch_setup: Optional[str] = None

    def __post_init__(self) -> None:
        normalised = str(self.mode).strip().lower()
        if normalised not in MODES:
            raise ConfigError("mode.mode must be one of %s, got %r" % (", ".join(MODES), self.mode))
        object.__setattr__(self, "mode", normalised)
        backend = str(self.dds_backend).strip().lower()
        if backend not in ("real", "stub"):
            raise ConfigError(
                "mode.dds_backend must be 'real' or 'stub', got %r" % (self.dds_backend,)
            )
        object.__setattr__(self, "dds_backend", backend)
        feedback = str(self.nav_feedback).strip().lower()
        if feedback not in ("tf", "planner"):
            raise ConfigError(
                "mode.nav_feedback must be 'tf' or 'planner', got %r" % (self.nav_feedback,)
            )
        object.__setattr__(self, "nav_feedback", feedback)

    @property
    def is_mock(self) -> bool:
        return self.mode == RobotMode.MOCK

    @property
    def is_real(self) -> bool:
        return self.mode == RobotMode.REAL


@dataclass(frozen=True)
class SafetyConfig:
    """Capability flags enforced by ``g1_api.core.safety.SafetyGate``.

    Every boolean defaults to ``False``. A state-changing request whose class is
    not explicitly allowed is refused with ``409 SAFETY_INTERLOCK``.
    """

    allow_motion: bool = False       # MoveAction (teleop, no obstacle avoidance)
    allow_navigation: bool = False   # MoveTo / Rotate / RotateTo
    allow_map_write: bool = False    # map upload / select / delete
    allow_arm: bool = False          # arm actions
    allow_hand: bool = False         # dexterous hand commands
    allow_voice: bool = False        # TTS / volume / LED
    allow_tour: bool = False         # tour orchestration
    require_localization_for_motion: bool = True
    max_linear_speed_mps: float = 1.0
    max_angular_speed_radps: float = 0.7
    #: MoveAction duration ceiling, ms. Default 3000 (the build prompt's cap).
    max_move_duration_ms: int = 3000
    #: Deadzones below which a MoveAction velocity is rejected with 400.
    deadzone_vx: float = 0.2
    deadzone_vy: float = 0.2
    deadzone_omega: float = 0.3
    max_action_duration_s: float = 900.0

    def __post_init__(self) -> None:
        if float(self.max_linear_speed_mps) <= 0:
            raise ConfigError("safety.max_linear_speed_mps must be > 0")
        if float(self.max_angular_speed_radps) <= 0:
            raise ConfigError("safety.max_angular_speed_radps must be > 0")
        if int(self.max_move_duration_ms) <= 0:
            raise ConfigError("safety.max_move_duration_ms must be > 0")

    @property
    def any_write_allowed(self) -> bool:
        return bool(
            self.allow_motion
            or self.allow_navigation
            or self.allow_map_write
            or self.allow_arm
            or self.allow_hand
            or self.allow_voice
            or self.allow_tour
        )


@dataclass(frozen=True)
class IdentityConfig:
    manufacturer_name: str = "Unitree"
    model_name: str = "G1"
    software_version: str = "0.1.0"
    device_id: Optional[str] = None


@dataclass(frozen=True)
class MapConfig:
    map_storage_dir: str = "~/.g1_api/maps"
    default_resolution_m: float = 0.05
    #: Where the course map_builder auto-saves map.pcd / ground_map.pcd on
    #: shutdown (hardcoded in map_builder_node.cpp) and where map_saver writes
    #: the 2D grid during ``/mapping/:finish``.
    mapping_output_dir: str = "/home/unitree/abotclaw_nv/navigate/map"

    def __post_init__(self) -> None:
        if float(self.default_resolution_m) <= 0:
            raise ConfigError("map.default_resolution_m must be > 0")


@dataclass(frozen=True)
class VoiceConfig:
    #: TTS completion has no callback on the G1; duration is estimated as
    #: ``len(text) * per-speaker coefficient``. 0.195 s/char fits Chinese
    #: (speaker 0, ~5 chars/s); English (speaker 1) counts letters, ~13/s.
    char_duration_s: float = 0.195
    char_duration_s_en: float = 0.075
    default_speaker_id: int = 0
    #: Extra safety margin added to the estimated TTS duration by the tour layer.
    completion_margin_s: float = 0.5

    def __post_init__(self) -> None:
        if float(self.char_duration_s) <= 0:
            raise ConfigError("voice.char_duration_s must be > 0")
        if float(self.char_duration_s_en) <= 0:
            raise ConfigError("voice.char_duration_s_en must be > 0")
        if float(self.completion_margin_s) < 0:
            raise ConfigError("voice.completion_margin_s must be >= 0")

    def char_duration_for(self, speaker_id: int) -> float:
        """Per-speaker seconds-per-character (1 = English letters, else 中文)."""
        return float(self.char_duration_s_en if int(speaker_id) == 1 else self.char_duration_s)


@dataclass(frozen=True)
class HandConfig:
    #: Robot IP for the dexterous-hand TCP server. Required only in real mode.
    robot_ip: Optional[str] = None
    port: int = 5678

    def __post_init__(self) -> None:
        if not (0 < int(self.port) < 65536):
            raise ConfigError("hand.port must be in 1..65535, got %r" % (self.port,))


@dataclass(frozen=True)
class TourConfig:
    #: Arm action id executed after every arm action to release the arm.
    arm_release_action_id: int = 99
    arm_release_wait_s: float = 3.0
    #: Endpoint for tour actions marked ``executor: "external"``. When set,
    #: each external action is POSTed there as JSON {point, action}; when unset
    #: (default), external actions are logged and skipped.
    external_executor_url: Optional[str] = None
    #: Library of uploaded external (replay) actions -- .npy dual-arm
    #: trajectories plus their metadata, executed by ``g1_replay_min``'s
    #: action server via ``external_executor_url``.
    external_actions_dir: str = "~/.g1_api/external_actions"
    #: Extra wait beyond an action's nominal duration before the execute POST
    #: is considered timed out (covers move-to-start-pose + release ramp).
    external_execute_timeout_margin_s: float = 30.0

    def __post_init__(self) -> None:
        if float(self.arm_release_wait_s) < 0:
            raise ConfigError("tour.arm_release_wait_s must be >= 0")
        if float(self.external_execute_timeout_margin_s) < 0:
            raise ConfigError("tour.external_execute_timeout_margin_s must be >= 0")


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    json_logs: bool = False
    include_timestamp: bool = True
    redact_keys: Tuple[str, ...] = (
        "token", "secret", "password", "passwd", "authorization",
        "api_key", "apikey", "credential", "cookie", "private_key",
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "level", str(self.level).strip().upper())
        object.__setattr__(
            self, "redact_keys", tuple(str(k).strip().lower() for k in self.redact_keys)
        )
        if not isinstance(logging.getLevelName(self.level), int):
            raise ConfigError("logging.level %r is not a known level name" % (self.level,))


@dataclass(frozen=True)
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    mode: ModeConfig = field(default_factory=ModeConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    map: MapConfig = field(default_factory=MapConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    hand: HandConfig = field(default_factory=HandConfig)
    tour: TourConfig = field(default_factory=TourConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    SECTIONS: typing.ClassVar[Tuple[Tuple[str, type], ...]] = (
        ("server", ServerConfig),
        ("mode", ModeConfig),
        ("safety", SafetyConfig),
        ("identity", IdentityConfig),
        ("map", MapConfig),
        ("voice", VoiceConfig),
        ("hand", HandConfig),
        ("tour", TourConfig),
        ("logging", LoggingConfig),
    )

    ENV_ALIASES: typing.ClassVar[Mapping[str, Tuple[str, str]]] = MappingProxyType(
        {
            "G1_API_MODE": ("mode", "mode"),
            "G1_API_LOG_LEVEL": ("logging", "level"),
        }
    )

    def startup_warnings(self) -> List[str]:
        out = []
        if self.safety.any_write_allowed:
            allowed = [
                name
                for name in (
                    "allow_motion", "allow_navigation", "allow_map_write",
                    "allow_arm", "allow_hand", "allow_voice", "allow_tour",
                )
                if getattr(self.safety, name)
            ]
            out.append(
                "Safety flags ENABLED: %s. This process can change the physical "
                "state of the robot." % (", ".join(allowed),)
            )
        if self.mode.mode != RobotMode.MOCK:
            out.append("Adapter mode is %r -- commands reach real hardware." % (self.mode.mode,))
        if self.mode.mode == RobotMode.REAL and not self.mode.network_interface:
            out.append(
                "Adapter mode is real but mode.network_interface is unset; the "
                "real adapter will refuse to initialize DDS."
            )
        return out

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for name, _cls in self.SECTIONS:
            section = getattr(self, name)
            values: Dict[str, Any] = {}
            for f in fields(section):
                value = getattr(section, f.name)
                if isinstance(value, tuple):
                    values[f.name] = list(value)
                else:
                    values[f.name] = value
            out[name] = values
        return out


def _read_yaml(path: str) -> Dict[str, Any]:
    if _yaml is None:
        _log.warning("PyYAML not installed; ignoring config file %s", path)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            loaded = _yaml.safe_load(handle)
    except OSError as exc:
        raise ConfigError("cannot read config file %s: %s" % (path, exc))
    except Exception as exc:  # noqa: BLE001
        raise ConfigError("cannot parse config file %s: %s" % (path, exc))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError("config file %s must contain a mapping at the top level" % (path,))
    return copy.deepcopy(loaded)


def _env_overrides(env: Mapping[str, str]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    field_names = {
        section: {f.name for f in fields(cls)} for section, cls in AppConfig.SECTIONS
    }
    for section, _cls in AppConfig.SECTIONS:
        prefix = "%s_%s_" % (ENV_PREFIX, section.upper())
        for key, value in env.items():
            if not key.startswith(prefix):
                continue
            name = key[len(prefix):].lower()
            if name not in field_names[section]:
                raise ConfigError(
                    "environment variable %s does not match any field of the %r "
                    "section (known fields: %s)"
                    % (key, section, ", ".join(sorted(field_names[section])))
                )
            out.setdefault(section, {})[name] = value
    for alias, (section, name) in AppConfig.ENV_ALIASES.items():
        if alias in env:
            out.setdefault(section, {})[name] = env[alias]
    return out


def _build_section(cls: type, overrides: Mapping[str, Any], where: str) -> Any:
    hints = typing.get_type_hints(cls)
    known = {f.name for f in fields(cls)}
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise ConfigError(
            "unknown key(s) %s in section %r (known keys: %s)"
            % (", ".join(unknown), where, ", ".join(sorted(known)))
        )
    kwargs: Dict[str, Any] = {}
    for name, raw in overrides.items():
        kwargs[name] = _coerce(raw, hints.get(name, Any), "%s.%s" % (where, name))
    return cls(**kwargs)


def load_config(
    path: Optional[str] = None, env: Optional[Mapping[str, str]] = None, warn: bool = True
) -> AppConfig:
    environ = os.environ if env is None else env
    file_data: Dict[str, Any] = {}
    chosen = path if path is not None else environ.get(ENV_CONFIG_PATH)
    if chosen:
        if not os.path.isfile(chosen):
            raise ConfigError("config file not found: %s" % (chosen,))
        file_data = _read_yaml(chosen)

    known_sections = {name for name, _ in AppConfig.SECTIONS}
    unknown_sections = sorted(set(file_data) - known_sections)
    if unknown_sections:
        raise ConfigError(
            "unknown config section(s) %s (known sections: %s)"
            % (", ".join(unknown_sections), ", ".join(sorted(known_sections)))
        )

    env_data = _env_overrides(environ)
    sections: Dict[str, Any] = {}
    for name, cls in AppConfig.SECTIONS:
        merged: Dict[str, Any] = {}
        from_file = file_data.get(name) or {}
        if not isinstance(from_file, Mapping):
            raise ConfigError("config section %r must be a mapping" % (name,))
        merged.update(from_file)
        merged.update(env_data.get(name, {}))
        sections[name] = _build_section(cls, merged, name)

    config = AppConfig(**sections)
    if warn:
        for message in config.startup_warnings():
            _log.warning("%s", message)
    return config


_ACTIVE: Optional[AppConfig] = None


def get_config() -> AppConfig:
    global _ACTIVE
    if _ACTIVE is None:
        _ACTIVE = load_config()
    return _ACTIVE


def set_config(config: AppConfig) -> AppConfig:
    global _ACTIVE
    if not isinstance(config, AppConfig):
        raise ConfigError("set_config expects an AppConfig, got %r" % (type(config),))
    _ACTIVE = config
    return _ACTIVE


def reset_config() -> None:
    global _ACTIVE
    _ACTIVE = None


def replace_config(**section_overrides: Any) -> AppConfig:
    return dataclasses.replace(get_config(), **section_overrides)
