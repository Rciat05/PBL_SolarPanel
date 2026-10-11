// Prueba completa ESP32: INA219 (V, I) + MAX6675 (temperatura) + celda de irradiancia (GPIO34)
// Sin librerías externas. Monitor Serial a 115200.
// Comandos: 1 = pulso M1 | 2 = pulso M2 | 3 = pulso M3 | d = alternar modo dashboard (CSV)
#include <Wire.h>

const int PIN_M1 = 13, PIN_M2 = 27, PIN_M3 = 14;      // HIGH = MOSFET apagado, LOW = encendido
const int MAX_SCK = 18, MAX_CS = 5, MAX_SO = 19;
const int PIN_IRR = 34;
const uint8_t INA_ADDR = 0x40;
const float R_SHUNT_INA = 0.1;                         // ohm (módulo INA219 típico: R100)

bool modoDashboard = false;                            // false = texto legible, true = CSV para el dashboard
float tempC = 25.0;                                    // última temperatura válida

// ---------- INA219 por registros ----------
uint16_t inaLeer(uint8_t reg) {
  Wire.beginTransmission(INA_ADDR);
  Wire.write(reg);
  Wire.endTransmission();
  Wire.requestFrom(INA_ADDR, (uint8_t)2);
  uint16_t v = Wire.read() << 8;
  v |= Wire.read();
  return v;
}

void leerINA(float &volt, float &mA) {
  float sh = 0, bus = 0;
  for (int i = 0; i < 8; i++) {                        // promedio de 8 lecturas
    sh  += (int16_t)inaLeer(0x01) * 0.01;              // mV (10 uV por bit)
    bus += (inaLeer(0x02) >> 3) * 0.004;               // V  (4 mV por bit), medido en VIN- (V_LOAD)
    delay(2);
  }
  sh /= 8; bus /= 8;
  mA = sh / R_SHUNT_INA;                               // mV / ohm = mA
  volt = bus + sh / 1000.0;                            // voltaje del panel = V_LOAD + caída en el shunt
}

// ---------- MAX6675 ----------
float leerMAX6675() {
  uint16_t v = 0;
  digitalWrite(MAX_CS, LOW);
  delayMicroseconds(10);
  for (int i = 15; i >= 0; i--) {
    digitalWrite(MAX_SCK, HIGH); delayMicroseconds(5);
    if (digitalRead(MAX_SO)) v |= (1 << i);
    digitalWrite(MAX_SCK, LOW);  delayMicroseconds(5);
  }
  digitalWrite(MAX_CS, HIGH);
  if (v & 0x04) return NAN;                            // termocupla no detectada
  return (v >> 3) * 0.25;
}

// ---------- Irradiancia: mV sobre el shunt de 1 ohm = mA de la celda ----------
float leerIrr_mV() {
  long s = 0;
  for (int i = 0; i < 64; i++) s += analogReadMilliVolts(PIN_IRR);
  return s / 64.0;
}

// ---------- Salida ----------
void imprimir() {
  float v, mA;
  leerINA(v, mA);
  float irr = leerIrr_mV();
  if (modoDashboard) {
    Serial.printf("%.1f,%.2f,%.3f,%.1f\n", irr, tempC, v, mA);   // irr_mV,temp_C,volt_V,corr_mA
  } else {
    Serial.printf("V=%.3f V | I=%.1f mA | P=%.3f W | T=%.2f C | Irr=%.1f mV\n", v, mA, v * mA / 1000.0, tempC, irr);
  }
}

// Enciende un MOSFET, mide a mitad del pulso y lo apaga
void pulso(int pin, const char* nombre, int ms) {
  if (!modoDashboard) Serial.printf(">> %s ON %d ms\n", nombre, ms);
  digitalWrite(pin, LOW);
  delay(ms / 2);
  float v, mA;
  leerINA(v, mA);
  Serial.printf("   [%s] V=%.3f V | I=%.1f mA\n", nombre, v, mA);
  delay(ms / 2);
  digitalWrite(pin, HIGH);
  if (!modoDashboard) Serial.printf(">> %s OFF\n", nombre);
}

void setup() {
  pinMode(PIN_M1, OUTPUT); digitalWrite(PIN_M1, HIGH);
  pinMode(PIN_M2, OUTPUT); digitalWrite(PIN_M2, HIGH);
  pinMode(PIN_M3, OUTPUT); digitalWrite(PIN_M3, HIGH);
  pinMode(MAX_CS, OUTPUT);  digitalWrite(MAX_CS, HIGH);
  pinMode(MAX_SCK, OUTPUT); digitalWrite(MAX_SCK, LOW);
  pinMode(MAX_SO, INPUT);
  analogSetPinAttenuation(PIN_IRR, ADC_0db);           // rango ~0 a 950 mV, sirve para 0 a 300 mV
  Serial.begin(115200);
  Wire.begin(21, 22);
  delay(500);
  Serial.println("\n=== PRUEBA COMPLETA === (1,2,3 = pulsos | d = modo dashboard)");
}

void loop() {
  if (Serial.available()) {
    char c = Serial.read();
    if (c == '1') pulso(PIN_M1, "M1 141ohm", 500);
    else if (c == '2') pulso(PIN_M2, "M2 47ohm", 500);
    else if (c == '3') pulso(PIN_M3, "M3 corto", 150);
    else if (c == 'd') { modoDashboard = !modoDashboard; Serial.println(modoDashboard ? "Modo dashboard" : "Modo legible"); }
  }

  static unsigned long t = 0;
  if (millis() - t >= 1000) {
    t = millis();
    float tc = leerMAX6675();
    if (!isnan(tc)) tempC = tc;
    else if (!modoDashboard) Serial.println("(termocupla no detectada)");
    imprimir();
  }
}
