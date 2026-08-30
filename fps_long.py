#!/usr/bin/env python3
"""Measure MJPEG frame rate: long-window average + boundary-completion timing.

Reads with a small buffer so frame start times are captured with minimal
read-size quantization; the long-run average is the robust FPS estimate.
"""
import sys
import time
import statistics
from urllib.request import urlopen
from collections import Counter

URL = "http://10.96.103.61:81/"
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
BOUNDARY = b"--frame"
CHUNK = 4096

start = time.monotonic()
deadline = start + DURATION
boundary_times = []   # time each '--frame' marker completes
sizes = []

with urlopen(URL, timeout=30) as resp:
    buf = b""
    while time.monotonic() < deadline:
        # consume up to the next boundary
        idx = buf.find(BOUNDARY)
        while idx == -1:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            buf += chunk
            idx = buf.find(BOUNDARY)
        if idx == -1:
            break
        now = time.monotonic() - start
        buf = buf[idx + len(BOUNDARY):]
        boundary_times.append(now)

        # headers
        hdr_end = buf.find(b"\r\n\r\n")
        while hdr_end == -1:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            buf += chunk
            hdr_end = buf.find(b"\r\n\r\n")
        if hdr_end == -1:
            break
        headers = buf[:hdr_end]
        buf = buf[hdr_end + 4:]
        clen = None
        for line in headers.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                clen = int(line.split(b":", 1)[1].strip())
        if clen is None:
            break
        while len(buf) < clen:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            buf += chunk
        buf = buf[clen:]
        sizes.append(clen)

n = len(boundary_times)
print(f"frames: {n}")

# frame *start* intervals
ivals_ms = [ (boundary_times[i+1] - boundary_times[i]) * 1000 for i in range(n-1) ]
ivals_ms.sort()
span = boundary_times[-1] - boundary_times[0]
print("=" * 62)
print(f"Stream        : {URL}")
print(f"Frames        : {n}")
print(f"Measure span  : {span:.2f}s")
print(f"Average FPS   : {(n - 1) / span:.2f}")
print(f"Median FPS    : {1000 / statistics.median(ivals_ms):.2f}  (median interval {statistics.median(ivals_ms):.1f} ms)")
print(f"Mean interval : {statistics.mean(ivals_ms):.1f} ms")
if sizes:
    print(f"Frame size    : avg {sum(sizes)//n//1024} KB (min {min(sizes)//1024}, max {max(sizes)//1024})")
print(f"Data received : {sum(sizes)/1048576:.2f} MB")
print("\ninterval histogram (ms -> fps):")
for lo, hi, label in [(0, 60, ">=16.7 fps"), (60, 80, "12.5-16.7"), (80, 120, "8.3-12.5"),
                      (120, 200, "5-8.3"), (200, 400, "2.5-5"), (400, 10**9, "<2.5")]:
    c = sum(1 for iv in ivals_ms if lo <= iv < hi)
    print(f"  {lo:>4}-{hi:<7}ms ({label:>9}): {c:>4} {'#' * (c * 50 // max(len(ivals_ms),1))}")
print("\nper-second frame counts:")
secs = Counter(int(t) for t in boundary_times)
for s in range(int(boundary_times[-1]) + 1):
    c = secs.get(s, 0)
    print(f"  second {s:>2}: {c:>3} frames")
print("=" * 62)
