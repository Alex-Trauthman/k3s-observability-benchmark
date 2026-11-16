#!/usr/bin/env bash
ACTION="$1"
if [ -f /tmp/stress.pids ]; then
    kill -9 $(cat /tmp/stress.pids) 2>/dev/null || true
    rm -f /tmp/stress.pids
fi
rm -f /tmp/io_stress.bin

if [ "$ACTION" = "S1_CPU_Saturation" ]; then
    python3 -c "import time; t=time.time()+75
while time.time()<t: pass" >/dev/null 2>&1 &
    echo $! >> /tmp/stress.pids
    python3 -c "import time; t=time.time()+75
while time.time()<t: pass" >/dev/null 2>&1 &
    echo $! >> /tmp/stress.pids
elif [ "$ACTION" = "S2_RAM_Pressure" ]; then
    python3 -c "import time
b = bytearray(270 * 1024 * 1024)
t = time.time() + 75
i = 0
while time.time() < t:
    b[i % len(b)] = (i % 251)
    i += 4096
    if i % (16*1024*1024) == 0: time.sleep(0.02)" >/dev/null 2>&1 &
    echo $! >> /tmp/stress.pids
elif [ "$ACTION" = "S3_Disk_IO_Saturation" ]; then
    python3 -c "import os, time
f = open('/tmp/io_stress.bin', 'wb')
buf = os.urandom(4 * 1024 * 1024)
t = time.time() + 75
while time.time() < t:
    f.seek(0)
    f.write(buf)
    f.flush()
    os.fsync(f.fileno())
f.close()" >/dev/null 2>&1 &
    echo $! >> /tmp/stress.pids
fi
