from fpdf import FPDF
from datetime import datetime

import streamlit as st
import paho.mqtt.client as mqtt
import json
import time
import pandas as pd

APP_ID = "lilygo-pma"
USERNAME = APP_ID + "@ttn"
PASSWORD = st.secrets["TTN_API_KEY"]

HOST = "eu1.cloud.thethings.network"
PORT = 8883
TOPIC = f"v3/{USERNAME}/devices/+/up"

# Ubicación manual de la LilyGO
#LILYGO_LAT = 39.4825
#LILYGO_LON = -0.3463

def decode_payload(payload_b64):
    try:
        if not payload_b64 or payload_b64 == "N/A":
            return None, None, 0
            
        # Convertir de Base64 a bytes
        payload_bytes = base64.b64decode(payload_b64)
        
        # Verificar que tenga los 10 bytes esperados (4 lat + 4 lon + 2 sat)
        if len(payload_bytes) < 10:
            return None, None, 0
            
        # Desempaquetar bytes (¡El formato binario debe coincidir con el bit-shifting de Arduino!)
        # >i: entero de 4 bytes con signo (Big Endian) para Latitud
        # >i: entero de 4 bytes con signo (Big Endian) para Longitud
        # >H: entero de 2 bytes sin signo (Big Endian) para Satélites
        lat_raw, lon_raw, sat = struct.unpack(">iiH", payload_bytes[:10])
        
        # Recuperar los decimales originales dividiendo por 1,000,000
        latitud = lat_raw / 1000000.0
        longitud = lon_raw / 1000000.0
        
        # Validación básica de coordenadas lógicas terrestres
        if latitud == 0.0 and longitud == 0.0:
            return None, None, 0
            
        return latitud, longitud, sat
    except Exception as e:
        print("Error decodificando payload:", e)
        return None, None, 0

st.set_page_config(page_title="LilyGO LoRaWAN", page_icon="📡", layout="wide")

@st.cache_resource
def start_mqtt():
    store = {"data": None}

    def on_connect(client, userdata, flags, rc):
        print("MQTT conectado:", rc)
        client.subscribe(TOPIC)

    def on_message(client, userdata, msg):
        store["data"] = json.loads(msg.payload.decode())

    client = mqtt.Client()
    client.username_pw_set(USERNAME, PASSWORD)
    client.tls_set()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(HOST, PORT, 60)
    client.loop_start()

    return store

store = start_mqtt()
data = store["data"]

st.title("📡 Dashboard LilyGO LoRaWAN")

if "historial" not in st.session_state:
    st.session_state.historial = []

if data:
    uplink = data.get("uplink_message", {})
    gateways = uplink.get("rx_metadata", [])
    settings = uplink.get("settings", {})

    frecuencia = settings.get("frequency", "N/A")
    toa = uplink.get("consumed_airtime", "N/A")
    
    paquete = {
    "fecha_registro": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "device": data.get("end_device_ids", {}).get("device_id", "N/A"),
    "frame_counter": uplink.get("f_cnt", "N/A"),
    "payload": uplink.get("frm_payload", "N/A"),
    "gateways": len(gateways),
    "frecuencia": frecuencia,
    "toa": toa
}

    for i, gw in enumerate(gateways, start=1):
        paquete[f"gateway_{i}"] = gw.get("gateway_ids", {}).get("gateway_id", "N/A")
        paquete[f"rssi_{i}"] = gw.get("rssi", "N/A")
        paquete[f"snr_{i}"] = gw.get("snr", "N/A")

    if "historial" not in st.session_state:
        st.session_state["historial"] = []

    if not st.session_state["historial"] or st.session_state["historial"][-1] != paquete:
        st.session_state["historial"].append(paquete)

    if not st.session_state["historial"] or st.session_state["historial"][-1] != paquete:
        st.session_state["historial"].append(paquete)

    st.success("Datos recibidos desde TTN")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Dispositivo", data["end_device_ids"]["device_id"])
    c2.metric("Frame counter", uplink.get("f_cnt"))
    c3.metric("Payload", uplink.get("frm_payload"))
    c4.metric("Gateways recibidos", len(gateways))

    st.subheader("📡 Gateways que han recibido la LilyGO")

    rows = []

    for i, gw in enumerate(gateways, start=1):
        gw_id = gw.get("gateway_ids", {}).get("gateway_id", "N/A")
        rssi = gw.get("rssi", "N/A")
        channel_rssi = gw.get("channel_rssi", "N/A")
        snr = gw.get("snr", "N/A")
        timestamp = gw.get("time", gw.get("timestamp", "N/A"))

        location = gw.get("location", {})
        lat = location.get("latitude", None)
        lon = location.get("longitude", None)
        alt = location.get("altitude", None)

        rows.append({
            "Nº": i,
            "Gateway ID": gw_id,
            "RSSI": rssi,
            "Channel RSSI": channel_rssi,
            "SNR": snr,
            "ToA": toa,
            "Frecuencia": frecuencia,
            "Timestamp": timestamp,
            "Latitud": lat,
            "Longitud": lon,
            "Altitud": alt
        })

        with st.container(border=True):
            st.markdown(f"### Gateway {i}: `{gw_id}`")

            a, b, c, d = st.columns(4)
            a.metric("RSSI", f"{rssi} dBm")
            b.metric("SNR", snr)
            c.metric("ToA", toa)
            d.metric("Frecuencia", frecuencia)

            e, f, g = st.columns(3)
            e.write(f"**Timestamp:** {timestamp}")
            f.write(f"**Latitud:** {lat if lat is not None else 'N/A'}")
            g.write(f"**Longitud:** {lon if lon is not None else 'N/A'}")

    st.subheader("📋 Tabla resumen de gateways")
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True)

    map_df = df.dropna(subset=["Latitud", "Longitud"])
    if not map_df.empty:
        st.subheader("🗺️ Localización de gateways")
        st.map(
            map_df.rename(columns={
                "Latitud": "lat",
                "Longitud": "lon"
            })[["lat", "lon"]]
        )

    st.subheader("📍 Localización de la LilyGO")

    st.write(f"**Latitud LilyGO:** {LILYGO_LAT}")
    st.write(f"**Longitud LilyGO:** {LILYGO_LON}")

    lilygo_df = pd.DataFrame({
        "lat": [LILYGO_LAT],
        "lon": [LILYGO_LON]
    })

    st.map(lilygo_df)

    with st.expander("JSON completo recibido"):
        st.json(data)
        

historial_df = pd.DataFrame(st.session_state["historial"])


csv = historial_df.to_csv(index=False).encode("utf-8")

st.download_button(
    "⬇️ Descargar CSV",
    csv,
    "historial_lilygo.csv",
    "text/csv"
)

def crear_pdf(df):
    pdf = FPDF()
    pdf.add_page()

    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Informe LilyGO LoRaWAN", ln=True)

    pdf.set_font("Arial", "", 10)

    for idx, row in df.iterrows():
        pdf.ln(5)
        pdf.cell(0, 8, f"Paquete {idx+1}", ln=True)

        for col, value in row.items():
            pdf.multi_cell(0, 6, f"{col}: {value}")

    return pdf.output(dest="S").encode("latin-1")

pdf_bytes = crear_pdf(historial_df)

st.download_button(
    "📄 Descargar PDF",
    pdf_bytes,
    "informe_lilygo.pdf",
    "application/pdf"
)

if not data:
    st.info("Esperando datos MQTT...")

time.sleep(3)
st.rerun()