"""Locust performance test for the /vehicle-summary endpoint.

Locust is the Python equivalent of Gatling: it ships a live web UI with
real-time response-time / throughput charts and can also export a standalone
HTML report (`--html`) plus CSV stats — the same kind of visualization Gatling
produces.

Load profile (all overridable via environment variables):

    WARMUP_SECONDS   warm-up window, default 5s. Requests sent during this
                     window are discarded from the final report so JIT, S3
                     clients, DuckDB caches etc. don't skew the numbers.
    RUN_SECONDS      measured window, default 60s.
    TARGET_REQUESTS  number of requests to spread across the measured window,
                     default 100 (=> ~1.67 req/s).

The total `--run-time` you pass to locust should be WARMUP_SECONDS + RUN_SECONDS
(65s by default). The bundled `run.sh` wires all of this up for you.

Run headless (generates HTML + CSV report):
    ./run.sh

Run with the live web UI (Gatling-style charts at http://localhost:8089):
    ./run.sh web
"""

from __future__ import annotations

import os
import random

import gevent
from locust import HttpUser, between, constant_throughput, events, task

# --------------------------------------------------------------------------- #
# Load profile configuration
# --------------------------------------------------------------------------- #
WARMUP_SECONDS = float(os.getenv("WARMUP_SECONDS", "5"))
RUN_SECONDS = float(os.getenv("RUN_SECONDS", "60"))
TARGET_REQUESTS = int(os.getenv("TARGET_REQUESTS", "100"))

# Per-user throughput so that, over the measured window, the configured number
# of requests is sent. With a single user this yields TARGET_REQUESTS spread
# evenly across RUN_SECONDS.
_THROUGHPUT_PER_SEC = TARGET_REQUESTS / RUN_SECONDS if RUN_SECONDS > 0 else 1.0

# --------------------------------------------------------------------------- #
# Query targets — verified joinable (manufacturer, model, year) tuples that all
# return HTTP 200 from /vehicle-summary. BMW X1 1999 is the deterministic
# anchor; the rest are real combinations sampled from the parquet data so the
# test exercises a variety of model_year_ids (different recall / part / owner
# branches), not just one hot row.
# --------------------------------------------------------------------------- #
TARGETS: list[tuple[str, str, int]] = [
    ("BMW", "X1", 1999),
    ("Maker_8867", "Model_14930", 2008),
    ("Maker_5517", "Model_41306", 2015),
    ("Maker_18795", "Model_92998", 2003),
    ("Maker_982", "Model_81534", 2005),
    ("Maker_13909", "Model_82872", 2024),
    ("Maker_4531", "Model_83584", 1998),
    ("Maker_9137", "Model_96309", 2019),
    ("Maker_3283", "Model_97879", 1995),
    ("Maker_7775", "Model_66653", 2021),
    ("Maker_6591", "Model_77898", 2010),
    ("Maker_5797", "Model_73965", 2006),
]


class VehicleSummaryUser(HttpUser):
    """Simulates a client hitting the heavy 10-table join endpoint."""

    # constant_throughput caps each user at N task iterations per second, which
    # is how we shape "TARGET_REQUESTS over RUN_SECONDS". Fallback to a small
    # wait if throughput shaping is disabled.
    if _THROUGHPUT_PER_SEC > 0:
        wait_time = constant_throughput(_THROUGHPUT_PER_SEC)
    else:
        wait_time = between(0.5, 1.0)

    @task
    def vehicle_summary(self) -> None:
        manufacturer, model, year = random.choice(TARGETS)
        with self.client.get(
            "/vehicle-summary",
            params={"manufacturer": manufacturer, "model": model, "year": year},
            name="/vehicle-summary",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(
                    f"HTTP {response.status_code} for "
                    f"{manufacturer} {model} {year}"
                )
                return
            body = response.json()
            if not body.get("manufacturer", {}).get("name"):
                response.failure("Missing manufacturer.name in response")


# --------------------------------------------------------------------------- #
# Warm-up handling: discard everything sent during the warm-up window so the
# final report only reflects the steady-state measured window.
# --------------------------------------------------------------------------- #
@events.test_start.add_listener
def _on_test_start(environment, **_kwargs) -> None:
    if WARMUP_SECONDS <= 0:
        return

    def _reset_after_warmup() -> None:
        gevent.sleep(WARMUP_SECONDS)
        runner = environment.runner
        if runner is not None:
            runner.stats.reset_all()
        print(
            f"[warmup] {WARMUP_SECONDS:.0f}s warm-up complete — stats reset; "
            f"measuring for {RUN_SECONDS:.0f}s "
            f"(~{TARGET_REQUESTS} requests target)."
        )

    gevent.spawn(_reset_after_warmup)
