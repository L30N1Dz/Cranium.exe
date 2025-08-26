// ===== Motor_Test_UART_v2_fixed.ino =====
// Robust single-file sketch that avoids Arduino auto-prototype issues.
// - Centers on boot
// - Text + JSON serial commands
// - Pair control (SET X / SET Y)
// - Per-servo invert & trim
// - EEPROM save/load with checksum + dump tools
// - Non-blocking tweening (optional)
// - No <algorithm> dependency; uses tiny inline imin/imax helpers
// - No function parameters of type Config (prevents auto-prototype conflicts)
//
// IMPORTANT: Keep this whole file in one tab. If you split into multiple .ino files,
// Arduino concatenates and auto-inserts prototypes in ways that may reintroduce
// the "Config was not declared" error.

// --- Make sure the type name exists before auto-generated prototypes ---
struct Config; // forward declare, belt-and-suspenders (we also avoid using Config in prototypes)

#include <Wire.h>
#include <EEPROM.h>
#include <Adafruit_PWMServoDriver.h>
#include <ArduinoJson.h>

// ---- Local helpers (avoid <algorithm> & macro pitfalls) ----
static inline int imin_int(int a,int b){return (a<b)?a:b;}
static inline int imax_int(int a,int b){return (a>b)?a:b;}

// ===== Hardware & IDs =====
static const uint8_t SERVO_CHANNELS[4] = {0, 1, 4, 5}; // LX, LY, RX, RY
static const uint8_t IDX_LX = 0; // Left X
static const uint8_t IDX_LY = 1; // Left Y
static const uint8_t IDX_RX = 2; // Right X
static const uint8_t IDX_RY = 3; // Right Y

// ===== Defaults =====
static const uint16_t DEFAULT_MIN_US = 500;   // pulse @ 0°
static const uint16_t DEFAULT_MAX_US = 2500;  // pulse @ 180°
static const uint16_t DEFAULT_FREQ_HZ = 50;   // 50 or 60 Hz
static const int8_t   DEFAULT_TRIM_DEG[4] = {0,0,0,0};
static const uint8_t  DEFAULT_INVERT[4]  = {0,0,1,1}; // invert RX/RY by default (typical mirrored mount)
static const int16_t  DEFAULT_ANGLE[4]   = {90,90,90,90};
static const uint8_t  DEFAULT_STEP_DEG   = 2;   // smoothing step (deg)
static const uint16_t DEFAULT_STEP_MS    = 10;  // smoothing interval (ms)

// ===== Persistent configuration (EEPROM) =====
struct Config {
  uint32_t magic;     // 'MTR1'
  uint8_t  version;   // 1
  uint16_t minUs[4];
  uint16_t maxUs[4];
  int8_t   trimDeg[4];
  uint8_t  invert[4];
  uint16_t stepMs;    // 0 disables tweening
  uint8_t  stepDeg;   // 0 disables tweening
  uint16_t freqHz;    // 50 or 60
  int16_t  lastAngle[4];
  uint32_t checksum;  // FNV-1a over all bytes up to (but not including) this field
};

static const uint32_t CFG_MAGIC = 0x4D545231; // 'MTR1'
static const uint8_t  CFG_VERSION = 1;

// ===== Globals =====
Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver();
Config   cfg;                    // live config
int16_t  targetAngle[4]  = {90,90,90,90};
int16_t  currentAngle[4] = {90,90,90,90};
int16_t  lastSentAngle[4]= {-1,-1,-1,-1};
uint32_t lastStepMs = 0;

// ===== Checksum (FNV-1a 32-bit) =====
uint32_t fnv1a32(const uint8_t* data, size_t len) {
  uint32_t hash = 2166136261UL;
  for (size_t i=0;i<len;i++) { hash ^= data[i]; hash *= 16777619UL; }
  return hash;
}

uint32_t computeCfgChecksum(const Config& c) {
  const uint8_t* p = (const uint8_t*)&c;
  return fnv1a32(p, sizeof(Config) - sizeof(uint32_t));
}

// ===== Helpers =====
uint16_t usToTicks(uint16_t microseconds) {
  uint32_t ticks = (uint32_t)microseconds * cfg.freqHz * 4096UL / 1000000UL;
  if (ticks > 4095) ticks = 4095;
  return (uint16_t)ticks;
}

uint16_t angleToUs(uint8_t idx, int angleDeg) {
  // Apply trim and inversion, then map to uS
  angleDeg = constrain(angleDeg, 0, 180);
  if (cfg.invert[idx]) angleDeg = 180 - angleDeg;
  angleDeg = constrain(angleDeg + cfg.trimDeg[idx], 0, 180);
  return (uint16_t)((long)angleDeg * (cfg.maxUs[idx] - cfg.minUs[idx]) / 180L + cfg.minUs[idx]);
}

void sendAngle(uint8_t idx, int angleDeg) {
  angleDeg = constrain(angleDeg, 0, 180);
  if (angleDeg == lastSentAngle[idx]) return;
  lastSentAngle[idx] = angleDeg;
  uint16_t us = angleToUs(idx, angleDeg);
  uint16_t ticks = usToTicks(us);
  pwm.setPWM(SERVO_CHANNELS[idx], 0, ticks);
}

void applyAllImmediate() {
  for (uint8_t i=0;i<4;i++) {
    currentAngle[i] = targetAngle[i];
    sendAngle(i, currentAngle[i]);
  }
}

void setTarget(uint8_t idx, int angleDeg) { targetAngle[idx] = constrain(angleDeg, 0, 180); }
void setPairX(int angleDeg) { setTarget(IDX_LX, angleDeg); setTarget(IDX_RX, angleDeg); }
void setPairY(int angleDeg) { setTarget(IDX_LY, angleDeg); setTarget(IDX_RY, angleDeg); }
void centerAll() { for (uint8_t i=0;i<4;i++) setTarget(i, 90); }

void printStatus() {
  Serial.print(F("Angles tgt/cur: "));
  Serial.print(F("LX=")); Serial.print(targetAngle[IDX_LX]); Serial.print('/'); Serial.print(currentAngle[IDX_LX]); Serial.print(' ');
  Serial.print(F("LY=")); Serial.print(targetAngle[IDX_LY]); Serial.print('/'); Serial.print(currentAngle[IDX_LY]); Serial.print(' ');
  Serial.print(F("RX=")); Serial.print(targetAngle[IDX_RX]); Serial.print('/'); Serial.print(currentAngle[IDX_RX]); Serial.print(' ');
  Serial.print(F("RY=")); Serial.print(targetAngle[IDX_RY]); Serial.print('/'); Serial.print(currentAngle[IDX_RY]); Serial.println();

  Serial.print(F("Ranges (us): "));
  for (uint8_t i=0;i<4;i++) { Serial.print(cfg.minUs[i]); Serial.print('-'); Serial.print(cfg.maxUs[i]); Serial.print(i==3?'\n':' ');}  

  Serial.print(F("Invert: "));
  Serial.print(F("LX=")); Serial.print(cfg.invert[IDX_LX]); Serial.print(' ');
  Serial.print(F("LY=")); Serial.print(cfg.invert[IDX_LY]); Serial.print(' ');
  Serial.print(F("RX=")); Serial.print(cfg.invert[IDX_RX]); Serial.print(' ');
  Serial.print(F("RY=")); Serial.println(cfg.invert[IDX_RY]);

  Serial.print(F("Trim: "));
  Serial.print(cfg.trimDeg[0]); Serial.print(' ');
  Serial.print(cfg.trimDeg[1]); Serial.print(' ');
  Serial.print(cfg.trimDeg[2]); Serial.print(' ');
  Serial.println(cfg.trimDeg[3]);

  Serial.print(F("Freq: ")); Serial.print(cfg.freqHz); Serial.println(F(" Hz"));
  Serial.print(F("Tween: stepDeg=")); Serial.print(cfg.stepDeg);
  Serial.print(F(" intervalMs=")); Serial.println(cfg.stepMs);
}

void printHelp() {
  Serial.println(F("\nCommands:"));
  Serial.println(F("  HELP | GET | CENTER"));
  Serial.println(F("  SET <ID|ALL|X|Y> <ANGLE>"));
  Serial.println(F("  MAP <ID|ALL> <MINus> <MAXus>"));
  Serial.println(F("  INVERT <ID> <0|1>"));
  Serial.println(F("  FREQ <50|60>"));
  Serial.println(F("  TWEEN <stepDeg> <intervalMs> (0 disables)"));
  Serial.println(F("  SAVE | LOAD | RESETCFG | DUMPCFG | DUMPBIN"));
  Serial.println(F("JSON e.g. {\"X\":90,\"invert\":{\"RX\":1},\"map\":{\"ALL\":[600,2400]},\"dumpcfg\":true}"));
}

int idToIndex(const String &id) {
  String s=id; s.trim(); s.toUpperCase();
  if (s=="LX") return IDX_LX; if (s=="LY") return IDX_LY; if (s=="RX") return IDX_RX; if (s=="RY") return IDX_RY;
  if (s.length()==1 && s[0]>='0' && s[0]<='3') return s[0]-'0';
  return -1;
}

// ===== EEPROM helpers (no Config params to avoid auto-prototype issues) =====
void loadDefaults() {
  cfg.magic   = CFG_MAGIC;  cfg.version = CFG_VERSION;
  for (uint8_t i=0;i<4;i++) {
    cfg.minUs[i]=DEFAULT_MIN_US; cfg.maxUs[i]=DEFAULT_MAX_US;
    cfg.trimDeg[i]=DEFAULT_TRIM_DEG[i]; cfg.invert[i]=DEFAULT_INVERT[i];
    cfg.lastAngle[i]=DEFAULT_ANGLE[i];
  }
  cfg.stepMs  = DEFAULT_STEP_MS;
  cfg.stepDeg = DEFAULT_STEP_DEG;
  cfg.freqHz  = DEFAULT_FREQ_HZ;
  cfg.checksum = computeCfgChecksum(cfg);
}

void saveToEEPROM() {
  for (uint8_t i=0;i<4;i++) cfg.lastAngle[i] = targetAngle[i];
  cfg.checksum = computeCfgChecksum(cfg);
  EEPROM.put(0, cfg);
  Serial.print(F("OK SAVE checksum=0x")); Serial.println(cfg.checksum, HEX);
}

bool loadFromEEPROM() {
  Config tmp; EEPROM.get(0, tmp);
  if (tmp.magic != CFG_MAGIC || tmp.version != CFG_VERSION) return false;
  uint32_t expect = computeCfgChecksum(tmp);
  if (tmp.checksum != expect) {
    Serial.print(F("ERR LOAD checksum mismatch stored=0x")); Serial.print(tmp.checksum, HEX);
    Serial.print(F(" expect=0x")); Serial.println(expect, HEX);
    return false;
  }
  cfg = tmp; return true;
}

void resetAndSave() { loadDefaults(); saveToEEPROM(); Serial.println(F("OK RESETCFG")); }

// ===== Command Parsing =====
void handleTextLine(String line) {
  line.trim(); if (!line.length()) return;
  // If it looks like JSON, try JSON first
  if (line[0]=='{' && line.endsWith("}")) {
    StaticJsonDocument<512> doc;
    DeserializationError err = deserializeJson(doc, line);
    if (!err) {
      // Angles by pair
      if (doc.containsKey("X")) setPairX(constrain((int)doc["X"],0,180));
      if (doc.containsKey("Y")) setPairY(constrain((int)doc["Y"],0,180));
      // Individual
      const char* keys[4] = {"LX","LY","RX","RY"};
      for (uint8_t i=0;i<4;i++) if (doc.containsKey(keys[i])) setTarget(i, constrain((int)doc[keys[i]],0,180));
      // center
      if (doc.containsKey("center") && doc["center"]) centerAll();
      // map
      if (doc.containsKey("map")) {
        JsonVariant m = doc["map"]; if (m.containsKey("ALL")) {
          JsonArray arr = m["ALL"]; if (arr.size()>=2) { uint16_t mi=arr[0], ma=arr[1]; if (mi<ma){ for(uint8_t i=0;i<4;i++){ cfg.minUs[i]=mi; cfg.maxUs[i]=ma; } } }
        }
        for (uint8_t i=0;i<4;i++) if (m.containsKey(keys[i])) {
          JsonArray arr = m[keys[i]]; if (arr.size()>=2){ uint16_t mi=arr[0], ma=arr[1]; if (mi<ma){ cfg.minUs[i]=mi; cfg.maxUs[i]=ma; } }
        }
      }
      // invert
      if (doc.containsKey("invert")) {
        JsonVariant inv = doc["invert"]; for (uint8_t i=0;i<4;i++) if (inv.containsKey(keys[i])) cfg.invert[i] = inv[keys[i]] ? 1 : 0;
      }
      // tween
      if (doc.containsKey("tween")) {
        JsonVariant tw = doc["tween"]; if (tw.containsKey("step_deg")) cfg.stepDeg = (uint8_t)max(0, (int)tw["step_deg"]);
        if (tw.containsKey("interval_ms")) cfg.stepMs = (uint16_t)max(0, (int)tw["interval_ms"]);
      }
      // freq
      if (doc.containsKey("freq")) { int f = (int)doc["freq"]; if (f==50||f==60){ cfg.freqHz=f; pwm.setPWMFreq(cfg.freqHz); } }
      // save/load/dumps
      if (doc.containsKey("save") && doc["save"]) saveToEEPROM();
      if (doc.containsKey("load") && doc["load"]) { if (loadFromEEPROM()) Serial.println(F("OK LOAD")); else Serial.println(F("ERR LOAD (no/invalid cfg)")); }
      if (doc.containsKey("dumpcfg") && doc["dumpcfg"]) { printStatus(); Serial.print(F("Checksum=0x")); Serial.println(computeCfgChecksum(cfg), HEX); }
      if (doc.containsKey("dumpbin") && doc["dumpbin"]) { const uint8_t* p = (const uint8_t*)&cfg; Serial.println(F("CFG BYTES:")); for (size_t i=0;i<sizeof(Config); i++) { if (i%16==0){ Serial.println(); } if (p[i]<16) Serial.print('0'); Serial.print(p[i], HEX); Serial.print(' ');} Serial.println(); }

      Serial.println(F("OK JSON"));
      return;
    }
    // If JSON failed, fall through to text commands
  }

  // Tokenize text
  const uint8_t MAX_TOK=5; String tok[MAX_TOK]; uint8_t ntok=0; int start=0;
  while (ntok<MAX_TOK) {
    int sp=line.indexOf(' ', start);
    if (sp<0){ tok[ntok++]=line.substring(start); break; }
    tok[ntok++]=line.substring(start, sp); start=sp+1; while (start<(int)line.length() && line[start]==' ') start++;
  }
  if (!ntok) return; String cmd = tok[0]; cmd.toUpperCase();

  if (cmd=="HELP") { printHelp(); return; }
  if (cmd=="GET")  { printStatus(); return; }
  if (cmd=="CENTER") { centerAll(); Serial.println(F("OK CENTER")); return; }

  if (cmd=="SET" && ntok>=3) {
    String id=tok[1]; id.toUpperCase(); int ang=constrain(tok[2].toInt(),0,180);
    if (id=="ALL") { for(uint8_t i=0;i<4;i++) setTarget(i, ang); Serial.print(F("OK SET ALL ")); Serial.println(ang); return; }
    if (id=="X")   { setPairX(ang); Serial.print(F("OK SET X ")); Serial.println(ang); return; }
    if (id=="Y")   { setPairY(ang); Serial.print(F("OK SET Y ")); Serial.println(ang); return; }
    int idx=idToIndex(id); if (idx<0){ Serial.println(F("ERR bad ID")); return; }
    setTarget(idx, ang); Serial.print(F("OK SET ")); Serial.print(id); Serial.print(' '); Serial.println(ang); return;
  }

  if (cmd=="MAP" && ntok>=4) {
    String id=tok[1]; uint16_t mi=(uint16_t)tok[2].toInt(); uint16_t ma=(uint16_t)tok[3].toInt(); if (mi>=ma){ Serial.println(F("ERR min>=max")); return; }
    if (id.equalsIgnoreCase("ALL")) { for(uint8_t i=0;i<4;i++){ cfg.minUs[i]=mi; cfg.maxUs[i]=ma; } Serial.println(F("OK MAP ALL")); return; }
    int idx=idToIndex(id); if (idx<0){ Serial.println(F("ERR bad ID")); return; }
    cfg.minUs[idx]=mi; cfg.maxUs[idx]=ma; Serial.print(F("OK MAP ")); Serial.println(id); return;
  }

  if (cmd=="INVERT" && ntok>=3) {
    int idx=idToIndex(tok[1]); if (idx<0){ Serial.println(F("ERR bad ID")); return; }
    cfg.invert[idx] = tok[2].toInt()?1:0; Serial.print(F("OK INVERT ")); Serial.print(tok[1]); Serial.print(' '); Serial.println((int)cfg.invert[idx]); return;
  }

  if (cmd=="FREQ" && ntok>=2) {
    int f=tok[1].toInt(); if (f==50||f==60){ cfg.freqHz=f; pwm.setPWMFreq(cfg.freqHz); Serial.print(F("OK FREQ ")); Serial.println(f); } else Serial.println(F("ERR freq")); return;
  }

  if (cmd=="TWEEN" && ntok>=3) {
    cfg.stepDeg=(uint8_t)max(0, tok[1].toInt()); cfg.stepMs=(uint16_t)max(0, tok[2].toInt()); Serial.println(F("OK TWEEN")); return;
  }

  if (cmd=="SAVE") { saveToEEPROM(); return; }
  if (cmd=="LOAD") { if (loadFromEEPROM()){ Serial.println(F("OK LOAD")); } else { Serial.println(F("ERR LOAD (no/invalid cfg)")); } return; }
  if (cmd=="RESETCFG") { resetAndSave(); return; }
  if (cmd=="DUMPCFG" ) { printStatus(); Serial.print(F("Checksum=0x")); Serial.println(computeCfgChecksum(cfg), HEX); return; }
  if (cmd=="DUMPBIN" ) {
    const uint8_t* p = (const uint8_t*)&cfg; Serial.println(F("CFG BYTES:"));
    for (size_t i=0;i<sizeof(Config); i++) { if (i%16==0){ Serial.println(); } if (p[i]<16) Serial.print('0'); Serial.print(p[i], HEX); Serial.print(' ');} Serial.println();
    return;
  }

  Serial.println(F("ERR unknown command. Type HELP."));
}

// ===== Arduino =====
String lineBuf;

void setup() {
  Serial.begin(115200); while(!Serial){}

  // Load configuration
  if (!loadFromEEPROM()) { loadDefaults(); saveToEEPROM(); }

  // Init I2C + PWM
  Wire.begin(); pwm.begin(); pwm.setPWMFreq(cfg.freqHz); delay(10);

  // Initialize angles
  for (uint8_t i=0;i<4;i++) { targetAngle[i]=cfg.lastAngle[i]; currentAngle[i]=cfg.lastAngle[i]; }
  applyAllImmediate();

  Serial.println(F("READY Motor_Test_UART v2-fixed"));
  printHelp();
  printStatus();
}

void loop() {
  // ---- Serial input ----
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;         // ignore carriage return
    if (c == '\n') {                 // newline ends the command
      handleTextLine(lineBuf);
      lineBuf = "";
    } else {
      if (lineBuf.length() < 192) lineBuf += c; // guard against overrun
    }
  }

  // ---- Tweening (non-blocking) ----
  if (cfg.stepDeg==0 || cfg.stepMs==0) {
    // Immediate apply
    for (uint8_t i=0;i<4;i++) {
      if (currentAngle[i]!=targetAngle[i]) {
        currentAngle[i]=targetAngle[i];
        sendAngle(i, currentAngle[i]);
      }
    }
  } else {
    uint32_t now = millis();
    if ((uint32_t)(now - lastStepMs) >= cfg.stepMs) {
      lastStepMs = now;
      for (uint8_t i=0;i<4;i++) {
        int diff = targetAngle[i] - currentAngle[i];
        if (diff!=0) {
          int step = (diff>0)
            ? imin_int((int)cfg.stepDeg, diff)
            : imax_int(-((int)cfg.stepDeg), diff);
          currentAngle[i] += step;
          sendAngle(i, currentAngle[i]);
        }
      }
    }
  }
}
