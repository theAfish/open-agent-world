# Future direct-socket networking for macOS Seatbelt

[Documentation](README.md) / [Sandbox networking](sandbox-networking.md)

The current Seatbelt backend can enable **proxy-mediated public IPv4 TCP**
without a signed system extension. A deny-default Seatbelt profile grants each
command access only to the exact loopback ports of its authenticated HTTP(S)
and SOCKS5 proxy. The host proxy resolves names, rejects non-public and host
addresses, and dials a checked numeric IPv4 address. Direct public sockets,
UDP and IPv6 remain unavailable. This is deliberate and is displayed as
`proxy_tcp`, not as direct-network parity with the Container VM.

A signed Network Extension would be relevant only if native Seatbelt commands
must open **direct** public TCP/UDP sockets. Simply granting unrestricted
`network-outbound` would expose private networks and backend services, so it
must never be used as a shortcut.

## Requirements for any future direct mode

1. The filter must be signed, installed and approved before a command is
   admitted; filter interruption must fail closed.
2. A privileged launcher must establish an OS-enforced command-tree identity
   that survives fork and exec and cannot be changed by the workload. Parent
   PID, process group and executable name alone are not sufficient.
3. The filter must use that identity to deny non-public IPv4, host-interface
   addresses, IPv6, inbound flows and unresolved endpoints; other Mac apps
   must not inherit the command policy. Apple's filter APIs expose a
   [source-process audit token](https://developer.apple.com/documentation/networkextension/nefilterflow/sourceprocessaudittoken)
   and [remote endpoint](https://developer.apple.com/documentation/networkextension/nefiltersocketflow/remoteendpoint),
   but the process-tree identity still requires proof.
4. A Mac-native adversarial test must verify direct public HTTPS and UDP,
   descendant processes, loopback/host/private denial, extension failure,
   and concurrent commands before advertising a distinct direct mode.

The present desktop bundle has no Network Extension target, entitlements,
signing/provisioning or approval flow. This is future work, not a prerequisite
for the proxy TCP mode. Use macOS Container VM where direct sockets or UDP are
required today.
