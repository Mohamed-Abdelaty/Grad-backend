"""Master test runner for all Phase 2 Verification Scenarios (A through J).
Executes each scenario in an isolated process and reports summary.
"""
from __future__ import annotations

import subprocess
import sys
import time

SCENARIOS = [
    ("Scenario A (CLI: Linear, Fanout, Invalid 7 Errors)", "scripts/verify/scenario_a_cli.py"),
    ("Scenario B (API Happy Path & Metrics Persist)", "scripts/verify/scenario_b_happy_path.py"),
    ("Scenario C (WebSocket Real-Time Streaming & IDOR)", "scripts/verify/scenario_c_ws_stream.py"),
    ("Scenario D (Mid-run Cooperative Stop on 200k rows)", "scripts/verify/scenario_d_stop.py"),
    ("Scenario E (Failure Isolation & Exponential Retry)", "scripts/verify/scenario_e_failure_retry.py"),
    ("Scenario F (Security: IDOR & Expression AST Defense)", "scripts/verify/scenario_f_security.py"),
    ("Scenario G (Concurrency: 5 Parallel Runs & Health)", "scripts/verify/scenario_g_concurrency.py"),
    ("Scenario H (Resilience: Orphan Run Startup Recovery)", "scripts/verify/scenario_h_resilience.py"),
    ("Scenario I (Robustness: Empty, BOM, Unicode, Missing)", "scripts/verify/scenario_i_robustness.py"),
    ("Scenario J (Memory Boundedness & Reclaim on 200k rows)", "scripts/verify/scenario_j_memory.py"),
]

def main():
    print("=" * 80)
    print("CHAOSFLOW PHASE 2 END-TO-END VERIFICATION SUITE")
    print("=" * 80)

    results = []
    overall_start = time.monotonic()

    for name, script_path in SCENARIOS:
        print(f"\n---> Running {name}...")
        t0 = time.monotonic()
        p = subprocess.run([sys.executable, script_path], capture_output=True, text=True)
        duration = time.monotonic() - t0

        if p.returncode == 0:
            print(f"  [PASS] {name} ({duration:.2f}s)")
            results.append((name, "PASS", duration, ""))
        else:
            print(f"  [FAIL] {name} ({duration:.2f}s)")
            print(f"STDOUT:\n{p.stdout}")
            print(f"STDERR:\n{p.stderr}")
            results.append((name, "FAIL", duration, p.stderr))

    total_time = time.monotonic() - overall_start

    print("\n" + "=" * 80)
    print("VERIFICATION SUMMARY")
    print("=" * 80)
    all_passed = True
    for name, status, duration, err in results:
        status_str = f"[{status}]"
        print(f"  {status_str:8s} {name:60s} ({duration:.2f}s)")
        if status != "PASS":
            all_passed = False

    print("-" * 80)
    print(f"Total time: {total_time:.2f}s")
    if all_passed:
        print("ALL 10 VERIFICATION SCENARIOS PASSED WITH ZERO FAILURES!")
        print("=" * 80)
        sys.exit(0)
    else:
        print("SOME SCENARIOS FAILED.")
        print("=" * 80)
        sys.exit(1)

if __name__ == "__main__":
    main()
