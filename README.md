# AeroOS - Telemetry-Driven Feed-Forward Micro-Ventilation

A formal, read-only monitoring dashboard and predictive control engine. It collects live system telemetry, ingests ESP32 sensor packets over local UDP, executes a trained Ridge Regression model to forecast air quality response (+30s horizon), and dispatches pre-emptive feed-forward ventilation commands to an ESP32 microcontroller controlling a 5V exhaust fan.

---

### Project Architecture & Operation

1. **Host Telemetry**: Collected via Python `psutil` (CPU %, RAM %, uptime, process table, and compiler/container workload detection).
2. **ESP32 Telemetry**: Broadcast every 2 seconds over UDP to `0.0.0.0:5006` containing `mq135`, `temp`, `hum`, `pir`, `fan`, and `src`.
3. **Ridge Regression Inference**: Pure-Python model (`models/ridge_model.json`) predicting future MQ-135 raw response from host metrics and sensor readings.
4. **Feed-Forward Control**: If predicted MQ-135 reaches or exceeds threshold (850 raw ADC), dispatches pre-emptive actuation `{"cmd":"fan","state":1,"src":1}` to the ESP32.
5. **Local Reactive Fallback**: If MQ-135 reaches raw 1000, ESP32 local firmware activates ventilation autonomously (`src: 2`).
6. **Data Integrity Logging**: Appends rows every 2 seconds to `data/log.csv`. Host timestamps remain distinct from ESP32 timestamps; missing readings during offline states are recorded as empty fields, never substituted with artificial zeros.

---

### Quick Start Guide

1. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Launch Backend Daemon & Dashboard**
   ```bash
   python3 app.py
   ```
   *Binds to `0.0.0.0:5000` (web dashboard) and `0.0.0.0:5006` (UDP telemetry listener).*

3. **Open Monitoring Dashboard**
   Navigate in your browser to:
   ```
   http://localhost:5000
   ```
   *(Or `http://<laptop-wifi-ip>:5000` from any mobile device connected to the same local Wi-Fi).*

4. **Test with Simulated Node (Telemetry + Actuation)**
   In a separate terminal, run:
   ```bash
   python3 tools/fake_esp32.py
   ```
   *Simulates an ESP32 broadcasting to port 5006 and receiving host feed-forward commands on port 5007.*

5. **Configuring Physical ESP32 Node**
   In the ESP32 Arduino/C++ firmware:
   - Destination IP: Laptop Wi-Fi IP (e.g. `192.168.1.X`)
   - Telemetry TX Port: `5006`
   - Command RX Port: `5007` (listen for incoming JSON `{"cmd":"fan","state":1,"src":1}`)

---

### Dashboard Navigation & Panels

The interface features a responsive left navigation sidebar with dedicated functional panels:

- **Live Monitor (`#tab-live`)**: Real-time overview of current ESP32 telemetry (MQ-135, Temperature, Humidity, PIR Occupancy, Fan Actuator state, and Packet Counter), host CPU/RAM summary progress bars, and prominent offline warning banner when telemetry packets stop.
- **Predictive Engine (`#tab-predictive`)**: Ridge Regression model inference statistics ($\hat{y}$ forecast, horizon, threshold, control mode), UDP dispatch state, ESP32 feedback tracking, and the full interactive 7-stage Live System Pipeline diagram.
- **Telemetry Trends (`#tab-trends`)**: Dual-axis HTML5 Canvas time-series plot comparing Host CPU Utilization (%) vs. MQ-135 Raw ADC readings over a rolling 60-sample window, with zero-fabrication gap rendering.
- **Process Inspector (`#tab-processes`)**: System load analytics, recognized heavy compiler/container task badge (`gcc`, `g++`, `docker`, `python3`), and top 10 memory-consuming processes table.
- **Data Logs & Export (`#tab-logs`)**: Logging daemon status, sample counter, zero-fabrication research standards statement, and one-click direct CSV export.

