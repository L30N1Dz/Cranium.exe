#include <Arduino.h>

// Pin assignments for Joystick 1
const int JOY1_X_PIN = A0;
const int JOY1_Y_PIN = A1;
const int JOY1_BTN_PIN = 2;

// Pin assignments for Joystick 2
const int JOY2_X_PIN = A2;
const int JOY2_Y_PIN = A3;
const int JOY2_BTN_PIN = 3;

const unsigned long BAUD_RATE = 9600;

bool started = false;

int readAnalogAveraged(int pin, uint8_t samples = 5) {
  long sum = 0;
  for (uint8_t i = 0; i < samples; ++i) {
    sum += analogRead(pin);
  }
  return sum / samples;
}

void setup() {
  Serial.begin(BAUD_RATE);
  pinMode(JOY1_BTN_PIN, INPUT_PULLUP);
  pinMode(JOY2_BTN_PIN, INPUT_PULLUP);
  Serial.println("Awaiting START command...");
}

void loop() {
  if (!started) {
    if (Serial.available()) {
      String cmd = Serial.readStringUntil('\n');
      cmd.trim();
      if (cmd.equalsIgnoreCase("START")) {
        started = true;
        Serial.println("Starting joystick readout");
      }
    }
  } else {
    int j1x = readAnalogAveraged(JOY1_X_PIN);
    int j1y = readAnalogAveraged(JOY1_Y_PIN);
    int j2x = readAnalogAveraged(JOY2_X_PIN);
    int j2y = readAnalogAveraged(JOY2_Y_PIN);
    bool j1Btn = digitalRead(JOY1_BTN_PIN) == LOW;
    bool j2Btn = digitalRead(JOY2_BTN_PIN) == LOW;

    Serial.print(j1x); Serial.print(',');
    Serial.print(j1y); Serial.print(',');
    Serial.print(j1Btn); Serial.print(',');
    Serial.print(j2x); Serial.print(',');
    Serial.print(j2y); Serial.print(',');
    Serial.println(j2Btn);

    delay(50); // limit update rate
  }
}

