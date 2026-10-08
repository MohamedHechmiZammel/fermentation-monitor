/*
 * sim_test.cpp -- host harness for firmware/src/sim_sensors.h.
 *
 * Compiles the REAL simulator header (via tools/arduino_shim/Arduino.h),
 * pulls samples out of it exactly as main.cpp's serviceSampler() does, and
 * pushes them through the real bubble_detector.h + autoencoder.h. This is what
 * validates the phase-accumulator fix and the SIM_PROFILE trajectories without
 * a Wokwi token.
 *
 *     g++ -std=c++11 -O2 -DSIMULATION -DSIM_PROFILE=STUCK \
 *         -I../src -Iarduino_shim -o sim_test sim_test.cpp -lm
 *     ./sim_test 35            # 35 fermentation hours
 *
 * stdout: "hour,bubbles,mean_period_s" per fermentation hour.
 * stderr: totals + anomaly outcome.
 */

#include <cstdio>
#include <cstdlib>

#include "sim_sensors.h"
#include "bubble_detector.h"
#include "autoencoder.h"

int main(int argc, char **argv) {
    double hours = (argc > 1) ? atof(argv[1]) : 35.0;
    long   n = (long)(hours * 3600.0 * AE_SAMPLE_RATE_HZ);

    BubbleDetector det;
    FeatureAggregator agg;
    bd_init(&det, AE_SAMPLE_RATE_HZ);
    fa_reset(&agg);

    int    n_hours = (int)hours + 1;
    long  *per_hour = (long *)calloc(n_hours, sizeof(long));
    double *sum_int = (double *)calloc(n_hours, sizeof(double));

    long  bubbles = 0, windows = 0, over = 0;
    int   run = 0;
    double first_flag_h = -1.0;
    float  min_dp = 1e9f, max_dp = -1e9f;

    for (long i = 0; i < n; i++) {
        Sim::advance();
        float dp = Sim::deltaPa();
        if (dp < min_dp) min_dp = dp;
        if (dp > max_dp) max_dp = dp;

        bool   fired, have_interval;
        double interval_s;
        bd_update(&det, dp, &fired, &have_interval, &interval_s);
        int h = (int)(det.t / 3600.0);
        if (h < 0) h = 0;
        if (h >= n_hours) h = n_hours - 1;
        if (fired) {
            bubbles++;
            per_hour[h]++;
            if (have_interval) sum_int[h] += interval_s;
            fa_push_bubble(&agg, have_interval, interval_s);
        }
        fa_push_temp(&agg, Sim::tempDHT());

        float feats[AE_N_INPUT];
        if (fa_maybe_emit(&agg, det.t, feats) == FA_EMIT) {
            float err = ae_score(feats);
            windows++;
            if (err > AE_RECON_THRESHOLD) {
                over++;
                if (run < AE_ANOMALY_N_WINDOWS) run++;
                if (run >= AE_ANOMALY_N_WINDOWS && first_flag_h < 0.0)
                    first_flag_h = det.t / 3600.0;
            } else {
                run = 0;
            }
        }
    }

    printf("hour,bubbles,mean_interval_s\n");
    for (int h = 0; h < (int)hours; h++) {
        double mi = per_hour[h] ? sum_int[h] / (double)per_hour[h] : 0.0;
        printf("%d,%ld,%.2f\n", h, per_hour[h], mi);
    }

    fprintf(stderr,
            "profile_id=%d samples=%ld delta_pa[%.2f..%.2f] bubbles=%ld "
            "windows=%ld over=%ld first_flag_h=%.2f\n",
            SIM_PROFILE_ID, n, min_dp, max_dp, bubbles, windows, over,
            first_flag_h);
    free(per_hour);
    free(sum_int);
    return 0;
}
