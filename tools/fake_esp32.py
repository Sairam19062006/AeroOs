#!/usr/bin/env python3
"""
Simulated ESP32 UDP Telemetry & Actuation Node for AeroOS.
- Periodically transmits JSON telemetry to the host laptop at 127.0.0.1:5006.
- Listens for incoming UDP host control commands (e.g. pre-emptive fan actuation).
- Simulates local reactive threshold fallback if MQ-135 exceeds 1000.
"""

import socket
import time
import json
import random

HOST_IP = "127.0.0.1"
HOST_UDP_PORT = 5006
CLIENT_UDP_PORT = 5007

def run_fake_esp32():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", CLIENT_UDP_PORT))
    sock.setblocking(False)

    print(f"==================================================")
    print(f" AeroOS Simulated ESP32 Firmware Node")
    print(f" Target Host: {HOST_IP}:{HOST_UDP_PORT}")
    print(f" Local Listen Port: {CLIENT_UDP_PORT}")
    print(f"==================================================")
    print("Press Ctrl+C to terminate.")

    mq135 = 780
    temp = 29.2
    hum = 61.5
    pir = 1
    fan = 0
    src = 0  # 0=off, 1=pre-emptive host, 2=local reactive threshold
    dht_ok = 1
    host_commanded_fan = None
    last_command_time = 0

    try:
        while True:
            loop_start = time.time()

            # Check for incoming UDP control packets from host
            try:
                data, addr = sock.recvfrom(2048)
                cmd_payload = json.loads(data.decode("utf-8", errors="ignore"))
                if isinstance(cmd_payload, dict):
                    cmd_type = cmd_payload.get("cmd")
                    state = cmd_payload.get("state", 0)
                    cmd_src = cmd_payload.get("src", 1)
                    print(f"[{time.strftime('%H:%M:%S')}] <- RECV HOST COMMAND from {addr}: {cmd_payload}")
                    if cmd_type == "fan":
                        host_commanded_fan = (state, cmd_src)
                        last_command_time = time.time()
            except BlockingIOError:
                pass
            except Exception as e:
                pass

            # Natural drift of environment
            mq135 = max(450, min(1350, mq135 + random.randint(-18, 22)))
            temp = round(max(22.0, min(38.0, temp + random.uniform(-0.2, 0.2))), 1)
            hum = round(max(40.0, min(80.0, hum + random.uniform(-0.3, 0.3))), 1)

            # PIR toggle occasionally
            if random.random() < 0.15:
                pir = 1 if pir == 0 else 0

            # Evaluate Actuator State:
            # 1. Local reactive threshold fallback if gas raw >= 1000 (Hardware safety override)
            if mq135 >= 1000:
                fan = 1
                src = 2  # Local reactive threshold
            # 2. Host pre-emptive command (valid for 10s unless updated)
            elif host_commanded_fan is not None and (time.time() - last_command_time) <= 10.0:
                fan = host_commanded_fan[0]
                src = host_commanded_fan[1] if fan == 1 else 0
            else:
                fan = 0
                src = 0

            payload = {
                "mq135": mq135,
                "temp": temp,
                "hum": hum,
                "pir": pir,
                "fan": fan,
                "src": src,
                "dht_ok": dht_ok,
                "simulated": 1
            }

            msg = json.dumps(payload).encode("utf-8")
            sock.sendto(msg, (HOST_IP, HOST_UDP_PORT))
            src_str = "OFF" if src == 0 else ("HOST PRE-EMPTIVE" if src == 1 else "LOCAL REACTIVE")
            print(f"[{time.strftime('%H:%M:%S')}] -> TX Telemetry: MQ-135={mq135}, PIR={pir}, Fan={fan} ({src_str})")

            # Sleep remainder of 2.0s
            elapsed = time.time() - loop_start
            sleep_time = max(0.1, 2.0 - elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("\nSimulated ESP32 sender stopped.")
    finally:
        sock.close()

if __name__ == "__main__":
    run_fake_esp32()
