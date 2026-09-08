"""The real G1 backend.

Imports ``rospy`` / ``unitree_sdk2py`` / ``tf`` lazily and inside try/except, so
``import g1_api`` never fails on a machine without ROS. Loaded only when
``G1_API_MODE=real``.
"""

from __future__ import annotations

from g1_api.adapters.g1.adapter import G1Adapter

__all__ = ["G1Adapter"]
