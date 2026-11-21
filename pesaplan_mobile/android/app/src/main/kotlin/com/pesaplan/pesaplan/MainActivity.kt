package com.pesaplan.pesaplan

import android.Manifest
import android.content.pm.PackageManager
import android.net.Uri
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private var pendingPermission: MethodChannel.Result? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "pesaplan/sms").setMethodCallHandler { call, result ->
            when (call.method) {
                "hasPermission" -> result.success(hasSms())
                "requestPermission" -> {
                    if (hasSms()) {
                        result.success(true)
                    } else {
                        pendingPermission = result
                        requestPermissions(arrayOf(Manifest.permission.READ_SMS, Manifest.permission.RECEIVE_SMS), 4711)
                    }
                }
                "readInbox" -> {
                    val since = (call.argument<Number>("since") ?: 0).toLong()
                    try {
                        result.success(readInbox(since))
                    } catch (e: Exception) {
                        result.error("sms", e.message, null)
                    }
                }
                "peekQueue" -> result.success(SmsQueue.peek(applicationContext))
                "ackQueue" -> {
                    val ids = (call.arguments as? List<*>)?.mapNotNull { (it as? Number)?.toLong() } ?: emptyList()
                    SmsQueue.ack(applicationContext, ids)
                    result.success(true)
                }
                "done" -> result.success(true)
                else -> result.notImplemented()
            }
        }
    }

    private fun hasSms(): Boolean =
        checkSelfPermission(Manifest.permission.READ_SMS) == PackageManager.PERMISSION_GRANTED &&
            checkSelfPermission(Manifest.permission.RECEIVE_SMS) == PackageManager.PERMISSION_GRANTED

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 4711) {
            pendingPermission?.success(grantResults.isNotEmpty() && grantResults.all { it == PackageManager.PERMISSION_GRANTED })
            pendingPermission = null
        }
    }

    private fun readInbox(since: Long): List<Map<String, Any>> {
        val out = ArrayList<Map<String, Any>>()
        val cur = contentResolver.query(
            Uri.parse("content://sms/inbox"), arrayOf("address", "body", "date"),
            "date >= ?", arrayOf(since.toString()), "date ASC"
        )
        cur?.use {
            while (it.moveToNext()) {
                val addr = it.getString(0) ?: ""
                if (!SmsQueue.isMpesa(addr)) continue
                out.add(mapOf("address" to addr, "body" to (it.getString(1) ?: ""), "date" to it.getLong(2)))
            }
        }
        return out
    }
}
