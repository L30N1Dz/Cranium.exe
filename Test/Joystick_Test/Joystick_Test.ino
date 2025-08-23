/*
  Joystick_Test.ino — JSON-configurable dual-joystick firmware
  ------------------------------------------------------------
  • Accepts config JSON on UART prefixed with "CFG:" and replies to "CFG?" with current config.
  • UART commands: HELLO, START, STOP, CFG?, CFG:{...}, INV <axis>[=0|1], SAVE
  • Persists config (including per‑axis invert) to EEPROM with CRC.
  • Report formats (10-bit ADC expected):
      j1x,j1y,j1b,j2x,j2y,j2b[,extra]
    Optionally emits:
      pot:<v>  and  pot2:<v>

  Dependencies (install via Arduino Library Manager):
    - ArduinoJson (>= 6)

  Notes:
    - Pin fields in JSON may be numeric (e.g. "34") or analog form ("A0").
    - Buttons are read with INPUT_PULLUP and reported 1 when PRESSED.
*/

#include <Arduino.h>
#include <EEPROM.h>
#include <ArduinoJson.h>

// ===== Build / storage config =====
#define FW_VERSION  3
#define EEPROM_ADDR 0  // start of config blob

// ===== Reporting =====
static const uint16_t STREAM_INTERVAL_MS = 20;  // ~50 Hz

// ===== Config struct persisted to EEPROM (packed) =====
struct Config {
  uint8_t version;          // for migrations
  uint8_t mode;             // 0 = stream, 1 = onchange

  int16_t j1x_pin;          // pins can exceed 8-bit on some MCUs
  int16_t j1y_pin;
  int16_t j1_sw_pin;
  int16_t j2x_pin;
  int16_t j2y_pin;
  int16_t j2_sw_pin;

  uint8_t extra_enabled;    // bool
  int16_t extra_sw_pin;

  uint8_t pot1_enabled;     // bool
  int16_t pot1_pin;
  int16_t pot1_min;
  int16_t pot1_max;

  uint8_t pot2_enabled;     // bool
  int16_t pot2_pin;
  int16_t pot2_min;
  int16_t pot2_max;

  int16_t thresh_axis;      // onchange Δ threshold for axes
  int16_t thresh_pot;       // onchange Δ threshold for pots

  uint8_t inv_j1x;          // bools
  uint8_t inv_j1y;
  uint8_t inv_j2x;
  uint8_t inv_j2y;

  uint16_t crc;             // must be last
};

Config cfg;              // current config (RAM)
bool reporting = false;  // START/STOP

// ====== Forward decls ======
static void applyPinModes();
static void sendCfgJson();
static bool parseCfgJson(const String &json, bool *wantSaveOut);
static void handleLine(const String &line);
static void emitPacket(uint16_t j1x, uint16_t j1y, bool j1b,
                       uint16_t j2x, uint16_t j2y, bool j2b,
                       bool haveExtra, bool extraPressed);
static uint16_t crc16(const uint8_t *data, size_t len);
static void loadConfig();
static void saveConfig();
static int parsePinToken(const String &tok);
static uint16_t readAxis(int pin, bool invert);
static bool readBtn(int pin);
static void emitPotsIfEnabled(uint16_t p1, uint16_t p2);

// ====== Utilities ======
uint16_t crc16(const uint8_t *data, size_t len) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < len; ++i) {
    crc ^= (uint16_t)data[i] << 8;
    for (uint8_t b = 0; b < 8; ++b) {
      if (crc & 0x8000) crc = (crc << 1) ^ 0x1021; else crc <<= 1;
    }
  }
  return crc;
}

void eepromReadBytes(int addr, void *dst, size_t len) {
  uint8_t *p = (uint8_t*)dst;
  for (size_t i = 0; i < len; ++i) p[i] = EEPROM.read(addr + i);
}

void eepromWriteBytes(int addr, const void *src, size_t len) {
  const uint8_t *p = (const uint8_t*)src;
  for (size_t i = 0; i < len; ++i) EEPROM.update(addr + i, p[i]);
}

void loadConfig() {
  eepromReadBytes(EEPROM_ADDR, &cfg, sizeof(Config));
  // validate
  uint16_t expect = cfg.crc;
  cfg.crc = 0;
  uint16_t got = crc16((uint8_t*)&cfg, sizeof(Config));
  bool ok = (expect == got) && (cfg.version == FW_VERSION);

  if (!ok) {
    // defaults
    memset(&cfg, 0, sizeof(cfg));
    cfg.version = FW_VERSION;
    cfg.mode = 0; // stream
    cfg.j1x_pin = parsePinToken("A0");
    cfg.j1y_pin = parsePinToken("A1");
    cfg.j1_sw_pin = 2;
    cfg.j2x_pin = parsePinToken("A2");
    cfg.j2y_pin = parsePinToken("A3");
    cfg.j2_sw_pin = 3;
    cfg.extra_enabled = 0;
    cfg.extra_sw_pin = 4;
    cfg.pot1_enabled = 0; cfg.pot1_pin = parsePinToken("A4"); cfg.pot1_min = 0; cfg.pot1_max = 1023;
    cfg.pot2_enabled = 0; cfg.pot2_pin = parsePinToken("A5"); cfg.pot2_min = 0; cfg.pot2_max = 1023;
    cfg.thresh_axis = 8; cfg.thresh_pot = 8;
    cfg.inv_j1x = 0; cfg.inv_j1y = 0; cfg.inv_j2x = 0; cfg.inv_j2y = 0;
    saveConfig();
  }
}

void saveConfig() {
  cfg.crc = 0;
  uint16_t c = crc16((uint8_t*)&cfg, sizeof(Config));
  cfg.crc = c;
  eepromWriteBytes(EEPROM_ADDR, &cfg, sizeof(Config));
}

int parsePinToken(const String &tok) {
  String s = tok; s.trim();
  if (s.length() == 0) return -1;
  if (s[0] == 'A' || s[0] == 'a') {
    // Handle forms like A0..A15
    int idx = s.substring(1).toInt();
    #ifdef A0
      return A0 + idx; // works on AVR-style boards
    #else
      // On boards where analog pins are numeric, allow direct numeric fallback
      return idx;
    #endif
  }
  return s.toInt();
}

void applyPinModes() {
  pinMode(cfg.j1_sw_pin, INPUT_PULLUP);
  pinMode(cfg.j2_sw_pin, INPUT_PULLUP);
  if (cfg.extra_enabled) pinMode(cfg.extra_sw_pin, INPUT_PULLUP);
  // analog pins need no pinMode for analogRead
}

uint16_t readAxis(int pin, bool invert) {
  int v = analogRead(pin);
  if (v < 0) v = 0; if (v > 1023) v = 1023;
  if (invert) v = 1023 - v;
  return (uint16_t)v;
}

bool readBtn(int pin) {
  // Active LOW with pullup; pressed = 1
  int r = digitalRead(pin);
  return (r == LOW);
}

void emitPacket(uint16_t j1x, uint16_t j1y, bool j1b,
                uint16_t j2x, uint16_t j2y, bool j2b,
                bool haveExtra, bool extraPressed) {
  // Base packet
  Serial.print(j1x); Serial.print(',');
  Serial.print(j1y); Serial.print(',');
  Serial.print(j1b ? 1 : 0); Serial.print(',');
  Serial.print(j2x); Serial.print(',');
  Serial.print(j2y); Serial.print(',');
  Serial.print(j2b ? 1 : 0);
  if (haveExtra) {
    Serial.print(',');
    Serial.print(extraPressed ? 1 : 0);
  }
  Serial.println();
}

void emitPotsIfEnabled(uint16_t p1, uint16_t p2) {
  if (cfg.pot1_enabled) {
    Serial.print("pot:"); Serial.println(p1);
  }
  if (cfg.pot2_enabled) {
    Serial.print("pot2:"); Serial.println(p2);
  }
}

void sendCfgJson() {
  StaticJsonDocument<512> doc;
  doc["mode"] = (cfg.mode == 0) ? "stream" : "onchange";
  JsonObject j1 = doc.createNestedObject("j1");
  j1["x"] = cfg.j1x_pin; j1["y"] = cfg.j1y_pin; j1["sw"] = cfg.j1_sw_pin;
  JsonObject j2 = doc.createNestedObject("j2");
  j2["x"] = cfg.j2x_pin; j2["y"] = cfg.j2y_pin; j2["sw"] = cfg.j2_sw_pin;
  JsonObject ex = doc.createNestedObject("extra");
  ex["enabled"] = (bool)cfg.extra_enabled; ex["sw"] = cfg.extra_sw_pin;
  JsonObject p1 = doc.createNestedObject("pot1");
  p1["enabled"] = (bool)cfg.pot1_enabled; p1["pin"] = cfg.pot1_pin; p1["min"] = cfg.pot1_min; p1["max"] = cfg.pot1_max;
  JsonObject p2 = doc.createNestedObject("pot2");
  p2["enabled"] = (bool)cfg.pot2_enabled; p2["pin"] = cfg.pot2_pin; p2["min"] = cfg.pot2_min; p2["max"] = cfg.pot2_max;
  JsonObject th = doc.createNestedObject("thresholds");
  th["axis"] = cfg.thresh_axis; th["pot"] = cfg.thresh_pot;
  JsonObject inv = doc.createNestedObject("invert");
  inv["j1x"] = (bool)cfg.inv_j1x; inv["j1y"] = (bool)cfg.inv_j1y; inv["j2x"] = (bool)cfg.inv_j2x; inv["j2y"] = (bool)cfg.inv_j2y;

  Serial.print("CFG:");
  serializeJson(doc, Serial);
  Serial.println();
}

bool parseCfgJson(const String &s, bool *wantSaveOut) {
  // s contains raw JSON text
  StaticJsonDocument<768> doc; // adjust if you add more fields
  DeserializationError err = deserializeJson(doc, s);
  if (err) return false;

  if (doc.containsKey("mode")) {
    const char *m = doc["mode"];
    cfg.mode = (m && String(m) == "onchange") ? 1 : 0;
  }

  if (doc.containsKey("j1")) {
    JsonObject j1 = doc["j1"];
    if (j1.containsKey("x")) cfg.j1x_pin = parsePinToken(String((const char*)j1["x"])) ;
    if (j1.containsKey("y")) cfg.j1y_pin = parsePinToken(String((const char*)j1["y"])) ;
    if (j1.containsKey("sw")) cfg.j1_sw_pin = (int)j1["sw"];
  }
  if (doc.containsKey("j2")) {
    JsonObject j2 = doc["j2"];
    if (j2.containsKey("x")) cfg.j2x_pin = parsePinToken(String((const char*)j2["x"])) ;
    if (j2.containsKey("y")) cfg.j2y_pin = parsePinToken(String((const char*)j2["y"])) ;
    if (j2.containsKey("sw")) cfg.j2_sw_pin = (int)j2["sw"];
  }
  if (doc.containsKey("extra")) {
    JsonObject ex = doc["extra"];
    if (ex.containsKey("enabled")) cfg.extra_enabled = (bool)ex["enabled"]; 
    if (ex.containsKey("sw")) cfg.extra_sw_pin = (int)ex["sw"];
  }
  if (doc.containsKey("pot1")) {
    JsonObject p1 = doc["pot1"];
    if (p1.containsKey("enabled")) cfg.pot1_enabled = (bool)p1["enabled"]; 
    if (p1.containsKey("pin")) cfg.pot1_pin = parsePinToken(String((const char*)p1["pin"])) ;
    if (p1.containsKey("min")) cfg.pot1_min = (int)p1["min"]; 
    if (p1.containsKey("max")) cfg.pot1_max = (int)p1["max"]; 
  }
  if (doc.containsKey("pot2")) {
    JsonObject p2 = doc["pot2"];
    if (p2.containsKey("enabled")) cfg.pot2_enabled = (bool)p2["enabled"]; 
    if (p2.containsKey("pin")) cfg.pot2_pin = parsePinToken(String((const char*)p2["pin"])) ;
    if (p2.containsKey("min")) cfg.pot2_min = (int)p2["min"]; 
    if (p2.containsKey("max")) cfg.pot2_max = (int)p2["max"]; 
  }
  if (doc.containsKey("thresholds")) {
    JsonObject th = doc["thresholds"];
    if (th.containsKey("axis")) cfg.thresh_axis = (int)th["axis"]; 
    if (th.containsKey("pot")) cfg.thresh_pot = (int)th["pot"]; 
  }
  if (doc.containsKey("invert")) {
    JsonObject inv = doc["invert"];
    if (inv.containsKey("j1x")) cfg.inv_j1x = (bool)inv["j1x"]; 
    if (inv.containsKey("j1y")) cfg.inv_j1y = (bool)inv["j1y"]; 
    if (inv.containsKey("j2x")) cfg.inv_j2x = (bool)inv["j2x"]; 
    if (inv.containsKey("j2y")) cfg.inv_j2y = (bool)inv["j2y"]; 
  }

  bool wantSave = false;
  if (doc.containsKey("save")) wantSave = (bool)doc["save"]; 
  if (wantSaveOut) *wantSaveOut = wantSave;

  applyPinModes();
  return true;
}

// ===== Serial line buffering =====
String rxBuf;

void handleLine(const String &raw) {
  String line = raw; line.trim();
  if (line.length() == 0) return;

  if (line.equalsIgnoreCase("HELLO")) {
    Serial.println("HELLO");
    sendCfgJson();
    return;
  }
  if (line.equalsIgnoreCase("START")) { reporting = true; return; }
  if (line.equalsIgnoreCase("STOP"))  { reporting = false; return; }

  if (line.equalsIgnoreCase("CFG?")) { sendCfgJson(); return; }

  if (line.startsWith("CFG:")) {
    String json = line.substring(4);
    bool doSave = false;
    if (parseCfgJson(json, &doSave)) {
      if (doSave) saveConfig();
      sendCfgJson(); // echo back what we applied
    }
    return;
  }

  if (line.length() >= 3 && line.substring(0,3).equalsIgnoreCase("INV")) {
    // Formats supported:
    //   INV J1X            -> toggle
    //   INV J1X=1 / =0     -> set explicit
    //   INV?               -> report current invert flags
    if (line.length() >= 4 && line[3] == '?') {
      StaticJsonDocument<128> doc;
      doc["invert"]["j1x"] = (bool)cfg.inv_j1x;
      doc["invert"]["j1y"] = (bool)cfg.inv_j1y;
      doc["invert"]["j2x"] = (bool)cfg.inv_j2x;
      doc["invert"]["j2y"] = (bool)cfg.inv_j2y;
      Serial.print("CFG:"); serializeJson(doc, Serial); Serial.println();
      return;
    }
    // parse token after space
    int sp = line.indexOf(' ');
    if (sp > 0) {
      String tok = line.substring(sp + 1); tok.trim();
      int eq = tok.indexOf('=');
      int setVal = -1; // -1=toggle, 0/1 explicit
      if (eq >= 0) { setVal = tok.substring(eq + 1).toInt(); tok = tok.substring(0, eq); tok.trim(); }
      bool *flag = nullptr;
      if (tok.equalsIgnoreCase("J1X")) flag = (bool*)&cfg.inv_j1x;
      else if (tok.equalsIgnoreCase("J1Y")) flag = (bool*)&cfg.inv_j1y;
      else if (tok.equalsIgnoreCase("J2X")) flag = (bool*)&cfg.inv_j2x;
      else if (tok.equalsIgnoreCase("J2Y")) flag = (bool*)&cfg.inv_j2y;
      if (flag) {
        if (setVal == 0) *flag = false;
        else if (setVal == 1) *flag = true;
        else *flag = !*flag;
        saveConfig();
        // Acknowledge with compact line and JSON snippet
        Serial.print("INV "); Serial.print(tok); Serial.print('='); Serial.println(*flag ? 1 : 0);
        StaticJsonDocument<96> doc; doc["invert"][tok] = *flag; Serial.print("CFG:"); serializeJson(doc, Serial); Serial.println();
      }
    }
    return;
  }

  if (line.equalsIgnoreCase("SAVE")) { saveConfig(); Serial.println("SAVED"); return; }
}

// ===== State for onchange mode =====
uint16_t last_j1x = 0, last_j1y = 0, last_j2x = 0, last_j2y = 0;
bool     last_b1 = false, last_b2 = false, last_extra = false;
uint16_t last_p1 = 0, last_p2 = 0;

void setup() {
  Serial.begin(19200);
  #if defined(LED_BUILTIN)
    pinMode(LED_BUILTIN, OUTPUT);
  #endif
  loadConfig();
  applyPinModes();
  // small hello so the desktop app can auto-detect
  Serial.println(F("READY"));
}

void loop() {
  // ---- Serial RX ----
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (rxBuf.length()) { handleLine(rxBuf); rxBuf = ""; }
    } else {
      if (rxBuf.length() < 512) rxBuf += c; // prevent runaway
    }
  }

  static uint32_t last = 0;
  uint32_t now = millis();
  if (!reporting) { last = now; return; }
  if (cfg.mode == 0) { // stream
    if (now - last >= STREAM_INTERVAL_MS) {
      last = now;
      uint16_t j1x = readAxis(cfg.j1x_pin, cfg.inv_j1x);
      uint16_t j1y = readAxis(cfg.j1y_pin, cfg.inv_j1y);
      bool b1 = readBtn(cfg.j1_sw_pin);
      uint16_t j2x = readAxis(cfg.j2x_pin, cfg.inv_j2x);
      uint16_t j2y = readAxis(cfg.j2y_pin, cfg.inv_j2y);
      bool b2 = readBtn(cfg.j2_sw_pin);
      bool xbtn = false;
      if (cfg.extra_enabled) xbtn = readBtn(cfg.extra_sw_pin);
      emitPacket(j1x, j1y, b1, j2x, j2y, b2, cfg.extra_enabled, xbtn);
      uint16_t p1 = 0, p2 = 0;
      if (cfg.pot1_enabled) p1 = analogRead(cfg.pot1_pin);
      if (cfg.pot2_enabled) p2 = analogRead(cfg.pot2_pin);
      emitPotsIfEnabled(p1, p2);
    }
  } else { // onchange
    if (now - last >= STREAM_INTERVAL_MS) {
      last = now;
      uint16_t j1x = readAxis(cfg.j1x_pin, cfg.inv_j1x);
      uint16_t j1y = readAxis(cfg.j1y_pin, cfg.inv_j1y);
      bool b1 = readBtn(cfg.j1_sw_pin);
      uint16_t j2x = readAxis(cfg.j2x_pin, cfg.inv_j2x);
      uint16_t j2y = readAxis(cfg.j2y_pin, cfg.inv_j2y);
      bool b2 = readBtn(cfg.j2_sw_pin);
      bool xbtn = false; if (cfg.extra_enabled) xbtn = readBtn(cfg.extra_sw_pin);

      bool changed = false;
      auto d = [](uint16_t a, uint16_t b){ return (a > b) ? (a - b) : (b - a); };
      if (d(j1x, last_j1x) >= (uint16_t)cfg.thresh_axis) { last_j1x = j1x; changed = true; }
      if (d(j1y, last_j1y) >= (uint16_t)cfg.thresh_axis) { last_j1y = j1y; changed = true; }
      if (d(j2x, last_j2x) >= (uint16_t)cfg.thresh_axis) { last_j2x = j2x; changed = true; }
      if (d(j2y, last_j2y) >= (uint16_t)cfg.thresh_axis) { last_j2y = j2y; changed = true; }
      if (b1 != last_b1) { last_b1 = b1; changed = true; }
      if (b2 != last_b2) { last_b2 = b2; changed = true; }
      if (cfg.extra_enabled && xbtn != last_extra) { last_extra = xbtn; changed = true; }

      if (cfg.pot1_enabled) {
        uint16_t p1 = analogRead(cfg.pot1_pin);
        if (d(p1, last_p1) >= (uint16_t)cfg.thresh_pot) { last_p1 = p1; Serial.print("pot:"); Serial.println(p1); }
      }
      if (cfg.pot2_enabled) {
        uint16_t p2 = analogRead(cfg.pot2_pin);
        if (d(p2, last_p2) >= (uint16_t)cfg.thresh_pot) { last_p2 = p2; Serial.print("pot2:"); Serial.println(p2); }
      }

      if (changed) emitPacket(j1x, j1y, b1, j2x, j2y, b2, cfg.extra_enabled, xbtn);
    }
  }

  // Blink LED lightly when reporting
  #if defined(LED_BUILTIN)
    static bool led = false; static uint32_t tLED = 0;
    if (now - tLED > 250) { tLED = now; led = !led; digitalWrite(LED_BUILTIN, led ? HIGH : LOW); }
  #endif
}
