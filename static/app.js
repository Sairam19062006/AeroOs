// AeroOS Monitoring Dashboard - Sidebar Navigation & Live Polling Client

const HEAVY_NAMES = ["gcc", "g++", "docker", "python3"];

const TAB_TITLES = {
    "tab-live": "Live Telemetry Monitor",
    "tab-predictive": "Predictive Engine",
    "tab-trends": "Live Telemetry Trends",
    "tab-processes": "System Telemetry & Process Inspector",
    "tab-logs": "Telemetry Logging & Data Integrity"
};

let cachedHistory = [];

function formatFanSource(src) {
    if (src === 0) return "Off";
    if (src === 1) return "Pre-emptive (Host)";
    if (src === 2) return "Local Threshold";
    return `Code ${src}`;
}

function updateBadgeClass(element, statusText) {
    if (!element) return;
    const cleanStatus = (statusText || "").toLowerCase().replace(/\s+/g, "-");
    element.className = `node-badge node-${cleanStatus}`;
    element.textContent = statusText;
}

// --- TAB SWITCHING LOGIC ---
function initNavigation() {
    const navButtons = document.querySelectorAll(".nav-item");
    const tabPanels = document.querySelectorAll(".tab-panel");
    const pageTitle = document.getElementById("page-section-title");
    const sidebar = document.getElementById("app-sidebar");
    const sidebarToggle = document.getElementById("sidebar-toggle");

    navButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            const targetId = btn.getAttribute("data-tab");
            if (!targetId) return;

            // Update Nav Item States
            navButtons.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");

            // Update Panels
            tabPanels.forEach(panel => {
                if (panel.id === targetId) {
                    panel.classList.add("active");
                } else {
                    panel.classList.remove("active");
                }
            });

            // Update Topbar Title
            if (pageTitle && TAB_TITLES[targetId]) {
                pageTitle.textContent = TAB_TITLES[targetId];
            }

            // Sync URL hash without scrolling
            if (history.replaceState) {
                history.replaceState(null, null, "#" + targetId);
            }

            // Close mobile sidebar if open
            if (sidebar && sidebar.classList.contains("open")) {
                sidebar.classList.remove("open");
            }

            // Render chart if opening trends tab
            if (targetId === "tab-trends" && cachedHistory.length > 0) {
                setTimeout(() => renderTrendPlot(cachedHistory), 50);
            }
        });
    });

    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener("click", (e) => {
            e.stopPropagation();
            sidebar.classList.toggle("open");
        });

        document.addEventListener("click", (e) => {
            if (!sidebar.contains(e.target) && e.target !== sidebarToggle) {
                sidebar.classList.remove("open");
            }
        });
    }

    // Deep-link to tab if present in URL hash
    const initialHash = window.location.hash.replace("#", "");
    if (initialHash && document.getElementById(initialHash)) {
        const targetBtn = document.querySelector(`.nav-item[data-tab="${initialHash}"]`);
        if (targetBtn) targetBtn.click();
    }
}

// --- DASHBOARD REAL-TIME UPDATE LOOP ---
function updateDashboard() {
    fetch("/api/data")
        .then(response => {
            if (!response.ok) {
                throw new Error("HTTP " + response.status);
            }
            return response.json();
        })
        .then(data => {
            // 0. Backend Status (Connected)
            const backendBanner = document.getElementById("backend-error-banner");
            if (backendBanner) backendBanner.classList.add("hidden");

            const backendPill = document.getElementById("backend-status-pill");
            if (backendPill) {
                backendPill.className = "status-pill status-online";
                backendPill.textContent = "CONNECTED";
            }

            // 1. ESP32 Status & Stale Banner
            const esp = data.esp32;
            const espPill = document.getElementById("esp32-status-pill");
            const espAge = document.getElementById("esp32-status-age");
            const staleBanner = document.getElementById("stale-warning-banner");
            const chartSimTag = document.getElementById("chart-sim-tag");
            const sensorCards = document.getElementById("sensor-cards-container");

            const isOnline = esp && esp.online;
            const hasData = esp && esp.has_received_data;

            if (isOnline) {
                if (espPill) {
                    espPill.className = "status-pill status-online";
                    espPill.textContent = "ONLINE";
                }
                if (espAge) {
                    espAge.textContent = `${esp.seconds_since_last}s ago`;
                }
                if (staleBanner) staleBanner.classList.add("hidden");
                if (sensorCards) sensorCards.classList.remove("cards-offline");
            } else {
                if (espPill) {
                    espPill.className = "status-pill status-offline";
                    espPill.textContent = "OFFLINE";
                }
                if (espAge) {
                    if (hasData && esp.seconds_since_last !== null) {
                        espAge.textContent = `last: ${esp.seconds_since_last}s`;
                    } else {
                        espAge.textContent = "No data";
                    }
                }
                if (staleBanner) {
                    if (hasData) staleBanner.classList.remove("hidden");
                    else staleBanner.classList.add("hidden");
                }
                if (sensorCards) sensorCards.classList.add("cards-offline");
            }

            // Simulated badge
            const isSim = esp && esp.is_simulated;
            if (chartSimTag) {
                if (isSim) chartSimTag.classList.remove("hidden");
                else chartSimTag.classList.add("hidden");
            }

            // 2. Ridge Model Status
            const model = data.model;
            const modelPill = document.getElementById("model-status-pill");
            if (model && modelPill) {
                if (model.status === "LOADED") {
                    modelPill.className = "status-pill status-online";
                } else if (model.status === "NOT LOADED") {
                    modelPill.className = "status-pill status-amber";
                } else {
                    modelPill.className = "status-pill status-offline";
                }
                modelPill.textContent = model.status;
            }

            // Tab 2: Minimal 3-Card Predictive Display & Rationale
            const predCurrentMq = document.getElementById("pred-current-mq");
            if (predCurrentMq) {
                predCurrentMq.textContent = (isOnline && esp && esp.values && esp.values.mq135 !== undefined) ?
                    esp.values.mq135 : "—";
            }

            const predValDisplay = document.getElementById("pred-val-display");
            const predValSub = document.getElementById("pred-val-sub");
            if (model && predValDisplay) {
                if (isOnline && model.predicted_value !== null && model.predicted_value !== undefined) {
                    predValDisplay.textContent = model.predicted_value;
                    if (predValSub) {
                        predValSub.textContent = `Trigger Threshold: ${model.threshold} raw ADC`;
                    }
                } else {
                    predValDisplay.textContent = "—";
                    if (predValSub) {
                        predValSub.textContent = isOnline ?
                            `Trigger Threshold: ${model.threshold} raw ADC` :
                            "ESP32 node is offline • Awaiting telemetry";
                    }
                }
            }

            const predFanState = document.getElementById("pred-fan-state");
            const predFanSourceBadge = document.getElementById("pred-fan-source-badge");
            const predFanSub = document.getElementById("pred-fan-sub");
            const ctrl = data.control;

            if (ctrl) {
                if (predFanState) {
                    predFanState.textContent = (isOnline && ctrl.reported_fan !== null && ctrl.reported_fan !== undefined) ?
                        (ctrl.reported_fan === 1 ? "ON" : "OFF") : "—";
                }
                if (predFanSourceBadge) {
                    if (isOnline && ctrl.reported_fan === 1) {
                        if (ctrl.reported_source === 1) {
                            predFanSourceBadge.textContent = "Pre-emptive (Host)";
                            predFanSourceBadge.className = "hero-source-badge badge-preemptive";
                        } else if (ctrl.reported_source === 2) {
                            predFanSourceBadge.textContent = "Local Safety Fallback";
                            predFanSourceBadge.className = "hero-source-badge badge-reactive";
                        } else {
                            predFanSourceBadge.textContent = "Override";
                            predFanSourceBadge.className = "hero-source-badge badge-idle";
                        }
                    } else if (isOnline) {
                        predFanSourceBadge.textContent = "Idle (Standby)";
                        predFanSourceBadge.className = "hero-source-badge badge-idle";
                    } else {
                        predFanSourceBadge.textContent = "Offline";
                        predFanSourceBadge.className = "hero-source-badge badge-idle";
                    }
                }
                if (predFanSub) {
                    predFanSub.textContent = isOnline ?
                        "Confirmed via node telemetry packet" :
                        "Actuator telemetry unavailable";
                }
            }

            // "Why is the fan running?" Rationale Explanation
            const fanRationaleText = document.getElementById("fan-rationale-text");
            if (fanRationaleText && ctrl && ctrl.rationale) {
                fanRationaleText.textContent = ctrl.rationale;
            }

            // Compact 3-Stage Visual Pipeline
            const stage1 = document.getElementById("stage-telemetry");
            const stage1Detail = document.getElementById("stage-telemetry-detail");
            if (stage1Detail) {
                if (isOnline && esp && esp.values) {
                    const mq = esp.values.mq135 !== undefined ? esp.values.mq135 : "—";
                    const cpu = (data.system && data.system.cpu_percent !== undefined) ? `${data.system.cpu_percent}%` : "—";
                    stage1Detail.textContent = `MQ-135: ${mq} • CPU: ${cpu}`;
                    if (stage1) stage1.classList.add("stage-active");
                } else {
                    stage1Detail.textContent = "ESP32 Offline";
                    if (stage1) stage1.classList.remove("stage-active");
                }
            }

            const stage2 = document.getElementById("stage-prediction");
            const stage2Detail = document.getElementById("stage-prediction-detail");
            if (stage2Detail) {
                if (isOnline && model && model.predicted_value !== null && model.predicted_value !== undefined) {
                    stage2Detail.textContent = `Forecast: ${model.predicted_value} vs ${model.threshold} Threshold`;
                    if (stage2) {
                        if (model.predicted_value >= model.threshold) {
                            stage2.classList.add("stage-active");
                        } else {
                            stage2.classList.remove("stage-active");
                        }
                    }
                } else {
                    stage2Detail.textContent = isOnline ? "Awaiting Telemetry" : "Model Paused";
                    if (stage2) stage2.classList.remove("stage-active");
                }
            }

            const stage3 = document.getElementById("stage-ventilation");
            const stage3Detail = document.getElementById("stage-ventilation-detail");
            if (stage3Detail) {
                if (isOnline && ctrl && ctrl.reported_fan !== null && ctrl.reported_fan !== undefined) {
                    const fanState = ctrl.reported_fan === 1 ? "ON" : "OFF";
                    let srcLabel = "";
                    if (ctrl.reported_fan === 1) {
                        srcLabel = ctrl.reported_source === 1 ? " (Pre-emptive Host)" : (ctrl.reported_source === 2 ? " (Safety Fallback)" : " (Active)");
                    }
                    stage3Detail.textContent = `Fan: ${fanState}${srcLabel}`;
                    if (stage3) {
                        if (ctrl.reported_fan === 1) stage3.classList.add("stage-active");
                        else stage3.classList.remove("stage-active");
                    }
                } else {
                    stage3Detail.textContent = "Actuator Offline";
                    if (stage3) stage3.classList.remove("stage-active");
                }
            }

            // Diagnostics Event Dispatch Log
            const diagCmdName = document.getElementById("diag-cmd-name");
            const diagCmdTs = document.getElementById("diag-cmd-ts");
            const diagCmdTarget = document.getElementById("diag-cmd-target");
            const diagCmdStatus = document.getElementById("diag-cmd-status");
            if (ctrl && ctrl.latest_command_sent) {
                if (diagCmdName) diagCmdName.textContent = ctrl.latest_command_sent.command;
                if (diagCmdTs) diagCmdTs.textContent = ctrl.latest_command_sent.timestamp;
                if (diagCmdTarget) diagCmdTarget.textContent = ctrl.latest_command_sent.target;
                if (diagCmdStatus) diagCmdStatus.textContent = ctrl.latest_command_sent.transmitted ? "Transmitted" : "Failed";
            } else {
                if (diagCmdName) diagCmdName.textContent = "None sent yet";
                if (diagCmdTs) diagCmdTs.textContent = "—";
                if (diagCmdTarget) diagCmdTarget.textContent = "—";
                if (diagCmdStatus) diagCmdStatus.textContent = "—";
            }

            // Diagnostics Auto-Retrain Card
            if (data.auto_retrain) {
                const ar = data.auto_retrain;
                const retrainBadge = document.getElementById("diag-retrain-badge");
                const retrainTime = document.getElementById("diag-retrain-time");
                const retrainSamples = document.getElementById("diag-retrain-samples");
                const retrainR2 = document.getElementById("diag-retrain-r2");
                const retrainMae = document.getElementById("diag-retrain-mae");
                const retrainBtn = document.getElementById("btn-manual-retrain");
                const retrainFeedback = document.getElementById("retrain-feedback-msg");

                if (retrainBadge) {
                    if (ar.status === "TRAINING") {
                        retrainBadge.className = "diag-status-pill status-retraining";
                        retrainBadge.textContent = "TRAINING...";
                        if (retrainBtn) retrainBtn.disabled = true;
                        if (retrainFeedback) retrainFeedback.textContent = "Retraining in background...";
                    } else if (ar.status === "ACTIVE") {
                        retrainBadge.className = "diag-status-pill status-active-auto";
                        retrainBadge.textContent = "ACTIVE (AUTO)";
                        if (retrainBtn) retrainBtn.disabled = false;
                    } else if (ar.status === "ERROR") {
                        retrainBadge.className = "diag-status-pill status-retraining";
                        retrainBadge.textContent = "ERROR";
                        if (retrainBtn) retrainBtn.disabled = false;
                    }
                }

                if (retrainTime && ar.last_retrain_time) {
                    retrainTime.textContent = ar.last_retrain_time;
                }
                if (retrainSamples && ar.samples_used !== undefined) {
                    retrainSamples.textContent = `${ar.samples_used} pairs`;
                }
                if (retrainR2) {
                    retrainR2.textContent = ar.r2_score !== null && ar.r2_score !== undefined ? `${ar.r2_score}` : "—";
                }
                if (retrainMae) {
                    retrainMae.textContent = ar.mae !== null && ar.mae !== undefined ? `${ar.mae} ADC` : "—";
                }
            }

            // Live Monitor Tab 1 Quick Decision
            const quickDecisionBadge = document.getElementById("quick-decision-badge");
            const quickDecisionSub = document.getElementById("quick-decision-sub");
            if (ctrl) {
                const decClass = "decision-badge " + (
                    ctrl.decision === "PRE-EMPTIVE" ? "dec-preemptive" :
                    ctrl.decision === "REACTIVE FALLBACK" ? "dec-reactive" : "dec-idle"
                );
                if (quickDecisionBadge) {
                    quickDecisionBadge.textContent = ctrl.decision;
                    quickDecisionBadge.className = decClass;
                }
                if (quickDecisionSub && model) {
                    quickDecisionSub.textContent = `Threshold: ${model.threshold} raw ADC`;
                }
            }

            // 5. Sensor Cards
            const mqCard = document.getElementById("card-mq135");
            const valMq = document.getElementById("val-mq135");
            const valTemp = document.getElementById("val-temp");
            const valHum = document.getElementById("val-hum");
            const valPir = document.getElementById("val-pir");
            const valFan = document.getElementById("val-fan");
            const valSrc = document.getElementById("val-src");

            const currentVals = (isOnline && esp.values) ? esp.values : (hasData ? esp.last_known_values : null);

            if (currentVals) {
                const mq = currentVals.mq135 !== undefined ? currentVals.mq135 : null;
                if (valMq) valMq.textContent = mq !== null ? mq : "—";
                if (valTemp) valTemp.textContent = currentVals.temp !== undefined ? `${currentVals.temp} °C` : "—";
                if (valHum) valHum.textContent = currentVals.hum !== undefined ? `${currentVals.hum} %` : "—";
                if (valPir) valPir.textContent = currentVals.pir === 1 ? "Occupied" : (currentVals.pir === 0 ? "Empty" : "—");
                if (valFan) valFan.textContent = currentVals.fan === 1 ? "ON" : (currentVals.fan === 0 ? "OFF" : "—");
                if (valSrc) valSrc.textContent = currentVals.src !== undefined ? formatFanSource(currentVals.src) : "—";

                if (mqCard) {
                    mqCard.classList.remove("card-gas-green", "card-gas-amber", "card-gas-red");
                    if (isOnline && mq !== null) {
                        if (mq < 800) mqCard.classList.add("card-gas-green");
                        else if (mq <= 1000) mqCard.classList.add("card-gas-amber");
                        else mqCard.classList.add("card-gas-red");
                    }
                }
            } else {
                if (valMq) valMq.textContent = "—";
                if (valTemp) valTemp.textContent = "—";
                if (valHum) valHum.textContent = "—";
                if (valPir) valPir.textContent = "—";
                if (valFan) valFan.textContent = "—";
                if (valSrc) valSrc.textContent = "—";
                if (mqCard) mqCard.classList.remove("card-gas-green", "card-gas-amber", "card-gas-red");
            }

            // 6. Host System Metrics
            const sys = data.system;
            if (sys) {
                const cpuVal = document.getElementById("sys-cpu-val");
                const quickCpuVal = document.getElementById("quick-cpu-val");
                const cpuBar = document.getElementById("sys-cpu-bar");
                const quickCpuBar = document.getElementById("quick-cpu-bar");

                const cpuText = sys.cpu_ready ? `${sys.cpu_percent}%` : "Collecting...";
                if (cpuVal) cpuVal.textContent = cpuText;
                if (quickCpuVal) quickCpuVal.textContent = cpuText;

                const cpuWidth = `${Math.min(100, Math.max(0, sys.cpu_percent))}%`;
                if (cpuBar) cpuBar.style.width = cpuWidth;
                if (quickCpuBar) quickCpuBar.style.width = cpuWidth;

                const ramVal = document.getElementById("sys-ram-val");
                const quickRamVal = document.getElementById("quick-ram-val");
                const ramBar = document.getElementById("sys-ram-bar");
                const quickRamBar = document.getElementById("quick-ram-bar");

                const ramText = `${sys.ram_percent}%`;
                if (ramVal) ramVal.textContent = ramText;
                if (quickRamVal) quickRamVal.textContent = ramText;

                const ramWidth = `${Math.min(100, Math.max(0, sys.ram_percent))}%`;
                if (ramBar) ramBar.style.width = ramWidth;
                if (quickRamBar) quickRamBar.style.width = ramWidth;

                const ramDetails = document.getElementById("sys-ram-details");
                if (ramDetails) ramDetails.textContent = `${sys.ram_used_mb} MB / ${sys.ram_total_mb} MB`;

                const procCount = document.getElementById("sys-proc-count");
                if (procCount) procCount.textContent = sys.process_count;

                const uptime = document.getElementById("sys-uptime");
                if (uptime) uptime.textContent = sys.uptime;
            }

            // Workload Detection
            const heavy = data.heavy_task;
            const heavyBadge = document.getElementById("sys-heavy-badge");
            const heavyList = document.getElementById("sys-heavy-list");
            if (heavy) {
                if (heavy.running) {
                    if (heavyBadge) {
                        heavyBadge.className = "heavy-badge badge-yes";
                        heavyBadge.textContent = "YES";
                    }
                    if (heavyList) heavyList.textContent = `Matches: ${heavy.matches.join(", ")}`;
                } else {
                    if (heavyBadge) {
                        heavyBadge.className = "heavy-badge badge-no";
                        heavyBadge.textContent = "NO";
                    }
                    if (heavyList) heavyList.textContent = "";
                }
            }

            // 7. Live Trends Plot
            if (data.history) {
                cachedHistory = data.history;
                renderTrendPlot(data.history);
            }

            // 8. Process Table
            const procTableBody = document.getElementById("processes-table-body");
            if (data.processes && data.processes.length > 0 && procTableBody) {
                const rowsHtml = data.processes.map(p => {
                    const isHeavy = HEAVY_NAMES.includes((p.name || "").toLowerCase());
                    const rowClass = isHeavy ? ' class="highlighted-process"' : '';
                    return `<tr${rowClass}>
                        <td>${p.pid}</td>
                        <td>${escapeHtml(p.name)}</td>
                        <td>${p.cpu_percent.toFixed(1)}%</td>
                        <td>${p.ram_mb.toFixed(1)}</td>
                    </tr>`;
                }).join("");
                procTableBody.innerHTML = rowsHtml;
            }

            // 9. Logging Status
            const logInfo = data.logging;
            const logBadge = document.getElementById("log-status-badge");
            const logLastWrite = document.getElementById("log-last-write");
            const logSampleCount = document.getElementById("log-sample-count");

            if (logInfo) {
                if (logBadge) {
                    logBadge.className = "status-pill " + (logInfo.status === "ACTIVE" ? "status-online" : "status-offline");
                    logBadge.textContent = logInfo.status;
                }
                if (logLastWrite) {
                    logLastWrite.textContent = logInfo.last_write_time || "Awaiting initial sample";
                }
                if (logSampleCount) {
                    logSampleCount.textContent = logInfo.sample_count;
                }
            }
        })
        .catch(err => {
            console.error("Dashboard fetch error:", err);
            const backendBanner = document.getElementById("backend-error-banner");
            if (backendBanner) backendBanner.classList.remove("hidden");

            const backendPill = document.getElementById("backend-status-pill");
            if (backendPill) {
                backendPill.className = "status-pill status-offline";
                backendPill.textContent = "UNAVAILABLE";
            }
        });
}

// --- LIGHTWEIGHT HTML5 CANVAS TIME-SERIES PLOT ---
function renderTrendPlot(history) {
    const canvas = document.getElementById("trendCanvas");
    const overlay = document.getElementById("chart-empty-overlay");
    if (!canvas) return;

    if (!history || history.length < 2) {
        if (overlay) overlay.classList.remove("hidden");
        return;
    }
    if (overlay) overlay.classList.add("hidden");

    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const displayWidth = rect.width || 1150;
    const displayHeight = 280;

    canvas.width = displayWidth * dpr;
    canvas.height = displayHeight * dpr;
    ctx.resetTransform();
    ctx.scale(dpr, dpr);

    const padLeft = 45;
    const padRight = 50;
    const padTop = 20;
    const padBottom = 30;

    const plotW = displayWidth - padLeft - padRight;
    const plotH = displayHeight - padTop - padBottom;

    // Canvas Background
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, displayWidth, displayHeight);

    // Compute Min/Max for MQ-135
    const validMqValues = history.map(d => d.mq135).filter(v => v !== null && v !== undefined);
    let minMq = 400;
    let maxMq = 1400;
    if (validMqValues.length > 0) {
        minMq = Math.min(400, Math.min(...validMqValues));
        maxMq = Math.max(1200, Math.max(...validMqValues));
    }
    const mqRange = maxMq - minMq || 1;

    // Draw Gridlines & Y-Axis Labels
    ctx.strokeStyle = "#f1f5f9";
    ctx.lineWidth = 1;
    ctx.font = "11px Arial, sans-serif";

    for (let i = 0; i <= 4; i++) {
        const y = padTop + (plotH / 4) * i;
        ctx.beginPath();
        ctx.moveTo(padLeft, y);
        ctx.lineTo(padLeft + plotW, y);
        ctx.stroke();

        // Left axis: CPU %
        const cpuLabelVal = Math.round(100 - i * 25);
        ctx.fillStyle = "#1c7293";
        ctx.textAlign = "right";
        ctx.textBaseline = "middle";
        ctx.fillText(`${cpuLabelVal}%`, padLeft - 6, y);

        // Right axis: MQ-135 ADC
        const mqLabelVal = Math.round(maxMq - (i / 4) * mqRange);
        ctx.fillStyle = "#b45309";
        ctx.textAlign = "left";
        ctx.fillText(`${mqLabelVal}`, padLeft + plotW + 6, y);
    }

    const n = history.length;
    const getX = (idx) => padLeft + (idx / (n - 1)) * plotW;
    const getCpuY = (cpu) => padTop + plotH - ((Math.min(100, Math.max(0, cpu || 0))) / 100) * plotH;
    const getMqY = (mq) => padTop + plotH - (((mq - minMq) / mqRange) * plotH);

    // Plot CPU Line (Teal)
    ctx.strokeStyle = "#1c7293";
    ctx.lineWidth = 2;
    ctx.beginPath();
    history.forEach((d, idx) => {
        const x = getX(idx);
        const y = getCpuY(d.cpu);
        if (idx === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // Plot MQ-135 Line (Amber)
    ctx.strokeStyle = "#b45309";
    ctx.lineWidth = 2;
    ctx.beginPath();
    let isDrawing = false;
    history.forEach((d, idx) => {
        const x = getX(idx);
        if (d.mq135 !== null && d.mq135 !== undefined) {
            const y = getMqY(d.mq135);
            if (!isDrawing) {
                ctx.moveTo(x, y);
                isDrawing = true;
            } else {
                ctx.lineTo(x, y);
            }
        } else {
            isDrawing = false; // Break path on offline
        }
    });
    ctx.stroke();

    // Plot Data Point for Latest Sample
    if (n > 0) {
        const lastIdx = n - 1;
        const lastSample = history[lastIdx];
        const lastX = getX(lastIdx);

        ctx.fillStyle = "#1c7293";
        ctx.beginPath();
        ctx.arc(lastX, getCpuY(lastSample.cpu), 3.5, 0, 2 * Math.PI);
        ctx.fill();

        if (lastSample.mq135 !== null && lastSample.mq135 !== undefined) {
            ctx.fillStyle = "#b45309";
            ctx.beginPath();
            ctx.arc(lastX, getMqY(lastSample.mq135), 3.5, 0, 2 * Math.PI);
            ctx.fill();
        }
    }

    // X-Axis Timestamp Labels
    ctx.fillStyle = "#64748b";
    ctx.font = "10px Arial, sans-serif";
    ctx.textBaseline = "top";

    ctx.textAlign = "left";
    ctx.fillText(history[0].time || "", padLeft, padTop + plotH + 8);

    if (n > 2) {
        const midIdx = Math.floor(n / 2);
        ctx.textAlign = "center";
        ctx.fillText(history[midIdx].time || "", getX(midIdx), padTop + plotH + 8);
    }

    ctx.textAlign = "right";
    ctx.fillText(history[n - 1].time || "", padLeft + plotW, padTop + plotH + 8);
}

function escapeHtml(str) {
    if (!str) return "";
    return String(str).replace(/&/g, "&amp;")
                      .replace(/</g, "&lt;")
                      .replace(/>/g, "&gt;")
                      .replace(/"/g, "&quot;")
                      .replace(/'/g, "&#039;");
}

// Initial script execution
if (typeof INITIAL_HISTORY !== "undefined" && Array.isArray(INITIAL_HISTORY) && INITIAL_HISTORY.length > 0) {
    cachedHistory = INITIAL_HISTORY;
}

document.addEventListener("DOMContentLoaded", () => {
    initNavigation();
    if (cachedHistory.length > 0) {
        try { renderTrendPlot(cachedHistory); } catch (e) {}
    }
    updateDashboard();
    setInterval(updateDashboard, 2000);

    // Manual Retrain Trigger Button
    const retrainBtn = document.getElementById("btn-manual-retrain");
    if (retrainBtn) {
        retrainBtn.addEventListener("click", () => {
            retrainBtn.disabled = true;
            const feedback = document.getElementById("retrain-feedback-msg");
            if (feedback) feedback.textContent = "Initiating background retraining...";
            fetch("/api/retrain", { method: "POST" })
                .then(res => res.json())
                .then(result => {
                    if (feedback) {
                        feedback.textContent = result.message || "Training initiated...";
                    }
                    setTimeout(() => {
                        if (feedback && feedback.textContent.includes("initiated")) {
                            feedback.textContent = "";
                        }
                    }, 4000);
                })
                .catch(err => {
                    if (feedback) feedback.textContent = "Request failed: " + err.message;
                    retrainBtn.disabled = false;
                });
        });
    }

    window.addEventListener("resize", () => {
        if (cachedHistory.length > 0) {
            renderTrendPlot(cachedHistory);
        }
    });
});
