# MIT License - Copyright (c) 2024 Mark Qvist / unsigned.io
# This interface integrates meshcore_py with Reticulum and follows
# the same custom interface structure as the ExampleInterface.

import asyncio
import importlib.util
import threading
import time

import RNS
from RNS.Interfaces.Interface import Interface


class MeshCoreInterface(Interface):
    DEFAULT_IFAC_SIZE = 8

    owner = None
    mesh = None
    loop = None
    thread = None

    def __init__(self, owner, configuration):
        if importlib.util.find_spec("meshcore") is None:
            RNS.log("Using this interface requires a meshcore module to be installed.", RNS.LOG_CRITICAL)
            RNS.log("You can install one with the command: python3 -m pip install meshcore", RNS.LOG_CRITICAL)
            RNS.panic()

        from meshcore import EventType, MeshCore

        super().__init__()

        ifconf = Interface.get_config_obj(configuration)

        self.name = ifconf["name"]

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

        self._meshcore = MeshCore
        self._event_type = EventType

        self.thread = threading.Thread(target=self._async_thread)
        self.thread.daemon = True
        self.thread.start()

    def _async_thread(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.create_task(self._connect_loop())
        self.loop.run_forever()

    async def _connect_loop(self):
        while not self.detached:
            try:
                await self._connect_once()
                return
            except Exception as e:
                self.online = False
                RNS.log(f"[{self.name}] MeshCore connect failed: {e}", RNS.LOG_ERROR)
                await asyncio.sleep(3)

    async def _connect_once(self):
        if self.transport == "serial":
            self.mesh = await self._meshcore.create_serial(self.port, self.baud)
        elif self.transport == "tcp":
            self.mesh = await self._meshcore.create_tcp(self.host, self.tcp_port)
        elif self.transport == "ble":
            self.mesh = await self._open_ble_mesh()
        else:
            raise ValueError(f"Invalid transport '{self.transport}'")

        if self.mesh is None:
            raise IOError("MeshCore returned no connection object")

        self.mesh.subscribe(self._event_type.RAW_DATA, self._rx)
        self.mesh.subscribe(self._event_type.ERROR, self._err)
        self.mesh.subscribe(self._event_type.DISCONNECTED, self._err)

        self.online = True
        RNS.log(f"[{self.name}] MeshCore connected over {self.transport}", RNS.LOG_INFO)

    async def _open_ble_mesh(self):
        if self.ble_name:
            from bleak import BleakScanner

            RNS.log(f"[{self.name}] Scanning for BLE device named '{self.ble_name}'...", RNS.LOG_INFO)
            devices = await BleakScanner.discover(timeout=5.0)
            for device in devices:
                if device.name == self.ble_name:
                    RNS.log(f"[{self.name}] Found {self.ble_name} @ {device.address}", RNS.LOG_INFO)
                    return await self._meshcore.create_ble(address=device.address)

            raise IOError(f"BLE device '{self.ble_name}' not found")

        return await self._meshcore.create_ble()

    async def _rx(self, event):
        try:
            payload = event.payload.get("data") if event and event.payload else None
            if payload:
                self.rxb += len(payload)
                self.owner.inbound(payload, self)
        except Exception as e:
            RNS.log(f"[{self.name}] RX error: {e}", RNS.LOG_ERROR)

    async def _err(self, event):
        self.online = False
        event_payload = event.payload if event and hasattr(event, "payload") else "unknown"
        RNS.log(f"[{self.name}] MeshCore event error/disconnect: {event_payload}", RNS.LOG_ERROR)
        if not self.detached:
            self.loop.create_task(self._connect_loop())

    def process_outgoing(self, data):
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
        self.txb += len(data)
        asyncio.run_coroutine_threadsafe(self._send(data), self.loop)

    async def _send(self, data):
        try:
            await self.mesh.commands.send(data)
        except Exception as e:
            self.online = False
            RNS.log(f"[{self.name}] TX failed: {e}", RNS.LOG_ERROR)

    def should_ingress_limit(self):
        return False

    def detach(self):
        self.detached = True
        self.online = False

        if self.mesh and self.loop:
            asyncio.run_coroutine_threadsafe(self.mesh.close(), self.loop)

        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)

        RNS.log(f"[{self.name}] Detached", RNS.LOG_INFO)

    def __str__(self):
        return f"MeshCoreInterface[{self.name}]"


interface_class = MeshCoreInterface
