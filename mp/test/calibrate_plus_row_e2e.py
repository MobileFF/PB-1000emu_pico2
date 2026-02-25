"""
Calibrate '+' key row for CAL E2E test by sweeping (row,7).

Run:
    %Run -c $EDITOR_CONTENT
or:
    import calibrate_plus_row_e2e as c; c.main()
"""

import test_cal_mode_e2e as t


def main():
    print("PLUS row calibration start")
    print("Each run prints STRICT reason flags; choose row where status_tail_only=0 first.")

    # Keep deterministic single sequence for fair comparison.
    t.RUN_ALL_SEQUENCES = False
    t.ALLOW_MODE_SEQUENCES = False

    for row in range(10):
        print("=" * 70)
        print(f"TRY PLUS KEY: (row={row}, col=7)")
        t.FORCED_TOKEN_KEYS["+"] = (row, 7)
        t.main()


if __name__ == "__main__":
    main()
