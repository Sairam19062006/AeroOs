#!/usr/bin/env python3
"""
AeroOS USB-to-UDP Serial Bridge
Reads real sensor data from ESP32 connected via USB (/dev/ttyUSB0)
and forwards standard telemetry packets to the AeroOS backend (UDP 127.0.0.1:5006).
"""

import sys
import time
import socket
import json
import re

try:
    import serial
except ImportError:
    print("[ERROR] pyserial is not installed. Run: pip install pyserial")
    sys.exit(1)

PORT = "/dev/ttyUSB0"
BAUD = 115200
UDP_TARGET_IP = "127.0.0.1"
UDP_TARGET_PORT = 5006

def run_bridge():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    print("=" * 60)
    print(" AeroOS Physical ESP32 USB Serial Bridge")
    print(f" Reading from: {PORT} @ {BAUD} baud")
    print(f" Forwarding to: {UDP_TARGET_IP}:{UDP_TARGET_PORT}")
    print("=" * 60)
    print("NOTE: Make sure the Serial Monitor in Arduino IDE is closed")
    print("      so this script can access /dev/ttyUSB0.")
    print("=" * 60)

    while True:
        try:
            print(f"[CONNECTING] Opening {PORT} @ {BAUD} baud...")
            ser = serial.Serial(PORT, BAUD, timeout=1)
            time.sleep(1.5)
            ser.reset_input_buffer()
            print(f"[OK] Successfully connected to physical ESP32 on {PORT}!\n")

            curr_temp = None
            curr_hum = None
            curr_mq135 = None
            curr_fan = 0
            seq = 0

            while True:
                try:
                    line_bytes = ser.readline()
                except (serial.SerialException, OSError) as e:
                    print(f"\n[WARNING] Serial read interrupted: {e}")
                    break

                if not line_bytes:
                    continue
                    
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                    
                print(f"[ESP32 Serial] {line}")

                # Check if ESP32 outputted direct JSON
                if line.startswith("{") and line.endswith("}"):
                    try:
                        payload = json.loads(line)
                        payload["simulated"] = 0
                        sock.sendto(json.dumps(payload).encode("utf-8"), (UDP_TARGET_IP, UDP_TARGET_PORT))
                        print(f"-> [BRIDGE JSON] Forwarded {payload} to dashboard")
                        continue
                    except Exception:
                        pass

                # Parse lines from sketch_oct22a.ino
                m_temp = re.search(r"Temperature:\s*([0-9.]+)", line, re.IGNORECASE)
                if m_temp:
                    curr_temp = float(m_temp.group(1))

                m_hum = re.search(r"Humidity:\s*([0-9.]+)", line, re.IGNORECASE)
                if m_hum:
                    curr_hum = float(m_hum.group(1))

                m_mq = re.search(r"MQ-135 Raw Value:\s*([0-9]+)", line, re.IGNORECASE)
                if m_mq:
                    curr_mq135 = int(m_mq.group(1))

                if "RELAY: ON" in line:
                    curr_fan = 1
                elif "RELAY: OFF" in line:
                    curr_fan = 0

                # Once we have valid values, broadcast to dashboard
                if curr_mq135 is not None and (curr_temp is not None or curr_hum is not None):
                    seq += 1
                    telemetry = {
                        "seq": seq,
                        "mq135": curr_mq135,
                        "temp": curr_temp if curr_temp is not None else 25.0,
                        "hum": curr_hum if curr_hum is not None else 50.0,
                        "pir": 1,
                        "fan": curr_fan,
                        "src": 2 if curr_fan == 1 else 0,
                        "dht_ok": 1,
                        "simulated": 0
                    }
                    sock.sendto(json.dumps(telemetry).encode("utf-8"), (UDP_TARGET_IP, UDP_TARGET_PORT))
                    print(f"-> [FORWARD TO DASHBOARD] MQ-135: {curr_mq135} | Temp: {curr_temp}°C | Hum: {curr_hum}% | Fan: {curr_fan}")
                    curr_mq135 = None

        except serial.SerialException as e:
            print(f"[RETRY] Could not connect to {PORT} ({e}). Retrying in 2 seconds...")
            time.sleep(2)
        except KeyboardInterrupt:
            print("\nStopping serial bridge.")
            break
        finally:
            try:
                ser.close()
            except Exception:
                pass


if __name__ == "__main__":
    run_bridge()
