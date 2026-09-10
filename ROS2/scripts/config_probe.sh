#!/usr/bin/env bash
LIB=$(find /opt/ros/humble /usr/lib -name "libddsc.so*" 2>/dev/null | head -1)
echo "LIB=${LIB}"
echo "=== shared-memory / multicast related config keys ==="
strings "$LIB" | grep -iE "sharedmemory|maxmessage|multicast|loopback|unicast|unreachable|receive_buffer|socket_buffer|enable" | sort -u | head -80
echo "=== any config docs shipped ==="
find /opt/ros/humble -iname "*.md" -o -iname "*.txt" 2>/dev/null | grep -iE "cyclone|ddsc" | head