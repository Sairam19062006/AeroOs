#!/usr/bin/env python3
"""
AeroOS - Ridge Regression Model Trainer
Trains a Ridge Regression model offline from data/log.csv
and exports learned weights, intercept, and threshold to models/ridge_model.json.

Can run with scikit-learn or via pure-Python closed-form normal equations.
"""

import os
import csv
import json
import math
from datetime import datetime

CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "log.csv")
MODEL_OUT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "ridge_model.json")

FEATURE_NAMES = [
    "mq135_current",
    "cpu_percent",
    "ram_percent",
    "heavy_task",
    "temp",
    "hum",
    "pir"
]

HORIZON_SECONDS = 30
SAMPLE_INTERVAL_SECONDS = 2
HORIZON_STEPS = HORIZON_SECONDS // SAMPLE_INTERVAL_SECONDS  # 15 steps ahead
ALPHA = 1.0
TRIGGER_THRESHOLD = 850.0

def load_dataset(csv_path):
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Dataset not found at {csv_path}")

    raw_samples = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return []

        # Find column indices
        col_map = {name.strip().lower(): idx for idx, name in enumerate(header)}
        
        for row in reader:
            if len(row) < len(header):
                continue
            try:
                # Check required fields are non-empty
                mq_str = row[col_map.get("mq135", 7)].strip()
                if not mq_str:
                    continue
                
                mq135 = float(mq_str)
                cpu = float(row[col_map.get("cpu_percent", 2)])
                ram = float(row[col_map.get("ram_percent", 3)])
                heavy = float(row[col_map.get("heavy_task", 4)])
                temp = float(row[col_map.get("temp", 8)])
                hum = float(row[col_map.get("hum", 9)])
                pir = float(row[col_map.get("pir", 10)])

                raw_samples.append({
                    "mq135": mq135,
                    "cpu_percent": cpu,
                    "ram_percent": ram,
                    "heavy_task": heavy,
                    "temp": temp,
                    "hum": hum,
                    "pir": pir
                })
            except (ValueError, IndexError, TypeError):
                continue

    # Build supervised pairs (X[t] -> y[t + HORIZON_STEPS])
    X = []
    y = []
    n = len(raw_samples)
    for i in range(n - HORIZON_STEPS):
        curr = raw_samples[i]
        future = raw_samples[i + HORIZON_STEPS]

        feat_vector = [
            curr["mq135"],
            curr["cpu_percent"],
            curr["ram_percent"],
            curr["heavy_task"],
            curr["temp"],
            curr["hum"],
            curr["pir"]
        ]
        X.append(feat_vector)
        y.append(future["mq135"])

    return X, y

def train_pure_python(X, y, alpha=ALPHA):
    """
    Fits Ridge Regression using closed-form Normal Equations with L2 regularization:
    w = (X^T X + alpha * I)^(-1) X^T y
    Includes bias (intercept) as an augmented column.
    """
    n_samples = len(X)
    n_features = len(FEATURE_NAMES)
    
    # Augment X with column of 1s for intercept
    X_aug = [[1.0] + row for row in X]
    m = n_features + 1

    # Compute X^T * X
    XtX = [[0.0] * m for _ in range(m)]
    for i in range(m):
        for j in range(m):
            s = 0.0
            for k in range(n_samples):
                s += X_aug[k][i] * X_aug[k][j]
            XtX[i][j] = s

    # Add L2 penalty alpha * I to diagonal (do NOT regularize bias at index 0)
    for i in range(1, m):
        XtX[i][i] += alpha

    # Compute X^T * y
    Xty = [0.0] * m
    for i in range(m):
        s = 0.0
        for k in range(n_samples):
            s += X_aug[k][i] * y[k]
        Xty[i] = s

    # Solve XtX * w = Xty using Gaussian Elimination with partial pivoting
    A = [row[:] for row in XtX]
    b = Xty[:]

    for i in range(m):
        # Pivot
        max_row = max(range(i, m), key=lambda r: abs(A[r][i]))
        A[i], A[max_row] = A[max_row], A[i]
        b[i], b[max_row] = b[max_row], b[i]

        pivot = A[i][i]
        if abs(pivot) < 1e-12:
            pivot = 1e-12

        for j in range(i + 1, m):
            factor = A[j][i] / pivot
            for k in range(i, m):
                A[j][k] -= factor * A[i][k]
            b[j] -= factor * b[i]

    # Back substitution
    w = [0.0] * m
    for i in range(m - 1, -1, -1):
        s = b[i]
        for j in range(i + 1, m):
            s -= A[i][j] * w[j]
        w[i] = s / A[i][i]

    intercept = w[0]
    coeffs = {name: round(w[idx + 1], 4) for idx, name in enumerate(FEATURE_NAMES)}

    # Evaluate Metrics
    y_mean = sum(y) / n_samples
    ss_tot = sum((val - y_mean) ** 2 for val in y)
    ss_res = 0.0
    abs_err_sum = 0.0

    for k in range(n_samples):
        y_hat = intercept + sum(w[j + 1] * X[k][j] for j in range(n_features))
        err = y[k] - y_hat
        ss_res += err ** 2
        abs_err_sum += abs(err)

    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    mae = abs_err_sum / n_samples
    rmse = math.sqrt(ss_res / n_samples)

    return intercept, coeffs, r2, mae, rmse

def train_and_export(csv_path=None, model_out_path=None):
    csv_file = csv_path or CSV_PATH
    out_file = model_out_path or MODEL_OUT_PATH

    print("==================================================")
    print(" AeroOS - Telemetry Ridge Regression Trainer")
    print("==================================================")
    print(f"Reading telemetry log from: {csv_file}")

    X, y = load_dataset(csv_file)
    n_pairs = len(X)
    print(f"Valid supervised sample pairs (t -> t+{HORIZON_SECONDS}s): {n_pairs}")

    if n_pairs < 15:
        raise ValueError(f"Insufficient supervised data pairs ({n_pairs}). At least 15 pairs required.")

    if n_pairs < 30:
        print("Warning: Dataset is small (< 30 samples). Training on available data.")

    # Try training with Scikit-Learn if installed, otherwise pure-Python normal equations
    used_engine = "Pure-Python Normal Equations"
    try:
        from sklearn.linear_model import Ridge
        from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error

        ridge = Ridge(alpha=ALPHA)
        ridge.fit(X, y)
        y_pred = ridge.predict(X)

        r2 = r2_score(y, y_pred)
        mae = mean_absolute_error(y, y_pred)
        rmse = math.sqrt(mean_squared_error(y, y_pred))
        intercept = float(ridge.intercept_)
        coeffs = {name: round(float(ridge.coef_[i]), 4) for i, name in enumerate(FEATURE_NAMES)}
        used_engine = "Scikit-Learn (sklearn.linear_model.Ridge)"
    except ImportError:
        intercept, coeffs, r2, mae, rmse = train_pure_python(X, y, alpha=ALPHA)

    print(f"\nTraining Engine: {used_engine}")
    print(f"Model: Ridge Regression (L2 alpha = {ALPHA})")
    print(f"Prediction Horizon: +{HORIZON_SECONDS} seconds")
    print(f"--------------------------------------------------")
    print(f"R² Score:                 {r2:.4f}")
    print(f"Mean Absolute Error (MAE): {mae:.2f} raw ADC")
    print(f"Root Mean Squared Error:  {rmse:.2f} raw ADC")
    print(f"--------------------------------------------------")
    print(f"Learned Coefficients:")
    for feat, weight in coeffs.items():
        print(f"  {feat:<16}: {weight:+.4f}")
    print(f"  {'intercept':<16}: {intercept:+.4f}")

    # Build model JSON artifact
    model_artifact = {
        "model_type": "Ridge Regression",
        "version": "1.0.0",
        "target": f"Future MQ-135 raw sensor response (+{HORIZON_SECONDS}s)",
        "horizon_seconds": HORIZON_SECONDS,
        "horizon_unit": "seconds",
        "threshold": TRIGGER_THRESHOLD,
        "threshold_unit": "raw ADC",
        "regularization_alpha": ALPHA,
        "training_dataset": "AeroOS Environmental Telemetry",
        "trained_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "training_metrics": {
            "samples_used": n_pairs,
            "r2_score": round(r2, 4),
            "mae": round(mae, 2),
            "rmse": round(rmse, 2),
            "engine": used_engine
        },
        "features": FEATURE_NAMES,
        "coefficients": coeffs,
        "intercept": round(intercept, 4)
    }

    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(model_artifact, f, indent=2)

    print(f"\n[OK] Model exported successfully to:")
    print(f"     {out_file}")
    print("AeroOS backend will dynamically detect the updated model on next inference cycle.")

    return model_artifact

if __name__ == "__main__":
    train_and_export()
