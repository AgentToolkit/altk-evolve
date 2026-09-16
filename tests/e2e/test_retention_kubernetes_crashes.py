"""Opt-in pod SIGKILL validation on an explicitly selected local Kubernetes context.

Reuses the container suite's crash boundaries and database assertions. The context
must expose the checkout via hostPath and already have the dependency images.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

import pytest

# Pytest adds this non-package test directory to sys.path during collection.
import test_retention_container_crashes as scenarios  # type: ignore[import-not-found]

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="session", autouse=True)
def phoenix_server():
    """No LLM or tracing service is used by these tests."""
    yield


@pytest.fixture
def kubernetes(monkeypatch):
    context = os.environ.get("EVOLVE_TEST_KUBERNETES_CONTEXT")
    image = os.environ.get("EVOLVE_TEST_CONTAINER_IMAGE")
    if not context or not image:
        pytest.skip("Set EVOLVE_TEST_KUBERNETES_CONTEXT and EVOLVE_TEST_CONTAINER_IMAGE for local pod tests")
    import psycopg
    from psycopg.rows import dict_row

    namespace = "evolve-gc-" + uuid.uuid4().hex[:10]
    prefix = ["kubectl", "--context", context, "--namespace", namespace]

    def kubectl(*args, manifest=None):
        return subprocess.check_output(
            prefix + list(args), input=json.dumps(manifest) if manifest else None, text=True, stderr=subprocess.STDOUT
        )

    def apply(kind, name, spec, **extra):
        kubectl(
            "apply",
            "-f",
            "-",
            manifest={
                "apiVersion": "apps/v1" if kind == "Deployment" else "v1",
                "kind": kind,
                "metadata": {"name": name, "namespace": namespace},
                "spec": spec,
                **extra,
            },
        )

    kubectl("create", "namespace", namespace)
    forward = None
    try:
        apply(
            "Pod",
            "postgres",
            {
                "containers": [
                    {
                        "name": "postgres",
                        "image": os.environ.get("EVOLVE_TEST_POSTGRES_IMAGE", "pgvector/pgvector:pg16"),
                        "imagePullPolicy": "Never",
                        "env": [{"name": "POSTGRES_HOST_AUTH_METHOD", "value": "trust"}],
                        "readinessProbe": {"exec": {"command": ["pg_isready", "-U", "postgres", "-h", "127.0.0.1"]}, "periodSeconds": 1},
                        "volumeMounts": [{"name": "data", "mountPath": "/var/lib/postgresql/data"}],
                    }
                ],
                "volumes": [{"name": "data", "emptyDir": {}}],
            },
        )
        apply("Service", "postgres", {"selector": {"app": "postgres"}, "ports": [{"port": 5432, "targetPort": 5432}]})
        kubectl("label", "pod", "postgres", "app=postgres")
        kubectl("wait", "--for=condition=Ready", "pod/postgres", "--timeout=90s")
        with tempfile.TemporaryFile(mode="w+") as output:
            forward = subprocess.Popen(
                prefix + ["port-forward", "pod/postgres", ":5432", "--address=127.0.0.1"],
                stdout=output,
                stderr=subprocess.STDOUT,
                text=True,
            )

            def forwarded():
                output.seek(0)
                return re.search(r"127\.0\.0\.1:(\d+) -> 5432", output.read())

            scenarios.eventually(forwarded, timeout=30)
            port = forwarded().group(1)
            conn = psycopg.connect(f"postgresql://postgres@127.0.0.1:{port}/postgres", autocommit=True, row_factory=dict_row)

            def start(name, phase=""):
                spec = {
                    "containers": [
                        {
                            "name": "scheduler",
                            "image": image,
                            "imagePullPolicy": "Never",
                            "command": [
                                "uv",
                                "run",
                                "--no-sync",
                                "--project",
                                "/app",
                                "python",
                                "/evolve/tests/e2e/retention_container_executor.py",
                            ],
                            "env": [
                                {"name": k, "value": v}
                                for k, v in {
                                    "TEST_DSN": "postgresql://postgres@postgres/postgres",
                                    "TEST_PAUSE": phase,
                                    "TEST_KILL_FILE": "/tmp/retention-kill",
                                    "PYTHONPATH": "/evolve",
                                    "PYTHONUNBUFFERED": "1",
                                }.items()
                            ],
                            "volumeMounts": [{"name": "source", "mountPath": "/evolve", "readOnly": True}],
                        }
                    ],
                    "volumes": [{"name": "source", "hostPath": {"path": str(Path(__file__).resolve().parents[2]), "type": "Directory"}}],
                }
                if name == "victim":
                    apply("Pod", name, {**spec, "restartPolicy": "Never"})
                    kubectl("wait", "--for=condition=Ready", "pod/victim", "--timeout=90s")
                elif name == "survivor-1":
                    apply(
                        "Deployment",
                        "schedulers",
                        {
                            "replicas": 1,
                            "selector": {"matchLabels": {"app": "scheduler"}},
                            "template": {"metadata": {"labels": {"app": "scheduler"}}, "spec": spec},
                        },
                    )
                    kubectl("rollout", "status", "deployment/schedulers", "--timeout=90s")
                else:
                    kubectl("scale", "deployment/schedulers", "--replicas=2")
                    kubectl("rollout", "status", "deployment/schedulers", "--timeout=90s")
                return name

            def runtime(*args):
                if args[0] == "logs":
                    return kubectl("logs", args[-1])
                if args[0] == "kill":
                    kubectl("exec", "victim", "--", "touch", "/tmp/retention-kill")
                    scenarios.eventually(
                        lambda: json.loads(kubectl("get", "pod", "victim", "-o", "json"))["status"]["phase"] == "Failed", timeout=30
                    )
                    return "victim"
                if args[0] == "inspect":
                    status = json.loads(kubectl("get", "pod", "victim", "-o", "json"))["status"]["containerStatuses"][0]
                    return str(status["state"]["terminated"]["exitCode"])
                raise AssertionError(args)

            monkeypatch.setattr(scenarios, "docker", runtime)

            def verify():
                deployment = json.loads(kubectl("get", "deployment", "schedulers", "-o", "json"))
                assert deployment["status"]["readyReplicas"] == 2

            with conn:
                yield conn, start, verify
    finally:
        print(kubectl("get", "pods", "-o", "wide"))
        if forward:
            forward.terminate()
            forward.wait(timeout=10)
        kubectl("delete", "namespace", namespace, "--wait=false")


@pytest.mark.parametrize("phase", ["mark", "delete", "committed", "heartbeat"])
def test_scheduler_pod_sigkill(kubernetes, phase):
    conn, start, verify = kubernetes
    scenarios.test_scheduler_container_sigkill((conn, start), phase)
    verify()
