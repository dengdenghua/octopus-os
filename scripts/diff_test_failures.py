"""Diff baseline failures vs post-refactor failures."""
import sys


def parse(path):
    s = set()
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("FAILED ") or line.startswith("ERROR "):
                # Strip trailing reason " - foo..."
                key = line.split(" - ", 1)[0]
                # FAILED tests/foo.py::test_x  -> normalize
                s.add(key)
    return s

baseline = parse(sys.argv[1])
after = parse(sys.argv[2])

new_failures = after - baseline
fixed = baseline - after
common = baseline & after

print(f"Baseline failures: {len(baseline)}")
print(f"After failures:    {len(after)}")
print(f"Common (still fail): {len(common)}")
print(f"FIXED (no longer fail):  {len(fixed)}")
print(f"NEW REGRESSIONS:         {len(new_failures)}")
if new_failures:
    print()
    print("=== NEW REGRESSIONS (must investigate) ===")
    for f in sorted(new_failures):
        print(f"  {f}")
if fixed:
    print()
    print("=== FIXED (likely flaky in baseline) ===")
    for f in sorted(fixed):
        print(f"  {f}")
