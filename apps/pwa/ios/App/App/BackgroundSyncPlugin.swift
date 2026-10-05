import Foundation
import Capacitor

// JS `BackgroundSync` plugin -> BGTaskScheduler; sync runs in OutboxSync, handler registered in AppDelegate.
// CONTRACT: plugin name and methods must match src/platform/outbox/backgroundSyncPlugin.ts.
@objc(BackgroundSyncPlugin)
public class BackgroundSyncPlugin: CAPPlugin, CAPBridgedPlugin {
    // Capacitor 8 (SPM) needs CAPBridgedPlugin, not CAP_PLUGIN; jsName must match the JS
    // registerPlugin name or every call rejects with UNIMPLEMENTED.
    public let identifier = "BackgroundSyncPlugin"
    public let jsName = "BackgroundSync"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "configure", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "schedule", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "cancel", returnType: CAPPluginReturnPromise),
    ]

    @objc func configure(_ call: CAPPluginCall) {
        guard let apiBaseUrl = call.getString("apiBaseUrl"), !apiBaseUrl.isEmpty else {
            call.reject("apiBaseUrl is required")
            return
        }
        OutboxSync.setApiBaseUrl(apiBaseUrl)
        call.resolve()
    }

    @objc func schedule(_ call: CAPPluginCall) {
        OutboxSync.scheduleBackgroundTask()
        call.resolve()
    }

    @objc func cancel(_ call: CAPPluginCall) {
        OutboxSync.cancelBackgroundTask()
        call.resolve()
    }
}
