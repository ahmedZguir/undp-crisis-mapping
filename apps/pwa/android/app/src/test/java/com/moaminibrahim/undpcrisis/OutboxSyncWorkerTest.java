package com.moaminibrahim.undpcrisis;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class OutboxSyncWorkerTest {
    @Test
    public void retriesUnderTheCap() {
        for (int attempt = 0; attempt < OutboxSyncWorker.MAX_WORK_RETRIES; attempt++) {
            assertTrue("attempt " + attempt, OutboxSyncWorker.shouldRetry(attempt));
        }
    }

    @Test
    public void stopsRetryingAtTheCap() {
        assertFalse(OutboxSyncWorker.shouldRetry(OutboxSyncWorker.MAX_WORK_RETRIES));
        assertFalse(OutboxSyncWorker.shouldRetry(OutboxSyncWorker.MAX_WORK_RETRIES + 10));
    }
}
