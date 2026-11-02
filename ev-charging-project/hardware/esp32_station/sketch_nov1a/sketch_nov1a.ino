
#include <WiFi.h>
#include <HTTPClient.h>

const char* WIFI_SSID = "yoyoy";
const char* WIFI_PASSWORD = "11111111";

const char* HEALTH_URL =
  "http://10.176.192.194:8000/health";

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("\nVoltGuide ESP32 Network Test");

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.print("Connecting to Wi-Fi");

  unsigned long start = millis();

  while (WiFi.status() != WL_CONNECTED &&
         millis() - start < 20000) {
    delay(500);
    Serial.print(".");
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\nWi-Fi connection FAILED");
    return;
  }

  Serial.println("\nWi-Fi CONNECTED");
  Serial.print("SSID: ");
Serial.println(WiFi.SSID());

Serial.print("Gateway: ");
Serial.println(WiFi.gatewayIP());

Serial.print("Subnet: ");
Serial.println(WiFi.subnetMask());
  Serial.print("ESP32 IP: ");
  Serial.println(WiFi.localIP());

  WiFiClient client;
  HTTPClient http;

  if (http.begin(client, HEALTH_URL)) {
    int responseCode = http.GET();

    Serial.print("HTTP Response: ");
    Serial.println(responseCode);

    if (responseCode > 0) {
      Serial.println(http.getString());
    }

    http.end();
  } else {
    Serial.println("HTTP initialization failed");
  }
}

void loop() {
  delay(1000);
}
