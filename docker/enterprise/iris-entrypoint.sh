#!/bin/sh
/iris-main "$@" &
IRIS_PID=$!
for i in $(seq 1 120); do
    if /usr/irissys/bin/iris session IRIS -U "%SYS" "Write 1" >/dev/null 2>&1; then
        /usr/irissys/bin/irispython -m pip install "iris-vector-graph>=1.81.0" numpy --break-system-packages -q 2>/dev/null || true
        break
    fi
    sleep 2
done
wait $IRIS_PID
