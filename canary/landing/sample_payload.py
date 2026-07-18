#!/usr/bin/env python3
"""Sample payload for the landing kit: bounded CPU and I/O work.

Stands in for a real payload so prmon has something meaningful to
measure: a CPU-bound hash loop with periodic file I/O, running for the
requested number of seconds. Real ePIC payloads arrive with the probe
jobs (PLAN.md increment 7).

Usage: sample_payload.py <seconds>
"""
import hashlib
import os
import sys
import tempfile
import time


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    deadline = time.monotonic() + seconds
    buf = os.urandom(1 << 20)
    digest = b''
    iterations = 0
    with tempfile.TemporaryFile() as f:
        while time.monotonic() < deadline:
            for _ in range(50):
                digest = hashlib.sha256(buf + digest).digest()
            f.write(buf)
            f.flush()
            f.seek(0)
            f.read()
            f.seek(0)
            f.truncate()
            iterations += 1
    print(f'sample payload: {iterations} iterations in {seconds}s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
