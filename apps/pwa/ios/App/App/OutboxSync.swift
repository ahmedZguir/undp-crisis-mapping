import Foundation
import BackgroundTasks
import SQLite3

// Drains the outbox SQLite DB from a BGProcessingTask (no WebView), POSTing rows to {API_BASE}/reports.
// Mirrored in TS (src/lib/outbox-core.ts) — keep in sync; body is pre-built request_data.
// Best-effort: iOS runs BGTasks on its own schedule, no wake-on-reconnect. client_submission_id
// makes racing the foreground flush safe.
enum OutboxSync {
    static let taskIdentifier = "com.moaminibrahim.undpcrisis.outboxsync"

    // CONTRACT: dbFilename must match the JS `${DB_NAME}SQLite.db` (capacitorExecutor.ts).
    private static let apiBaseKey = "undp_bg_sync_api_base"
    private static let dbFilename = "undp-appSQLite.db"

    // Mirror of outbox-core.ts retry policy.
    private static let maxAttempts = 20
    private static let backoffScheduleMs: [Int64] = [5_000, 30_000, 120_000, 600_000, 3_600_000]
    private static let claimLeaseMs: Int64 = 5 * 60_000
    // Mirror of isReady. Binds (now, now - lease).
    private static let readyWhere =
        "((status = 'queued' AND next_attempt_at <= ?) OR (status = 'submitting' AND updated_at < ?))"

    private static let SQLITE_TRANSIENT = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

    static func setApiBaseUrl(_ url: String) {
        UserDefaults.standard.set(url, forKey: apiBaseKey)
    }

    private static func apiBaseUrl() -> String? {
        UserDefaults.standard.string(forKey: apiBaseKey)
    }

    static func scheduleBackgroundTask() {
        let request = BGProcessingTaskRequest(identifier: taskIdentifier)
        request.requiresNetworkConnectivity = true
        request.requiresExternalPower = false
        request.earliestBeginDate = Date(timeIntervalSinceNow: 60)
        do {
            try BGTaskScheduler.shared.submit(request)
        } catch {
            NSLog("[OutboxSync] failed to submit BG task: \(error)")
        }
    }

    static func cancelBackgroundTask() {
        BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: taskIdentifier)
    }

    static func handleBackgroundTask(_ task: BGProcessingTask) {
        scheduleBackgroundTask()

        let queue = OperationQueue()
        let op = BlockOperation { OutboxSync.flush() }
        task.expirationHandler = {
            // A row claimed mid-POST is reclaimed once its lease expires.
            queue.cancelAllOperations()
        }
        op.completionBlock = {
            task.setTaskCompleted(success: !op.isCancelled)
        }
        queue.addOperation(op)
    }

    private static func backoffDelayMs(_ attempt: Int) -> Int64 {
        if attempt < 1 { return 0 }
        let idx = min(attempt - 1, backoffScheduleMs.count - 1)
        return backoffScheduleMs[idx]
    }

    // -1 status == network failure (transient). Mirrors classifyStatus.
    private static func isTransient(_ status: Int) -> Bool {
        if status < 0 { return true }
        if status == 408 || status == 429 { return true }
        return status >= 500
    }

    private static func resolveDbPath() -> String? {
        let fm = FileManager.default
        let lib = fm.urls(for: .libraryDirectory, in: .userDomainMask).first
        let docs = fm.urls(for: .documentDirectory, in: .userDomainMask).first
        // Plugin default is Library/CapacitorDatabase; older configs use Documents or Library.
        var candidates: [URL] = []
        if let lib = lib {
            candidates.append(lib.appendingPathComponent("CapacitorDatabase").appendingPathComponent(dbFilename))
            candidates.append(lib.appendingPathComponent(dbFilename))
        }
        if let docs = docs {
            candidates.append(docs.appendingPathComponent(dbFilename))
        }
        for url in candidates where fm.fileExists(atPath: url.path) {
            return url.path
        }
        return nil
    }

    static func flush() {
        guard let apiBase = apiBaseUrl(), !apiBase.isEmpty else {
            NSLog("[OutboxSync] no apiBaseUrl configured; nothing to do")
            return
        }
        guard let dbPath = resolveDbPath() else {
            NSLog("[OutboxSync] outbox db not present yet; nothing to flush")
            return
        }

        var db: OpaquePointer?
        guard sqlite3_open_v2(dbPath, &db, SQLITE_OPEN_READWRITE, nil) == SQLITE_OK, let db = db else {
            NSLog("[OutboxSync] failed to open db at \(dbPath)")
            return
        }
        defer { sqlite3_close(db) }
        sqlite3_exec(db, "PRAGMA busy_timeout = 3000", nil, nil, nil)

        let now = nowMs()
        // Snapshot rows so the statement isn't held open across HTTP.
        var ready: [(id: String, data: String)] = []
        var stmt: OpaquePointer?
        let sql = "SELECT id, data FROM outbox WHERE \(readyWhere) ORDER BY created_at ASC"
        if sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK {
            sqlite3_bind_int64(stmt, 1, now)
            sqlite3_bind_int64(stmt, 2, now - claimLeaseMs)
            while sqlite3_step(stmt) == SQLITE_ROW {
                let id = String(cString: sqlite3_column_text(stmt, 0))
                let data = String(cString: sqlite3_column_text(stmt, 1))
                ready.append((id, data))
            }
        }
        sqlite3_finalize(stmt)

        for row in ready {
            processRow(db: db, apiBase: apiBase, id: row.id, dataStr: row.data)
        }

        if isOutboxEmpty(db) {
            cancelBackgroundTask()
        }
    }

    private static func nowMs() -> Int64 {
        Int64(Date().timeIntervalSince1970 * 1000)
    }

    private static func processRow(db: OpaquePointer, apiBase: String, id: String, dataStr: String) {
        guard
            let data = dataStr.data(using: .utf8),
            let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            NSLog("[OutboxSync] unparseable outbox row \(id); skipping")
            return
        }

        // Rows enqueued before request_data existed, or queued before a crisis
        // was picked, are left for the foreground path. Not a failure.
        guard let requestData = obj["request_data"] as? [String: Any] else { return }
        let crisisId = requestData["crisis_id"] as? String ?? ""
        if crisisId.isEmpty { return }
        // Absent for description-only reports (photo is optional).
        let photoId = obj["photo_id"] as? String ?? ""
        let hasPhoto = !photoId.isEmpty

        // Atomic CAS claim; 0 rows means the foreground won the race.
        let now = nowMs()
        let claimed = runUpdate(db,
            "UPDATE outbox SET status = 'submitting', updated_at = ? WHERE id = ? AND \(readyWhere)",
            [.int(now), .text(id), .int(now), .int(now - claimLeaseMs)])
        if claimed == 0 { return }

        var photoBytes: Data? = nil
        var mime = "application/octet-stream"
        if hasPhoto {
            // Referenced photo bytes gone: unrecoverable.
            guard let loaded = loadPhoto(db: db, photoId: photoId) else {
                markResult(db: db, id: id, obj: obj, kind: "terminal", error: "photo missing from local storage")
                return
            }
            photoBytes = loaded.0
            mime = loaded.1
        }

        guard let requestBody = try? JSONSerialization.data(withJSONObject: requestData) else {
            markResult(db: db, id: id, obj: obj, kind: "terminal", error: "request_data not serializable")
            return
        }

        let status = postReport(apiBase: apiBase, dataJson: requestBody, photoBytes: photoBytes, mime: mime)

        if status >= 200 && status < 300 {
            _ = runUpdate(db, "DELETE FROM outbox WHERE id = ?", [.text(id)])
            if hasPhoto {
                _ = runUpdate(db, "DELETE FROM photos WHERE id = ?", [.text(photoId)])
            }
            return
        }

        let transient = isTransient(status)
        markResult(db: db, id: id, obj: obj, kind: transient ? "transient" : "terminal", error: "POST failed: \(status)")
    }

    // Mirrors nextOutboxState for the non-success transitions.
    private static func markResult(db: OpaquePointer, id: String, obj: [String: Any], kind: String, error: String) {
        let now = nowMs()
        let attemptCount = (obj["attempt_count"] as? Int) ?? 0
        let nextAttempt = attemptCount + 1
        var status: String
        var nextAttemptAt = (obj["next_attempt_at"] as? Int64) ?? Int64((obj["next_attempt_at"] as? Int) ?? 0)

        if kind == "terminal" {
            status = "failed"
        } else {
            let isMax = nextAttempt >= maxAttempts
            status = isMax ? "failed" : "queued"
            if !isMax { nextAttemptAt = now + backoffDelayMs(nextAttempt) }
        }

        var updated = obj
        updated["status"] = status
        updated["attempt_count"] = nextAttempt
        updated["next_attempt_at"] = nextAttemptAt
        updated["last_error"] = error
        updated["updated_at"] = now

        guard let json = try? JSONSerialization.data(withJSONObject: updated),
              let jsonStr = String(data: json, encoding: .utf8) else { return }

        _ = runUpdate(db,
            "UPDATE outbox SET data = ?, status = ?, updated_at = ?, next_attempt_at = ? WHERE id = ?",
            [.text(jsonStr), .text(status), .int(now), .int(nextAttemptAt), .text(id)])
    }

    private static func loadPhoto(db: OpaquePointer, photoId: String) -> (Data, String)? {
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_prepare_v2(db, "SELECT data_url FROM photos WHERE id = ?", -1, &stmt, nil) == SQLITE_OK else {
            return nil
        }
        sqlite3_bind_text(stmt, 1, (photoId as NSString).utf8String, -1, SQLITE_TRANSIENT)
        guard sqlite3_step(stmt) == SQLITE_ROW else { return nil }
        let dataUrl = String(cString: sqlite3_column_text(stmt, 0))
        guard let comma = dataUrl.firstIndex(of: ",") else { return nil }
        let meta = String(dataUrl[dataUrl.startIndex..<comma])
        var mime = "application/octet-stream"
        if let colon = meta.firstIndex(of: ":"), let semi = meta.firstIndex(of: ";"), colon < semi {
            mime = String(meta[meta.index(after: colon)..<semi])
        }
        let b64 = String(dataUrl[dataUrl.index(after: comma)...])
        guard let bytes = Data(base64Encoded: b64) else { return nil }
        return (bytes, mime)
    }

    private static func isOutboxEmpty(_ db: OpaquePointer) -> Bool {
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_prepare_v2(db, "SELECT COUNT(*) FROM outbox WHERE status != 'failed'", -1, &stmt, nil) == SQLITE_OK else {
            return false
        }
        return sqlite3_step(stmt) == SQLITE_ROW && sqlite3_column_int(stmt, 0) == 0
    }

    private enum Bind {
        case text(String)
        case int(Int64)
    }

    private static func runUpdate(_ db: OpaquePointer, _ sql: String, _ binds: [Bind]) -> Int {
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return 0 }
        for (i, b) in binds.enumerated() {
            let idx = Int32(i + 1)
            switch b {
            case .text(let s): sqlite3_bind_text(stmt, idx, (s as NSString).utf8String, -1, SQLITE_TRANSIENT)
            case .int(let n): sqlite3_bind_int64(stmt, idx, n)
            }
        }
        guard sqlite3_step(stmt) == SQLITE_DONE else { return 0 }
        return Int(sqlite3_changes(db))
    }

    private static func extensionForMime(_ mime: String) -> String {
        switch mime {
        case "image/webp": return "webp"
        case "image/jpeg": return "jpg"
        case "image/png": return "png"
        default: return "bin"
        }
    }

    /// Synchronous multipart POST of `data` plus optional `photo`; returns status or -1 on network failure.
    private static func postReport(apiBase: String, dataJson: Data, photoBytes: Data?, mime: String) -> Int {
        guard let url = URL(string: apiBase + "/reports") else { return -1 }
        let boundary = "----undpOutboxBoundary\(UUID().uuidString)"
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        var body = Data()
        func append(_ s: String) { body.append(s.data(using: .utf8)!) }
        let crlf = "\r\n"

        append("--\(boundary)\(crlf)")
        append("Content-Disposition: form-data; name=\"data\"\(crlf)")
        append("Content-Type: application/json\(crlf)\(crlf)")
        body.append(dataJson)
        append(crlf)

        if let photoBytes = photoBytes {
            append("--\(boundary)\(crlf)")
            append("Content-Disposition: form-data; name=\"photo\"; filename=\"photo.\(extensionForMime(mime))\"\(crlf)")
            append("Content-Type: \(mime)\(crlf)\(crlf)")
            body.append(photoBytes)
            append(crlf)
        }

        append("--\(boundary)--\(crlf)")

        var statusCode = -1
        let semaphore = DispatchSemaphore(value: 0)
        let task = URLSession.shared.uploadTask(with: request, from: body) { _, response, _ in
            if let http = response as? HTTPURLResponse { statusCode = http.statusCode }
            semaphore.signal()
        }
        task.resume()
        // Bound the wait so we never block a BGTask past its window.
        _ = semaphore.wait(timeout: .now() + 35)
        return statusCode
    }
}
