# version PM - con página de estado de gateways + debug MQTT
from fpdf import FPDF
from datetime import datetime

import streamlit as st
import paho.mqtt.client as mqtt
import json
import time
import pandas as pd
import base64
import struct

APP_ID    = st.secrets["TTN_APP_ID"]
USERNAME  = APP_ID + "@ttn"
PASSWORD  = st.secrets["TTN_API_KEY"]
HOST      = "eu1.cloud.thethings.network"
PORT      = 8883
TOPIC     = f"v3/{USERNAME}/devices/+/up"

GATEWAY_TIMEOUT_S = 300  # 5 min

# --- DECODIFICADOR SEGURO DEL PAYLOAD ---
def decode_payload(payload_b64):
    try:
        if not payload_b64 or payload_b64 == "N/A":
            return None, None, 0
        payload_bytes = base64.b64decode(payload_b64)
        if len(payload_bytes) < 10:
            return None, None, 0
        lat_raw, lon_raw, sat = struct.unpack(">iiH", payload_bytes[:10])
        latitud  = lat_raw / 1000000.0
        longitud = lon_raw / 1000000.0
        if latitud == 0.0 and longitud == 0.0:
            return None, None, 0
        return latitud, longitud, sat
    except Exception as e:
        print("Error decodificando payload:", e)
        return None, None, 0

st.set_page_config(page_title="LilyGO LoRaWAN", page_icon="📡", layout="wide")

# ---------------------------------------------------------------------------
# MQTT  — credentials passed as arguments so the cache key changes if they do
# ---------------------------------------------------------------------------
@st.cache_resource
def start_mqtt(host, port, username, password, topic):
    store = {
        "data": None,
        "connected": False,
        "rc": None,
        "error": None,
        "msg_count": 0,
        "last_topic": None,
    }

    def on_connect(client, userdata, flags, rc):
        store["rc"] = rc
        if rc == 0:
            store["connected"] = True
            client.subscribe(topic)
        else:
            store["connected"] = False
            store["error"] = f"Conexión rechazada, rc={rc}"

    def on_disconnect(client, userdata, rc):
        store["connected"] = False
        store["error"] = f"Desconectado, rc={rc}"

    def on_message(client, userdata, msg):
        store["data"]       = json.loads(msg.payload.decode())
        store["msg_count"] += 1
        store["last_topic"] = msg.topic

    try:
        client = mqtt.Client()
        client.username_pw_set(username, password)
        client.tls_set()
        client.on_connect    = on_connect
        client.on_disconnect = on_disconnect
        client.on_message    = on_message
        client.connect(host, port, 60)
        client.loop_start()
    except Exception as e:
        store["error"] = str(e)

    return store

store = start_mqtt(HOST, PORT, USERNAME, PASSWORD, TOPIC)
data  = store["data"]

# --- SESSION STATE ---
if "historial" not in st.session_state:
    st.session_state.historial = []
if "gateway_registry" not in st.session_state:
    st.session_state.gateway_registry = {}

# --- PROCESAR MENSAJE MQTT ENTRANTE ---
lilygo_lat    = None
lilygo_lon    = None
gps_satellites = 0

if data:
    uplink     = data.get("uplink_message", {})
    gateways   = uplink.get("rx_metadata", [])
    settings   = uplink.get("settings", {})
    frecuencia = settings.get("frequency", "N/A")
    toa        = uplink.get("consumed_airtime", "N/A")
    frm_payload = uplink.get("frm_payload", "N/A")
    device_id  = data.get("end_device_ids", {}).get("device_id", "N/A")

    lilygo_lat, lilygo_lon, gps_satellites = decode_payload(frm_payload)

    now = datetime.now()
    for gw in gateways:
        gw_id    = gw.get("gateway_ids", {}).get("gateway_id", "N/A")
        rssi     = gw.get("rssi", None)
        snr      = gw.get("snr", None)
        location = gw.get("location", {})
        lat = location.get("latitude", None)
        lon = location.get("longitude", None)
        alt = location.get("altitude", None)

        if gw_id not in st.session_state.gateway_registry:
            st.session_state.gateway_registry[gw_id] = {
                "last_seen":    now,
                "last_rssi":    rssi,
                "last_snr":     snr,
                "lat": lat, "lon": lon, "alt": alt,
                "mensajes":     1,
                "devices_seen": [device_id],
                "rssi_history": [rssi] if rssi is not None else [],
            }
        else:
            entry = st.session_state.gateway_registry[gw_id]
            entry["last_seen"] = now
            entry["last_rssi"] = rssi
            entry["last_snr"]  = snr
            entry["mensajes"] += 1
            if device_id not in entry["devices_seen"]:
                entry["devices_seen"].append(device_id)
            if rssi is not None:
                entry["rssi_history"].append(rssi)
                if len(entry["rssi_history"]) > 50:
                    entry["rssi_history"] = entry["rssi_history"][-50:]
            if lat is not None:
                entry["lat"] = lat
                entry["lon"] = lon
                entry["alt"] = alt

    paquete = {
        "fecha_registro": now.strftime("%Y-%m-%d %H:%M:%S"),
        "device":         device_id,
        "frame_counter":  uplink.get("f_cnt", "N/A"),
        "payload":        frm_payload,
        "gps_latitud":    lilygo_lat if lilygo_lat is not None else "Sin Fix",
        "gps_longitud":   lilygo_lon if lilygo_lon is not None else "Sin Fix",
        "satelites":      gps_satellites,
        "gateways":       len(gateways),
        "frecuencia":     frecuencia,
        "toa":            toa,
    }
    for i, gw in enumerate(gateways, start=1):
        paquete[f"gateway_{i}"] = gw.get("gateway_ids", {}).get("gateway_id", "N/A")
        paquete[f"rssi_{i}"]    = gw.get("rssi", "N/A")
        paquete[f"snr_{i}"]     = gw.get("snr", "N/A")

    if not st.session_state["historial"] or st.session_state["historial"][-1] != paquete:
        st.session_state["historial"].append(paquete)

# ============================================================
# SIDEBAR — navegación + estado MQTT
# ============================================================
pagina = st.sidebar.radio(
    "Navegación",
    ["📡 Dashboard", "🗼 Estado de Gateways"],
    index=0,
)

# --- Panel de debug MQTT en el sidebar ---
with st.sidebar.expander("🔧 Estado MQTT", expanded=not store["connected"]):
    if store["connected"]:
        st.success("🟢 Conectado a TTN")
    elif store["error"]:
        st.error(f"🔴 Error: {store['error']}")
    else:
        st.warning("🟡 Conectando...")

    st.caption(f"**Host:** {HOST}:{PORT}")
    st.caption(f"**Topic:** {TOPIC}")
    st.caption(f"**Mensajes recibidos:** {store['msg_count']}")
    if store["last_topic"]:
        st.caption(f"**Último topic:** {store['last_topic']}")
    if store["rc"] is not None and store["rc"] != 0:
        rc_msgs = {
            1: "Protocolo incorrecto",
            2: "Client ID rechazado",
            3: "Servidor no disponible",
            4: "Usuario/contraseña incorrectos",
            5: "No autorizado",
        }
        st.error(f"RC {store['rc']}: {rc_msgs.get(store['rc'], 'Error desconocido')}")

    if st.button("🔄 Reiniciar conexión MQTT"):
        st.cache_resource.clear()
        st.rerun()

# ============================================================
# PÁGINA 1 — DASHBOARD
# ============================================================
if pagina == "📡 Dashboard":
    st.title("📡 Dashboard LilyGO LoRaWAN")

    if data:
        uplink     = data.get("uplink_message", {})
        gateways   = uplink.get("rx_metadata", [])
        settings   = uplink.get("settings", {})
        frecuencia = settings.get("frequency", "N/A")
        toa        = uplink.get("consumed_airtime", "N/A")
        frm_payload = uplink.get("frm_payload", "N/A")

        st.success("Datos recibidos desde TTN")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Dispositivo",       data["end_device_ids"]["device_id"])
        c2.metric("Frame counter",     uplink.get("f_cnt"))
        c3.metric("Payload",           frm_payload)
        c4.metric("Gateways recibidos", len(gateways))

        st.subheader("📡 Gateways que han recibido la LilyGO")
        rows = []
        for i, gw in enumerate(gateways, start=1):
            gw_id       = gw.get("gateway_ids", {}).get("gateway_id", "N/A")
            rssi        = gw.get("rssi", "N/A")
            channel_rssi = gw.get("channel_rssi", "N/A")
            snr         = gw.get("snr", "N/A")
            timestamp   = gw.get("time", gw.get("timestamp", "N/A"))
            location    = gw.get("location", {})
            lat = location.get("latitude", None)
            lon = location.get("longitude", None)
            alt = location.get("altitude", None)

            rows.append({
                "Nº": i, "Gateway ID": gw_id, "RSSI": rssi,
                "Channel RSSI": channel_rssi, "SNR": snr, "ToA": toa,
                "Frecuencia": frecuencia, "Timestamp": timestamp,
                "Latitud": lat, "Longitud": lon, "Altitud": alt,
            })

            with st.container(border=True):
                st.markdown(f"### Gateway {i}: `{gw_id}`")
                a, b, c, d = st.columns(4)
                a.metric("RSSI",      f"{rssi} dBm")
                b.metric("SNR",       snr)
                c.metric("ToA",       toa)
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
            st.map(map_df.rename(columns={"Latitud": "lat", "Longitud": "lon"})[["lat", "lon"]])

        st.subheader("📍 Localización de la LilyGO (GPS Dinámico)")
        if lilygo_lat is not None and lilygo_lon is not None:
            st.info(f"🛰️ Satélites GPS: **{gps_satellites}**")
            st.write(f"**Latitud:** {lilygo_lat}  |  **Longitud:** {lilygo_lon}")
            st.map(pd.DataFrame({"lat": [lilygo_lat], "lon": [lilygo_lon]}))
        else:
            st.warning("⚠️ GPS sin FIX todavía. Mostrando posición de respaldo...")
            st.map(pd.DataFrame({"lat": [39.4825], "lon": [-0.3463]}))

        with st.expander("JSON completo recibido"):
            st.json(data)

    else:
        st.info("Esperando datos MQTT de TTN...")
        st.caption("Si el panel MQTT del sidebar muestra un error, revisa las credenciales en `.streamlit/secrets.toml`.")

    historial_df = pd.DataFrame(st.session_state["historial"])
    if not historial_df.empty:
        csv = historial_df.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Descargar CSV", csv, "historial_lilygo.csv", "text/csv")

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
        st.download_button("📄 Descargar PDF", pdf_bytes, "informe_lilygo.pdf", "application/pdf")

# ============================================================
# PÁGINA 2 — ESTADO DE GATEWAYS
# ============================================================
elif pagina == "🗼 Estado de Gateways":
    st.title("🗼 Estado de Gateways LoRaWAN")
    st.caption("Acumulado desde que se inició la sesión. Se actualiza automáticamente.")

    registry = st.session_state.gateway_registry
    now = datetime.now()

    if not registry:
        st.info("Todavía no se ha recibido ningún mensaje. Esperando datos MQTT...")
    else:
        total_gw   = len(registry)
        activos    = sum(1 for v in registry.values()
                        if (now - v["last_seen"]).total_seconds() < GATEWAY_TIMEOUT_S)
        inactivos  = total_gw - activos
        total_msgs = sum(v["mensajes"] for v in registry.values())

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Gateways vistos",             total_gw)
        k2.metric("🟢 Activos (< 5 min)",        activos)
        k3.metric("🔴 Inactivos (≥ 5 min)",      inactivos)
        k4.metric("Mensajes totales procesados", total_msgs)

        st.divider()
        st.subheader("Detalle por gateway")

        def sort_key(item):
            _, v = item
            activo = (now - v["last_seen"]).total_seconds() < GATEWAY_TIMEOUT_S
            return (not activo, -v["mensajes"])

        for gw_id, v in sorted(registry.items(), key=sort_key):
            seg    = (now - v["last_seen"]).total_seconds()
            activo = seg < GATEWAY_TIMEOUT_S

            if activo:
                estado_label = "🟢 Activo"
                estado_color = "green"
                tiempo_label = f"Hace {int(seg)}s"
            else:
                mins = int(seg // 60)
                estado_label = "🔴 Inactivo"
                estado_color = "red"
                tiempo_label = f"Hace {mins} min" if mins < 60 else f"Hace {mins//60}h {mins%60}min"

            with st.container(border=True):
                col_titulo, col_estado = st.columns([4, 1])
                col_titulo.markdown(f"### `{gw_id}`")
                col_estado.markdown(
                    f"<div style='text-align:right;color:{estado_color};font-size:1.1rem;padding-top:8px'>"
                    f"<b>{estado_label}</b></div>",
                    unsafe_allow_html=True,
                )

                m1, m2, m3, m4, m5 = st.columns(5)
                m1.metric("Último mensaje", tiempo_label)
                m1.caption(v["last_seen"].strftime("%H:%M:%S"))
                m2.metric("RSSI", f"{v['last_rssi']} dBm" if v["last_rssi"] is not None else "N/A")
                m3.metric("SNR",  str(v["last_snr"]) if v["last_snr"] is not None else "N/A")
                m4.metric("Mensajes",    v["mensajes"])
                m5.metric("Dispositivos", len(v["devices_seen"]))

                with st.expander("Ver dispositivos y RSSI histórico"):
                    st.write("**Dispositivos detectados:**")
                    st.write(", ".join(v["devices_seen"]) or "Ninguno")
                    if len(v["rssi_history"]) > 1:
                        rssi_df = pd.DataFrame({
                            "Muestra":   range(1, len(v["rssi_history"]) + 1),
                            "RSSI (dBm)": v["rssi_history"],
                        }).set_index("Muestra")
                        st.line_chart(rssi_df, height=150)
                    else:
                        st.caption("Historial RSSI insuficiente para graficar.")
                    if v["lat"] is not None:
                        st.write(f"**Coordenadas:** {v['lat']:.5f}, {v['lon']:.5f}"
                                 + (f" | Alt: {v['alt']} m" if v["alt"] else ""))

        st.divider()
        st.subheader("📋 Tabla resumen")
        tabla_rows = []
        for gw_id, v in registry.items():
            seg = (now - v["last_seen"]).total_seconds()
            tabla_rows.append({
                "Gateway ID":     gw_id,
                "Estado":         "Activo" if seg < GATEWAY_TIMEOUT_S else "Inactivo",
                "Último mensaje": v["last_seen"].strftime("%Y-%m-%d %H:%M:%S"),
                "Mensajes":       v["mensajes"],
                "Dispositivos":   len(v["devices_seen"]),
                "RSSI":           v["last_rssi"],
                "SNR":            v["last_snr"],
                "Lat":            v["lat"],
                "Lon":            v["lon"],
            })
        tabla_df = pd.DataFrame(tabla_rows).sort_values("Mensajes", ascending=False)
        st.dataframe(tabla_df, use_container_width=True)

        map_rows = [r for r in tabla_rows if r["Lat"] is not None]
        if map_rows:
            st.subheader("🗺️ Mapa de gateways")
            st.map(pd.DataFrame(map_rows).rename(columns={"Lat": "lat", "Lon": "lon"})[["lat", "lon"]])
        else:
            st.info("Ningún gateway ha enviado coordenadas todavía.")

        csv_gw = tabla_df.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Descargar CSV gateways", csv_gw, "estado_gateways.csv", "text/csv")

# --- Auto-refresco ---
time.sleep(3)
st.rerun()
