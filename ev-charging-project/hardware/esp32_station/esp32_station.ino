
/*
 * VOLTGUIDE - ESP32 STATION CONTROLLER
 * ------------------------------------
 * Hardware:
 *   GPIO26 - Green LED  (AVAILABLE)
 *   GPIO27 - Blue LED   (CHARGING)
 *   GPIO14 - White LED  (COMPLETE)
 *   GPIO18 - Red LED    (FAULT)
 *   GPIO32 - Push Button (FAULT / RESTORE toggle)
 *
 * Backend:
 *   POST /station/telemetry/ingest
 *   Station ID: 7 (Grand Mercure Mysore)
 *
 * IMPORTANT:
 *   Electrical values are NOT measured.
 *   No INA219 sensor or MOSFET load is connected.
 *   Electrical telemetry is explicitly SIMULATED.
 */

#include <WiFi.h>
#include <WiFiClient.h>
#include <HTTPClient.h>

// =================================================
// 1. WIFI & BACKEND CONFIGURATION
// =================================================

// Enter your existing Wi-Fi credentials here.
// Do not share your password in screenshots or messages.

const char* WIFI_SSID = "yoyoy";
const char* WIFI_PASSWORD = "11111111";

const char* API_BASE_URL = "http://10.176.192.194:8000";
const char* STATION_ID = "7";

const unsigned long TELEMETRY_INTERVAL = 3000;
const unsigned long WIFI_RETRY_INTERVAL = 10000;

// =================================================
// 2. GPIO CONFIGURATION
// =================================================

const int LED_AVAILABLE = 26;
const int LED_CHARGING  = 27;
const int LED_COMPLETE  = 14;
const int LED_FAULT     = 18;

const int FAULT_BUTTON = 32;

// =================================================
// 3. STATION STATE MACHINE
// =================================================

enum StationState {
  STATE_AVAILABLE,
  STATE_CHARGING,
  STATE_COMPLETE,
  STATE_FAULT
};

StationState currentState = STATE_AVAILABLE;

String faultReason = "";

// =================================================
// 4. TIMERS & BUTTON DEBOUNCING
// =================================================

unsigned long lastTelemetryPost = 0;
unsigned long lastWiFiAttempt = 0;

int lastRawButtonState = HIGH;
int stableButtonState = HIGH;

unsigned long lastDebounceTime = 0;

const unsigned long DEBOUNCE_DELAY = 50;

String serialBuffer = "";

// =================================================
// 5. STATE HELPERS
// =================================================

const char* getStateString(StationState state) {

  switch (state) {
    case STATE_AVAILABLE: return "AVAILABLE";
    case STATE_CHARGING:  return "CHARGING";
    case STATE_COMPLETE:  return "COMPLETE";
    case STATE_FAULT:     return "FAULT";
    default:              return "UNKNOWN";
  }
}

const char* getBackendStateString(StationState state) {

  switch (state) {
    case STATE_AVAILABLE: return "AVAILABLE";
    case STATE_CHARGING:  return "CHARGING";
    case STATE_COMPLETE:  return "COMPLETE";
    case STATE_FAULT:     return "FAULTED";
    default:              return "UNKNOWN";
  }
}

// =================================================
// 6. LED CONTROL
// =================================================

void updateLEDs() {

  // Turn all LEDs OFF first
  digitalWrite(LED_AVAILABLE, LOW);
  digitalWrite(LED_CHARGING, LOW);
  digitalWrite(LED_COMPLETE, LOW);
  digitalWrite(LED_FAULT, LOW);

  // Turn ON only the LED corresponding to state

  switch (currentState) {

    case STATE_AVAILABLE:
      digitalWrite(LED_AVAILABLE, HIGH);
      break;

    case STATE_CHARGING:
      digitalWrite(LED_CHARGING, HIGH);
      break;

    case STATE_COMPLETE:
      digitalWrite(LED_COMPLETE, HIGH);
      break;

    case STATE_FAULT:
      digitalWrite(LED_FAULT, HIGH);
      break;
  }

  Serial.print("[LED] Station state: ");
  Serial.println(getStateString(currentState));
}

// =================================================
// 7. HTTP TELEMETRY
// =================================================

void sendTelemetryToBackend() {

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[HTTP] Wi-Fi unavailable. Telemetry pending.");
    return;
  }

  WiFiClient client;
  HTTPClient http;

  String endpoint = String(API_BASE_URL) +
                    "/station/telemetry/ingest";

  // No INA219 or physical load measurement is connected.
  // Keep electrical fields at zero and explicitly label them
  // as simulated rather than reporting invented measurements.

  const bool isFault = (currentState == STATE_FAULT);

  char payload[512];

  snprintf(
    payload,
    sizeof(payload),

    "{"
      "\"station_id\":\"%s\","
      "\"source\":\"HARDWARE\","
      "\"state\":\"%s\","
      "\"voltage_v\":0.0,"
      "\"current_a\":0.0,"
      "\"power_w\":0.0,"
      "\"energy_wh\":0.0,"
      "\"fault\":%s,"
      "\"fault_code\":%s,"
      "\"controller_connected\":true,"
      "\"telemetry_connected\":false,"
      "\"telemetry_quality\":\"SIMULATED\","
      "\"relay_closed\":false"
    "}",

    STATION_ID,
    getBackendStateString(currentState),
    isFault ? "true" : "false",
    isFault ? "\"MANUAL_FAULT\"" : "null"
  );

  Serial.println("\n[HTTP] Sending station telemetry...");

  http.setConnectTimeout(3000);
  http.setTimeout(4000);

  if (!http.begin(client, endpoint)) {
    Serial.println("[HTTP] Initialization failed.");
    return;
  }

  http.addHeader("Content-Type", "application/json");

  int responseCode = http.POST(payload);

  Serial.print("[HTTP] Response code: ");
  Serial.println(responseCode);

  if (responseCode >= 200 && responseCode < 300) {

    Serial.println("[HTTP] Telemetry accepted.");
    Serial.println(http.getString());

  } else if (responseCode > 0) {

    Serial.println("[HTTP] Backend rejected the request.");
    Serial.println(http.getString());

  } else {

    Serial.print("[HTTP] Connection error: ");
    Serial.println(http.errorToString(responseCode));
  }

  http.end();
}

// =================================================
// 8. STATION STATE TRANSITION
// =================================================

void setStationState(StationState newState,
                     const char* reason = "") {

  if (currentState == newState) {
    return;
  }

  Serial.print("\n[STATE] ");
  Serial.print(getStateString(currentState));
  Serial.print(" -> ");
  Serial.println(getStateString(newState));

  currentState = newState;

  if (newState == STATE_FAULT) {
    faultReason = reason;
  } else if (newState == STATE_AVAILABLE) {
    faultReason = "";
  }

  updateLEDs();

  // Immediately report every state transition.
  sendTelemetryToBackend();
}

// =================================================
// 9. PHYSICAL BUTTON
// =================================================

void handlePhysicalButton() {

  int reading = digitalRead(FAULT_BUTTON);

  if (reading != lastRawButtonState) {

    lastDebounceTime = millis();
    lastRawButtonState = reading;
  }

  if ((millis() - lastDebounceTime) > DEBOUNCE_DELAY) {

    if (reading != stableButtonState) {

      stableButtonState = reading;

      // Button is active LOW.
      if (stableButtonState == LOW) {

        Serial.println("\n[BUTTON] Physical button pressed.");

        /*
         * DEMONSTRATION TOGGLE:
         * First press  -> FAULT
         * Second press -> AVAILABLE
         *
         * A real emergency-stop system should remain
         * latched until a separate safe reset procedure.
         */

        if (currentState == STATE_FAULT) {

          setStationState(STATE_AVAILABLE);

        } else {

          setStationState(
            STATE_FAULT,
            "MANUAL_FAULT"
          );
        }
      }
    }
  }
}

// =================================================
// 10. SERIAL COMMANDS
// =================================================

void handleSerialCommands() {

  while (Serial.available() > 0) {

    char c = (char)Serial.read();

    if (c == '\n' || c == '\r') {

      serialBuffer.trim();
      serialBuffer.toUpperCase();

      if (serialBuffer.length() == 0) {
        continue;
      }

      Serial.print("[SERIAL] Command: ");
      Serial.println(serialBuffer);

      if (serialBuffer == "START") {

        if (currentState == STATE_AVAILABLE) {
          setStationState(STATE_CHARGING);
        } else {
          Serial.println("[REJECTED] Station is not AVAILABLE.");
        }

      } else if (serialBuffer == "STOP") {

        if (currentState == STATE_CHARGING) {
          setStationState(STATE_AVAILABLE);
        } else {
          Serial.println("[REJECTED] Station is not CHARGING.");
        }

      } else if (serialBuffer == "COMPLETE") {

        if (currentState == STATE_CHARGING) {
          setStationState(STATE_COMPLETE);
        } else {
          Serial.println("[REJECTED] Station is not CHARGING.");
        }

      } else if (serialBuffer == "FAULT") {

        setStationState(STATE_FAULT, "MANUAL_FAULT");

      } else if (serialBuffer == "RESTORE") {

        if (currentState == STATE_FAULT ||
            currentState == STATE_COMPLETE) {

          setStationState(STATE_AVAILABLE);

        } else {
          Serial.println("[NOTICE] Already AVAILABLE.");
        }

      } else if (serialBuffer == "STATUS") {

        printStatus();

      } else if (serialBuffer == "POST") {

        sendTelemetryToBackend();

      } else {

        Serial.println(
          "Commands: START, STOP, COMPLETE, FAULT, RESTORE, STATUS, POST"
        );
      }

      serialBuffer = "";

    } else {

      if (serialBuffer.length() < 32) {
        serialBuffer += c;
      }
    }
  }
}

// =================================================
// 11. WIFI CONNECTION MANAGEMENT
// =================================================

void manageWiFi() {

  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  unsigned long now = millis();

  if (now - lastWiFiAttempt >= WIFI_RETRY_INTERVAL) {

    lastWiFiAttempt = now;

    Serial.println("[WIFI] Attempting reconnection...");

    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  }
}

// =================================================
// 12. STATUS DISPLAY
// =================================================

void printStatus() {

  Serial.println("\n========== VOLTGUIDE STATUS ==========");

  Serial.print("Station ID: ");
  Serial.println(STATION_ID);

  Serial.print("Station State: ");
  Serial.println(getStateString(currentState));

  Serial.print("Fault: ");
  Serial.println(currentState == STATE_FAULT ? "ACTIVE" : "CLEAR");

  Serial.print("Wi-Fi: ");
  Serial.println(
    WiFi.status() == WL_CONNECTED ? "CONNECTED" : "DISCONNECTED"
  );

  if (WiFi.status() == WL_CONNECTED) {

    Serial.print("ESP32 IP: ");
    Serial.println(WiFi.localIP());
  }

  Serial.println("Electrical sensing: NOT CONNECTED");
  Serial.println("Electrical telemetry: SIMULATED");
  Serial.println("MOSFET/load control: NOT CONNECTED");
  Serial.println("=====================================\n");
}

// =================================================
// 13. SETUP
// =================================================

void setup() {

  Serial.begin(115200);
  delay(1000);

  Serial.println("\n==================================");
  Serial.println("  VOLTGUIDE ESP32 STATION NODE");
  Serial.println("==================================");

  pinMode(LED_AVAILABLE, OUTPUT);
  pinMode(LED_CHARGING, OUTPUT);
  pinMode(LED_COMPLETE, OUTPUT);
  pinMode(LED_FAULT, OUTPUT);

  pinMode(FAULT_BUTTON, INPUT_PULLUP);

  // Initial station state
  currentState = STATE_AVAILABLE;
  updateLEDs();

  // Configure Wi-Fi
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);

  Serial.print("[WIFI] Connecting to: ");
  Serial.println(WIFI_SSID);

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  lastWiFiAttempt = millis();

  Serial.println("[SYSTEM] Ready.");
  Serial.println("[SYSTEM] Serial commands available.");
}

// =================================================
// 14. MAIN LOOP
// =================================================

void loop() {

  handlePhysicalButton();

  handleSerialCommands();

  manageWiFi();

  unsigned long now = millis();

  // Periodic telemetry / heartbeat
  if (WiFi.status() == WL_CONNECTED &&
      now - lastTelemetryPost >= TELEMETRY_INTERVAL) {

    lastTelemetryPost = now;

    sendTelemetryToBackend();
  }

  delay(5);
}
