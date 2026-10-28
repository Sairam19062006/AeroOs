/*
 * AeroOS ESP32 Micro-Ventilation Firmware
 * Architecture: Offline Cyber-Physical Telemetry & Actuation Node
 * Link: UDP over local Wi-Fi, port 5005
 */

#include <WiFi.h>
#include <WiFiUdp.h>
#include <DHT.h>

// ============================================================================
// CONFIGURATION BLOCK (Adjust for your local network and physical pins)
// ============================================================================
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// Host IP running AeroOS Python Bridge Backend
IPAddress HOST_IP(192, 168, 1, 100); 
const uint16_t UDP_PORT   = 5005;

// Pin Definitions
#define PIN_MQ135         34    // ADC1_CH6 (Analog Gas Sensor AO)
#define PIN_DHT22         4     // Digital DHT22 Data line
#define PIN_PIR           15    // Digital PIR Motion Sensor OUT
#define PIN_RELAY         23    // Digital Output to Optocoupled Relay IN

// Actuator Polarity: Many optocoupled 5V relay modules are active-LOW.
// Set to true if active-LOW (0 = ON, 1 = OFF), or false if active-HIGH (1 = ON, 0 = OFF).
const bool RELAY_ACTIVE_LOW = true;

// Safety Guardrail Parameters
const unsigned long HEARTBEAT_TIMEOUT_MS = 30000;   // 30s without host command -> failsafe OFF
const unsigned long MAX_CONTINUOUS_ON_MS = 600000;  // 10 minutes max continuous run safety limit
const unsigned long TELEMETRY_INTERVAL_MS = 1000;  // 1 Hz telemetry report rate
// ============================================================================

WiFiUDP udp;
DHT dht(PIN_DHT22, DHT22);

// Internal State
unsigned long seq = 0;
int relayState = 0;      // 0 = OFF, 1 = ON
int fanState = 0;
unsigned long lastCommandTime = 0;
unsigned long fanOnStartTime = 0;
unsigned long lastTelemetryTime = 0;

void setRelay(bool turnOn, const char* reason) {
  if (turnOn) {
    digitalWrite(PIN_RELAY, RELAY_ACTIVE_LOW ? LOW : HIGH);
    if (relayState == 0) {
      fanOnStartTime = millis();
    }
    relayState = 1;
    fanState = 1;
    Serial.printf("[AeroOS] Relay ON (%s)\n", reason);
  } else {
    digitalWrite(PIN_RELAY, RELAY_ACTIVE_LOW ? HIGH : LOW);
    relayState = 0;
    fanState = 0;
    fanOnStartTime = 0;
    Serial.printf("[AeroOS] Relay OFF (%s)\n", reason);
  }
}

// Oversampled 12-bit ADC reading for MQ-135 to filter line noise
int readMQ135Smoothed() {
  long sum = 0;
  const int SAMPLES = 16;
  for (int i = 0; i < SAMPLES; i++) {
    sum += analogRead(PIN_MQ135);
    delayMicroseconds(250);
  }
  return (int)(sum / SAMPLES);
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n[AeroOS] Initializing ESP32 Micro-Ventilation Firmware...");

  // Initialize GPIOs
  pinMode(PIN_RELAY, OUTPUT);
  // Default Fan OFF at boot for safety
  digitalWrite(PIN_RELAY, RELAY_ACTIVE_LOW ? HIGH : LOW);
  pinMode(PIN_PIR, INPUT);
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);

  dht.begin();

  // Connect to Wi-Fi
  Serial.printf("[WiFi] Connecting to %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int retries = 0;
  while (WiFi.status() != WL_CONNECTED && retries < 40) {
    delay(500);
    Serial.print(".");
    retries++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[WiFi] Connected! IP: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("\n[WiFi] Connection timed out. Running in offline fallback loop.");
  }

  udp.begin(UDP_PORT);
  Serial.printf("[UDP] Listening on port %d\n", UDP_PORT);

  lastCommandTime = millis();
}

void loop() {
  unsigned long now = millis();

  // 1. Maintain Wi-Fi Connection
  if (WiFi.status() != WL_CONNECTED && (now % 5000 == 0)) {
    WiFi.reconnect();
  }

  // 2. Process Incoming UDP Commands from Host
  int packetSize = udp.parsePacket();
  if (packetSize > 0) {
    char packetBuffer[256];
    int len = udp.read(packetBuffer, sizeof(packetBuffer) - 1);
    if (len > 0) {
      packetBuffer[len] = '\0';
      String cmd = String(packetBuffer);
      cmd.trim();

      lastCommandTime = now;

      if (cmd == "FAN_PREEMPTIVE_ON") {
        setRelay(true, "UDP PREEMPTIVE ON");
      } else if (cmd == "FAN_REACTIVE_ON") {
        setRelay(true, "UDP REACTIVE ON");
      } else if (cmd == "FAN_OFF") {
        setRelay(false, "UDP COMMAND OFF");
      } else {
        Serial.printf("[UDP] Unknown command: %s\n", cmd.c_str());
      }
    }
  }

  // 3. Failsafe Safety Guards
  // A) Heartbeat loss watchdog (30s)
  if (relayState == 1 && (now - lastCommandTime >= HEARTBEAT_TIMEOUT_MS)) {
    setRelay(false, "FAILSAFE: Host UDP heartbeat timeout (>30s)");
  }

  // B) Max continuous run limit guard (10m)
  if (relayState == 1 && (now - fanOnStartTime >= MAX_CONTINUOUS_ON_MS)) {
    setRelay(false, "FAILSAFE: Continuous run exceeded safety ceiling (10m)");
  }

  // 4. Transmit 1 Hz Telemetry JSON Packet to Host
  if (now - lastTelemetryTime >= TELEMETRY_INTERVAL_MS) {
    lastTelemetryTime = now;
    seq++;

    int rawGas = readMQ135Smoothed();
    float temp = dht.readTemperature();
    float hum = dht.readHumidity();
    int pir = digitalRead(PIN_PIR);

    if (isnan(temp)) temp = 24.0;
    if (isnan(hum)) hum = 50.0;

    unsigned long uptime = now / 1000;

    char jsonBuffer[256];
    snprintf(
      jsonBuffer,
      sizeof(jsonBuffer),
      "{\"seq\":%lu,\"mq135\":%d,\"temp\":%.1f,\"hum\":%.1f,\"pir\":%d,\"relay\":%d,\"fan\":%d,\"uptime\":%lu}",
      seq, rawGas, temp, hum, pir, relayState, fanState, uptime
    );

    if (WiFi.status() == WL_CONNECTED) {
      udp.beginPacket(HOST_IP, UDP_PORT);
      udp.write((const uint8_t*)jsonBuffer, strlen(jsonBuffer));
      udp.endPacket();
    }
  }
}
