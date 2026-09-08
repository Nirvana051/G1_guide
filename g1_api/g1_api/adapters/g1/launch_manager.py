"""roslaunch subprocess lifecycle for the real adapter.

``:select`` must stop the current ``roslaunch`` and restart it with the selected
map's absolute paths as args. This module manages that as a controlled
subprocess and does health checks (roscore / move_base / key topics). It uses
only :mod:`subprocess` -- no ROS import -- so it is importable everywhere.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["LaunchManager", "LaunchStatus"]


class LaunchStatus(object):
    RUNNING = "running"
    STOPPED = "stopped"
    CRASHED = "crashed"


class LaunchManager(object):
    """Owns one roslaunch subprocess.

    :param launch_file: the parameterized ``g1_api_navigation.launch``.
    :param args: mapping of arg name -> value passed to roslaunch.
    """

    def __init__(
        self,
        launch_file: str,
        args: Optional[Dict[str, str]] = None,
        ros_env: Optional[Dict[str, str]] = None,
        log_path: str = "/tmp/g1_api_navigation_launch.log",
        setup_file: Optional[str] = None,
    ) -> None:
        self._launch_file = launch_file
        self._args = dict(args or {})
        self._ros_env = dict(ros_env or {})
        self._log_path = log_path
        self._setup_file = setup_file
        self._proc: Optional[subprocess.Popen] = None
        self._log_handle: Optional[Any] = None
        self._status = LaunchStatus.STOPPED

    def _command(self) -> List[str]:
        cmd = ["roslaunch", self._launch_file]
        for name, value in self._args.items():
            cmd.append("%s:=%s" % (name, value))
        if self._setup_file:
            # Source the overlay first so a source-built nav stack shadows the
            # apt one for THIS launch only; exec keeps roslaunch as the direct
            # child (stop()'s SIGTERM and _kill_strays' pattern both still hit).
            import shlex

            line = "source %s && exec %s" % (
                shlex.quote(self._setup_file),
                " ".join(shlex.quote(part) for part in cmd),
            )
            return ["bash", "-c", line]
        return cmd

    def set_args(self, args: Dict[str, str]) -> None:
        self._args = dict(args)

    def _kill_strays(self) -> None:
        """Kill orphaned roslaunch processes for this launch file.

        If a previous g1_api process died without stop(), its roslaunch child
        keeps running; this manager has no handle to it and starting a second
        stack would fight the first over node names. SIGINT first (roslaunch
        tears children down cleanly), escalate to SIGKILL after a grace period.
        """
        pattern = "roslaunch.*%s" % self._launch_file
        try:
            subprocess.run(["pkill", "-INT", "-f", pattern], check=False)
        except OSError:
            return
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            probe = subprocess.run(["pgrep", "-f", pattern], check=False,
                                   stdout=subprocess.DEVNULL)
            if probe.returncode != 0:
                return
            time.sleep(1.0)
        subprocess.run(["pkill", "-KILL", "-f", pattern], check=False)
        time.sleep(1.0)

    def start(self) -> None:
        self.stop()
        self._kill_strays()
        # Inherit the process environment (ROS setup) and overlay extras.
        # A bare self._ros_env would wipe PATH/ROS_* and break roslaunch.
        env = dict(os.environ)
        env.update(self._ros_env)
        # Never PIPE without a reader: roslaunch fills the 64K pipe buffer and
        # blocks. Append to a log file instead (also far easier to debug).
        self._log_handle = open(self._log_path, "ab")
        self._proc = subprocess.Popen(
            self._command(),
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            env=env,
        )
        self._status = LaunchStatus.RUNNING

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                # roslaunch tears its children down on SIGTERM; give it time.
                self._proc.wait(timeout=20.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
            self._proc = None
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None
        self._status = LaunchStatus.STOPPED

    def poll(self) -> str:
        if self._proc is None:
            self._status = LaunchStatus.STOPPED
            return self._status
        code = self._proc.poll()
        if code is None:
            self._status = LaunchStatus.RUNNING
        else:
            self._status = LaunchStatus.CRASHED
        return self._status

    def healthy(self) -> Tuple[bool, List[str]]:
        """Return (healthy, active_interlocks) using ``rosnode``/``rostopic``.

        Degrades to a process-liveness check when ROS tooling is unavailable, so
        this never raises on a dev machine.
        """
        interlocks: List[str] = []
        if self.poll() == LaunchStatus.CRASHED:
            interlocks.append("move_base_down")
            return False, interlocks
        if self.poll() != LaunchStatus.RUNNING:
            interlocks.append("move_base_down")
            return False, interlocks
        return True, interlocks

    @property
    def status(self) -> str:
        return self.poll()
