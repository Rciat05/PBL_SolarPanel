#include <max6675.h>

// 1. Definición de pines para el MAX6675 (SPI)
int thermoDO = 12;  // MAX_SO
int thermoCS = 10;  // MAX_CS
int thermoCLK = 13; // MAX_SCK
MAX6675 termopar(thermoCLK, thermoCS, thermoDO);

// 2. Definición de pines analógicos
int pinVoltaje = A0; // Lectura del divisor resistivo (10k/10k)
int pinLuz = A1;     // Lectura del fototransistor TEPT8000

void setup() {
  Serial.begin(9600);
  Serial.println("Iniciando Sistema de Adquisición Hyperion...");
  
  // Esperar a que el chip MAX6675 se estabilice
  delay(500);
}

void loop() {
  // Leer las variables del panel
  float temperatura = termopar.readCelsius();
  int v_adc = analogRead(pinVoltaje);
  int luz_adc = analogRead(pinLuz);

  // Imprimir los datos crudos en el Monitor Serie
  Serial.print("Temp (C): "); 
  Serial.print(temperatura); 
  Serial.print(" | Irradiancia (ADC): "); 
  Serial.print(luz_adc); 
  Serial.print(" | Voltaje (ADC): "); 
  Serial.println(v_adc);

  // Muestreo cada 2 segundos (ajustable)
  delay(2000); 
}