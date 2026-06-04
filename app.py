# ============================================================
# IMPORTS
# ============================================================

from datetime import datetime
import base64
import json
import struct
import time

import pandas as pd
import paho.mqtt.client as mqtt
import streamlit as st
from fpdf import FPDF


# ============================================================
# CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="LilyGO LoRaWAN",
    page_icon="📡",
    layout="wide",
)

APP_ID = st.secrets["TTN_APP_ID"]
USERNAME = f"{APP_ID}@ttn"
PASSWORD = st.secrets["TTN_API_KEY"]

HOST = "eu1.cloud.thethings.network"
PORT = 8883
TOPIC = f"v3/{USERNAME}/devices/+/up"

GATEWAY_TIMEOUT_S = 300


# ============================================================
# PAYLOAD DECODER
# ============================================================

def decode_payload(payload_b64: str):
    """
    Decode GPS payload received from TTN.

    Returns:
        tuple(latitude, longitude, satellites)
    """
    try:
        if not payload_b64 or payload_b64 == "N/A":
            return None, None, 0

        payload_bytes = base64.b64decode(payload_b64)

        if len(payload_bytes) < 10:
            return None, None, 0

        lat_raw, lon_raw, satellites = struct.unpack(">iiH", payload_bytes[:10])

        latitude = lat_raw / 1_000_000
        longitude = lon_raw / 1_000_000

        if latitude == 0.0 and longitude == 0.0:
            return None, None, 0

        return latitude, longitude, satellites

    except Exception as error:
        print("Payload decode error:", error)
        return None, None, 0


# ============================================================
# MQTT CONNECTION
# ============================================================

@st.cache_resource
def start_mqtt(
    host: str,
    port: int,
    username: str,
    password: str,
    topic: str,
):
    """
    Initialize MQTT client connection.
    """

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
            store["error"] = f"Connection rejected, rc={rc}"

    def on_disconnect(client, userdata, rc):
        store["connected"] = False
        store["error"] = f"Disconnected, rc={rc}"

    def on_message(client, userdata, msg):
        try:
            store["data"] = json.loads(msg.payload.decode())
            store["msg_count"] += 1
            store["last_topic"] = msg.topic
        except Exception as error:
            store["error"] = str(error)

    try:
        client = mqtt.Client()

        client.username_pw_set(username, password)
        client.tls_set()

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message

        client.connect(host, port, 60)
        client.loop_start()

    except Exception as error:
        store["error"] = str(error)

    return store


# ============================================================
# SESSION STATE
# ============================================================

if "history" not in st.session_state:
    st.session_state.history = []

if "gateway_registry" not in st.session_state:
    st.session_state.gateway_registry = {}


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def render_metric_row(metrics):
    """
    Render metrics in columns.

    metrics format:
    [
        ("Label", "Value"),
        ...
    ]
    """
    columns = st.columns(len(metrics))

    for col, (label, value) in zip(columns, metrics):
        col.metric(label, value)


def generate_pdf_report(df: pd.DataFrame) -> bytes:
    """
    Generate PDF report from dataframe.
    """

    pdf = FPDF()

    pdf.add_page()
    pdf.set_font("Arial", "B", 16)

    pdf.cell(0, 10, "LilyGO LoRaWAN Report", ln=True)

    pdf.set_font("Arial", "", 10)

    for index, row in df.iterrows():
        pdf.ln(5)
        pdf.cell(0, 8, f"Packet {index + 1}", ln=True)

        for column, value in row.items():
            pdf.multi_cell(0, 6, f"{column}: {value}")

    return pdf.output(dest="S").encode("latin-1")


def initialize_gateway_entry(
    now,
    rssi,
    snr,
    latitude,
    longitude,
    altitude,
    device_id,
):
    return {
        "last_seen": now,
        "last_rssi": rssi,
        "last_snr": snr,
        "lat": latitude,
        "lon": longitude,
        "alt": altitude,
        "messages": 1,
        "devices_seen": [device_id],
        "rssi_history": [rssi] if rssi is not None else [],
    }


def update_gateway_entry(
    entry,
    now,
    rssi,
    snr,
    latitude,
    longitude,
    altitude,
    device_id,
):
    entry["last_seen"] = now
    entry["last_rssi"] = rssi
    entry["last_snr"] = snr
    entry["messages"] += 1

    if device_id not in entry["devices_seen"]:
        entry["devices_seen"].append(device_id)

    if rssi is not None:
        entry["rssi_history"].append(rssi)
        entry["rssi_history"] = entry["rssi_history"][-50:]

    if latitude is not None:
        entry["lat"] = latitude
        entry["lon"] = longitude
        entry["alt"] = altitude


# ============================================================
# MQTT DATA
# ============================================================

store = start_mqtt(
    HOST,
    PORT,
    USERNAME,
    PASSWORD,
    TOPIC,
)

data = store["data"]


# ============================================================
# PROCESS INCOMING MQTT MESSAGE
# ============================================================

lilygo_lat = None
lilygo_lon = None
gps_satellites = 0

if data:

    uplink = data.get("uplink_message", {})
    gateways = uplink.get("rx_metadata", [])
    settings = uplink.get("settings", {})

    frequency = settings.get("frequency", "N/A")
    airtime = uplink.get("consumed_airtime", "N/A")
    payload = uplink.get("frm_payload", "N/A")

    device_id = data.get(
        "end_device_ids",
        {},
    ).get("device_id", "N/A")

    lilygo_lat, lilygo_lon, gps_satellites = decode_payload(payload)

    now = datetime.now()

    for gateway in gateways:

        gateway_id = gateway.get(
            "gateway_ids",
            {},
        ).get("gateway_id", "N/A")

        rssi = gateway.get("rssi")
        snr = gateway.get("snr")

        location = gateway.get("location", {})

        latitude = location.get("latitude")
        longitude = location.get("longitude")
        altitude = location.get("altitude")

        registry = st.session_state.gateway_registry

        if gateway_id not in registry:

            registry[gateway_id] = initialize_gateway_entry(
                now,
                rssi,
                snr,
                latitude,
                longitude,
                altitude,
                device_id,
            )

        else:

            update_gateway_entry(
                registry[gateway_id],
                now,
                rssi,
                snr,
                latitude,
                longitude,
                altitude,
                device_id,
            )

    packet = {
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "device": device_id,
        "frame_counter": uplink.get("f_cnt", "N/A"),
        "payload": payload,
        "gps_latitude": lilygo_lat if lilygo_lat else "No Fix",
        "gps_longitude": lilygo_lon if lilygo_lon else "No Fix",
        "satellites": gps_satellites,
        "gateways": len(gateways),
        "frequency": frequency,
        "airtime": airtime,
    }

    for index, gateway in enumerate(gateways, start=1):

        packet[f"gateway_{index}"] = gateway.get(
            "gateway_ids",
            {},
        ).get("gateway_id", "N/A")

        packet[f"rssi_{index}"] = gateway.get("rssi", "N/A")
        packet[f"snr_{index}"] = gateway.get("snr", "N/A")

    if (
        not st.session_state.history
        or st.session_state.history[-1] != packet
    ):
        st.session_state.history.append(packet)


# ============================================================
# SIDEBAR
# ============================================================

page = st.sidebar.radio(
    "Navigation",
    [
        "🗼 Gateway Status",
        "☑️ Future work",
    ],
)

with st.sidebar.expander(
    "🔧 MQTT Status",
    expanded=not store["connected"],
):

    if store["connected"]:
        st.success("🟢 Connected to TTN")

    elif store["error"]:
        st.error(f"🔴 Error: {store['error']}")

    else:
        st.warning("🟡 Connecting...")

    st.caption(f"Host: {HOST}:{PORT}")
    st.caption(f"Topic: {TOPIC}")
    st.caption(f"Messages received: {store['msg_count']}")

    if store["last_topic"]:
        st.caption(f"Last topic: {store['last_topic']}")

    if store["rc"] not in [None, 0]:

        rc_messages = {
            1: "Incorrect protocol",
            2: "Client ID rejected",
            3: "Server unavailable",
            4: "Wrong username/password",
            5: "Unauthorized",
        }

        st.error(
            f"RC {store['rc']}: "
            f"{rc_messages.get(store['rc'], 'Unknown error')}"
        )

    if st.button("🔄 Restart MQTT Connection"):
        st.cache_resource.clear()
        st.rerun()


# ============================================================
# GATEWAY STATUS PAGE
# ============================================================

def render_gateway_status():

    st.title("🗼 LoRaWAN Gateway Status")

    registry = st.session_state.gateway_registry
    now = datetime.now()

    if not registry:
        st.info("No gateway messages received yet.")
        return

    total_gateways = len(registry)

    active_gateways = sum(
        1
        for value in registry.values()
        if (now - value["last_seen"]).total_seconds()
        < GATEWAY_TIMEOUT_S
    )

    inactive_gateways = total_gateways - active_gateways

    total_messages = sum(
        value["messages"]
        for value in registry.values()
    )

    render_metric_row([
        ("Gateways", total_gateways),
        ("🟢 Active", active_gateways),
        ("🔴 Inactive", inactive_gateways),
        ("Messages", total_messages),
    ])

    st.divider()

    for gateway_id, value in registry.items():

        seconds = (
            now - value["last_seen"]
        ).total_seconds()

        active = seconds < GATEWAY_TIMEOUT_S

        if active:
            status = "🟢 Active"
            elapsed = f"{int(seconds)}s ago"
        else:
            minutes = int(seconds // 60)
            status = "🔴 Inactive"
            elapsed = f"{minutes} min ago"

        with st.container(border=True):

            st.markdown(f"### `{gateway_id}`")
            st.caption(status)

            render_metric_row([
                ("Last Message", elapsed),
                ("RSSI", value["last_rssi"]),
                ("SNR", value["last_snr"]),
                ("Messages", value["messages"]),
                ("Devices", len(value["devices_seen"])),
            ])

            with st.expander("Details"):

                st.write("Devices:")
                st.write(", ".join(value["devices_seen"]))

                if len(value["rssi_history"]) > 1:

                    rssi_df = pd.DataFrame({
                        "RSSI": value["rssi_history"]
                    })

                    st.line_chart(rssi_df)

                if value["lat"] is not None:

                    st.write(
                        f"Coordinates: "
                        f"{value['lat']:.5f}, "
                        f"{value['lon']:.5f}"
                    )

    rows = []

    for gateway_id, value in registry.items():

        rows.append({
            "Gateway ID": gateway_id,
            "Messages": value["messages"],
            "Devices": len(value["devices_seen"]),
            "RSSI": value["last_rssi"],
            "SNR": value["last_snr"],
            "Latitude": value["lat"],
            "Longitude": value["lon"],
        })

    dataframe = pd.DataFrame(rows)

    st.subheader("📋 Gateway Table")
    st.dataframe(dataframe, use_container_width=True)

    map_rows = dataframe.dropna(
        subset=["Latitude", "Longitude"]
    )

    if not map_rows.empty:

        st.subheader("🗺️ Gateway Map")

        st.map(
            map_rows.rename(
                columns={
                    "Latitude": "lat",
                    "Longitude": "lon",
                }
            )[["lat", "lon"]]
        )


# ============================================================
# ROUTING
# ============================================================

if page == "🗼 Gateway Status":
    render_gateway_status()


# ============================================================
# AUTO REFRESH
# ============================================================

time.sleep(3)
st.rerun()