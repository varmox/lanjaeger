"""Long-running `docker compose` actions, one at a time, with captured output.

The web UI's own container is never in the service list: recreating it
would kill the process running the job. Update the web UI with update.sh.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time

MANAGED = ["blocky", "dns", "monolithic", "logrotate"]

ACTIONS = {
    # Pull new images, recreate what changed.
    "update": [["pull", *MANAGED], ["up", "-d", *MANAGED]],
    # Recreate with the current .env (after a settings change).
    "apply": [["up", "-d", *MANAGED]],
}


class JobRunner:
    def __init__(self, project_dir: str, project: str | None):
        self.project_dir = project_dir
        self.project = project
        self._lock = threading.Lock()
        self.current: dict | None = None

    def _base(self) -> list[str]:
        cmd = ["docker", "compose", "--project-directory", self.project_dir]
        if self.project:
            cmd += ["-p", self.project]
        return cmd

    def start(self, action: str) -> dict:
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        with self._lock:
            if self.current and self.current["state"] == "running":
                raise RuntimeError("another action is still running")
            self.current = {"action": action, "state": "running", "output": "",
                            "started": time.time(), "finished": None}
            job = self.current
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return dict(job)

    def _run(self, job: dict) -> None:
        # Compose interpolates ${PWD} in docker-compose.yml: make it the host
        # path of the project, which is mounted at the same path in here.
        env = {**os.environ, "PWD": self.project_dir}
        ok = True
        for args in ACTIONS[job["action"]]:
            cmd = self._base() + args
            job["output"] += f"$ docker compose {' '.join(args)}\n"
            try:
                proc = subprocess.Popen(cmd, cwd=self.project_dir, env=env, text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                for line in proc.stdout:
                    job["output"] = (job["output"] + line)[-50_000:]
                ok = proc.wait() == 0
            except OSError as exc:
                job["output"] += f"{exc}\n"
                ok = False
            if not ok:
                break
        job["state"] = "succeeded" if ok else "failed"
        job["finished"] = time.time()

    def status(self) -> dict | None:
        return dict(self.current) if self.current else None
