package com.pesaplan.pesaplan

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/** Messages caught by the receiver, kept on disk until the Dart side has saved them. */
object SmsQueue {
    private const val PREFS = "pesaplan_sms"
    private const val KEY = "queue"
    private var counter = 0L

    fun isMpesa(address: String): Boolean =
        address.filter { it.isLetter() }.equals("MPESA", ignoreCase = true)

    @Synchronized
    fun add(ctx: Context, body: String, date: Long) {
        val p = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val arr = JSONArray(p.getString(KEY, "[]"))
        counter += 1
        val o = JSONObject()
        o.put("id", System.currentTimeMillis() * 1000 + (counter % 1000))
        o.put("body", body)
        o.put("date", date)
        arr.put(o)
        p.edit().putString(KEY, arr.toString()).apply()
    }

    @Synchronized
    fun peek(ctx: Context): List<Map<String, Any>> {
        val arr = JSONArray(ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, "[]"))
        val out = ArrayList<Map<String, Any>>()
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            out.add(mapOf("id" to o.getLong("id"), "body" to o.getString("body"), "date" to o.getLong("date")))
        }
        return out
    }

    @Synchronized
    fun ack(ctx: Context, ids: List<Long>) {
        val p = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val arr = JSONArray(p.getString(KEY, "[]"))
        val keep = JSONArray()
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            if (!ids.contains(o.getLong("id"))) keep.put(o)
        }
        p.edit().putString(KEY, keep.toString()).apply()
    }
}
