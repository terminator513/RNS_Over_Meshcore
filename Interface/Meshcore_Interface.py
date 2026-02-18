# MeshCore Interface for Reticulum Network Stack

import asyncio
import importlib.util
import threading
import time

import RNS
from RNS.Interfaces.Interface import Interface


class MeshCoreInterface(Interface):
    DEFAULT_IFAC_SIZE = 8

    def __init__(self, owner, configuration):
        if importlib.util.find_spec("meshcore") is None:
            RNS.log("The MeshCore interface requires the 'meshcore' module to be installed.", RNS.LOG_CRITICAL)
            RNS.log("Install it with: pip install meshcore", RNS.LOG_CRITICAL)
            RNS.panic()

        from meshcore import EventType, MeshCore

        super().__init__()

        ifconf = Interface.get_config_obj(configuration)
        self.name = ifconf.get("name", "MeshCore")
        self.owner = owner
        
        self.transport = ifconf.get("transport", "ble").lower()
        self.port = ifconf.get("port", "/dev/ttyUSB0")
        self.baud = int(ifconf.get("baudrate", 115200))
        self.host = ifconf.get("host", "127.0.0.1")
        self.tcp_port = int(ifconf.get("tcp_port", 4403))
        self.ble_name = ifconf.get("ble_name", None)
        
        self.HW_MTU = int(ifconf.get("mtu", 256))
        self.bitrate = int(ifconf.get("bitrate", 2000))
        
        self.online = False
        self.detached = False
        self._last_tx = 0
        self._lock = threading.Lock()
        
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
                with self._lock:
                    self.rxb += len(payload)
                self.owner.inbound(payload, self)
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

        if len(data) > self.HW_MTU:
            RNS.log(f"[{self.name}] Packet too large ({len(data)} > {self.HW_MTU})", RNS.LOG_WARNING)
            return

        now = time.time()
        min_interval = len(data) / self.bitrate if self.bitrate > 0 else 0
        if now - self._last_tx < min_interval:
            return
        self._last_tx = now

        with self._lock:
            self.txb += len(data)

        future = asyncio.run_coroutine_threadsafe(self._send(data), self.loop)
        
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
        return f"{self.name}: {status}, {self.transport}://{location}, MTU={self.HW_MTU}"

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