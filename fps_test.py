#!/usr/bin/env python3
"""Measure the frame rate of an MJPEG (multipart/x-mixed-replace) stream."""
import sys
import time
import socket
import re

URL = "http://10.96.103.61:81/"
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
BOUNDARY = b"--frame"

def measure(url, duration):
    from urllib.request import urlopen
    times = []  # wall-clock time when each frame's boundary was seen
    bytes_total = 0
    start = time.monotonic()
    with urlopen(url, timeout=30) as resp:
        # read raw chunks and scan for the boundary marker
        buf = b""
        while time.monotonic() - start < duration:
            chunk = resp.read(65536)
            if not chunk:
                break
            bytes_total += len(chunk)
            buf += chunk
            # count boundaries in the buffered data
            idx = buf.find(BOUNDARY)
            while idx != -1:
                times.append(time.monotonic() - start)
                idx = buf.find(BOUNDARY, idx + len(BOUNDARY))
            # keep a small tail to avoid missing boundaries split across chunks
            if len(buf) > len(BOUNDARY) + 1024:
                tail = buf[-len(BOUNDARY) - 1:]
                buf = tail
    return times, bytes_total

times, bytes_total = measure(URL, DURATION)
n = len(times)
elapsed = times[-1] - times[0] if n >= 2 else 0.0
avg_fps = (n - 1) / elapsed if elapsed > 0 else 0.0

# instantaneous frame intervals (fps between consecutive frames)
if n >= 2:
    intervals = [times[i + 1] - times[i] for i in range(n - 1)]
    inst_fps = [1.0 / iv for iv in intervals if iv > 0]
    min_fps = min(inst_fps)
    max_fps = max(inst_fps)
    median_iv = sorted(intervals)[len(intervals) // 2]
else:
    intervals = inst_fps = []
    min_fps = max_fps = 0.0
    median_iv = 0.0

print("=" * 60)
print(f"Stream        : {URL}")
print(f"Capture window: {DURATION:.1f}s (measure span {elapsed:.2f}s)")
print(f"Frames seen   : {n}")
print(f"Data received : {bytes_total / 1024 / 1024:.2f} MB")
if n >= 2:
    print(f"Average FPS   : {avg_fps:.2f}")
    print(f"Min/Max FPS   : {min_fps:.2f} / {max_fps:.2f}")
    print(f"Median frame interval: {median_iv * 1000:.1f} ms")
else:
    print("No frames captured!")
print("=" * 60)
