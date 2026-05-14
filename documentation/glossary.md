# Thread Observability Glossary

This glossary is the shared MCP background resource for the Thread Observability add-on.
Use it to interpret the tool catalog, mesh snapshots, issue payloads, and diagnostics fields. For live versions and deployment identity, call `get_environment`.

## Core identifiers

### EUI-64
A globally unique 64-bit IEEE identifier for a Thread device. Most node-scoped tools use the node EUI-64 as their stable handle.

### RLOC16
A 16-bit Routing Locator assigned within the current Thread partition. It can change after reattachment or topology churn, so it is useful for short-term diagnostics but not as a durable node identity.

### Partition ID
The Thread partition identifier for the current mesh partition. Nodes in different partitions are not currently attached to the same active mesh.

### Dataset
The Thread operational dataset containing network parameters such as network name, channel, PAN ID, and security material.

## Mesh topology terms

### Parent
The router or leader that a sleepy or end device is currently attached to.

### Neighbor
A node with a recently observed direct link relationship. Neighbor data is useful for explaining alternate paths, instability, and radio reachability.

### Phantom node
A stale-reference node that still exists in persisted state even though the backing device is no longer actively observed. Use inventory and topology tools to confirm before deleting or dismissing it.

### Role
A Thread role such as leader, router, child, detached, or disabled.

## Radio and link metrics

### RSSI
Received Signal Strength Indicator. More negative values indicate weaker received signal strength.

### LQI
Link Quality Indicator. A higher LQI generally indicates a healthier observed radio link.

### tx_retry
A MAC counter tracking retry attempts during transmission. Sustained growth can indicate interference, congestion, or poor link quality.

### tx_err_cca
A MAC counter tracking clear-channel-assessment failures, often associated with a busy or noisy RF environment.

### parent_change
An attachment-related counter or event indicating the node switched parents. Repeated growth often correlates with instability.

### attach_attempt
A counter tracking attempts to attach or reattach to the Thread mesh.

## Timeline and issue terms

### Canonical event
A normalized event inserted into the SQLite event log from OTBR logs, discovery passes, or other observers.

### Observer outage
A period where the add-on, OTBR observer, or Matter observer could not provide fresh data. Timeline tools surface outage windows so they are not mistaken for mesh behavior.

### Active issue
An open issue raised by the add-on's health logic or background diagnostics. Issues remain open until auto-resolution or explicit closure.

### Assessment finding
A background-diagnostics finding produced by the AI assessment loop. Findings are tracked separately from health issues so operators can confirm whether the AI signal was useful.

## Home Assistant integration terms

### OTBR
OpenThread Border Router. In this project it is usually the Home Assistant add-on whose logs are ingested for canonical Thread events.

### Matter Server
The Home Assistant Matter Server add-on. It provides discovery and device context that can be correlated with Thread node identities.

### Supervisor slug
The Home Assistant Supervisor identifier for an add-on, such as the OTBR add-on slug configured for ingestion.

## References

- Thread Group overview: https://www.threadgroup.org/What-is-Thread
- OpenThread concepts: https://openthread.io/guides/thread-primer
- Matter specification overview: https://csa-iot.org/all-solutions/matter/
- Home Assistant add-ons and Supervisor: https://developers.home-assistant.io/docs/add-ons/
