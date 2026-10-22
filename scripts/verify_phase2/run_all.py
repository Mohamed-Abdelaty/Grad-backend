"""Main test runner for Phase 2 Practical Verifications."""

from __future__ import annotations

import sys
import time

from scripts.verify_phase2.verify_d_dst_timezone import verify_dst_and_timezones
from scripts.verify_phase2.verify_f_webhook_signature import verify_webhook_signature
from scripts.verify_phase2.verify_j_templates import verify_templates
from scripts.verify_phase2.verify_k_diff_rollback import verify_diff_and_rollback
from scripts.verify_phase2.verify_l_secret_leak import verify_secret_leak_sweep
from scripts.verify_phase2.verify_m_idor import verify_idor_sweep
from scripts.verify_phase2.verify_n_load_robustness import verify_load_and_robustness


def main() -> None:
    print("=" * 70)
    print("   ChaosFlow Phase 2 Practical Verification Suite")
    print("=" * 70)

    start = time.time()
    steps = [
        ("D. DST & Timezone Preview", verify_dst_and_timezones),
        ("F. Webhook HMAC & Tamper Resistance", verify_webhook_signature),
        ("J. Templates Seeding, Visibility & Atomic Usage", verify_templates),
        ("K. Diff & Rollback Immutability", verify_diff_and_rollback),
        ("L. Secret Leak Sweep (Canary tokens)", verify_secret_leak_sweep),
        ("M. IDOR Sweep Across All Phase 2 Endpoints", verify_idor_sweep),
        ("N. Load & Robustness Fuzzing", verify_load_and_robustness),
    ]

    passed = 0
    failed = 0
    for name, fn in steps:
        print(f"\n--- Running: {name} ---")
        try:
            fn()
            passed += 1
        except Exception as exc:
            print(f"[FAIL] {name} failed: {exc}")
            import traceback
            traceback.print_exc()
            failed += 1

    duration = time.time() - start
    print("\n" + "=" * 70)
    print(f"Results: {passed} PASSED, {failed} FAILED in {duration:.2f}s")
    print("=" * 70)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
