package com.example.wificamera

import android.Manifest
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.graphics.Bitmap
import android.graphics.Matrix
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.view.MotionEvent
import android.view.OrientationEventListener
import android.view.ScaleGestureDetector
import android.view.Surface
import android.view.WindowManager
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.*
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLDecoder
import java.net.URLEncoder
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class MainActivity : ComponentActivity() {
    private class ConnectionRejected(message: String) : Exception(message)
    private data class Connection(val url: URL, val code: String, val session: String = java.util.UUID.randomUUID().toString())
    val preferences by lazy { getSharedPreferences("camera-settings", MODE_PRIVATE) }
    lateinit var screen: CameraScreen
        private set
    @Volatile var streaming = false
        private set
    @Volatile var mirrored = false
        private set
    var resolutionIndex = 0
        private set
    @Volatile var jpegQuality = 65
        private set
    var sessionCode = ""
        private set
    private var front = false
    private var torchOn = false
    private var provider: ProcessCameraProvider? = null
    private var camera: Camera? = null
    private var analysis: ImageAnalysis? = null
    private var localPreview: Preview? = null
    private val worker = Executors.newSingleThreadExecutor()
    @Volatile private var cameraEpoch = 0
    @Volatile private var streamEpoch = 0
    @Volatile private var connection: Connection? = null
    @Volatile private var request: HttpURLConnection? = null
    private var audio: AudioStreamer? = null
    private var microphoneEnabled = false
    private val microphonePermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted && streaming) startMicrophone()
        else { screen.microphoneState(false); Toast.makeText(this, R.string.microphone_denied, Toast.LENGTH_LONG).show() }
    }
    fun toggleMicrophone() {
        if (microphoneEnabled) { stopMicrophone(); return }
        if (!streaming) { Toast.makeText(this, R.string.microphone_connect, Toast.LENGTH_SHORT).show(); return }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) startMicrophone()
        else microphonePermission.launch(Manifest.permission.RECORD_AUDIO)
    }
    private fun startMicrophone() {
        val target = connection ?: return
        stopMicrophone()
        val session = streamEpoch
        val streamer = AudioStreamer(this, URL(target.url, "/audio"), target.code, target.session) { message ->
            runOnUiThread { if (streaming && session == streamEpoch && microphoneEnabled) {
                stopMicrophone(); Toast.makeText(this, message, Toast.LENGTH_LONG).show()
            } }
        }
        audio = streamer; microphoneEnabled = true
        screen.microphoneState(true)
        streamer.start()
    }
    private fun stopMicrophone() {
        microphoneEnabled = false
        audio?.stop(); audio = null
        if (::screen.isInitialized) screen.microphoneState(false)
    }
    private val orientation by lazy { object : OrientationEventListener(this) {
        override fun onOrientationChanged(value: Int) { if (value != ORIENTATION_UNKNOWN) updateRotation() }
    } }
    private val permission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        screen.permission(granted)
        if (granted) bindCamera() else stopStream(getString(R.string.camera_permission))
    }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        front = preferences.getBoolean("front", false)
        mirrored = preferences.getBoolean("mirror", false)
        resolutionIndex = preferences.getInt("resolution", 0).coerceIn(0, 1)
        jpegQuality = preferences.getInt("quality", 65).coerceIn(50, 85)
        screen = CameraScreen(this)
        configureGestures()
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() { if (!screen.leaveFullScreen()) finish() }
        })
    }
    private fun cameraGranted() = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
    fun requestCamera() { permission.launch(Manifest.permission.CAMERA) }
    fun connectTo(address: String, code: String): String? {
        if (address.trim().isEmpty()) return getString(R.string.address_required)
        val url = try { endpoint(address.trim()) } catch (_: Exception) { return getString(R.string.address_invalid) }
        if (!code.trim().matches(Regex("[0-9]{4}"))) return getString(R.string.code_required)
        preferences.edit().putString("address", address.trim()).apply()
        sessionCode = code.trim()
        stopMicrophone()
        connection = Connection(url, sessionCode)
        ++streamEpoch
        streaming = true
        screen.status(getString(R.string.connecting), getString(R.string.waiting_pc))
        if (!cameraGranted()) requestCamera() else if (camera == null) bindCamera()
        return null
    }
    private fun endpoint(address: String): URL {
        val url = URL(if (address.contains("://")) address else "http://$address")
        require(url.protocol == "http" && url.userInfo == null && url.query == null && url.ref == null && url.path in listOf("", "/"))
        val parts = url.host.split('.')
        require(parts.size == 4 && parts.all { it.matches(Regex("[0-9]{1,3}")) && it.toInt() in 0..255 })
        val n = parts.map { it.toInt() }
        require(n[0] == 10 || n[0] == 192 && n[1] == 168 || n[0] == 172 && n[1] in 16..31)
        val port = if (url.port == -1) 8765 else url.port
        require(port in 1..65535)
        return URL("http", url.host, port, "/upload")
    }
    fun stopStream(message: String = getString(R.string.local_preview)) {
        streaming = false
        stopMicrophone()
        ++streamEpoch
        request?.disconnect()
        screen.status(getString(R.string.disconnected), message)
    }
    fun switchCamera() {
        val selected = if (!front) CameraSelector.DEFAULT_FRONT_CAMERA else CameraSelector.DEFAULT_BACK_CAMERA
        if (provider?.hasCamera(selected) != true) return
        front = !front
        preferences.edit().putBoolean("front", front).apply()
        bindCamera()
    }
    fun toggleMirror() {
        mirrored = !mirrored
        preferences.edit().putBoolean("mirror", mirrored).apply()
        updatePreviewMirror(); screen.applyAppearance()
    }
    fun toggleTorch() {
        val active = camera ?: return
        if (!active.cameraInfo.hasFlashUnit()) return
        val future = active.cameraControl.enableTorch(!torchOn)
        future.addListener({ try { future.get() } catch (_: Exception) {
            Toast.makeText(this, R.string.torch_error, Toast.LENGTH_SHORT).show()
        } }, ContextCompat.getMainExecutor(this))
    }
    fun updateVideoSettings(resolution: Int, quality: Int) {
        val rebind = resolutionIndex != resolution
        resolutionIndex = resolution.coerceIn(0, 1); jpegQuality = quality.coerceIn(50, 85)
        preferences.edit().putInt("resolution", resolutionIndex).putInt("quality", jpegQuality).apply()
        if (rebind && cameraGranted()) bindCamera()
    }
    @Suppress("DEPRECATION")
    private fun bindCamera() {
        if (!cameraGranted()) return
        screen.permission(true)
        val binding = ++cameraEpoch
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            if (binding != cameraEpoch) return@addListener
            try {
                val cameras = future.get(); provider = cameras
                camera?.cameraInfo?.torchState?.removeObservers(this)
                cameras.unbindAll()
                val selector = if (front) CameraSelector.DEFAULT_FRONT_CAMERA else CameraSelector.DEFAULT_BACK_CAMERA
                require(cameras.hasCamera(selector))
                val rotation = screen.preview.display?.rotation ?: Surface.ROTATION_0
                val view = Preview.Builder().setTargetRotation(rotation).build().also { it.setSurfaceProvider(screen.preview.surfaceProvider) }
                val landscape = resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE
                val longSide = if (resolutionIndex == 0) 1280 else 1920
                val shortSide = if (resolutionIndex == 0) 720 else 1080
                val size = if (landscape) android.util.Size(longSide, shortSide) else android.util.Size(shortSide, longSide)
                val analyzer = ImageAnalysis.Builder().setTargetResolution(size).setTargetRotation(rotation)
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST).build()
                localPreview = view; analysis = analyzer
                var last = 0L
                analyzer.setAnalyzer(worker) { image ->
                    val session = streamEpoch
                    try {
                        val now = SystemClock.elapsedRealtime(); val target = connection
                        if (binding == cameraEpoch && streaming && target != null && now - last >= 100) {
                            last = now
                            val raw = image.toBitmap()
                            val matrix = Matrix().apply {
                                postRotate(image.imageInfo.rotationDegrees.toFloat())
                                if (mirrored) postScale(-1f, 1f)
                            }
                            val upright = Bitmap.createBitmap(raw, 0, 0, raw.width, raw.height, matrix, true)
                            val bytes = ByteArrayOutputStream()
                            upright.compress(Bitmap.CompressFormat.JPEG, jpegQuality, bytes)
                            val dimensions = "${upright.width} × ${upright.height}"
                            if (upright !== raw) upright.recycle()
                            raw.recycle()
                            if (session == streamEpoch && streaming && binding == cameraEpoch) {
                                val name = upload(target, bytes.toByteArray(), now)
                                runOnUiThread { if (session == streamEpoch && streaming && binding == cameraEpoch)
                                    screen.status(getString(R.string.connected), getString(R.string.stream_info, name, dimensions), true) }
                            }
                        }
                    } catch (error: Exception) {
                        runOnUiThread {
                            if (streaming && session == streamEpoch && binding == cameraEpoch) {
                                if (error is ConnectionRejected) {
                                    stopStream(error.message ?: getString(R.string.receiver_error)); screen.showConnection(error.message)
                                } else screen.status(getString(R.string.connection_lost), getString(R.string.retrying))
                            }
                        }
                    } finally { image.close() }
                }
                camera = cameras.bindToLifecycle(this, selector, view, analyzer)
                torchOn = false
                camera?.cameraInfo?.torchState?.observe(this) { state -> torchOn = state == TorchState.ON; screen.torchEnabled(torchOn) }
                screen.capabilities(camera?.cameraInfo?.hasFlashUnit() == true,
                    cameras.hasCamera(if (front) CameraSelector.DEFAULT_BACK_CAMERA else CameraSelector.DEFAULT_FRONT_CAMERA))
                updatePreviewMirror()
            } catch (_: Exception) { camera = null; stopStream(getString(R.string.camera_error)) }
        }, ContextCompat.getMainExecutor(this))
    }
    private fun upload(target: Connection, data: ByteArray, captured: Long): String {
        val active = target.url.openConnection() as HttpURLConnection; request = active
        try {
            active.requestMethod = "POST"; active.instanceFollowRedirects = false
            active.connectTimeout = 1500; active.readTimeout = 1500; active.doOutput = true
            active.setRequestProperty("Content-Type", "image/jpeg")
            active.setRequestProperty("X-Media-Time", captured.toString())
            active.setRequestProperty("X-Media-Session", target.session)
            active.setRequestProperty("Authorization", "Bearer ${target.code}")
            active.setRequestProperty("X-Phone-Name", URLEncoder.encode("${Build.MANUFACTURER} ${Build.MODEL}", "UTF-8"))
            active.setFixedLengthStreamingMode(data.size)
            active.outputStream.use { it.write(data) }
            when (active.responseCode) {
                401 -> throw ConnectionRejected(getString(R.string.bad_code))
                403 -> throw ConnectionRejected(getString(R.string.receiver_busy))
                429 -> throw ConnectionRejected(getString(R.string.too_many_codes))
                204 -> return active.getHeaderField("X-Receiver-Name")?.let { URLDecoder.decode(it, "UTF-8") } ?: target.url.host
                else -> throw ConnectionRejected(getString(R.string.receiver_error))
            }
        } finally { active.disconnect(); if (request === active) request = null }
    }
    private fun updatePreviewMirror() { screen.preview.scaleX = if (front != mirrored) -1f else 1f }
    private fun updateRotation() {
        if (!::screen.isInitialized) return
        val rotation = screen.preview.display?.rotation ?: return
        if (analysis?.targetRotation != rotation) analysis?.targetRotation = rotation
        if (localPreview?.targetRotation != rotation) localPreview?.targetRotation = rotation
    }
    private fun configureGestures() {
        val pinch = ScaleGestureDetector(this, object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
            override fun onScale(detector: ScaleGestureDetector): Boolean {
                val active = camera ?: return false
                val state = active.cameraInfo.zoomState.value ?: return false
                active.cameraControl.setZoomRatio((state.zoomRatio * detector.scaleFactor).coerceIn(state.minZoomRatio, state.maxZoomRatio)); return true
            }
        })
        var tapPoint: Pair<Float, Float>? = null
        var longPressed = false
        fun focus() {
            val active = camera ?: return
            val xy = tapPoint ?: Pair(screen.preview.width / 2f, screen.preview.height / 2f)
            val point = screen.preview.meteringPointFactory.createPoint(xy.first, xy.second)
            val action = FocusMeteringAction.Builder(point).setAutoCancelDuration(3, TimeUnit.SECONDS).build()
            if (active.cameraInfo.isFocusMeteringSupported(action)) active.cameraControl.startFocusAndMetering(action)
        }
        screen.preview.setOnClickListener {
            screen.toggleFullScreen()
            tapPoint = null
        }
        screen.preview.setOnLongClickListener { focus(); true }
        val hold = Runnable { longPressed = true; screen.preview.performLongClick() }
        var startX = 0f
        var startY = 0f
        var multiTouch = false
        val slop = android.view.ViewConfiguration.get(this).scaledTouchSlop
        screen.preview.setOnTouchListener { view, event ->
            if (event.actionMasked == MotionEvent.ACTION_DOWN) {
                startX = event.x; startY = event.y; multiTouch = false; longPressed = false
                tapPoint = Pair(event.x, event.y)
                view.postDelayed(hold, android.view.ViewConfiguration.getLongPressTimeout().toLong())
            }
            if (event.pointerCount > 1) multiTouch = true
            val moved = kotlin.math.abs(event.x - startX) >= slop || kotlin.math.abs(event.y - startY) >= slop
            if (multiTouch || moved || event.actionMasked == MotionEvent.ACTION_CANCEL) view.removeCallbacks(hold)
            pinch.onTouchEvent(event)
            if (event.actionMasked == MotionEvent.ACTION_UP) {
                view.removeCallbacks(hold)
                if (!multiTouch && !moved && !longPressed) view.performClick()
            }
            true
        }
    }
    override fun onConfigurationChanged(newConfig: Configuration) { super.onConfigurationChanged(newConfig); screen.orientationChanged(); screen.preview.post { updateRotation() } }
    override fun onStart() { super.onStart(); orientation.enable(); screen.permission(cameraGranted()); if (cameraGranted()) bindCamera() }
    override fun onStop() { screen.leaveFullScreen(); stopStream(); ++cameraEpoch; orientation.disable(); provider?.unbindAll(); camera = null; super.onStop() }
    override fun onDestroy() { worker.shutdown(); super.onDestroy() }
}
