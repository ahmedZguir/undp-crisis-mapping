package com.moaminibrahim.undpcrisis;

import android.content.Context;

import androidx.work.BackoffPolicy;
import androidx.work.Constraints;
import androidx.work.ExistingPeriodicWorkPolicy;
import androidx.work.ExistingWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.OneTimeWorkRequest;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;

import java.util.concurrent.TimeUnit;

/**
 * One-shot (fires on reconnect while the WebView is suspended) + 15-min periodic safety net.
 * Survive process death (not force-stop); the worker cancels both once the queue drains.
 */
final class OutboxSyncScheduler {
    private OutboxSyncScheduler() {}

    // CONTRACT: written by BackgroundSyncPlugin.configure(), read by OutboxSyncWorker.
    static final String PREFS = "undp_bg_sync";
    static final String PREF_API_BASE = "apiBaseUrl";

    private static final String UNIQUE_ONESHOT = "undp-outbox-sync-oneshot";
    private static final String UNIQUE_PERIODIC = "undp-outbox-sync-periodic";

    // @capacitor-community/sqlite names connection "undp-app" as <db>SQLite.db.
    static final String DB_FILENAME = "undp-appSQLite.db";

    private static final long BACKOFF_SECONDS = 30;

    static void schedule(Context context) {
        Constraints constraints = new Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build();

        WorkManager wm = WorkManager.getInstance(context);

        // OutboxSyncWorker caps retries at MAX_WORK_RETRIES (~15 min at this backoff).
        OneTimeWorkRequest oneShot = new OneTimeWorkRequest.Builder(OutboxSyncWorker.class)
                .setConstraints(constraints)
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, BACKOFF_SECONDS, TimeUnit.SECONDS)
                .build();
        wm.enqueueUniqueWork(UNIQUE_ONESHOT, ExistingWorkPolicy.KEEP, oneShot);

        PeriodicWorkRequest periodic = new PeriodicWorkRequest.Builder(
                OutboxSyncWorker.class, 15, TimeUnit.MINUTES)
                .setConstraints(constraints)
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, BACKOFF_SECONDS, TimeUnit.SECONDS)
                .build();
        wm.enqueueUniquePeriodicWork(UNIQUE_PERIODIC, ExistingPeriodicWorkPolicy.KEEP, periodic);
    }

    static void cancel(Context context) {
        WorkManager wm = WorkManager.getInstance(context);
        wm.cancelUniqueWork(UNIQUE_ONESHOT);
        wm.cancelUniqueWork(UNIQUE_PERIODIC);
    }
}
