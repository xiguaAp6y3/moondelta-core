#!/usr/bin/env python3
"""Static self-check for the S5 harness.

This runs anywhere (no QEMU, no Linux host) and answers one question: are the
harness pieces still describing the same experiment? It is not evidence — it
only stops the most common way this kind of matrix rots: a boundary renamed in
C, a cmdline parameter renamed on one side, or a plan that never reaches one of
the required boundaries.

    python3 selfcheck.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent

BOUNDARY_C = REPO / "src" / "atomic_file_native.c"
MOONBIT_CALLERS = [
    REPO / "src" / "atomic_file.mbt",
    REPO / "src" / "txn.mbt",
    REPO / "src" / "write.mbt",
]
GUEST_INIT = HERE / "guest" / "init"
RUNNER = HERE / "run_powerloss.py"
BOUNDARIES = HERE / "boundaries.json"

PHASES = ["intent-publish", "data-publish", "commit-publish"]
CLEANUP_OPS = ["unlinked", "dir-fsynced"]


def check(condition: bool, ok: str, bad: str, problems: list[str]) -> None:
    if condition:
        print(f"  ok    {ok}")
    else:
        print(f"  FAIL  {bad}")
        problems.append(bad)


def main() -> int:
    problems: list[str] = []
    for path in [BOUNDARY_C, GUEST_INIT, RUNNER, BOUNDARIES, *MOONBIT_CALLERS]:
        if not path.is_file():
            print(f"  FAIL  missing file: {path}")
            return 1

    declared = json.loads(BOUNDARIES.read_text(encoding="utf-8"))
    names = [entry["name"] for entry in declared["boundaries"]]
    required = list(declared["required"])
    c_text = BOUNDARY_C.read_text(encoding="utf-8")
    init_text = GUEST_INIT.read_text(encoding="utf-8")
    runner_text = RUNNER.read_text(encoding="utf-8")

    print("boundaries")
    check(len(names) == len(set(names)), "no duplicate boundary names", "duplicate names", problems)
    check(
        all(name in names for name in required),
        "every required boundary is declared",
        "required boundary missing from boundaries.json",
        problems,
    )

    publish_ops = sorted(set(re.findall(r'moondelta_boundary\(phase, "([a-z-]+)"', c_text)))
    emitted = {f"{phase}-{op}" for phase in PHASES for op in publish_ops}
    emitted |= {f"cleanup-{op}" for op in CLEANUP_OPS if f'moondelta_boundary("cleanup", "{op}"' in c_text}
    check(bool(publish_ops), f"publication probe calls found ({len(publish_ops)} operations)",
          "no publication boundary calls found in atomic_file_native.c", problems)
    check(
        emitted == set(names),
        "declared boundaries are exactly the ones the probe can emit",
        f"probe emits a different set than boundaries.json: "
        f"only-in-code={sorted(emitted - set(names))} only-in-json={sorted(set(names) - emitted)}",
        problems,
    )

    moon_text = "\n".join(path.read_text(encoding="utf-8") for path in MOONBIT_CALLERS)
    for phase in PHASES:
        check(
            f'"{phase}"' in moon_text,
            f'phase "{phase}" is still used by the writer',
            f'phase "{phase}" disappeared from the writer; boundary names would drift',
            problems,
        )

    print("guest cmdline contract")
    init_keys = set(re.findall(r"arg moondelta\.([a-z]+)", init_text))
    runner_keys = set(re.findall(r"moondelta\.([a-z]+)=", runner_text))
    check(
        init_keys <= runner_keys,
        f"guest reads only parameters the runner supplies ({len(init_keys)} keys)",
        f"guest reads unsupplied parameters: {sorted(init_keys - runner_keys)}",
        problems,
    )
    check(
        runner_keys <= init_keys,
        f"runner supplies only parameters the guest reads ({len(runner_keys)} keys)",
        f"runner supplies unread parameters: {sorted(runner_keys - init_keys)}",
        problems,
    )

    print("injection points")
    check(
        "MOONDELTA_POWERLOSS_AT" in init_text and "MOONDELTA_POWERLOSS_SIGNAL" in init_text,
        "guest arms the probe through its documented environment",
        "guest no longer exports the probe environment",
        problems,
    )
    check(
        "qemu.kill()" in runner_text and "self.nbd.kill()" in runner_text
        and runner_text.index("self.qemu.kill()") < runner_text.index("self.nbd.kill()", 
                                                                    runner_text.index("def cut")),
        "cut kills QEMU before the NBD device cache",
        "cut order is wrong: the device cache must die after QEMU's own cache",
        problems,
    )

    print("plan coverage")
    rounds = 100
    plan: list[str] = []
    import random
    rng = random.Random(0)
    while len(plan) < rounds:
        block = list(names)
        rng.shuffle(block)
        plan.extend(block)
    plan = plan[:rounds]
    missing = [name for name in required if name not in plan]
    check(not missing, f"a {rounds}-round plan reaches every required boundary",
          f"plan never reaches: {missing}", problems)

    print("content-expectation contract")
    verifier_text = (HERE / "verify_round.py").read_text(encoding="utf-8")
    check(
        "capture_baseline=True" in runner_text and "expected=expected" in runner_text,
        "the runner records a baseline and compares every cut round against it",
        "the runner no longer passes an expectation to the verifier; a content edit "
        "inside a Parquet payload would pass unnoticed",
        problems,
    )
    check(
        "no expected content" in runner_text,
        "a round without a content comparison cannot be judged PASS",
        "judge_round no longer rejects rounds with no content comparison",
        problems,
    )
    check(
        "def record_baseline" in verifier_text and "def compare_content" in verifier_text,
        "the verifier owns baseline capture and two-engine content comparison",
        "verify_round.py lost its content comparison",
        problems,
    )
    # The host predicts the target row from the payload size and the round number
    # alone; the guest must derive the same id, or the expectation is wrong and
    # every round fails for the wrong reason.
    sys.path.insert(0, str(HERE))
    import run_powerloss  # noqa: E402  (same directory)
    predicted = run_powerloss.expected_append_row(8192, 7)
    check(
        predicted["id"] == 8192 + run_powerloss.TARGET_ID_OFFSET + 7
        and predicted["payload"] == "x" * 8192
        and "PAYLOAD + 1000 + ROUND" in init_text
        and run_powerloss.TARGET_ID_OFFSET == 1000,
        "the guest derives the target row id exactly as the host predicts it",
        "the guest's target row id formula and the host's prediction disagree",
        problems,
    )

    print("S3 recover-interrupt matrix")
    s3_path = HERE / "test_recover_interrupts.py"
    if s3_path.is_file():
        s3_text = s3_path.read_text(encoding="utf-8")
        used = sorted(set(re.findall(r'"(cleanup-[a-z-]+)"', s3_text)))
        check(
            bool(used) and all(name in names for name in used),
            f"the recover-interrupt matrix interrupts only real probe boundaries ({used})",
            f"the recover-interrupt matrix names boundaries the probe cannot emit: "
            f"{[name for name in used if name not in names]}",
            problems,
        )
        check(
            '"recover"' in s3_text and '"1" if apply else "0"' in s3_text,
            "the recover-interrupt matrix drives the shipped `recover --apply` path",
            "the recover-interrupt matrix no longer drives `recover --apply`",
            problems,
        )
    else:
        check(False, "the recover-interrupt matrix exists",
              "test_recover_interrupts.py is missing", problems)

    print("verdict discipline")
    gate = (HERE / "check_environment.py").read_text(encoding="utf-8")
    check('"PASS"' not in gate, "the environment gate cannot print PASS",
          "check_environment.py prints PASS", problems)
    check(
        "INVALID" in runner_text and "BLOCKED" in runner_text,
        "runner distinguishes INVALID/BLOCKED from FAILED",
        "runner lost its INVALID/BLOCKED verdicts",
        problems,
    )

    print()
    if problems:
        print(f"SELFCHECK: {len(problems)} problem(s)")
        return 1
    print("SELFCHECK: harness pieces agree (this is not power-loss evidence)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
