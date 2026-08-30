#!/usr/bin/env python3
"""Analyze MJPEG frame timing distribution in more detail."""
import sys
import time
from urllib.request import urlopen
from collections import Counter

URL = "http://10.96.103.61:81/"
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
BOUNDARY = b"--frame"

times = []
sizes = []
start = time.monotonic()
with urlopen(URL, timeout=30) as resp:
    buf = b""
    while time.monotonic() - start < DURATION:
        chunk = resp.read(65536)
        if not chunk:
            break
        buf += chunk
        idx = buf.find(BOUNDARY)
        while idx != -1:
            times.append(time.monotonic() - start)
            idx = buf.find(BOUNDARY, idx + len(BOUNDARY))
        if len(buf) > len(BOUNDARY) + 1024:
            buf = buf[-len(BOUNDARY) - 1:]

n = len(times)
print(f"frames: {n} in {times[-1] - times[0]:.2f}s -> avg {n/(times[-1]-times[0]):.2f} fps")

# filter out sub-ms intervals (duplicate/empty boundaries)
ivals = [ (times[i+1]-times[i])*1000 for i in range(n-1) ]
real = [iv for iv in ivals if iv >= 1.0]
dups = [iv for iv in ivals if iv < 1.0]
print(f"intervals <1ms (dup boundaries): {len(dups)}")
print(f"real frame intervals: {len(real)}")

if real:
    real.sort()
    # histogram
    buckets = [(0, 40, "25+ fps"), (40, 80, "12.5-25"), (80, 120, "8.3-12.5"),
               (120, 200, "5-8.3"), (200, 400, "2.5-5"), (400, 10**9, "<2.5")]
    print("\ninterval histogram (ms -> fps):")
    for lo, hi, label in buckets:
        c = sum(1 for iv in real if lo <= iv < hi)
        bar = "#" * (c * 40 // max(len(real), 1))
        print(f"  {lo:>4}-{hi:<4}ms ({label:>9}): {c:>4} {bar}")
    import statistics
    print(f"\nmedian interval: {statistics.median(real):.1f} ms -> {1000/statistics.median(real):.1f} fps")
    print(f"mean interval  : {statistics.mean(real):.1f} ms -> {1000/statistics.mean(real):.1f} fps")
    # fps per second of capture
    print("\nper-second frame counts:")
    secs = Counter(int(t) for t in times)
    for s in range(int(times[-1]) + 1):
        c = secs.get(s, 0)
        print(f"  second {s:>2}: {c:>3} frames {'#'*min(c,60)}")
