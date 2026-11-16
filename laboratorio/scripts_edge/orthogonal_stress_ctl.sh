#!/usr/bin/env bash
# Uso: ~/orthogonal_stress_ctl.sh <CPU_PCT> <RAM_PCT> <DISK_PCT> <NET_PCT>
CPU_PCT="${1:-0}"
RAM_PCT="${2:-0}"
DISK_PCT="${3:-0}"
NET_PCT="${4:-0}"

# 1. Limpa estressores anteriores com precisao via PID file e tc
if [ -f /tmp/ortho_stress.pids ]; then
    kill -9 $(cat /tmp/ortho_stress.pids) 2>/dev/null || true
    rm -f /tmp/ortho_stress.pids
fi
rm -f /tmp/direct_io.bin
sudo /usr/sbin/tc qdisc del dev cni0 root 2>/dev/null || true

if [ "$CPU_PCT" = "stop" ]; then
    exit 0
fi

# 2. CPU ORTOGONAL (2 workers com duty-cycle preciso em janela de 100ms, 0 MB RAM, 0 I/O)
if [ "$CPU_PCT" -gt 0 ]; then
    for w in 1 2; do
        python3 -c "import time
pct = $CPU_PCT / 100.0
busy = 0.08 * pct
idle = 0.08 * (1.0 - pct)
end_t = time.time() + 70
while time.time() < end_t:
    t0 = time.monotonic()
    while (time.monotonic() - t0) < busy:
        pass
    if idle > 0.001:
        time.sleep(idle)
" >/dev/null 2>&1 &
        echo $! >> /tmp/ortho_stress.pids
    done
fi

# 3. RAM ORTOGONAL (Alocacao estatica travada em RAM + sleep profundo = 0.0% CPU, 0 KB/s Disco)
if [ "$RAM_PCT" -gt 0 ]; then
    # 100% da folga segura de teste = 272 MB (25%=68MB, 50%=136MB, 75%=204MB, 100%=272MB)
    MB=$(( (272 * RAM_PCT) / 100 ))
    python3 -c "import time, ctypes
size = $MB * 1024 * 1024
buf = bytearray(size)
# Toca 1 byte por pagina (4KB) uma unica vez na partida para materializar RSS fisico
for i in range(0, size, 4096):
    buf[i] = 1
try:
    libc = ctypes.CDLL('libc.so.6')
    libc.mlock(ctypes.c_char.from_buffer(buf), ctypes.c_size_t(size))
except Exception:
    pass
# Dorme passivamente com 0.0% de CPU e 0 I/O
time.sleep(70)
" >/dev/null 2>&1 &
    echo $! >> /tmp/ortho_stress.pids
fi

# 4. DISCO ORTOGONAL (dd com oflag=direct,dsync -> Bypass total do Page Cache da RAM e <1% CPU)
if [ "$DISK_PCT" -gt 0 ]; then
    case "$DISK_PCT" in
        25) SLEEP_IO="0.18" ;;
        50) SLEEP_IO="0.08" ;;
        75) SLEEP_IO="0.03" ;;
        *)  SLEEP_IO="0.00" ;;
    esac
    sh -c "
    END=\$(( \$(date +%s) + 70 ))
    while [ \$(date +%s) -lt \$END ]; do
        dd if=/dev/zero of=/tmp/direct_io.bin bs=1M count=4 oflag=direct,dsync 2>/dev/null
        if [ '$SLEEP_IO' != '0.00' ]; then sleep $SLEEP_IO; fi
    done
    " >/dev/null 2>&1 &
    echo $! >> /tmp/ortho_stress.pids
fi

# 5. REDE ORTOGONAL (tc qdisc netem na interface cni0 -> 0% CPU extra, 0 MB RAM)
if [ "$NET_PCT" -gt 0 ]; then
    case "$NET_PCT" in
        25) sudo /usr/sbin/tc qdisc replace dev cni0 root netem delay 25ms 10ms loss 5% rate 50mbit ;;
        50) sudo /usr/sbin/tc qdisc replace dev cni0 root netem delay 50ms 20ms loss 10% rate 10mbit ;;
        75) sudo /usr/sbin/tc qdisc replace dev cni0 root netem delay 100ms 35ms loss 18% rate 2mbit ;;
        *)  sudo /usr/sbin/tc qdisc replace dev cni0 root netem delay 150ms 50ms loss 25% rate 512kbit ;;
    esac
fi
