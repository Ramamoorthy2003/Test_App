/*
 * Silambam Scoreboard - ESP32 hit sender
 *
 * Wire a hit sensor (piezo / button / IR) to HIT_PIN and a penalty
 * button to PEN_PIN. Set PLAYER to "A" or "B" on each board.
 * Set SCOREBOARD_IP to the IP shown at the bottom of the app screen.
 */

#include <WiFi.h>

const char* WIFI_SSID     = "YOUR_WIFI";
const char* WIFI_PASS     = "YOUR_PASSWORD";
const char* SCOREBOARD_IP = "192.168.1.50";   // shown in the app
const uint16_t PORT       = 5005;

const char* PLAYER = "A";                     // "A" on one board, "B" on the other

const int HIT_PIN = 4;                        // active LOW button / sensor
const int PEN_PIN = 5;
const unsigned long DEBOUNCE_MS = 250;

WiFiClient client;
unsigned long lastHit = 0, lastPen = 0;

void ensureConnected() {
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    while (WiFi.status() != WL_CONNECTED) { delay(300); Serial.print('.'); }
    Serial.print("\nWiFi ok, ip=");
    Serial.println(WiFi.localIP());
  }
  if (!client.connected()) {
    client.stop();
    if (client.connect(SCOREBOARD_IP, PORT)) {
      Serial.println("connected to scoreboard");
    } else {
      delay(500);
    }
  }
}

void send(const char* action) {
  ensureConnected();
  if (client.connected()) {
    client.printf("%s:%s\n", PLAYER, action);   // newline terminated
    client.flush();
    Serial.printf("sent %s:%s\n", PLAYER, action);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(HIT_PIN, INPUT_PULLUP);
  pinMode(PEN_PIN, INPUT_PULLUP);
  WiFi.mode(WIFI_STA);
  ensureConnected();
}

void loop() {
  unsigned long now = millis();

  if (digitalRead(HIT_PIN) == LOW && now - lastHit > DEBOUNCE_MS) {
    lastHit = now;
    send("HIT");
  }
  if (digitalRead(PEN_PIN) == LOW && now - lastPen > DEBOUNCE_MS) {
    lastPen = now;
    send("PENALTY");
  }

  if (now % 5000 < 2) ensureConnected();   // keep the socket alive
  delay(5);
}
