#!/usr/bin/env python3
"""Accurate MJPEG frame-rate measurement via a buffered multipart parser.

Parses --frame boundaries + headers and consumes exactly Content-Length
bytes per frame, so boundaries can never be double-counted.
"""
import sys
import time
import statistics
from urllib.request import urlopen
from collections import Counter

URL = "http://10.96.103.61:81/"
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
BOUNDARY = b"--frame"

times = []   # monotonic time when each frame finished arriving
sizes = []   # frame payload sizes
start = time.monotonic()
deadline = start + DURATION

with urlopen(URL, timeout=30) as resp:
    buf = b""
    while time.monotonic() < deadline:
        # 1. wait for the boundary marker
        idx = buf.find(BOUNDARY)
        while idx == -1:
            chunk = resp.read(65536)
            if not chunk:
                break
            buf += chunk
            idx = buf.find(BOUNDARY)
        if idx == -1:
            break
        buf = buf[idx + len(BOUNDARY):]

        # 2. read headers up to the blank line
        hdr_end = buf.find(b"\r\n\r\n")
        while hdr_end == -1:
            chunk = resp.read(65536)
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
            print("WARN: no Content-Length in headers:", headers[:120])
            break

        # 3. consume exactly the frame payload
        while len(buf) < clen:
            chunk = resp.read(65536)
            if not chunk:
                break
            buf += chunk
        if len(buf) < clen:
            break
        buf = buf[clen:]

        times.append(time.monotonic() - start)
        sizes.append(clen)

n = len(times)
if n < 2:
    print(f"Only {n} frames captured")
    sys.exit(1)
span = times[-1] - times[0]
ivals_ms = sorted((times[i + 1] - times[i]) * 1000 for i in range(n - 1))
med = statistics.median(ivals_ms)

print("=" * 62)
print(f"Stream        : {URL}")
print(f"Frames parsed : {n}")
print(f"Capture span  : {span:.2f}s")
print(f"Average FPS   : {(n - 1) / span:.2f}")
print(f"Median FPS    : {1000 / med:.2f}   (median interval {med:.1f} ms)")
print(f"Mean interval : {statistics.mean(ivals_ms):.1f} ms")
print(f"Frame size    : min {min(sizes) // 1024} KB / max {max(sizes) // 1024} KB / avg {sum(sizes) // n // 1024} KB")
print(f"Data received : {sum(sizes) / 1048576:.2f} MB")
print("\ninterval histogram (ms -> fps):")
for lo, hi, label in [(0, 80, ">=12.5 fps"), (80, 120, "8.3-12.5"), (120, 200, "5-8.3"),
                      (200, 400, "2.5-5"), (400, 10 ** 9, "<2.5")]:
    c = sum(1 for iv in ivals_ms if lo <= iv < hi)
    print(f"  {lo:>4}-{hi:<7}ms ({label:>9}): {c:>4} {'#' * (c * 50 // max(len(ivals_ms), 1))}")
print("\nper-second frame counts:")
secs = Counter(int(t) for t in times)
for s in range(int(times[-1]) + 1):
    c = secs.get(s, 0)
    print(f"  second {s:>2}: {c:>3} frames")
print("=" * 62)
