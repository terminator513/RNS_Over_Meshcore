# RNS_Over_MeshCore
Interface for RNS using Meshtastic as the underlying networking layer to utilize existing meshtastic hardware.

- This is a direct followup project to https://github.com/landandair/Meshtastic_File_Transfer and fixes many of its issues (use the rncp utility of RNS and get a more reliable easy to use utility)
- Consider the expected max speed to be around 500 bytes/s so notably worse than RNode
- has the benefit of being propagated and functional alongside existing standalone Meshtastic Nodes
- Ideal use case would be as a faster secondary meshtastic network covering an area providing a route for more intensive data uses such as RNS

## Usage
- Install MeshCore Python Library
- Add the file [Meshcore_Interface.py](Interface%2FMeshcore_Interface.py) to your interfaces folder for reticulum
- Modify the node config file and add the following
```
[[MeshCore]]
    type = Meshcore_Interface
    interface_enabled = true
    #port = /dev/ttyUSB0
    #baudrate = 115200
    transport = ble #serial|tcp|ble
    #tcp_port = 25565
    ble_name = MeshCore-Obdolbus
    mtu = 256
    #bitrate = 2000
```
