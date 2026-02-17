import threading
import asyncio
import collections
import time
from RNS.Interfaces.Interface import Interface



class MeshCoreInterface(Interface):

    MTU_DEFAULT = 512
    # All interface classes must define a default
    # IFAC size, used in IFAC setup when the user
    # has not specified a custom IFAC size. This
    # option is specified in bytes.
    DEFAULT_IFAC_SIZE = 8
    def __init__(self, owner, configuration):
        #import inspect
        #print(inspect.getsource(MeshCore))
        try:
            from meshcore import MeshCore, EventType
            HAS_MESHCORE = True
        except ImportError:
            RNS.log("Using this interface requires a meshcore_py module to be installed.", RNS.LOG_CRITICAL)
            RNS.log("You can install one with the command: python3 -m pip install meshcore", RNS.LOG_CRITICAL)
            RNS.panic()
        super().__init__()
        # === RNS REQUIRED FIELDS ===
        self.owner = owner
        self.online = False
        self.IN = True
        self.OUT = False
        self.bitrate = 2000
        self.rxb = 0
        self.txb = 0
        self.detached = False

        self.ingress_control = False
        self.held_announces = []
        self.rate_violation_occurred = False
        self.clients = 0
        self.ia_freq_deque = collections.deque(maxlen=100)
        self.oa_freq_deque = collections.deque(maxlen=100)
        self.announce_cap = 0
        self.ifac_identity = None

        try:
            self.mode = RNS.Interfaces.Interface.MODE_ACCESS_POINT
        except Exception:
            self.mode = 1

        # === CONFIG ===
        ifconf = Interface.get_config_obj(configuration)

        self.name = ifconf["name"]

        self.transport = ifconf.get("transport", "ble")
        self.port = ifconf.get("port", "COM3")
        self.baud = int(ifconf.get("baudrate", 115200))
        self.host = ifconf.get("host", "127.0.0.1")
        self.tcp_port = int(ifconf.get("tcp_port", 4403))
        self.MTU = int(ifconf.get("mtu", 256))
        self.ble_name = ifconf.get("ble_name", None)


        self.mesh = None
        self.loop = None
        self._last_tx = 0

        if not HAS_MESHCORE:
            RNS.log(f"[{self.name}] meshcore_py missing", RNS.LOG_ERROR)
            return

        self.thread = threading.Thread(target=self._async_thread)
        self.thread.daemon = True
        self.thread.start()

    # ======================================================
    # ASYNC LOOP
    # ======================================================

    def _async_thread(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._connect())
        self.loop.run_forever()

    async def _connect(self):
        while not self.detached:
            try:
                if self.transport == "serial":
                    self.mesh = await MeshCore.create_serial(self.port, self.baud)
                elif self.transport == "tcp":
                    self.mesh = await MeshCore.create_tcp(self.host, self.tcp_port)
                elif self.transport == "ble":
                    from bleak import BleakScanner

                    target_address = None

                    if self.ble_name:
                        RNS.log(f"[{self.name}] Scanning for BLE device named '{self.ble_name}'...", RNS.LOG_INFO)

                        devices = await BleakScanner.discover(timeout=5.0)

                        for d in devices:
                            if d.name == self.ble_name:
                                target_address = d.address
                                break

                        if not target_address:
                            raise Exception(f"BLE device '{self.ble_name}' not found")

                        RNS.log(f"[{self.name}] Found {self.ble_name} @ {target_address}", RNS.LOG_INFO)
                        mc = await MeshCore.create_ble(address=target_address)

                        if mc is None:
                            raise Exception("MeshCore returned None during BLE init")

                        self.mesh = mc

                    else:
                        # fallback — без имени
                        self.mesh = await MeshCore.create_ble()

                else:
                    raise Exception("Invalid transport")

                self.mesh.subscribe(EventType.RAW_DATA, self._rx)
                self.mesh.subscribe(EventType.ERROR, self._err)
                self.mesh.subscribe(EventType.DISCONNECTED, self._err)


                self.online = True
                self.OUT = True

                RNS.log(f"[{self.name}] MeshCore connected", RNS.LOG_INFO)
                return

            except Exception as e:
                self.online = False
                RNS.log(f"[{self.name}] connect failed: {e}", RNS.LOG_ERROR)
                await asyncio.sleep(3)

    # ======================================================
    # RX
    # ======================================================

    async def _rx(self, event):
        print("RX EVENT:", event.type)
        print("PAYLOAD:", event.payload)
        try:
            payload = event.payload.get("data", None)
            if payload:
                self.rxb += len(payload)
                self.owner.inbound(payload, self)
        except Exception as e:
            RNS.log(f"[{self.name}] RX error: {e}", RNS.LOG_ERROR)

    async def _err(self, event):
        self.online = False
        RNS.log(f"[{self.name}] MeshCore error: {event.payload}", RNS.LOG_ERROR)


    # ======================================================
    # TX
    # ======================================================

    def process_outgoing(self, data):
        print("1")
        if not self.online or not self.mesh:
            return
        print("2")

        # MTU CHECK
        if len(data) > self.MTU:
            RNS.log(f"[{self.name}] Packet too large ({len(data)} > {self.MTU})", RNS.LOG_WARNING)
            return

        # Simple airtime throttle (primitive but prevents flood)
        now = time.time()
        min_interval = len(data) / self.bitrate
        if now - self._last_tx < min_interval:
            return

        self._last_tx = now
        self.txb += len(data)

        asyncio.run_coroutine_threadsafe(self._send(data), self.loop)

    async def _send(self, data):
        try:
            # broadcast
            await self.mesh.commands.send_data(None, data)
        except Exception as e:
            self.online = False
            RNS.log(f"[{self.name}] TX failed: {e}", RNS.LOG_ERROR)

    # ======================================================
    # SHUTDOWN
    # ======================================================

    def detach(self):
        self.detached = True
        self.online = False

        if self.mesh:
            asyncio.run_coroutine_threadsafe(self.mesh.close(), self.loop)

        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)

        RNS.log(f"[{self.name}] Detached", RNS.LOG_INFO)

    def __str__(self):
        return f"MeshCore ({self.transport})"


interface_class = MeshCoreInterface
