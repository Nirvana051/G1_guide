"""g1_api: Slamtec-style core API for the Unitree G1 humanoid robot.

Three layers over one robot core, exactly as the build prompt demands:

* **Core REST API** -- the Slamtec asynchronous action pattern (move / moveto /
  rotate / rotateto), the ``.g1map`` atomic map package, localization and
  relocalization, system health, TTS, arm and hand commands.
* **SDK debug console** at ``/sdk`` -- an offline, dependency-free accordion
  page: one collapsible panel per endpoint, with a Chinese description, a
  pre-filled sample and an Execute button against the live API.
* **Tour application** at ``/api/tour/v1/*`` -- sits *above* the core API, eats
  its own dog food (every leg is a ``MoveToAction``), and runs arm action +
  TTS in parallel at each point.

The default backend is the **mock** adapter; the real G1 adapter is loaded only
when ``G1_API_MODE=real`` and imports ``rospy`` / ``unitree_sdk2py`` lazily, so
``import g1_api`` never fails on a machine without ROS.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
