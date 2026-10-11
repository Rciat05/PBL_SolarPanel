"""
Hyperion - Dashboard minimalista de monitoreo de panel solar
Ejecutar:  python -m streamlit run hyperion_dashboard.py
Requisitos: pip install streamlit pandas numpy pyserial

Formato serial del ESP32 (115200 baud, una línea por segundo):
    irr_mV,temp_C,volt_V,corr_mA      ej:  85.0,41.25,12.31,236.0
"""
import math, os, random
from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None

# ═════════ PARÁMETROS (editar aquí) ═════════
P_NOM, V_MP = 3.0, 12.0            # W y V en el punto de máxima potencia (hoja de datos)
I_MP = P_NOM / V_MP                # A
GAMMA, ALPHA, BETA = -0.0040, 0.0005, -0.0035   # coef. de temperatura: potencia, corriente, voltaje (1/°C)
K_G = 3.3                          # W/m² por mV de la celda de irradiancia (calibrar a pleno sol)
UMBRAL_AVISO, UMBRAL_CRIT = 0.90, 0.80          # eficiencia normalizada (PR)
G_MIN, VENTANA, VISTA = 200, 30, 300            # W/m² mínimos para diagnosticar, muestras del diagnóstico, muestras en pantalla
REFRESH, BAUD = 1, 115200
AZUL, CIAN, AMBAR, ROJO, BLANCO, GRIS = "#3D8BFF", "#22D3EE", "#FFC933", "#FF6B5E", "#E8F0FF", "#9FB0C3"
LOG = "hyperion_log.csv"

st.set_page_config(page_title="Hyperion", layout="wide")
st.markdown("""<style>
.block-container{padding:1.2rem 2rem 2rem 2rem;max-width:100%}
div[data-testid="stMetric"]{background:#121923;border:1px solid #1F2C3F;border-top:3px solid #0B6BFF;padding:.6rem .9rem;border-radius:6px}
.alerta{padding:.7rem 1rem;border-radius:6px;margin:.2rem 0 1rem 0}
.ok{background:#0F2A1D;border-left:6px solid #2ECC71}
.warn{background:#2E2410;border-left:6px solid #FFB020}
.crit{background:#34161A;border-left:6px solid #FF4D4F}
.gris{background:#182230;border-left:6px solid #6B7C93}
.badge{background:#0B6BFF;color:#fff;padding:.15rem .7rem;border-radius:999px;font-size:.8rem;margin-left:.8rem;vertical-align:middle}
.badge.demo{background:#FFB020;color:#0B0F14}
</style>""", unsafe_allow_html=True)

# ═════════ Barra lateral: solo lo esencial ═════════
modo = st.sidebar.radio("Fuente de datos", ["Demo", "ESP32"])
escenario, puerto = "Normal", None
if modo == "Demo":
    escenario = st.sidebar.selectbox("Escenario", ["Normal", "Suciedad (soiling)", "PID", "Sombra parcial"])
else:
    puertos = [p.device for p in list_ports.comports()] if serial else []
    puerto = st.sidebar.selectbox("Puerto COM", puertos or ["(ninguno)"])

ss = st.session_state
ss.setdefault("datos", []); ss.setdefault("n", 0); ss.setdefault("ser", None); ss.setdefault("prev", None)
if ss.prev != (modo, escenario, puerto):
    ss.datos, ss.n, ss.prev = [], 0, (modo, escenario, puerto)
    if ss.ser:
        ss.ser.close(); ss.ser = None


# ═════════ Adquisición ═════════
def muestra_demo(n, esc):
    G = max(0, 850 * math.sin(math.pi * (n % 600) / 600) + random.gauss(0, 10))
    if esc == "Sombra parcial" and (n // 80) % 3 == 1:
        G *= 0.35
    T = 25 + 0.03 * G + random.gauss(0, 0.3)
    f_i = 1 - (0.25 * min(n / 600, 1) if esc == "Suciedad (soiling)" else 0)
    f_v = 1 - (0.15 * min(n / 600, 1) if esc == "PID" else 0)
    I = I_MP * (G / 1000) * (1 + ALPHA * (T - 25)) * f_i + random.gauss(0, .002)
    V = V_MP * (1 + BETA * (T - 25)) * (1 + 0.04 * math.log(max(G, 1) / 1000)) * f_v + random.gauss(0, .03)
    return G / K_G, T, max(V, 0), max(I, 0) * 1000


def muestra_serial():
    """Lee todo lo que haya en el buffer y se queda con la última línea válida (evita retraso)."""
    if serial is None or not puerto or puerto.startswith("("):
        return None
    try:
        if ss.ser is None:
            ss.ser = serial.Serial(puerto, BAUD, timeout=1)
        ultima = None
        while True:
            linea = ss.ser.readline().decode(errors="ignore").strip()
            try:
                v = tuple(map(float, linea.split(",")))
                if len(v) == 4:
                    ultima = v
            except ValueError:
                pass
            if ss.ser.in_waiting == 0:
                break
        return ultima
    except Exception:
        ss.ser = None
        return None


def agregar(irr_mV, t, v, ma):
    G, I = irr_mV * K_G, ma / 1000
    P = v * I
    P_esp = P_NOM * (G / 1000) * (1 + GAMMA * (t - 25))
    I_esp = I_MP * (G / 1000) * (1 + ALPHA * (t - 25))
    V_esp = V_MP * (1 + BETA * (t - 25))
    fila = dict(ts=datetime.now(), G=G, Tc=t, V=v, I_mA=ma, P=P, P_esp=P_esp, P_nom=P_NOM,
                PR=P / P_esp if P_esp > 0.05 else np.nan,
                Ir=I / I_esp if I_esp > 0.02 else np.nan,
                Vr=v / V_esp if G > 0 else np.nan, Aviso=UMBRAL_AVISO, Critico=UMBRAL_CRIT)
    ss.datos = (ss.datos + [fila])[-1000:]
    pd.DataFrame([fila]).to_csv(LOG, mode="a", header=not os.path.exists(LOG), index=False)


# ═════════ Diagnóstico de mantenimiento ═════════
def diagnostico(df):
    """PR = P real / P esperada(G,T). Suciedad/sombra: cae la corriente. PID: cae el voltaje.
    Reglas heurísticas válidas cerca del MPP; se reemplazan por el modelo de degradación."""
    w = df[df.G > G_MIN].tail(VENTANA)
    if len(w) < max(5, VENTANA // 3):
        return "gris", "Datos insuficientes", "Poca irradiancia o pocas muestras para diagnosticar."
    pr, ir, vr = w.PR.median(), w.Ir.median(), w.Vr.median()
    if pr >= UMBRAL_AVISO:
        return "ok", "Panel en buen estado", f"PR = {pr:.2f} (mínimo aceptable {UMBRAL_AVISO:.2f})."
    if vr < 0.93 and ir >= 0.95:
        causa = "Posible PID: baja el voltaje con corriente normal."
    elif ir < 0.90 and vr >= 0.95:
        causa = "Posible suciedad o sombra: baja la corriente con voltaje normal. Limpiar el panel."
    else:
        causa = "Pérdida sin patrón claro; revisar conexiones, sombras y sensores."
    nivel = "crit" if pr < UMBRAL_CRIT else "warn"
    return nivel, f"PR = {pr:.2f}", causa + (" Programar mantenimiento ya." if nivel == "crit" else " Vigilar.")


# ═════════ Pantalla ═════════
def grafica(titulo, df, cols, tipo="line", colores=None):
    st.markdown(f"**{titulo}**")
    f = {"line": st.line_chart, "area": st.area_chart, "bar": st.bar_chart}[tipo]
    f(df[cols], color=colores, height=230)


def render():
    m = muestra_demo(ss.n, escenario) if modo == "Demo" else muestra_serial()
    if m:
        agregar(*m)
        ss.n += 1
    if not ss.datos:
        st.info("Esperando datos del ESP32. Cierra el Monitor Serial del IDE y elige el puerto COM.")
        return
    df = pd.DataFrame(ss.datos)
    u = df.iloc[-1]
    h = df.tail(VISTA).set_index("ts")

    nivel, titulo, detalle = diagnostico(df)
    st.markdown(f'<div class="alerta {nivel}"><b>Mantenimiento: {titulo}.</b> {detalle}</div>', unsafe_allow_html=True)

    c = st.columns(6)
    c[0].metric("Voltaje", f"{u.V:.2f} V")
    c[1].metric("Corriente", f"{u.I_mA:.0f} mA")
    c[2].metric("Potencia", f"{u.P:.2f} W")
    c[3].metric("Temp. celda", f"{u.Tc:.1f} °C")
    c[4].metric("Irradiancia", f"{u.G:.0f} W/m²")
    c[5].metric("Eficiencia (PR)", "—" if np.isnan(u.PR) else f"{u.PR * 100:.0f} %")

    a, b = st.columns(2)
    with a: grafica("Irradiancia (W/m²)", h, ["G"], "area", AMBAR)
    with b: grafica("Temperatura de celda (°C)", h, ["Tc"], "line", ROJO)
    a, b = st.columns(2)
    with a: grafica("Voltaje (V)", h, ["V"], "line", AZUL)
    with b: grafica("Corriente (mA)", h, ["I_mA"], "bar", CIAN)
    a, b = st.columns(2)
    with a: grafica("Potencia real vs esperada vs nominal (W)", h, ["P", "P_esp", "P_nom"], "line", [AZUL, GRIS, BLANCO])
    with b: grafica("Eficiencia normalizada (PR) y umbrales", h, ["PR", "Aviso", "Critico"], "line", [BLANCO, AMBAR, ROJO])


badge = '<span class="badge demo">DEMO · datos simulados</span>' if modo == "Demo" else '<span class="badge">EN VIVO · ESP32</span>'
st.markdown(f"<h2>Hyperion · Monitoreo de panel solar{badge}</h2>", unsafe_allow_html=True)
st.fragment(run_every=REFRESH)(render)()