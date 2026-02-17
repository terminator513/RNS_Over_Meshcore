# RNS_Over_MeshCore

Interface for Reticulum Network Stack (RNS) using MeshCore as the underlying networking layer to utilize existing LoRa/mesh hardware.

> ⚠️ **NOT TESTED** — This interface is under active development.
>
> **Note:** Currently, all raw data received from the air is passed to Reticulum without additional wrapping or filtering. This means RF noise and non-RNS packets may be processed, resulting in parse errors in the logs. Future versions may add protocol tagging to distinguish RNS traffic at the transport layer.

---

## Requirements

- Python 3.8+
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
[[MeshCore]]
   type = Meshcore_Interface
   interface_enabled = true
    
   # Transport type: serial | tcp | ble
   transport = ble
    
   # === Serial Settings (if transport = serial) ===
   # port = /dev/ttyUSB0
   # baudrate = 115200
    
   # === TCP Settings (if transport = tcp) ===
   # host = 127.0.0.1
   # tcp_port = 4403
    
   # === BLE Settings (if transport = ble) ===
   ble_name = MeshCore-Obdolbus
    
   # === Interface Settings ===
   mtu = 256
   # bitrate = 2000
```

### Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `transport` | `ble` | Transport type: `serial`, `tcp`, or `ble` |
| `port` | `/dev/ttyUSB0` | Serial port path (for `serial` transport) |
| `baudrate` | `115200` | Serial baud rate (for `serial` transport) |
| `host` | `127.0.0.1` | TCP host (for `tcp` transport) |
| `tcp_port` | `4403` | TCP port (for `tcp` transport) |
| `ble_name` | `None` | BLE device name to connect to (for `ble` transport) |
| `mtu` | `256` | Maximum transmission unit in bytes |
| `bitrate` | `2000` | Interface bitrate for rate limiting (bits/sec) |

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `Error while loading public key, ...` errors | Normal — RF noise is being filtered by RNS. Reduce `loglevel` in config |
| Interface not connecting | Check `ble_name` matches your device, or verify serial port |
| `meshcore module not found` | Run `pip install meshcore` |

---

## License

MIT License — See LICENSE file for details.
