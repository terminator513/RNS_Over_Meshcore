# RNS_Over_MeshCore

Interface for Reticulum Network Stack (RNS) using MeshCore as the underlying networking layer to utilize existing LoRa/mesh hardware.

⚠️ TESTED — BEHAVIOR WARNING

>MeshCore-based firmware on many devices is extremely unstable. Even under perfect conditions, sending a single CHUNK of a Reticulum packet often requires repeating the same command multiple times, and delivery is still not guaranteed. This interface handles fragmentation internally: each outgoing packet is split into CHUNKS with fragment IDs, and incoming fragments are reassembled automatically. Future improvements may include enhanced transport-layer reliability and protocol tagging.

---

## Requirements

- Python 3.10+
- [meshcore](https://pypi.org/project/meshcore/) Python library
- Reticulum Network Stack (`rnsd`)
- Compatible LoRa device with MeshCore firmware

---

## Installation

1. **Install MeshCore Python library:**
   ```bash
   pip install meshcore
   ```

2. **Copy the interface file:**
   - Place `Meshcore_Interface.py` in your Reticulum interfaces folder:
     - **Linux/macOS:** `~/.reticulum/interfaces/`
     - **Windows:** `C:\Users\<YourName>\.reticulum\interfaces\`

3. **Configure the interface** (see below)

4. **Restart `rnsd`**

---

## Configuration

Add the following to your `~/.reticulum/config` file:

```ini
# =========================================================
# ⚠️ WARNING: MeshCore firmware is extremely unstable.
# You will often need to spam the same command multiple times
# just to get one CHUNK of a packet sent. Even then delivery
# is not guaranteed. Test one device at a time, expect failures,
# and do not rely on this for critical communication.
# =========================================================

[[MeshCore]]
   type = MeshCoreInterface
   interface_enabled = true

# === Transport settings ===
   transport = ble           # Options: ble | serial | tcp
   #port = /dev/ttyUSB0       # Serial port if transport = serial
   #baudrate = 115200         # Serial baudrate
   #host = 127.0.0.1          # TCP host if transport = tcp
   #tcp_port = 4403           # TCP port if transport = tcp
   ble_name = MeshCore-Obdolbus  # BLE device name (optional, auto-scan if empty)

   # === RNS channel settings (DO NOT CHANGE SECRET unless you know what you are doing) ===
   # channel_name = RNSTunnel
   # channel_secret = c4d2b6c8254e3b11200f57e95dcb1197  # 16 bytes hex
   # channel_idx =                                        # Leave empty to auto-select, fallback = 39

   # === Fragmentation / reliability ===
   #count_repeat = 7            # How many times to send each fragment (spam to increase chance of delivery)
   #fragment_timeout = 3600     # Timeout for incomplete fragment reassembly (seconds)
   #fragment_delay = 20         # Delay between fragments in seconds — yes, MeshCore is THAT bad
   #bitrate = 200               # Rate limiting in bytes/sec, 0 = unlimited

```

## Acknowledgements

Special thanks to [HDDen](https://github.com/HDDen/) for their help with the MeshCore integration and debugging.

## License

MIT License — See LICENSE file for details.
