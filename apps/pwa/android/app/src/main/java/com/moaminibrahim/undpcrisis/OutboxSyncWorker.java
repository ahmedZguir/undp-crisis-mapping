package com.moaminibrahim.undpcrisis;

import android.content.Context;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteDatabaseLockedException;
import android.database.sqlite.SQLiteException;
import android.util.Base64;
import android.util.Log;

import androidx.annotation.NonNull;
import androidx.work.Worker;
import androidx.work.WorkerParameters;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.DataOutputStream;
import java.io.File;
import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * Drains the outbox SQLite DB with no WebView running, POSTing each row to {API_BASE}/reports.
 * Mirrored in TS (src/lib/outbox-core.ts) — keep in sync; body is pre-built request_data.
 * Racing the foreground flush is safe: client_submission_id makes duplicate POSTs return 200.
 */
public class OutboxSyncWorker extends Worker {
    private static final String TAG = "OutboxSyncWorker";

    // Mirror of outbox-core.ts retry policy.
    // CONTRACT: outbox-core.ts is the source of truth; keep these values identical.
    private static final int MAX_OUTBOX_ATTEMPTS = 20;
    private static final long[] BACKOFF_SCHEDULE_MS = {5_000, 30_000, 120_000, 600_000, 3_600_000};
    private static final long CLAIM_LEASE_MS = 5 * 60_000;
    // Mirror of isReady. Binds (now, now - lease).
    private static final String READY_WHERE =
            "((status = 'queued' AND next_attempt_at <= ?) OR (status = 'submitting' AND updated_at < ?))";

    // Rows carry their own backoff, so WorkManager only needs to wake us a few times; after that
    // the periodic request takes over.
    static final int MAX_WORK_RETRIES = 5;

    private static final int CONNECT_TIMEOUT_MS = 30_000;
    private static final int READ_TIMEOUT_MS = 30_000;

    public OutboxSyncWorker(@NonNull Context context, @NonNull WorkerParameters params) {
        super(context, params);
    }

    private static long backoffDelayMs(int attempt) {
        if (attempt < 1) return 0;
        int idx = Math.min(attempt - 1, BACKOFF_SCHEDULE_MS.length - 1);
        return BACKOFF_SCHEDULE_MS[idx];
    }

    // 408/429/5xx are transient; a network failure is signalled as status -1
    // (transient); every other code is terminal. Mirrors classifyStatus.
    private static boolean isTransient(int status) {
        if (status < 0) return true;
        if (status == 408 || status == 429) return true;
        return status >= 500;
    }

    static boolean shouldRetry(int runAttemptCount) {
        return runAttemptCount < MAX_WORK_RETRIES;
    }

    private Result retryCapped() {
        if (shouldRetry(getRunAttemptCount())) return Result.retry();
        Log.w(TAG, "retry cap reached after " + getRunAttemptCount() + " run(s); waiting for periodic run");
        return Result.success();
    }

    @NonNull
    @Override
    public Result doWork() {
        Context ctx = getApplicationContext();
        SharedPreferences prefs = ctx.getSharedPreferences(OutboxSyncScheduler.PREFS, Context.MODE_PRIVATE);
        String apiBase = prefs.getString(OutboxSyncScheduler.PREF_API_BASE, null);
        if (apiBase == null || apiBase.isEmpty()) {
            Log.w(TAG, "no apiBaseUrl configured; nothing to do");
            return Result.success();
        }

        File dbFile = ctx.getDatabasePath(OutboxSyncScheduler.DB_FILENAME);
        if (!dbFile.exists()) {
            Log.i(TAG, "outbox db not present yet; nothing to flush");
            return Result.success();
        }

        SQLiteDatabase db = null;
        boolean sawTransient = false;
        try {
            db = SQLiteDatabase.openDatabase(
                    dbFile.getAbsolutePath(), null, SQLiteDatabase.OPEN_READWRITE);
            // Tolerate a momentary lock from the live foreground connection.
            db.execSQL("PRAGMA busy_timeout = 3000");

            long now = System.currentTimeMillis();
            Cursor cursor = db.rawQuery(
                    "SELECT id, data FROM outbox WHERE " + READY_WHERE + " ORDER BY created_at ASC",
                    new String[]{Long.toString(now), Long.toString(now - CLAIM_LEASE_MS)});

            Log.i(TAG, "doWork: started, apiBase=" + apiBase + ", " + cursor.getCount() + " row(s) ready");
            while (cursor.moveToNext()) {
                String id = cursor.getString(0);
                String dataStr = cursor.getString(1);
                if (processRow(db, apiBase, id, dataStr)) {
                    sawTransient = true;
                }
            }
            cursor.close();

            // Stop waking for an empty queue; re-armed on the next enqueue.
            if (isOutboxEmpty(db)) {
                OutboxSyncScheduler.cancel(ctx);
            }
        } catch (SQLiteDatabaseLockedException e) {
            // The foreground connection held the lock past busy_timeout.
            Log.w(TAG, "doWork: database locked", e);
            return retryCapped();
        } catch (SQLiteException e) {
            // Missing table, corrupt file, schema drift: permanent.
            Log.e(TAG, "doWork: database error", e);
            return Result.failure();
        } finally {
            if (db != null) db.close();
        }

        return sawTransient ? retryCapped() : Result.success();
    }

    /** Returns true if this row hit a transient failure (caller schedules a retry). */
    private boolean processRow(SQLiteDatabase db, String apiBase, String id, String dataStr) {
        JSONObject row;
        JSONObject requestData;
        String photoId;
        try {
            row = new JSONObject(dataStr);
            requestData = row.optJSONObject("request_data");
            // Absent for description-only reports (photo is optional).
            photoId = row.isNull("photo_id") ? null : row.optString("photo_id", null);
        } catch (JSONException e) {
            Log.w(TAG, "unparseable outbox row " + id + "; skipping", e);
            return false;
        }

        // Rows enqueued before request_data existed, or queued before a crisis
        // was picked, are left for the foreground path. Not a failure.
        if (requestData == null) return false;
        String crisisId = requestData.optString("crisis_id", "");
        if (crisisId.isEmpty()) return false;

        // Atomic CAS claim; 0 rows means the foreground won the race.
        long now = System.currentTimeMillis();
        int claimed = runUpdate(db,
                "UPDATE outbox SET status = 'submitting', updated_at = ? WHERE id = ? AND " + READY_WHERE,
                new Object[]{now, id, now, now - CLAIM_LEASE_MS});
        if (claimed == 0) {
            Log.i(TAG, "row " + id + ": claim lost/skipped");
            return false;
        }
        Log.i(TAG, "row " + id + ": claimed, POSTing to " + apiBase + "/reports");

        byte[] photoBytes = null;
        String mime = "application/octet-stream";
        boolean hasPhoto = photoId != null && !photoId.isEmpty();
        if (hasPhoto) {
            Cursor pc = db.rawQuery("SELECT data_url FROM photos WHERE id = ?", new String[]{photoId});
            try {
                if (pc.moveToNext()) {
                    String dataUrl = pc.getString(0);
                    int comma = dataUrl.indexOf(',');
                    if (comma > 0) {
                        String meta = dataUrl.substring(0, comma);
                        int colon = meta.indexOf(':');
                        int semi = meta.indexOf(';');
                        if (colon >= 0 && semi > colon) mime = meta.substring(colon + 1, semi);
                        try {
                            photoBytes = Base64.decode(dataUrl.substring(comma + 1), Base64.DEFAULT);
                        } catch (IllegalArgumentException e) {
                            // Treated as missing below.
                            Log.w(TAG, "row " + id + ": photo data unreadable", e);
                        }
                    }
                }
            } finally {
                pc.close();
            }

            // Referenced photo bytes gone or unreadable: unrecoverable.
            if (photoBytes == null) {
                markResult(db, id, row, "terminal", "photo missing from local storage");
                return false;
            }
        }

        int status;
        try {
            status = postReport(apiBase, requestData.toString(), photoBytes, mime);
        } catch (IOException e) {
            Log.w(TAG, "row " + id + ": POST threw (network unreachable? VPN down?): " + e, e);
            status = -1; // network failure → transient
        }
        Log.i(TAG, "row " + id + ": HTTP " + status + (status < 0 ? " (network failure)" : ""));

        if (status >= 200 && status < 300) {
            runUpdate(db, "DELETE FROM outbox WHERE id = ?", new Object[]{id});
            if (hasPhoto) {
                runUpdate(db, "DELETE FROM photos WHERE id = ?", new Object[]{photoId});
            }
            return false;
        }

        boolean transient_ = isTransient(status);
        markResult(db, id, row, transient_ ? "transient" : "terminal", "POST failed: " + status);
        return transient_;
    }

    /** Mirrors nextOutboxState for the non-success transitions. */
    private void markResult(SQLiteDatabase db, String id, JSONObject row, String kind, String error) {
        try {
            long now = System.currentTimeMillis();
            int attemptCount = row.optInt("attempt_count", 0);
            int nextAttempt = attemptCount + 1;
            String status;
            long nextAttemptAt = row.optLong("next_attempt_at", 0);

            if ("terminal".equals(kind)) {
                status = "failed";
            } else { // transient
                boolean isMax = nextAttempt >= MAX_OUTBOX_ATTEMPTS;
                status = isMax ? "failed" : "queued";
                if (!isMax) nextAttemptAt = now + backoffDelayMs(nextAttempt);
            }

            row.put("status", status);
            row.put("attempt_count", nextAttempt);
            row.put("next_attempt_at", nextAttemptAt);
            row.put("last_error", error);
            row.put("updated_at", now);

            runUpdate(db,
                    "UPDATE outbox SET data = ?, status = ?, updated_at = ?, next_attempt_at = ? WHERE id = ?",
                    new Object[]{row.toString(), status, now, nextAttemptAt, id});
        } catch (JSONException e) {
            Log.e(TAG, "markResult failed for " + id, e);
        }
    }

    private boolean isOutboxEmpty(SQLiteDatabase db) {
        Cursor c = db.rawQuery("SELECT COUNT(*) FROM outbox WHERE status != 'failed'", null);
        try {
            return c.moveToNext() && c.getInt(0) == 0;
        } finally {
            c.close();
        }
    }

    private int runUpdate(SQLiteDatabase db, String sql, Object[] args) {
        android.database.sqlite.SQLiteStatement stmt = db.compileStatement(sql);
        try {
            for (int i = 0; i < args.length; i++) {
                Object a = args[i];
                int idx = i + 1;
                if (a instanceof Long) stmt.bindLong(idx, (Long) a);
                else if (a instanceof Integer) stmt.bindLong(idx, ((Integer) a).longValue());
                else if (a == null) stmt.bindNull(idx);
                else stmt.bindString(idx, a.toString());
            }
            return stmt.executeUpdateDelete();
        } finally {
            stmt.close();
        }
    }

    private static String extensionForMime(String mime) {
        if ("image/webp".equals(mime)) return "webp";
        if ("image/jpeg".equals(mime)) return "jpg";
        if ("image/png".equals(mime)) return "png";
        return "bin";
    }

    /** Multipart POST of `data` plus optional `photo`; returns the HTTP status. */
    private int postReport(String apiBase, String dataJson, byte[] photoBytes, String mime) throws IOException {
        String boundary = "----undpOutboxBoundary" + System.currentTimeMillis();
        String crlf = "\r\n";

        URL url = new URL(apiBase + "/reports");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        try {
            conn.setConnectTimeout(CONNECT_TIMEOUT_MS);
            conn.setReadTimeout(READ_TIMEOUT_MS);
            conn.setDoOutput(true);
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);

            DataOutputStream out = new DataOutputStream(conn.getOutputStream());
            try {
                StringBuilder head = new StringBuilder();
                head.append("--").append(boundary).append(crlf);
                head.append("Content-Disposition: form-data; name=\"data\"").append(crlf);
                head.append("Content-Type: application/json").append(crlf).append(crlf);
                head.append(dataJson).append(crlf);
                if (photoBytes != null) {
                    String filename = "photo." + extensionForMime(mime);
                    head.append("--").append(boundary).append(crlf);
                    head.append("Content-Disposition: form-data; name=\"photo\"; filename=\"")
                            .append(filename).append("\"").append(crlf);
                    head.append("Content-Type: ").append(mime).append(crlf).append(crlf);
                }
                out.write(head.toString().getBytes(StandardCharsets.UTF_8));

                if (photoBytes != null) {
                    out.write(photoBytes);
                    out.write(crlf.getBytes(StandardCharsets.UTF_8));
                }

                out.write(("--" + boundary + "--" + crlf).getBytes(StandardCharsets.UTF_8));
                out.flush();
            } finally {
                out.close();
            }
            return conn.getResponseCode();
        } finally {
            conn.disconnect();
        }
    }
}
