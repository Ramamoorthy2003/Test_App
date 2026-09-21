"""
Fake ESP32. Run this on a laptop on the same WiFi as the phone.

    python test_sender.py 192.168.1.50

Then type:  a  (A hit)   b  (B hit)   ap  (A penalty)   bp  (B penalty)   q (quit)
"""
import socket
import sys

ip = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
port = int(sys.argv[2]) if len(sys.argv) > 2 else 5005

sock = socket.create_connection((ip, port), timeout=5)
print('connected to %s:%d' % (ip, port))

MAP = {'a': 'A:HIT', 'b': 'B:HIT', 'ap': 'A:PENALTY', 'bp': 'B:PENALTY'}

try:
    while True:
        key = input('> ').strip().lower()
        if key in ('q', 'quit', 'exit'):
            break
        cmd = MAP.get(key)
        if not cmd:
            print('use: a / b / ap / bp / q')
            continue
        sock.sendall((cmd + '\n').encode())
        print('sent', cmd)
finally:
    sock.close()
