#pragma once
#ifdef SIMULATION

#include <Arduino.h>
#include <math.h>

/*
 * Simulated sensor readings for Wokwi CI testing.
 *
 * Time compression: 1 real minute = 1 simulated fermentation hour (60x).
 * A complete fermentation curve (0 → peak → finish) plays out in ~35 real minutes.
 *
 * Pressure model: Gaussian activity envelope (peak at sim-hour 24) modulates
 * bubble cycle period. Each cycle is a sawtooth: linear pressure rise to ~80 Pa
 * above baseline, sharp drop when the bubble fires through the airlock.
 *
 * This faithfully reproduces the physical signal the real BMP280 would see:
 * short cycles (high pressure swings) at peak activity, long quiet periods at
 * finish. The backend activity classification logic is fully exercisable.
 */

namespace Sim {

static float _activityEnvelope(float t_hours) {
    // Gaussian: peaks at hour 24, FWHM ~25 hours
    return expf(-powf((t_hours - 24.0f) / 15.0f, 2.0f));
}

// Returns simulated delta_pressure in Pa (vessel headspace above baseline).
float deltaPa() {
    float t_hours = millis() / 60000.0f;       // real minutes → sim hours
    float activity = _activityEnvelope(t_hours);

    // Bubble period: 3 s at peak activity, 300 s when nearly finished (sim-seconds)
    float bubble_period_s = 3.0f + (1.0f - activity) * 297.0f;
    float t_sim_s = t_hours * 3600.0f;
    float cycle_pos = fmodf(t_sim_s, bubble_period_s) / bubble_period_s;  // 0..1

    // Sawtooth: 90% of cycle is pressure rise, 10% is the drop
    float raw = (cycle_pos < 0.9f) ? (cycle_pos / 0.9f) * 80.0f * activity : 0.0f;

    // ±3 Pa sensor noise
    float noise = (float)(random(-300, 300)) / 100.0f;
    return raw + noise;
}

// Ambient temperature: rises ~3°C during peak fermentation (yeast heat).
float tempDHT() {
    float t_hours = millis() / 60000.0f;
    return 22.0f + 3.0f * _activityEnvelope(t_hours);
}

float humidityDHT() {
    float t_hours = millis() / 60000.0f;
    return 58.0f + 10.0f * _activityEnvelope(t_hours);
}

// BMP280 also reads temperature (used as cross-check in real hardware).
float tempBMP() {
    return tempDHT() - 0.5f;  // BMP280 runs slightly cooler than ambient DHT
}

// Absolute pressure (baseline + delta), so delta computation in main.cpp works unchanged.
float pressurePa(float baseline_pa) {
    return baseline_pa + deltaPa();
}

}  // namespace Sim

#endif  // SIMULATION
