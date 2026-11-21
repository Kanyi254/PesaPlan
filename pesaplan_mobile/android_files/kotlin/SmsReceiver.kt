package com.pesaplan.pesaplan

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.provider.Telephony
import io.flutter.FlutterInjector
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.embedding.engine.dart.DartExecutor
import io.flutter.plugin.common.MethodChannel
import io.flutter.plugins.GeneratedPluginRegistrant

/**
 * Catches incoming M-Pesa SMS even when the app is closed. The message is written to a small on-disk queue first
 * (so it can never be lost), then a short-lived background Flutter engine saves it and syncs it to the server.
 */
class SmsReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Telephony.Sms.Intents.SMS_RECEIVED_ACTION) return
        val msgs = Telephony.Sms.Intents.getMessagesFromIntent(intent) ?: return
        val bySender = LinkedHashMap<String, StringBuilder>()
        var date = System.currentTimeMillis()
        for (m in msgs) {
            val a = m.originatingAddress ?: ""
            bySender.getOrPut(a) { StringBuilder() }.append(m.messageBody ?: "")
            date = m.timestampMillis
        }
        var queued = false
        for ((addr, body) in bySender) {
            if (SmsQueue.isMpesa(addr)) {
                SmsQueue.add(context, body.toString(), date)
                queued = true
            }
        }
        if (queued) startBackgroundEngine(context.applicationContext, goAsync())
    }

    private fun startBackgroundEngine(app: Context, pending: PendingResult) {
        Handler(Looper.getMainLooper()).post {
            var finished = false
            var engine: FlutterEngine? = null
            val finish = {
                if (!finished) {
                    finished = true
                    try { engine?.destroy() } catch (e: Exception) { }
                    try { pending.finish() } catch (e: Exception) { }
                }
            }
            try {
                val loader = FlutterInjector.instance().flutterLoader()
                loader.startInitialization(app)
                loader.ensureInitializationComplete(app, null)
                val eng = FlutterEngine(app)
                engine = eng
                GeneratedPluginRegistrant.registerWith(eng)
                MethodChannel(eng.dartExecutor.binaryMessenger, "pesaplan/sms").setMethodCallHandler { call, result ->
                    when (call.method) {
                        "peekQueue" -> result.success(SmsQueue.peek(app))
                        "ackQueue" -> {
                            val ids = (call.arguments as? List<*>)?.mapNotNull { (it as? Number)?.toLong() } ?: emptyList()
                            SmsQueue.ack(app, ids)
                            result.success(true)
                        }
                        "done" -> {
                            result.success(true)
                            finish()
                        }
                        else -> result.notImplemented()
                    }
                }
                eng.dartExecutor.executeDartEntrypoint(
                    DartExecutor.DartEntrypoint(loader.findAppBundlePath(), "smsBackground")
                )
                Handler(Looper.getMainLooper()).postDelayed({ finish() }, 25000)
            } catch (e: Exception) {
                finish() // the message is still queued; the app picks it up when opened
            }
        }
    }
}
