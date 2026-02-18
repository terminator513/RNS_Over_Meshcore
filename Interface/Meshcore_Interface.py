# MeshCore Interface for Reticulum Network Stack
# Implements internal fragmentation to respect MeshCore MAX_PACKET_PAYLOAD=184

import asyncio
import importlib.util
import threading
import time
import hashlib
from collections import defaultdict

import RNS
from RNS.Interfaces.Interface import Interface


class MeshCoreInterface(Interface):
    DEFAULT_IFAC_SIZE = 8

    # Fragmentation: to fit within MAX_PACKET_PAYLOAD=184,
    # reserve 4 bytes for fragment header -> payload 180 bytes
    FRAGMENT_MTU = 179  # 184 - 5 байт заголовка = 179
    FRAGMENT_HEADER_SIZE = 4

    def __init__(self, owner, configuration):
        if importlib.util.find_spec("meshcore") is None:
            RNS.log("The MeshCore interface requires the 'meshcore' module to be installed.", RNS.LOG_CRITICAL)
            RNS.log("Install it with: pip install meshcore", RNS.LOG_CRITICAL)
            RNS.panic()

        from meshcore import EventType, MeshCore
        
        
        super().__init__()
        self.HW_MTU = 564
        ifconf = Interface.get_config_obj(configuration)
        self.name = ifconf.get("name", "MeshCore")
        self.owner = owner
        
        self.transport = ifconf.get("transport", "ble").lower()
        self.port = ifconf.get("port", "/dev/ttyUSB0")
        self.baud = int(ifconf.get("baudrate", 115200))
        self.host = ifconf.get("host", "127.0.0.1")
        self.tcp_port = int(ifconf.get("tcp_port", 4403))
        self.ble_name = ifconf.get("ble_name", None)
        
        self.bitrate = int(ifconf.get("bitrate", 2000))
        
        self.online = False
        self.detached = False
        self._last_tx = 0
        self._lock = threading.Lock()
        
        # Buffers for reassembling incoming fragments
        self._fragment_buffers = defaultdict(dict)
        self._fragment_meta = {}
        
        self._meshcore_cls = MeshCore
        self._event_type_cls = EventType
        self.mesh = None
        self.loop = None
        self.thread = None

        self.thread = threading.Thread(target=self._async_thread, daemon=True)
        self.thread.start()

    def _async_thread(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.create_task(self._connect_loop())
        try:
            self.loop.run_forever()
        finally:
            self.loop.close()

    async def _connect_loop(self):
        while not self.detached:
            try:
                await self._connect_once()
                return
            except Exception as e:
                with self._lock:
                    self.online = False
                RNS.log(f"[{self.name}] MeshCore connect failed: {e}", RNS.LOG_ERROR)
                await asyncio.sleep(3)

    async def _connect_once(self):
        if self.transport == "serial":
            self.mesh = await self._meshcore_cls.create_serial(self.port, self.baud)
        elif self.transport == "tcp":
            self.mesh = await self._meshcore_cls.create_tcp(self.host, self.tcp_port)
        elif self.transport == "ble":
            self.mesh = await self._open_ble_mesh()
        else:
            raise ValueError(f"Invalid transport '{self.transport}'")

        if self.mesh is None:
            raise IOError("MeshCore returned no connection object")

        self.mesh.subscribe(self._event_type_cls.RX_LOG_DATA, self._rx)
        self.mesh.subscribe(self._event_type_cls.ERROR, self._err)
        self.mesh.subscribe(self._event_type_cls.DISCONNECTED, self._err)

        with self._lock:
            self.online = True
        
        RNS.log(f"[{self.name}] MeshCore connected over {self.transport}", RNS.LOG_INFO)

    async def _open_ble_mesh(self):
        if self.ble_name:
            try:
                from bleak import BleakScanner
            except ImportError:
                raise ImportError("BLE transport requires 'bleak' package: pip install bleak")

            RNS.log(f"[{self.name}] Scanning for BLE device '{self.ble_name}'...", RNS.LOG_INFO)
            devices = await BleakScanner.discover(timeout=5.0)
            
            for device in devices:
                if device.name == self.ble_name:
                    RNS.log(f"[{self.name}] Found {self.ble_name} @ {device.address}", RNS.LOG_INFO)
                    return await self._meshcore_cls.create_ble(address=device.address)
            
            raise IOError(f"BLE device '{self.ble_name}' not found")
        
        return await self._meshcore_cls.create_ble()

    def _fragment_outgoing(self, data: bytes):
        # If packet is small, send as-is with flag 0x00
        if len(data) <= self.FRAGMENT_MTU:
            return [b'\x00' + data]
        
        # Fragment format: [0x01][frag_id:2][chunk_idx:1][total:1][payload:<=180]
        fragments = []
        frag_id = hashlib.md5(data).digest()[:2]
        total_chunks = (len(data) + self.FRAGMENT_MTU - 1) // self.FRAGMENT_MTU
        
        for idx in range(total_chunks):
            start = idx * self.FRAGMENT_MTU
            end = min(start + self.FRAGMENT_MTU, len(data))
            chunk = data[start:end]
            
            header = b'\x01' + frag_id + bytes([idx, total_chunks])
            fragments.append(header + chunk)
        
        return fragments

    def _reassemble_incoming(self, payload: bytes):
        if len(payload) < 1:
            return None
        
        flags = payload[0]
        
        # Flag 0x00: not fragmented, return data directly
        if flags == 0x00:
            return payload[1:]
        
        # Flag 0x01: fragmented packet
        elif flags == 0x01:
            if len(payload) < 5:
                RNS.log(f"[{self.name}] RX: fragment header too short", RNS.LOG_DEBUG)
                return None
            
            frag_id = payload[1:3].hex()
            chunk_idx = payload[3]
            total_chunks = payload[4]
            chunk_data = payload[5:]
            
            key = frag_id
            
            if key not in self._fragment_meta:
                self._fragment_meta[key] = {"total": total_chunks, "received": set()}
                self._fragment_buffers[key] = {}
            
            meta = self._fragment_meta[key]
            buf = self._fragment_buffers[key]
            
            buf[chunk_idx] = chunk_data
            meta["received"].add(chunk_idx)
            
            if len(meta["received"]) == meta["total"]:
                assembled = b''.join(buf[i] for i in range(meta["total"]))
                del self._fragment_buffers[key]
                del self._fragment_meta[key]
                return assembled
            
            return None
        
        else:
            RNS.log(f"[{self.name}] RX: unknown fragment flag 0x{flags:02X}", RNS.LOG_DEBUG)
            return None

    async def _rx(self, event):
        try:
            payload = None
            
            if event and hasattr(event, "payload") and event.payload:
                evt_payload = event.payload
                
                if isinstance(evt_payload, dict):
                    raw = evt_payload.get("payload")
                    
                    if isinstance(raw, str):
                        payload = bytes.fromhex(raw.replace(" ", ""))
                    elif isinstance(raw, (bytes, bytearray)):
                        payload = bytes(raw)
                    
                elif isinstance(evt_payload, (bytes, bytearray)):
                    payload = bytes(evt_payload)
            
            if payload and len(payload) > 0:
                assembled = self._reassemble_incoming(payload)
                
                if assembled is None:
                    return
                
                with self._lock:
                    self.rxb += len(assembled)
                self.owner.inbound(assembled, self)
            else:
                RNS.log(f"[{self.name}] RX: empty or unparsable payload", RNS.LOG_DEBUG)
                
        except Exception as e:
            RNS.log(f"[{self.name}] RX error: {e}", RNS.LOG_ERROR)

    async def _err(self, event):
        with self._lock:
            self.online = False
            
        event_payload = "unknown"
        if event and hasattr(event, "payload"):
            event_payload = event.payload
        elif event:
            event_payload = str(event)
            
        RNS.log(f"[{self.name}] MeshCore event error/disconnect: {event_payload}", RNS.LOG_ERROR)
        
        if not self.detached and self.loop and self.loop.is_running():
            self.loop.create_task(self._connect_loop())

    def process_outgoing(self, data):
        with self._lock:
            if not self.online or self.mesh is None or self.loop is None:
                return

        fragments = self._fragment_outgoing(data)
        
        now = time.time()
        
        for fragment in fragments:
            if self.bitrate > 0:
                min_interval = len(fragment) / self.bitrate
                elapsed = now - self._last_tx
                if elapsed < min_interval:
                    time.sleep(min_interval - elapsed)
                    now = time.time()
            self._last_tx = now

            with self._lock:
                self.txb += len(fragment)

            future = asyncio.run_coroutine_threadsafe(self._send(fragment), self.loop)
            
            def _tx_callback(fut):
                try:
                    fut.result()
                except Exception as e:
                    RNS.log(f"[{self.name}] TX async error: {e}", RNS.LOG_ERROR)
                    with self._lock:
                        self.online = False
            
            future.add_done_callback(_tx_callback)

    async def _send(self, data):
        try:
            RNS.log(f"[{self.name}] TX: calling mesh.commands.send({len(data)} bytes)", RNS.LOG_DEBUG)
            await self.mesh.commands.send(data)
        except Exception as e:
            RNS.log(f"[{self.name}] TX failed: {e}", RNS.LOG_ERROR)
            raise

    def should_ingress_limit(self):
        return False

    def get_status_string(self):
        status = "Online" if self.online else "Offline"
        if self.transport == "serial":
            location = f"{self.port}@{self.baud}"
        elif self.transport == "tcp":
            location = f"{self.host}:{self.tcp_port}"
        elif self.transport == "ble":
            location = self.ble_name or "BLE:auto"
        else:
            location = "unknown"
        return f"{self.name}: {status}, {self.transport}://{location}, MTU={self.HW_MTU} (frag={self.FRAGMENT_MTU})"

    def detach(self):
        self.detached = True
        
        with self._lock:
            self.online = False
        
        if self.loop and self.loop.is_running():
            if self.mesh:
                try:
                    asyncio.run_coroutine_threadsafe(self.mesh.close(), self.loop)
                except:
                    pass
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except:
                pass
        
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        
        RNS.log(f"[{self.name}] Detached", RNS.LOG_INFO)

    def __str__(self):
        return f"MeshCoreInterface[{self.name}]"


interface_class = MeshCoreInterface