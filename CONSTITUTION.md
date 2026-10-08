# Fermentation Monitor — Constitution

## Purpose
Build a low-cost, self-contained IoT system that monitors the active fermentation process
(beer, wine, or bread) in real-time, logging CO₂ off-gassing rate, temperature, and humidity.
The system should surface a clear fermentation curve so the user can know when fermentation is
active, peaking, or complete — without touching or disturbing the vessel.

## Core Values
1. **Offline-first** — all data is stored locally; no cloud account required.
2. **Zero-cost tooling** — no paid services; open-source stack only.
3. **Non-invasive** — sensors sit outside or near the vessel; nothing enters the liquid.
4. **Actionable data** — every metric shown must answer a real question about fermentation health.
5. **Reproducible** — firmware and backend are version-controlled and flashable in < 10 min.

## Users
- Solo home brewer / baker (the builder and primary user).

## Out of Scope
- pH or specific-gravity measurement (invasive; excluded by design).
- Cloud sync or mobile push notifications (zero-cost constraint).
- Multi-vessel support (v1 is single vessel).
- Cloud-based or third-party ML training/inference — the anomaly model is trained offline on a dev machine and its entire forward pass runs on the ESP32 itself; no TensorFlow Lite Micro, Edge Impulse, or hosted inference API is used.

## Success Criteria
- ESP32 reads CO₂ pressure rate, temperature, and humidity every 30 s.
- Data is published over MQTT to a local Python broker/recorder.
- React + Vite dashboard shows a live fermentation curve and alerts when activity drops below threshold.
- Full stack runs on a single laptop + ESP32 with no paid infrastructure.
- On-device anomaly detection flags stuck or contaminated fermentation directly from the pressure sawtooth pattern, with no cloud dependency or ML framework.
