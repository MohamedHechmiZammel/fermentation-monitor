/*
 * host_test.c -- host-side cross-check of bubble_detector.h + autoencoder.h
 * against the Python reference (model_training/features.py + quantize.py).
 *
 * Build & run (see tools/run_host_test.sh, which also generates the inputs):
 *
 *     gcc -std=c99 -O2 -I../src -o host_test host_test.c -lm
 *     ./host_test samples.csv > c_windows.csv
 *
 * Input  CSV: one line per 10 Hz sample, "delta_pa,temp_c" (no header).
 * Output CSV: one line per emitted window,
 *             "t_s,bubble_rate,mean_interval,std_interval,interval_trend,
 *              temp_delta,recon_error"
 * plus a summary on stderr.
 *
 * Neither header pulls in Arduino, so this compiles as plain C99.
 */

#include <stdio.h>
#include <stdlib.h>

#include "bubble_detector.h"
#include "autoencoder.h"

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <samples.csv>\n", argv[0]);
        return 2;
    }
    FILE *f = fopen(argv[1], "r");
    if (!f) { perror(argv[1]); return 2; }

    BubbleDetector det;
    FeatureAggregator agg;
    bd_init(&det, AE_SAMPLE_RATE_HZ);
    fa_reset(&agg);

    long   n_samples = 0, n_bubbles = 0, n_intervals = 0;
    long   n_windows = 0, n_over = 0;
    int    anomaly_run = 0;
    long   first_flag_window = -1;

    printf("t_s,bubble_rate,mean_interval,std_interval,interval_trend,"
           "temp_delta,recon_error\n");

    double delta_pa, temp_c;
    while (fscanf(f, "%lf,%lf", &delta_pa, &temp_c) == 2) {
        bool   fired, have_interval;
        double interval_s;
        bd_update(&det, (float)delta_pa, &fired, &have_interval, &interval_s);
        if (fired) {
            n_bubbles++;
            if (have_interval) n_intervals++;
            fa_push_bubble(&agg, have_interval, interval_s);
        }
        fa_push_temp(&agg, (float)temp_c);

        float feats[AE_N_INPUT];
        if (fa_maybe_emit(&agg, det.t, feats) == FA_EMIT) {
            float err = ae_score(feats);
            n_windows++;
            if (err > AE_RECON_THRESHOLD) {
                n_over++;
                if (anomaly_run < AE_ANOMALY_N_WINDOWS) anomaly_run++;
                if (anomaly_run >= AE_ANOMALY_N_WINDOWS && first_flag_window < 0)
                    first_flag_window = n_windows;
            } else {
                anomaly_run = 0;
            }
            printf("%.17g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g\n",
                   det.t, feats[0], feats[1], feats[2], feats[3], feats[4], err);
        }
        n_samples++;
    }
    fclose(f);

    fprintf(stderr,
            "samples=%ld bubbles=%ld intervals=%ld windows=%ld "
            "over_threshold=%ld first_flagged_window=%ld\n",
            n_samples, n_bubbles, n_intervals, n_windows, n_over,
            first_flag_window);
    return 0;
}
