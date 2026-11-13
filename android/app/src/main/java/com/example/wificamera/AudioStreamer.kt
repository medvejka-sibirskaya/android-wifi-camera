package com.example.wificamera

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioTimestamp
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.SystemClock
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit

/** PCM16 mono 16 kHz. Explicit opt-in, bounded queue, no background recording. */
class AudioStreamer(private val context: Context, private val url: URL,
                    private val code: String, private val session: String,
                    private val onError: (String) -> Unit) {
    private data class Packet(val data: ByteArray, val captured: Long)
    private val packets = ArrayBlockingQueue<Packet>(4)
    @Volatile private var running = true
    @Volatile private var recorder: AudioRecord? = null
    @Volatile private var request: HttpURLConnection? = null

    fun start() {
        Thread({ capture() }, "phone-microphone").start()
        Thread({ send() }, "microphone-upload").start()
    }
    fun stop() {
        running = false
        request?.disconnect()
        try { recorder?.stop() } catch (_: IllegalStateException) { }
        packets.clear()
    }
    private fun fail(message: String) {
        if (running) { stop(); onError(message) }
    }
    @SuppressLint("MissingPermission") // Permission checked immediately before start.
    private fun capture() {
        var active: AudioRecord? = null
        try {
            val minimum = AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            require(minimum > 0)
            active = AudioRecord.Builder().setAudioSource(MediaRecorder.AudioSource.MIC)
                .setAudioFormat(AudioFormat.Builder().setSampleRate(16000)
                    .setChannelMask(AudioFormat.CHANNEL_IN_MONO).setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                .setBufferSizeInBytes(maxOf(minimum, 12800)).build()
            require(active.state == AudioRecord.STATE_INITIALIZED)
            recorder = active
            if (!running) return
            active.startRecording()
            val buffer = ByteArray(3200)
            val clock = AudioTimestamp()
            var readFrames = 0L
            var fallbackAnchor = 0L
            while (running) {
                val count = active.read(buffer, 0, buffer.size, AudioRecord.READ_BLOCKING)
                if (!running) break
                check(count > 0 && count % 2 == 0)
                if (fallbackAnchor == 0L) fallbackAnchor = SystemClock.elapsedRealtime() - (readFrames + count / 2) * 1000L / 16000
                val captured = if (active.getTimestamp(clock, AudioTimestamp.TIMEBASE_BOOTTIME) == AudioRecord.SUCCESS)
                    clock.nanoTime / 1000000 + (readFrames - clock.framePosition) * 1000L / 16000
                else fallbackAnchor + readFrames * 1000L / 16000
                readFrames += count / 2
                val packet = Packet(buffer.copyOf(count), captured)
                if (!packets.offer(packet)) { packets.poll(); packets.offer(packet) }
            }
        } catch (_: Exception) {
            fail("Не удалось включить микрофон. Видео продолжит работать.")
        } finally {
            try { active?.stop() } catch (_: IllegalStateException) { }
            active?.release()
            recorder = null
        }
    }
    private fun send() {
        while (running) {
            val packet = try { packets.poll(200, TimeUnit.MILLISECONDS) } catch (_: InterruptedException) { null } ?: continue
            if (SystemClock.elapsedRealtime() - packet.captured > 500) continue
            val active = url.openConnection() as HttpURLConnection
            request = active
            try {
                active.requestMethod = "POST"; active.instanceFollowRedirects = false
                active.connectTimeout = 1000; active.readTimeout = 1000; active.doOutput = true
                active.setRequestProperty("Content-Type", "audio/pcm;rate=16000;channels=1;bits=16")
                active.setRequestProperty("Authorization", "Bearer $code")
                active.setRequestProperty("X-Media-Session", session)
                active.setRequestProperty("X-Media-Time", packet.captured.toString())
                active.setFixedLengthStreamingMode(packet.data.size)
                active.outputStream.use { it.write(packet.data) }
                when (active.responseCode) {
                    204, 409 -> { } // Video establishes/re-establishes this session first.
                    401, 403, 429 -> fail("Приёмник отклонил звук. Проверь подключение и код.")
                    else -> fail("Обнови приёмник Windows для передачи звука.")
                }
            } catch (_: Exception) {
                // Keep bounded capture alive across brief network outages.
                if (running) Thread.sleep(150)
            } finally {
                active.disconnect()
                if (request === active) request = null
            }
        }
    }
}
