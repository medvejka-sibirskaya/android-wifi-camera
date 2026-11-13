package com.example.wificamera

import android.app.AlertDialog
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.InputFilter
import android.text.InputType
import android.view.ContextThemeWrapper
import android.view.Gravity
import android.view.View
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.content.Context
import android.content.res.Configuration
import android.widget.*
import androidx.camera.view.PreviewView
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsControllerCompat

/** Native Android views: no additional UI framework or remote services. */
class CameraScreen(private val owner: MainActivity) {
    private val root = LinearLayout(owner).apply { orientation = LinearLayout.VERTICAL }
    private val top = LinearLayout(owner).apply { gravity = Gravity.CENTER_VERTICAL; setPadding(dp(12), dp(8), dp(12), dp(8)) }
    private val bottom = LinearLayout(owner).apply { gravity = Gravity.CENTER_VERTICAL; setPadding(dp(12), dp(8), dp(12), dp(8)) }
    private val title = TextView(owner).apply { textSize = 17f; setTypeface(null, Typeface.BOLD); maxLines = 2 }
    private val detail = TextView(owner).apply { textSize = 12f; maxLines = 2 }
    val preview = PreviewView(owner).apply {
        scaleType = PreviewView.ScaleType.FIT_CENTER
        implementationMode = PreviewView.ImplementationMode.COMPATIBLE
        setBackgroundColor(Color.BLACK)
    }
    private val connect = button(R.string.connect) { if (owner.streaming) owner.stopStream() else showConnection() }
    private val camera = button(R.string.camera) { owner.switchCamera() }
    private val settings = button(R.string.settings) { showSettings() }
    private val torch = button(R.string.torch) { owner.toggleTorch() }
    private val mirror = button(R.string.mirror) { owner.toggleMirror() }
    private val microphone = button(R.string.microphone) { owner.toggleMicrophone() }
    private var micOn = false
    private val allow = button(R.string.allow_camera) { owner.requestCamera() }
    private val permissionPanel = LinearLayout(owner).apply {
        orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER; setPadding(dp(24), 0, dp(24), 0)
        addView(TextView(owner).apply { text = owner.getString(R.string.camera_permission); textSize = 18f; gravity = Gravity.CENTER; setTextColor(Color.WHITE) })
        addView(allow)
    }
    private var immersive = false
    private val fullScreenStatus = TextView(owner).apply {
        textSize = 13f; setPadding(dp(10), dp(6), dp(10), dp(6)); visibility = View.GONE
    }
    private val allButtons get() = listOf(connect, camera, settings, torch, mirror, microphone, allow)
    private var dialog: AlertDialog? = null

    init {
        val labels = LinearLayout(owner).apply { orientation = LinearLayout.VERTICAL; addView(title); addView(detail) }
        fun icon(view: Button, symbol: String, description: Int) {
            view.text = symbol; view.textSize = 24f; view.minWidth = dp(52); view.minimumWidth = dp(52)
            view.setPadding(0, 0, 0, 0); view.contentDescription = owner.getString(description)
            view.tooltipText = owner.getString(description)
        }
        icon(camera, "⇄", R.string.camera); icon(settings, "⚙︎", R.string.settings)
        icon(torch, "⚡︎", R.string.torch); icon(mirror, "↔", R.string.mirror)
        top.addView(labels, LinearLayout.LayoutParams(0, -2, 1f))
        top.addView(camera, LinearLayout.LayoutParams(dp(52), dp(52)))
        top.addView(settings, LinearLayout.LayoutParams(dp(52), dp(52)))
        val stage = FrameLayout(owner).apply {
            setBackgroundColor(Color.BLACK)
            addView(preview, FrameLayout.LayoutParams(-1, -1))
            addView(permissionPanel, FrameLayout.LayoutParams(-1, -1))
            addView(fullScreenStatus, FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM or Gravity.START).apply {
                setMargins(dp(12), dp(12), dp(12), dp(12))
            })
        }
        bottom.addView(connect, LinearLayout.LayoutParams(0, dp(56), 1f))
        bottom.addView(microphone, LinearLayout.LayoutParams(dp(62), dp(56)))
        bottom.addView(torch, LinearLayout.LayoutParams(dp(52), dp(56)))
        bottom.addView(mirror, LinearLayout.LayoutParams(dp(52), dp(56)))
        root.addView(top)
        root.addView(stage, LinearLayout.LayoutParams(-1, 0, 1f))
        root.addView(bottom)
        owner.setContentView(root)
        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }
        torch.visibility = View.GONE
        camera.isEnabled = false
        applyAppearance()
        status(owner.getString(R.string.disconnected), owner.getString(R.string.local_preview))
    }

    private fun dp(value: Int) = (value * owner.resources.displayMetrics.density).toInt()
    private fun button(label: Int, action: () -> Unit) = Button(owner).apply {
        text = owner.getString(label); isAllCaps = false; textSize = 14f; minWidth = dp(76); minimumHeight = dp(48)
        setPadding(dp(12), 0, dp(12), 0); setOnClickListener { action() }
    }

    fun applyAppearance() {
        val dark = owner.preferences.getBoolean("dark", true)
        val surface = owner.getColor(if (dark) R.color.petrol_dark_surface else R.color.petrol_light_surface)
        val foreground = owner.getColor(if (dark) R.color.petrol_dark_text else R.color.petrol_light_text)
        root.setBackgroundColor(surface); top.setBackgroundColor(surface); bottom.setBackgroundColor(surface)
        WindowCompat.getInsetsController(owner.window, root).apply {
            isAppearanceLightStatusBars = !dark
            isAppearanceLightNavigationBars = !dark
        }
        title.setTextColor(foreground)
        detail.setTextColor(owner.getColor(if (dark) R.color.petrol_dark_muted else R.color.petrol_light_muted))
        fullScreenStatus.setTextColor(foreground)
        fullScreenStatus.background = GradientDrawable().apply { cornerRadius = dp(10).toFloat(); setColor(surface) }
        allButtons.forEach { button ->
            button.setTextColor(if (button == connect) Color.WHITE else foreground)
            button.background = GradientDrawable().apply {
                cornerRadius = dp(14).toFloat()
                setColor(if (button == connect) owner.getColor(R.color.petrol_primary) else owner.getColor(if (dark) R.color.petrol_dark_button else R.color.petrol_light_button))
            }
        }
        microphoneState(micOn)
        mirror.alpha = if (owner.mirrored) 1f else 0.65f
        mirror.contentDescription = owner.getString(if (owner.mirrored) R.string.mirror_on else R.string.mirror_off)
        mirror.tooltipText = mirror.contentDescription
    }

    fun microphoneState(enabled: Boolean) {
        micOn = enabled
        microphone.text = if (enabled) "Микр. ●" else owner.getString(R.string.microphone)
        microphone.textSize = 11f
        microphone.alpha = if (enabled) 1f else 0.65f
        microphone.contentDescription = owner.getString(if (enabled) R.string.microphone_on else R.string.microphone_off)
        microphone.tooltipText = microphone.contentDescription
    }
    fun permission(granted: Boolean) { permissionPanel.visibility = if (granted) View.GONE else View.VISIBLE }
    fun capabilities(hasTorch: Boolean, canSwitch: Boolean) {
        torch.visibility = if (hasTorch) View.VISIBLE else View.GONE
        camera.isEnabled = canSwitch
    }
    fun torchEnabled(enabled: Boolean) {
        torch.alpha = if (enabled) 1f else 0.65f
        torch.contentDescription = owner.getString(if (enabled) R.string.torch_on else R.string.torch_off)
        torch.tooltipText = torch.contentDescription
    }
    fun status(label: String, message: String, connected: Boolean = false) {
        if (title.text.toString() != label) title.text = label
        if (detail.text.toString() != message) detail.text = message
        connect.text = owner.getString(if (owner.streaming) R.string.disconnect else R.string.connect)
        connect.contentDescription = connect.text
        title.contentDescription = owner.getString(R.string.connection_state, label)
        fullScreenStatus.text = owner.getString(R.string.fullscreen_status, label)
        if (connected) title.setTextColor(owner.getColor(if (owner.preferences.getBoolean("dark", true)) R.color.petrol_connected else R.color.petrol_primary)) else {
            title.setTextColor(owner.getColor(if (owner.preferences.getBoolean("dark", true)) R.color.petrol_dark_text else R.color.petrol_light_text))
        }
    }

    fun toggleFullScreen() {
        setFullScreen(!immersive)
    }
    fun leaveFullScreen(): Boolean {
        if (!immersive) return false
        setFullScreen(false)
        return true
    }
    fun orientationChanged() {
        ViewCompat.requestApplyInsets(root)
    }
    private fun setFullScreen(enabled: Boolean) {
        immersive = enabled
        top.visibility = if (enabled) View.GONE else View.VISIBLE
        bottom.visibility = if (enabled) View.GONE else View.VISIBLE
        fullScreenStatus.visibility = if (enabled) View.VISIBLE else View.GONE
        WindowCompat.getInsetsController(owner.window, root).apply {
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            if (enabled) hide(WindowInsetsCompat.Type.systemBars()) else show(WindowInsetsCompat.Type.systemBars())
        }
        ViewCompat.requestApplyInsets(root)
    }

    private fun context(): ContextThemeWrapper = ContextThemeWrapper(owner,
        if (owner.preferences.getBoolean("dark", true)) R.style.PetrolDialogDark
        else R.style.PetrolDialogLight)

    fun showConnection(error: String? = null) {
        if (dialog?.isShowing == true) return
        val context = context()
        val dark = owner.preferences.getBoolean("dark", true)
        val errorColor = Color.parseColor(if (dark) "#FFB4A8" else "#B3261E")
        val helperColor = owner.getColor(if (dark) R.color.petrol_dark_muted else R.color.petrol_light_muted)
        val form = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(22), dp(8), dp(22), dp(8)) }
        fun label(id: Int) { form.addView(TextView(context).apply { text = owner.getString(id); textSize = 14f; setPadding(0, dp(12), 0, dp(5)) }) }
        form.addView(TextView(context).apply { text = owner.getString(R.string.connection_help); textSize = 14f })
        label(R.string.computer_address)
        val address = EditText(context).apply {
            hint = "192.168.1.10"; textSize = 20f; setSingleLine()
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            imeOptions = EditorInfo.IME_FLAG_NO_EXTRACT_UI
            setText(owner.preferences.getString("address", "")); setSelectAllOnFocus(true)
        }
        form.addView(address)
        label(R.string.connection_code)
        val code = EditText(context).apply {
            hint = "0000"; textSize = 26f; setSingleLine(); inputType = InputType.TYPE_CLASS_NUMBER
            filters = arrayOf(InputFilter.LengthFilter(4)); isSaveEnabled = false
            imeOptions = EditorInfo.IME_FLAG_NO_EXTRACT_UI; setText(owner.sessionCode); setSelectAllOnFocus(true)
        }
        form.addView(code)
        val message = TextView(context).apply { text = error ?: owner.getString(R.string.home_network); setTextColor(if (error != null) errorColor else helperColor); setPadding(0, dp(12), 0, 0) }
        form.addView(message)
        val scroll = ScrollView(context).apply { addView(form) }
        dialog = AlertDialog.Builder(context).setTitle(R.string.connection).setView(scroll)
            .setNegativeButton(R.string.cancel, null).setPositiveButton(R.string.connect, null).create()
        dialog?.setOnShowListener {
            dialog?.getButton(AlertDialog.BUTTON_POSITIVE)?.setOnClickListener {
                val issue = owner.connectTo(address.text.toString(), code.text.toString())
                if (issue != null) { message.text = issue; message.setTextColor(errorColor) }
                else {
                    (owner.getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager).hideSoftInputFromWindow(code.windowToken, 0)
                    dialog?.dismiss()
                }
            }
        }
        dialog?.show()
    }

    fun showSettings() {
        val context = context()
        val form = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(22), 0, dp(22), 0) }
        fun label(id: Int) { form.addView(TextView(context).apply { text = owner.getString(id); setPadding(0, dp(15), 0, dp(6)); textSize = 15f }) }
        label(R.string.resolution)
        val resolution = Spinner(context).apply {
            adapter = ArrayAdapter(context, android.R.layout.simple_spinner_dropdown_item, listOf("720p", "1080p"))
            setSelection(owner.resolutionIndex)
        }
        form.addView(resolution)
        label(R.string.quality)
        val qualityLabel = TextView(context)
        val quality = SeekBar(context).apply { max = 35; progress = owner.jpegQuality - 50 }
        fun updateQuality() { qualityLabel.text = owner.getString(R.string.quality_value, 50 + quality.progress) }
        quality.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(seekBar: SeekBar?, progress: Int, fromUser: Boolean) { updateQuality() }
            override fun onStartTrackingTouch(seekBar: SeekBar?) {}
            override fun onStopTrackingTouch(seekBar: SeekBar?) {}
        })
        updateQuality(); form.addView(quality); form.addView(qualityLabel)
        label(R.string.appearance)
        val dark = Switch(context).apply { text = owner.getString(R.string.dark_theme); isChecked = owner.preferences.getBoolean("dark", true); minHeight = dp(48) }
        form.addView(dark)
        form.addView(TextView(context).apply { text = owner.getString(R.string.camera_gestures); setPadding(0, dp(16), 0, dp(16)); textSize = 13f })
        AlertDialog.Builder(context).setTitle(R.string.settings).setView(ScrollView(context).apply { addView(form) })
            .setNegativeButton(R.string.cancel, null).setPositiveButton(R.string.apply) { _, _ ->
                owner.preferences.edit().putBoolean("dark", dark.isChecked).apply()
                owner.updateVideoSettings(resolution.selectedItemPosition, 50 + quality.progress)
                applyAppearance()
            }.show()
    }
}
