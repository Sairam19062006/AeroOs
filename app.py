import os
import sys
import time
import json
import socket
import threading
import csv
from datetime import datetime
from collections import deque
from flask import Flask, render_template, jsonify, send_file
import psutil
from tools.train_model import train_and_export

UDP_PORT = 5006
LOG_FILE_PATH = os.path.join(os.path.dirname(__file__), "data", "log.csv")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "ridge_model.json")
HEAVY_PROCESS_NAMES = {"gcc", "g++", "docker", "python3"}

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

# Thread-safe synchronization
state_lock = threading.Lock()

# Rolling telemetry history for frontend canvas plot (bounded to 60 samples)
telemetry_history = deque(maxlen=60)

# Global State
esp32_state = {
    "last_packet_time": None,
    "last_packet_time_str": None,
    "last_addr": None,
    "data": None,
    "is_simulated": False,
    "has_received_data": False
}

model_state = {
    "status": "NOT LOADED",
    "metadata": None,
    "last_prediction": None,
    "last_prediction_time": None
}

control_state = {
    "decision": "IDLE",  # IDLE, PRE-EMPTIVE, REACTIVE FALLBACK
    "latest_command_sent": None,
    "last_command_time": 0,
    "last_command_addr": None
}

logging_state = {
    "status": "ACTIVE",
    "last_write_time": None,
    "sample_count": 0
}

# Global Autonomous Retraining State
auto_retrain_state = {
    "status": "ACTIVE",              # "ACTIVE", "TRAINING", "ERROR"
    "enabled": True,
    "last_retrain_time": None,
    "samples_used": 0,
    "r2_score": None,
    "mae": None,
    "rmse": None,
    "engine": "Autonomous Daemon",
    "retrain_count": 0,
    "trigger_interval_samples": 30,  # Retrain every ~30 new samples (~60s of telemetry)
    "last_retrained_sample_count": 0,
    "message": "Autonomous background retrainer standing by."
}

cpu_sample_count = 0

# --- RIDGE REGRESSION MODEL ENGINE ---
class RidgeModelEngine:
    def __init__(self, model_file):
        self.model_file = model_file
        self.status = "NOT LOADED"
        self.model_data = None
        self.last_mtime = 0
        self.load_model()

    def load_model(self):
        if not os.path.exists(self.model_file):
            self.status = "NOT LOADED"
            self.model_data = None
            self.last_mtime = 0
            return False

        try:
            mtime = os.path.getmtime(self.model_file)
            with open(self.model_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Validate required fields
            required_keys = ["model_type", "target", "features", "coefficients", "intercept", "threshold", "horizon_seconds"]
            for k in required_keys:
                if k not in data:
                    self.status = "ERROR"
                    self.model_data = None
                    return False

            self.model_data = data
            self.status = "LOADED"
            self.last_mtime = mtime
            return True
        except Exception:
            self.status = "ERROR"
            self.model_data = None
            return False

    def refresh_model(self):
        if not os.path.exists(self.model_file):
            self.status = "NOT LOADED"
            self.model_data = None
            self.last_mtime = 0
            return False
        # If file exists, reload if status not LOADED or file timestamp changed
        try:
            mtime = os.path.getmtime(self.model_file)
            if self.status != "LOADED" or not self.model_data or mtime != self.last_mtime:
                return self.load_model()
        except Exception:
            pass
        return True

    def predict(self, feature_dict):
        if self.status != "LOADED" or not self.model_data:
            return None

        try:
            coeffs = self.model_data.get("coefficients", {})
            intercept = float(self.model_data.get("intercept", 0.0))
            y_pred = intercept

            for feat_name, weight in coeffs.items():
                val = feature_dict.get(feat_name)
                if val is None:
                    # Missing feature, cannot perform reliable inference
                    return None
                y_pred += float(weight) * float(val)

            return round(max(0.0, y_pred), 1)
        except Exception:
            return None

model_engine = RidgeModelEngine(MODEL_PATH)

# Seed auto-retrain state from existing model artifact if available
if model_engine.status == "LOADED" and model_engine.model_data:
    tm = model_engine.model_data.get("training_metrics", {})
    auto_retrain_state["last_retrain_time"] = model_engine.model_data.get("trained_timestamp")
    auto_retrain_state["samples_used"] = tm.get("samples_used", 0)
    auto_retrain_state["r2_score"] = tm.get("r2_score")
    auto_retrain_state["mae"] = tm.get("mae")
    auto_retrain_state["rmse"] = tm.get("rmse")
    auto_retrain_state["engine"] = tm.get("engine", "Normal Equations")

# --- AUTONOMOUS BACKGROUND RETRAINING WORKER ---
def execute_background_retrain(reason="Scheduled"):
    """
    Executes model retraining in a non-blocking background thread.
    Hot-reloads weights into model_engine and updates auto_retrain_state.
    """
    def _worker():
        global auto_retrain_state
        with state_lock:
            if auto_retrain_state["status"] == "TRAINING":
                return
            auto_retrain_state["status"] = "TRAINING"
            auto_retrain_state["message"] = f"Retraining in background ({reason})..."

        try:
            artifact = train_and_export(LOG_FILE_PATH, MODEL_PATH)
            model_engine.refresh_model()

            with state_lock:
                metrics = artifact.get("training_metrics", {})
                curr_cnt = logging_state["sample_count"]
                auto_retrain_state["status"] = "ACTIVE"
                auto_retrain_state["last_retrain_time"] = artifact.get("trained_timestamp")
                auto_retrain_state["samples_used"] = metrics.get("samples_used", 0)
                auto_retrain_state["r2_score"] = metrics.get("r2_score")
                auto_retrain_state["mae"] = metrics.get("mae")
                auto_retrain_state["rmse"] = metrics.get("rmse")
                auto_retrain_state["engine"] = metrics.get("engine", "Normal Equations")
                auto_retrain_state["retrain_count"] += 1
                auto_retrain_state["last_retrained_sample_count"] = curr_cnt
                auto_retrain_state["message"] = f"Auto-retrained successfully ({metrics.get('samples_used')} pairs, R²={metrics.get('r2_score')})."
        except Exception as e:
            with state_lock:
                auto_retrain_state["status"] = "ACTIVE"
                auto_retrain_state["message"] = f"Retrain cycle skipped: {str(e)}"

    worker_thread = threading.Thread(target=_worker, daemon=True)
    worker_thread.start()
    return worker_thread

def auto_retrain_daemon_loop():
    """
    Autonomous background loop: periodically checks whether new telemetry
    samples have accumulated in data/log.csv, and triggers model retraining.
    """
    global auto_retrain_state
    while True:
        time.sleep(10)
        try:
            with state_lock:
                enabled = auto_retrain_state["enabled"]
                status = auto_retrain_state["status"]
                current_cnt = logging_state["sample_count"]
                last_cnt = auto_retrain_state["last_retrained_sample_count"]
                thresh = auto_retrain_state["trigger_interval_samples"]

            if not enabled or status == "TRAINING":
                continue

            # Retrain if:
            # 1. No retrain has ever occurred and we have accumulated >= 20 samples
            # 2. Accumulated >= trigger_interval_samples (e.g. 30 samples) since last retrain
            if (last_cnt == 0 and current_cnt >= 20) or ((current_cnt - last_cnt) >= thresh):
                execute_background_retrain(reason=f"+{current_cnt - last_cnt} new samples")
        except Exception:
            continue

# --- UDP TELEMETRY LISTENER ---
def udp_listener():
    global esp32_state
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", UDP_PORT))

    while True:
        try:
            data, addr = sock.recvfrom(2048)
            payload = json.loads(data.decode("utf-8", errors="ignore"))
            if isinstance(payload, dict):
                now = time.time()
                ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with state_lock:
                    esp32_state["last_packet_time"] = now
                    esp32_state["last_packet_time_str"] = ts_str
                    esp32_state["last_addr"] = addr
                    esp32_state["data"] = payload
                    esp32_state["is_simulated"] = bool(payload.get("simulated", False))
                    esp32_state["has_received_data"] = True
        except Exception:
            continue

# --- UDP COMMAND DISPATCHER ---
def send_udp_command(target_addr, command_dict):
    """Safely dispatches a UDP command packet to the ESP32 node."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        msg = json.dumps(command_dict).encode("utf-8")
        sock.sendto(msg, target_addr)
        # Also ensure delivery to standard hardware command listen port 5007 if source port was ephemeral
        if target_addr and len(target_addr) == 2 and target_addr[1] != 5007:
            try:
                sock.sendto(msg, (target_addr[0], 5007))
            except Exception:
                pass
        sock.close()
        return True
    except Exception:
        return False

# --- SYSTEM METRICS & PROCESSES ---
def get_system_metrics():
    global cpu_sample_count
    cpu_pct = psutil.cpu_percent(interval=None)
    cpu_sample_count += 1
    cpu_ready = cpu_sample_count > 1

    mem = psutil.virtual_memory()
    ram_pct = mem.percent
    ram_used_mb = round(mem.used / (1024 * 1024), 1)
    ram_total_mb = round(mem.total / (1024 * 1024), 1)
    proc_count = len(psutil.pids())

    boot_t = psutil.boot_time()
    uptime_sec = int(time.time() - boot_t)
    hours, remainder = divmod(uptime_sec, 3600)
    minutes, _ = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m"

    return {
        "cpu_percent": cpu_pct,
        "cpu_ready": cpu_ready,
        "ram_percent": ram_pct,
        "ram_used_mb": ram_used_mb,
        "ram_total_mb": ram_total_mb,
        "process_count": proc_count,
        "uptime": uptime_str
    }

def get_processes_and_heavy_tasks():
    current_pid = os.getpid()
    proc_list = []
    heavy_matches = set()

    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
        try:
            info = p.info
            pid = info["pid"]
            name = info["name"] or ""

            # Check for heavy processes, strictly excluding Flask process
            if pid != current_pid and name.lower() in HEAVY_PROCESS_NAMES:
                heavy_matches.add(name)

            mem_info = info["memory_info"]
            ram_mb = round(mem_info.rss / (1024 * 1024), 1) if mem_info else 0.0
            cpu = info["cpu_percent"] if info["cpu_percent"] is not None else 0.0

            proc_list.append({
                "pid": pid,
                "name": name,
                "cpu_percent": round(cpu, 1),
                "ram_mb": ram_mb
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    proc_list.sort(key=lambda x: x["ram_mb"], reverse=True)
    top_10 = proc_list[:10]

    return top_10, len(heavy_matches) > 0, sorted(list(heavy_matches))

# --- CSV LOGGING INITIALIZATION ---
def init_csv_log():
    os.makedirs(os.path.dirname(LOG_FILE_PATH), exist_ok=True)
    if not os.path.exists(LOG_FILE_PATH) or os.path.getsize(LOG_FILE_PATH) == 0:
        with open(LOG_FILE_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "host_timestamp",
                "esp32_timestamp",
                "cpu_percent",
                "ram_percent",
                "heavy_task",
                "predicted_mq135",
                "control_decision",
                "mq135",
                "temp",
                "hum",
                "pir",
                "fan",
                "fan_src"
            ])

def get_csv_sample_count():
    if not os.path.exists(LOG_FILE_PATH):
        return 0
    try:
        with open(LOG_FILE_PATH, "r", encoding="utf-8") as f:
            lines = sum(1 for line in f if line.strip())
            return max(0, lines - 1)  # Exclude header
    except Exception:
        return 0

# --- BACKGROUND DAEMON (PREDICTION, CONTROL, AND CSV LOGGING) ---
def background_daemon_loop():
    global control_state, logging_state, telemetry_history, auto_retrain_state
    init_csv_log()
    cnt = get_csv_sample_count()
    logging_state["sample_count"] = cnt
    if auto_retrain_state["last_retrained_sample_count"] == 0:
        auto_retrain_state["last_retrained_sample_count"] = cnt

    while True:
        time.sleep(2)
        now = time.time()
        host_ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. System state
        sys_metrics = get_system_metrics()
        cpu_pct = sys_metrics["cpu_percent"]
        ram_pct = sys_metrics["ram_percent"]
        _, heavy_running, _ = get_processes_and_heavy_tasks()
        heavy_val = 1 if heavy_running else 0

        # 2. Check ESP32 status
        with state_lock:
            last_t = esp32_state["last_packet_time"]
            esp32_ts_str = esp32_state["last_packet_time_str"]
            last_addr = esp32_state["last_addr"]
            data = esp32_state["data"]
            is_online = (last_t is not None) and ((now - last_t) <= 10.0)
            is_sim = esp32_state["is_simulated"]

        # 3. Feature extraction & Ridge prediction
        predicted_mq = None
        if is_online and data:
            feature_vector = {
                "mq135_current": data.get("mq135"),
                "cpu_percent": cpu_pct,
                "ram_percent": ram_pct,
                "heavy_task": heavy_val,
                "temp": data.get("temp"),
                "hum": data.get("hum"),
                "pir": data.get("pir")
            }
            predicted_mq = model_engine.predict(feature_vector)
            with state_lock:
                model_state["last_prediction"] = predicted_mq
                model_state["last_prediction_time"] = now
        else:
            with state_lock:
                model_state["last_prediction"] = None

        # 4. Control Decision Logic
        threshold = model_engine.model_data.get("threshold", 850.0) if model_engine.model_data else 850.0
        new_decision = "IDLE"
        command_to_send = None

        if is_online and data:
            current_mq = data.get("mq135", 0)
            reported_fan = data.get("fan", 0)
            reported_src = data.get("src", 0)

            # Predictive feed-forward demand strictly determined by forecast vs threshold
            if predicted_mq is not None and predicted_mq >= threshold:
                new_decision = "PRE-EMPTIVE"
                # If fan is not already running under host pre-emption and not under local safety fallback:
                if (reported_fan != 1 or reported_src != 1) and reported_src != 2:
                    command_to_send = {"cmd": "fan", "state": 1, "src": 1}
            else:
                new_decision = "IDLE"
                # If fan was pre-emptively commanded by host and forecast is now safe:
                # Do NOT shut off if node is currently under local hardware safety fallback (src == 2)
                if reported_fan == 1 and reported_src == 1:
                    command_to_send = {"cmd": "fan", "state": 0, "src": 0}
        else:
            new_decision = "IDLE"

        # 5. Dispatch command if needed
        if command_to_send and last_addr:
            success = send_udp_command(last_addr, command_to_send)
            cmd_label = "VENTILATE_ON (PRE-EMPTIVE)" if command_to_send["state"] == 1 else "VENTILATE_OFF"
            with state_lock:
                control_state["latest_command_sent"] = {
                    "command": cmd_label,
                    "state": command_to_send["state"],
                    "src": command_to_send["src"],
                    "timestamp": host_ts_str,
                    "target": f"{last_addr[0]}:{last_addr[1]}",
                    "transmitted": success
                }
                control_state["last_command_time"] = now
                control_state["last_command_addr"] = last_addr

        with state_lock:
            control_state["decision"] = new_decision

        # 6. Update rolling telemetry history
        hist_mq = data.get("mq135") if (is_online and data) else None
        time_label = datetime.now().strftime("%H:%M:%S")
        with state_lock:
            telemetry_history.append({
                "time": time_label,
                "cpu": cpu_pct,
                "mq135": hist_mq,
                "simulated": is_sim if is_online else False
            })

        # 7. Append row to CSV log
        try:
            pred_str = str(predicted_mq) if predicted_mq is not None else ""
            if is_online and data:
                row = [
                    host_ts_str,
                    esp32_ts_str or "",
                    cpu_pct,
                    ram_pct,
                    heavy_val,
                    pred_str,
                    new_decision,
                    data.get("mq135", ""),
                    data.get("temp", ""),
                    data.get("hum", ""),
                    data.get("pir", ""),
                    data.get("fan", ""),
                    data.get("src", "")
                ]
            else:
                row = [
                    host_ts_str,
                    "",
                    cpu_pct,
                    ram_pct,
                    heavy_val,
                    pred_str,
                    new_decision,
                    "", "", "", "", "", ""
                ]

            with open(LOG_FILE_PATH, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(row)

            with state_lock:
                logging_state["status"] = "ACTIVE"
                logging_state["last_write_time"] = host_ts_str
                logging_state["sample_count"] += 1
        except Exception:
            with state_lock:
                logging_state["status"] = "ERROR"

# --- PIPELINE STATUS COMPUTATION ---
def compute_pipeline_status(is_online, esp_data, sys_metrics, predicted_mq, decision):
    # Stage 1: OS Telemetry
    os_status = "ACTIVE" if sys_metrics["cpu_ready"] else "WAITING FOR DATA"

    # Stage 2: Feature Extraction
    feat_status = "ACTIVE" if (is_online and esp_data is not None) else "WAITING FOR DATA"

    # Stage 3: Ridge Regression
    if model_engine.status == "LOADED":
        ridge_status = "ACTIVE" if (predicted_mq is not None) else "WAITING FOR DATA"
    elif model_engine.status == "NOT LOADED":
        ridge_status = "NOT LOADED"
    else:
        ridge_status = "ERROR"

    # Stage 4: Control Decision
    ctrl_status = "ACTIVE" if (is_online and esp_data is not None) else "WAITING FOR DATA"

    # Stage 5: UDP Command
    with state_lock:
        last_cmd = control_state.get("latest_command_sent")
        last_cmd_t = control_state.get("last_command_time", 0)
    if last_cmd and (time.time() - last_cmd_t) <= 15.0:
        udp_cmd_status = "ACTIVE"
    elif not esp32_state["last_addr"]:
        udp_cmd_status = "WAITING FOR TARGET"
    else:
        udp_cmd_status = "IDLE"

    # Stage 6: ESP32 Device
    esp_status = "ACTIVE" if is_online else "OFFLINE"

    # Stage 7: Relay / Fan Actuator
    if not is_online:
        actuator_status = "UNAVAILABLE"
    else:
        fan_val = esp_data.get("fan", 0) if esp_data else 0
        actuator_status = "ACTIVE" if fan_val == 1 else "IDLE"

    # Human-readable decision summary
    if not is_online:
        summary = "ESP32 telemetry offline. Predictive feed-forward paused."
    elif decision == "PRE-EMPTIVE":
        summary = "Predicted gas rise exceeded threshold. Feed-forward ventilation commanded."
    elif decision == "REACTIVE FALLBACK":
        summary = "Local reactive threshold active on ESP32 node. Fan running."
    else:
        summary = "Environmental parameters within nominal operating bounds. System idle."

    return {
        "os_telemetry": os_status,
        "feature_extraction": feat_status,
        "ridge_regression": ridge_status,
        "control_decision": ctrl_status,
        "udp_command": udp_cmd_status,
        "esp32_device": esp_status,
        "relay_fan": actuator_status,
        "decision_summary": summary
    }

def compute_fan_rationale(is_online, current_mq, predicted_mq, threshold, reported_fan, reported_src, host_decision):
    if not is_online:
        return "ESP32 node is currently offline. Environmental telemetry and actuator feedback are unavailable."

    if reported_fan == 1:
        if reported_src == 1:
            pred_txt = f"{predicted_mq:.0f} raw ADC" if predicted_mq is not None else "forecast"
            return f"Fan is running pre-emptively: Ridge Regression forecast ({pred_txt}) exceeded the trigger threshold ({int(threshold)} ADC), initiating ventilation before air quality degrades."
        elif reported_src == 2:
            mq_txt = f"{current_mq} raw ADC" if current_mq is not None else "high"
            return f"Fan is running on local reactive fallback: uncalibrated MQ-135 reading ({mq_txt}) reached the hardware safety threshold (≥1000 ADC) on the ESP32 node."
        else:
            return "Fan is active on the ESP32 node under direct actuator override."
    else:
        if host_decision == "PRE-EMPTIVE":
            pred_txt = f"{predicted_mq:.0f}" if predicted_mq is not None else ">=" + str(int(threshold))
            return f"Pre-emptive activation commanded (forecast {pred_txt} ≥ {int(threshold)} ADC); awaiting actuator confirmation from ESP32."
        else:
            pred_part = f"forecasted {predicted_mq:.0f} ADC" if predicted_mq is not None else "forecast nominal"
            mq_part = f"current MQ-135 ({current_mq or '—'} ADC)"
            return f"Fan is OFF: {mq_part} and {pred_part} remain safely below trigger thresholds."

def compute_compact_pipeline(is_online, current_mq, cpu_pct, predicted_mq, threshold, reported_fan, reported_src):
    # Step 1: Telemetry Ingestion
    if not is_online:
        s1 = {"status": "offline", "val": "ESP32 Offline"}
    else:
        s1 = {"status": "ok", "val": f"MQ-135: {current_mq or '—'} • CPU: {cpu_pct:.0f}%"}

    # Step 2: Prediction
    if not is_online or predicted_mq is None:
        s2 = {"status": "offline", "val": "Awaiting Telemetry"}
    elif predicted_mq >= threshold:
        s2 = {"status": "triggered", "val": f"Forecast: {predicted_mq:.0f} ≥ {int(threshold)} (Triggered)"}
    else:
        s2 = {"status": "nominal", "val": f"Forecast: {predicted_mq:.0f} < {int(threshold)} (Nominal)"}

    # Step 3: Ventilation
    if not is_online:
        s3 = {"status": "offline", "val": "Actuator Offline"}
    elif reported_fan == 1:
        if reported_src == 1:
            s3 = {"status": "active-preemptive", "val": "Fan: ON (Pre-emptive Host)"}
        elif reported_src == 2:
            s3 = {"status": "active-reactive", "val": "Fan: ON (Safety Fallback)"}
        else:
            s3 = {"status": "active", "val": "Fan: ON"}
    else:
        s3 = {"status": "idle", "val": "Fan: OFF (Standby)"}

    return {"step1": s1, "step2": s2, "step3": s3}

# --- VIEW ROUTES ---
@app.route("/")
def index():
    now = time.time()
    with state_lock:
        last_t = esp32_state["last_packet_time"]
        last_t_str = esp32_state["last_packet_time_str"]
        sensor_data = esp32_state["data"]
        is_sim = esp32_state["is_simulated"]
        has_recv = esp32_state["has_received_data"]
        last_pred = model_state["last_prediction"]
        curr_decision = control_state["decision"]
        last_cmd = control_state["latest_command_sent"]
        log_stat = dict(logging_state)
        hist_list = list(telemetry_history)

    is_online = (last_t is not None) and ((now - last_t) <= 10.0)
    seconds_since = round(now - last_t, 1) if last_t is not None else None

    top_10, heavy_running, heavy_matches = get_processes_and_heavy_tasks()
    sys_metrics = get_system_metrics()

    # Model metadata
    model_engine.refresh_model()
    m_data = model_engine.model_data or {}
    model_info = {
        "status": model_engine.status,
        "target": m_data.get("target", "Future MQ-135 raw sensor response (+30s)"),
        "horizon": m_data.get("horizon_seconds", 30),
        "horizon_unit": m_data.get("horizon_unit", "seconds"),
        "threshold": m_data.get("threshold", 850.0),
        "threshold_unit": m_data.get("threshold_unit", "raw ADC"),
        "predicted_value": last_pred if is_online else None,
        "version": m_data.get("version", "1.0.0")
    }

    pipeline = compute_pipeline_status(is_online, sensor_data, sys_metrics, last_pred, curr_decision)

    cur_mq = sensor_data.get("mq135") if (is_online and sensor_data) else None
    rep_fan = sensor_data.get("fan") if (is_online and sensor_data) else None
    rep_src = sensor_data.get("src") if (is_online and sensor_data) else None
    thresh = m_data.get("threshold", 850.0)
    cpu_val = sys_metrics.get("cpu_percent", 0.0)

    fan_rationale = compute_fan_rationale(is_online, cur_mq, last_pred, thresh, rep_fan, rep_src, curr_decision)
    compact_pipe = compute_compact_pipeline(is_online, cur_mq, cpu_val, last_pred, thresh, rep_fan, rep_src)

    with state_lock:
        ar_stat = dict(auto_retrain_state)
        curr_log_samples = logging_state["sample_count"]
    ar_stat["samples_since_retrain"] = max(0, curr_log_samples - ar_stat.get("last_retrained_sample_count", 0))

    initial_state = {
        "esp32": {
            "online": is_online,
            "seconds_since_last": seconds_since,
            "last_packet_time_str": last_t_str,
            "values": sensor_data,
            "is_simulated": is_sim,
            "has_received_data": has_recv
        },
        "system": sys_metrics,
        "processes": top_10,
        "heavy_task": {
            "running": heavy_running,
            "matches": heavy_matches
        },
        "model": model_info,
        "auto_retrain": ar_stat,
        "control": {
            "decision": curr_decision,
            "latest_command_sent": last_cmd,
            "reported_fan": rep_fan,
            "reported_source": rep_src,
            "rationale": fan_rationale,
            "compact_pipeline": compact_pipe
        },
        "pipeline": pipeline,
        "logging": log_stat,
        "history": hist_list
    }

    return render_template("index.html", s=initial_state)

@app.route("/api/data")
def api_data():
    now = time.time()
    with state_lock:
        last_t = esp32_state["last_packet_time"]
        last_t_str = esp32_state["last_packet_time_str"]
        sensor_data = esp32_state["data"]
        is_sim = esp32_state["is_simulated"]
        has_recv = esp32_state["has_received_data"]
        last_pred = model_state["last_prediction"]
        curr_decision = control_state["decision"]
        last_cmd = control_state["latest_command_sent"]
        log_stat = dict(logging_state)
        hist_list = list(telemetry_history)
        ar_stat = dict(auto_retrain_state)
        curr_log_samples = logging_state["sample_count"]

    is_online = (last_t is not None) and ((now - last_t) <= 10.0)
    seconds_since = round(now - last_t, 1) if last_t is not None else None

    top_10, heavy_running, heavy_matches = get_processes_and_heavy_tasks()
    sys_metrics = get_system_metrics()

    model_engine.refresh_model()
    m_data = model_engine.model_data or {}
    model_info = {
        "status": model_engine.status,
        "target": m_data.get("target", "Future MQ-135 raw sensor response (+30s)"),
        "horizon": m_data.get("horizon_seconds", 30),
        "horizon_unit": m_data.get("horizon_unit", "seconds"),
        "threshold": m_data.get("threshold", 850.0),
        "threshold_unit": m_data.get("threshold_unit", "raw ADC"),
        "predicted_value": last_pred if is_online else None,
        "version": m_data.get("version", "1.0.0")
    }

    pipeline = compute_pipeline_status(is_online, sensor_data, sys_metrics, last_pred, curr_decision)

    cur_mq = sensor_data.get("mq135") if (is_online and sensor_data) else None
    rep_fan = sensor_data.get("fan") if (is_online and sensor_data) else None
    rep_src = sensor_data.get("src") if (is_online and sensor_data) else None
    thresh = m_data.get("threshold", 850.0)
    cpu_val = sys_metrics.get("cpu_percent", 0.0)

    fan_rationale = compute_fan_rationale(is_online, cur_mq, last_pred, thresh, rep_fan, rep_src, curr_decision)
    compact_pipe = compute_compact_pipeline(is_online, cur_mq, cpu_val, last_pred, thresh, rep_fan, rep_src)

    ar_stat["samples_since_retrain"] = max(0, curr_log_samples - ar_stat.get("last_retrained_sample_count", 0))

    return jsonify({
        "esp32": {
            "online": is_online,
            "seconds_since_last": seconds_since,
            "last_packet_time_str": last_t_str,
            "values": sensor_data if is_online else None,
            "last_known_values": sensor_data,
            "is_simulated": is_sim,
            "has_received_data": has_recv
        },
        "system": sys_metrics,
        "processes": top_10,
        "heavy_task": {
            "running": heavy_running,
            "matches": heavy_matches
        },
        "model": model_info,
        "auto_retrain": ar_stat,
        "control": {
            "decision": curr_decision,
            "latest_command_sent": last_cmd,
            "reported_fan": rep_fan,
            "reported_source": rep_src,
            "rationale": fan_rationale,
            "compact_pipeline": compact_pipe
        },
        "pipeline": pipeline,
        "logging": log_stat,
        "history": hist_list
    })

@app.route("/api/retrain", methods=["POST", "GET"])
def api_retrain():
    with state_lock:
        if auto_retrain_state["status"] == "TRAINING":
            return jsonify({
                "success": False,
                "status": "TRAINING",
                "message": "Retraining is already running in background."
            })
    execute_background_retrain(reason="Manual API/UI Trigger")
    return jsonify({
        "success": True,
        "status": "TRAINING",
        "message": "Background retraining initiated."
    })

@app.route("/download/log.csv")
def download_log():
    init_csv_log()
    return send_file(
        LOG_FILE_PATH,
        as_attachment=True,
        download_name="log.csv",
        mimetype="text/csv"
    )

if __name__ == "__main__":
    # Prime initial psutil metrics
    psutil.cpu_percent(interval=None)

    # Start UDP listener daemon thread
    udp_thread = threading.Thread(target=udp_listener, daemon=True)
    udp_thread.start()

    # Start background daemon for control & CSV logging
    bg_thread = threading.Thread(target=background_daemon_loop, daemon=True)
    bg_thread.start()

    # Start autonomous background auto-retraining daemon
    retrain_thread = threading.Thread(target=auto_retrain_daemon_loop, daemon=True)
    retrain_thread.start()

    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
