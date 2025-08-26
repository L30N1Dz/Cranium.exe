// Motor_Test_UART.ino
// Arduino Uno + Adafruit PCA9685 (16-ch PWM/Servo driver)
// MG90S micro servos (approx 0–180°). Centers all servos on boot,
// then accepts simple UART commands to position each servo.
//
// Wiring:
//  - PCA9685 VCC -> 5V (logic), GND -> GND, SDA -> A4, SCL -> A5 (Arduino Uno)
//  - External 5-6V supply to PCA9685 V+ for servos (share GND with Arduino!)
//  - Servos on channels 0..3
//
// Commands (newline-terminated):
//   CENTER                         -> center all four servos (90°)
//   SET <ID> <ANGLE>               -> set one servo to angle (0..180)
//     IDs: 0 1 2 3  or  LX LY RX RY  (case-insensitive)
//     e.g.  SET LX 120
//           SET 2 45
//   SET ALL <ANGLE>                -> set all to one angle
//   MAP <ID> <MINµs> <MAXµs>       -> set uS range for a servo (defaults below)
//   MAP ALL <MINµs> <MAXµs>        -> set uS range for all four
//   GET                            -> print current angles & ranges
//   HELP                           -> print help
//
// Notes:
//  * We only update a channel if the commanded angle changes (to avoid redundant writes).
//  * Adjust SERVO_FREQ_HZ if your servos prefer 50 Hz; MG90S usually works at 50–60 Hz.
//  * If your mechanical center isn’t at 90°, you can add per-servo TRIM_DEG offsets.

#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>

// ======== User Config ========
static const uint8_t SERVO_CHANNELS[4] = {0, 1, 4, 5}; // LX, LY, RX, RY
static const uint8_t IDX_LX = 0;
static const uint8_t IDX_LY = 1;
static const uint8_t IDX_RX = 2;
static const uint8_t IDX_RY = 3;

static const uint16_t DEFAULT_MIN_US = 500;   // pulse @ 0°
static const uint16_t DEFAULT_MAX_US = 2500;  // pulse @ 180°
static const uint16_t SERVO_FREQ_HZ = 50;     // 50 or 60 Hz

// Optional per-servo angle trim (in degrees) to fine-center
int8_t TRIM_DEG[4] = {0, 0, 0, 0};

// ======== Globals ========
Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver();

// Per-servo limits and current targets
uint16_t minUs[4] = {DEFAULT_MIN_US, DEFAULT_MIN_US, DEFAULT_MIN_US, DEFAULT_MIN_US};
uint16_t maxUs[4] = {DEFAULT_MAX_US, DEFAULT_MAX_US, DEFAULT_MAX_US, DEFAULT_MAX_US};
int16_t targetAngle[4] = {90, 90, 90, 90};
int16_t lastAngleSent[4] = {-1, -1, -1, -1};

// ======== Helpers ========
uint16_t usToTicks(uint16_t microseconds) {
  // PCA9685: 12-bit (0..4095) over one period
  // ticks = microseconds / (1e6 / freq / 4096) = microseconds * freq * 4096 / 1e6
  // To reduce rounding error, compute in 32-bit
  uint32_t ticks = (uint32_t)microseconds * SERVO_FREQ_HZ * 4096UL / 1000000UL;
  if (ticks > 4095) ticks = 4095;
  return (uint16_t)ticks;
}

uint16_t angleToUs(uint8_t idx, int angleDeg) {
  // Constrain and map angle to uS using that servo's limits
  angleDeg = constrain(angleDeg + TRIM_DEG[idx], 0, 180);
  return (uint16_t) ( (long)angleDeg * (maxUs[idx] - minUs[idx]) / 180L + minUs[idx] );
}

void writeServoIfChanged(uint8_t idx, int angleDeg) {
  angleDeg = constrain(angleDeg, 0, 180);
  if (angleDeg == lastAngleSent[idx]) return; // no-op if unchanged
  lastAngleSent[idx] = angleDeg;

  uint16_t us = angleToUs(idx, angleDeg);
  uint16_t ticks = usToTicks(us);
  pwm.setPWM(SERVO_CHANNELS[idx], 0, ticks);
}

// Map text IDs to indices
int idToIndex(const String &id) {
  String s = id; s.trim(); s.toUpperCase();
  if (s == "LX") return IDX_LX;
  if (s == "LY") return IDX_LY;
  if (s == "RX") return IDX_RX;
  if (s == "RY") return IDX_RY;
  // Also allow numeric 0..3
  if (s.length() == 1 && s[0] >= '0' && s[0] <= '3') return s[0] - '0';
  return -1;
}

void printHelp() {
  Serial.println(F("\nCommands:"));
  Serial.println(F("  HELP"));
  Serial.println(F("  GET"));
  Serial.println(F("  CENTER"));
  Serial.println(F("  SET <ID> <ANGLE>       (ID: 0-3 or LX LY RX RY, ANGLE: 0-180)"));
  Serial.println(F("  SET ALL <ANGLE>"));
  Serial.println(F("  MAP <ID> <MINus> <MAXus>"));
  Serial.println(F("  MAP ALL <MINus> <MAXus>"));
}

void printStatus() {
  Serial.print(F("Angles: "));
  Serial.print(F("LX=")); Serial.print(targetAngle[IDX_LX]);
  Serial.print(F(" LY=")); Serial.print(targetAngle[IDX_LY]);
  Serial.print(F(" RX=")); Serial.print(targetAngle[IDX_RX]);
  Serial.print(F(" RY=")); Serial.println(targetAngle[IDX_RY]);

  Serial.print(F("Ranges (us): "));
  Serial.print(F("LX=")); Serial.print(minUs[IDX_LX]); Serial.print('-'); Serial.print(maxUs[IDX_LX]); Serial.print(' ');
  Serial.print(F("LY=")); Serial.print(minUs[IDX_LY]); Serial.print('-'); Serial.print(maxUs[IDX_LY]); Serial.print(' ');
  Serial.print(F("RX=")); Serial.print(minUs[IDX_RX]); Serial.print('-'); Serial.print(maxUs[IDX_RX]); Serial.print(' ');
  Serial.print(F("RY=")); Serial.print(minUs[IDX_RY]); Serial.print('-'); Serial.print(maxUs[IDX_RY]); Serial.println();
}

void centerAll() {
  for (uint8_t i = 0; i < 4; i++) targetAngle[i] = 90;
}

void pushAllIfChanged() {
  for (uint8_t i = 0; i < 4; i++) writeServoIfChanged(i, targetAngle[i]);
}

// Parse a line of input
void handleLine(String line) {
  line.trim();
  if (line.length() == 0) return;

  // Tokenize by spaces
  const uint8_t MAX_TOK = 5;
  String tok[MAX_TOK];
  uint8_t ntok = 0;
  int start = 0;
  while (ntok < MAX_TOK) {
    int sp = line.indexOf(' ', start);
    if (sp < 0) {
      tok[ntok++] = line.substring(start);
      break;
    } else {
      tok[ntok++] = line.substring(start, sp);
      start = sp + 1;
      while (start < (int)line.length() && line[start] == ' ') start++; // skip multiple spaces
    }
  }
  if (ntok == 0) return;

  String cmd = tok[0];
  cmd.toUpperCase();

  if (cmd == "HELP") {
    printHelp();
    return;
  }
  if (cmd == "GET") {
    printStatus();
    return;
  }
  if (cmd == "CENTER") {
    centerAll();
    pushAllIfChanged();
    Serial.println(F("OK CENTER"));
    return;
  }
  if (cmd == "SET" && ntok >= 3) {
    String id = tok[1];
    int idx = idToIndex(id);
    if (idx < 0) { Serial.println(F("ERR bad ID")); return; }
    int ang = tok[2].toInt();
    ang = constrain(ang, 0, 180);
    targetAngle[idx] = ang;
    writeServoIfChanged(idx, targetAngle[idx]);
    Serial.print(F("OK SET ")); Serial.print(id); Serial.print(' '); Serial.println(ang);
    return;
  }
  if (cmd == "SET" && ntok >= 3 && tok[1].equalsIgnoreCase("ALL")) {
    int ang = tok[2].toInt();
    ang = constrain(ang, 0, 180);
    for (uint8_t i = 0; i < 4; i++) targetAngle[i] = ang;
    pushAllIfChanged();
    Serial.print(F("OK SET ALL ")); Serial.println(ang);
    return;
  }
  if (cmd == "MAP" && ntok >= 4 && !tok[1].equalsIgnoreCase("ALL")) {
    int idx = idToIndex(tok[1]);
    if (idx < 0) { Serial.println(F("ERR bad ID")); return; }
    uint16_t mi = (uint16_t) tok[2].toInt();
    uint16_t ma = (uint16_t) tok[3].toInt();
    if (mi >= ma) { Serial.println(F("ERR min>=max")); return; }
    minUs[idx] = mi; maxUs[idx] = ma;
    // Re-apply current angle with new mapping
    writeServoIfChanged(idx, targetAngle[idx]);
    Serial.print(F("OK MAP ")); Serial.print(tok[1]); Serial.print(' ');
    Serial.print(mi); Serial.print(' '); Serial.println(ma);
    return;
  }
  if (cmd == "MAP" && ntok >= 4 && tok[1].equalsIgnoreCase("ALL")) {
    uint16_t mi = (uint16_t) tok[2].toInt();
    uint16_t ma = (uint16_t) tok[3].toInt();
    if (mi >= ma) { Serial.println(F("ERR min>=max")); return; }
    for (uint8_t i = 0; i < 4; i++) { minUs[i] = mi; maxUs[i] = ma; }
    pushAllIfChanged();
    Serial.print(F("OK MAP ALL ")); Serial.print(mi); Serial.print(' '); Serial.println(ma);
    return;
  }

  Serial.println(F("ERR unknown command. Type HELP."));
}

// ======== Arduino ========
void setup() {
  Serial.begin(115200);
  while (!Serial) { ; }

  Wire.begin();
  pwm.begin();
  pwm.setPWMFreq(SERVO_FREQ_HZ);
  delay(10);

  // Center all on boot
  centerAll();
  pushAllIfChanged();

  Serial.println(F("READY Motor_Test_UART v1.0"));
  printHelp();
  printStatus();
}

String lineBuf;

void loop() {
  // Read newline-terminated commands
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue; // ignore CR
    if (c == '\n') {
      handleLine(lineBuf);
      lineBuf = "";
    } else {
      // Basic safety to avoid unbounded growth
      if (lineBuf.length() < 96) lineBuf += c;
    }
  }

  // Nothing else required in loop; updates happen on command
}

