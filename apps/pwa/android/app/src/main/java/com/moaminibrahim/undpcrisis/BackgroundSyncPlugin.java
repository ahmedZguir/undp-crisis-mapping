package com.moaminibrahim.undpcrisis;

import android.content.Context;
import android.content.SharedPreferences;

import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * JS `BackgroundSync` plugin -> WorkManager; sync itself runs in {@link OutboxSyncWorker}.
 * CONTRACT: plugin name and methods must match src/platform/outbox/backgroundSyncPlugin.ts.
 */
@CapacitorPlugin(name = "BackgroundSync")
public class BackgroundSyncPlugin extends Plugin {

    @PluginMethod
    public void configure(PluginCall call) {
        String apiBaseUrl = call.getString("apiBaseUrl");
        if (apiBaseUrl == null || apiBaseUrl.isEmpty()) {
            call.reject("apiBaseUrl is required");
            return;
        }
        Context ctx = getContext();
        SharedPreferences prefs = ctx.getSharedPreferences(
                OutboxSyncScheduler.PREFS, Context.MODE_PRIVATE);
        prefs.edit().putString(OutboxSyncScheduler.PREF_API_BASE, apiBaseUrl).apply();
        call.resolve();
    }

    @PluginMethod
    public void schedule(PluginCall call) {
        OutboxSyncScheduler.schedule(getContext());
        call.resolve();
    }

    @PluginMethod
    public void cancel(PluginCall call) {
        OutboxSyncScheduler.cancel(getContext());
        call.resolve();
    }
}
