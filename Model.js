.pragma library

// Pure logic for the Bose headphones widget: parsing bosectl output, labels, option lists
// and the bosectl argument vectors for writes. No QML imports so tests can load it directly.

function glyph(codepoint) {
  return String.fromCodePoint(codepoint)
}

// Material Design Icons from the Nerd Font, one per Bose mode type.
var ICONS = {
  quiet: glyph(0xf0a12),         // md-shield_account_outline
  aware: glyph(0xf05cb),         // md-account_voice
  transparent: glyph(0xf06d0),   // md-eye_outline
  transparency: glyph(0xf0208),  // md-eye
  masking: glyph(0xf147d),       // md-waveform
  comfort: glyph(0xf156e),       // md-sofa_single
  commute: glyph(0xf1012),       // md-bus_stop
  outdoor: glyph(0xf02f5),       // md-image_filter_hdr
  workout: glyph(0xf115d),       // md-weight_lifter
  home: glyph(0xf06a1),          // md-home_outline
  work: glyph(0xf0814),          // md-briefcase_outline
  music: glyph(0xf0387),         // md-music_note
  focus: glyph(0xf06e9),         // md-lightbulb_on_outline
  relax: glyph(0xf01f5),         // md-emoticon_happy_outline
  flight: glyph(0xf001d),        // md-airplane
  airport: glyph(0xf084b),       // md-airport
  driving: glyph(0xf010b),       // md-car
  training: glyph(0xf09b6),      // md-whistle
  gym: glyph(0xf01e6),           // md-dumbbell
  run: glyph(0xf070e),           // md-run
  walk: glyph(0xf15c8),          // md-shoe_sneaker
  hike: glyph(0xf0d7f),          // md-hiking
  talk: glyph(0xf0b79),          // md-chat
  call: glyph(0xf03f6),          // md-phone_in_talk
  whisper: glyph(0xf0ed4),       // md-account_voice_off
  hearing: glyph(0xf07c5),       // md-ear_hearing
  learn: glyph(0xf0474),         // md-school
  podcast: glyph(0xf0994),       // md-podcast
  audiobook: glyph(0xf0067),     // md-book_music
  calm: glyph(0xf032a),          // md-leaf
  sleep: glyph(0xf04b2),         // md-sleep
  meditate: glyph(0xf117b),      // md-meditation
  yoga: glyph(0xf117c),          // md-yoga
  immersion: glyph(0xf05c5),     // md-surround_sound
  stereo: glyph(0xf04c3),        // md-speaker
  cinema: glyph(0xf0fcf)         // md-movie_open_outline
}

var GLYPHS = {
  headphones: glyph(0xf02cb),     // md-headphones
  headphonesOff: glyph(0xf07ce),  // md-headphones_off
  refresh: glyph(0xf0450),        // md-refresh
  plus: glyph(0xf0415),           // md-plus
  remove: glyph(0xf0a7a),         // md-trash_can_outline
  bluetooth: glyph(0xf00af)       // md-bluetooth
}

var SPATIAL_LABELS = { off: "Off", still: "Still", motion: "Motion" }
var SIDETONE_LABELS = { off: "Off", low: "Low", medium: "Medium", high: "High" }
var AUTO_OFF_MINUTES = [0, 5, 20, 40, 60, 180]
var SHORTCUT_LABELS = {
  battery_level: "Battery level", disabled: "Disabled", spotify_tap: "Spotify Tap",
  voice_assistant: "Voice assistant", play_pause: "Play / pause", modes_carousel: "Cycle modes",
  conversation_mode: "Conversation mode", spatial_audio_mode: "Immersive audio", wind_mode: "Wind block",
  cnc_up: "More noise cancelling", cnc_down: "Less noise cancelling", track_forward: "Next track",
  track_back: "Previous track", alexa: "Alexa"
}

function titleCase(text) {
  return String(text || "").replace(/_/g, " ").replace(/\b\w/g, function(c) { return c.toUpperCase() })
}

function iconGlyph(name) {
  return ICONS[name] || GLYPHS.headphones
}

// ---------------------------------------------------------------- bosectl output

// One line of `bosectl watch`. Returns { connected, state } for state snapshots, null otherwise.
function parseWatchLine(line) {
  var text = String(line || "").trim()
  if (text === "") return null
  var msg
  try {
    msg = JSON.parse(text)
  } catch (e) {
    return null
  }
  if (!msg || msg.event !== "state") return null
  return { connected: msg.connected === true && !!msg.state, state: msg.state || null,
           error: msg.error ? String(msg.error) : "" }
}

// Why the widget can't reach the headset, as shown in the panel.
function disconnectedMessage(loading, error) {
  if (error.indexOf("bluetoothctl") !== -1) return error
  if (error.indexOf("python3") !== -1 || error.indexOf("No such file") !== -1)
    return "Python 3 is needed to talk to the headset. Install it with: sudo pacman -S --needed python"
  if (loading) return "Looking for your headphones…"
  return "Turn on your Bose headphones and connect them over Bluetooth. The widget reconnects automatically."
}

// stdout of a one-shot `bosectl -j ...`; returns an error message or "".
function commandError(exitCode, stdoutText, stderrText) {
  if (exitCode === 0) return ""
  try {
    var msg = JSON.parse(String(stdoutText || "").trim().split("\n").pop())
    if (msg && msg.error) return String(msg.error)
  } catch (e) {}
  var err = String(stderrText || "").trim().replace(/^bosectl:\s*/, "")
  return err !== "" ? err : "The headset rejected the change."
}

// ---------------------------------------------------------------- state helpers

function modes(state) {
  return state && state.modes ? state.modes : []
}

function activeMode(state) {
  var list = modes(state)
  for (var i = 0; i < list.length; i++) if (list[i].active) return list[i]
  return null
}

function findMode(state, index) {
  var list = modes(state)
  for (var i = 0; i < list.length; i++) if (list[i].index === index) return list[i]
  return null
}

function batteryPercent(state) {
  if (!state || !state.battery || state.battery.length === 0) return -1
  return Number(state.battery[0].percent)
}

function deviceName(state) {
  return state && state.device && state.device.name ? state.device.name : "Bose headphones"
}

function tooltip(connected, state) {
  if (!connected) return "Bose headphones not connected"
  var parts = [deviceName(state)]
  var mode = activeMode(state)
  if (mode) parts.push(mode.name)
  var battery = batteryPercent(state)
  if (battery >= 0) parts.push(battery + "%")
  return parts.join(" · ")
}

function subtitle(connected, loading, state) {
  if (!connected) return loading ? "CONNECTING" : "NOT CONNECTED"
  var parts = []
  var battery = batteryPercent(state)
  if (battery >= 0) parts.push("BATTERY " + battery + "%")
  var mode = activeMode(state)
  if (mode) parts.push(String(mode.name).toUpperCase())
  return parts.join(" · ")
}

function modeSummary(mode) {
  if (!mode) return ""
  var parts = []
  if (mode.noise_cancel !== undefined)
    parts.push("Noise cancelling " + mode.noise_cancel + "/" + mode.noise_cancel_max)
  if (mode.wind_block === true) parts.push("Wind block")
  if (mode.auto_cnc === true) parts.push("ActiveSense")
  if (mode.spatial && mode.spatial !== "off") parts.push("Immersive " + (SPATIAL_LABELS[mode.spatial] || mode.spatial))
  return parts.join(" · ")
}

function hasEditableSettings(mode) {
  if (!mode || !mode.editable) return false
  var e = mode.editable
  return e.noise_cancel || e.auto_cnc || e.spatial || e.wind_block || e.anc_toggle
}

function customModeCount(state) {
  var n = 0
  var list = modes(state)
  for (var i = 0; i < list.length; i++) if (list[i].custom) n++
  return n
}

function canCreateMode(state) {
  if (!state || !state.capabilities) return false
  return customModeCount(state) < Number(state.capabilities.user_modes)
}

// Wheel on the bar icon: step through modes in index order, wrapping around.
function stepModeIndex(state, delta) {
  var list = modes(state).slice(0).sort(function(a, b) { return a.index - b.index })
  if (list.length === 0) return -1
  var current = 0
  for (var i = 0; i < list.length; i++) if (list[i].active) current = i
  var next = (current + (delta > 0 ? 1 : -1) + list.length) % list.length
  return list[next].index
}

// ---------------------------------------------------------------- option lists

function iconOptions(state, currentIcon) {
  var names = state && state.icon_choices ? state.icon_choices.slice(0) : []
  if (currentIcon && names.indexOf(currentIcon) === -1) names.unshift(currentIcon)
  return names.map(function(name) {
    return { value: name, label: titleCase(name), icon: iconGlyph(name), tooltip: titleCase(name) }
  })
}

function spatialOptions() {
  return ["off", "still", "motion"].map(function(v) { return { value: v, label: SPATIAL_LABELS[v] } })
}

function sidetoneOptions(settings) {
  var available = settings && settings.sidetone ? settings.sidetone.modes : []
  return available.map(function(v) { return { value: v, label: SIDETONE_LABELS[v] || titleCase(v) } })
}

function autoOffLabel(minutes) {
  var m = Number(minutes)
  if (m === 0) return "Never"
  if (m % 60 === 0) return (m / 60) + (m === 60 ? " hour" : " hours")
  return m + " minutes"
}

function autoOffOptions(current) {
  var values = AUTO_OFF_MINUTES.slice(0)
  if (current !== undefined && current !== null && values.indexOf(Number(current)) === -1) values.push(Number(current))
  values.sort(function(a, b) { return a - b })
  return values.map(function(m) { return { value: String(m), label: autoOffLabel(m) } })
}

function shortcutOptions(settings) {
  if (!settings || !settings.shortcut) return []
  var actions = settings.shortcut.actions.slice(0)
  if (actions.indexOf(settings.shortcut.action) === -1) actions.unshift(settings.shortcut.action)
  return actions.map(function(a) { return { value: a, label: SHORTCUT_LABELS[a] || titleCase(a) } })
}

function eqBands(settings) {
  if (!settings || !settings.eq) return []
  var order = ["bass", "low_mid", "mid", "high_mid", "treble"]
  var bands = []
  for (var i = 0; i < order.length; i++) {
    var band = settings.eq[order[i]]
    if (band) bands.push({ key: order[i], label: titleCase(order[i]), value: band.value, min: band.min, max: band.max })
  }
  return bands
}

function signedLabel(value) {
  var v = Math.round(Number(value))
  return v > 0 ? "+" + v : String(v)
}

// ---------------------------------------------------------------- writes

// Mode setting keys as used in state → bosectl mode-edit flags.
var MODE_FLAGS = {
  noise_cancel: "--noise-cancel", wind_block: "--wind-block", auto_cnc: "--auto-cnc",
  spatial: "--spatial", anc_toggle: "--anc-toggle", icon: "--icon", name: "--rename"
}

function flagValue(value) {
  if (value === true) return "on"
  if (value === false) return "off"
  return String(value)
}

function modeEditArgs(index, key, value) {
  return ["-j", "mode-edit", String(index), MODE_FLAGS[key], flagValue(value)]
}

function switchModeArgs(index, announce) {
  var args = ["-j", "mode", String(index)]
  if (announce) args.push("--prompt")
  return args
}

// A custom mode keeps a name the user typed; a name that still matches its type follows the type,
// the way the Bose app names modes.
function followsIconName(mode) {
  return !!mode && mode.custom === true && mode.name === titleCase(mode.icon)
}

function modeIconArgs(mode, icon) {
  var args = ["-j", "mode-edit", String(mode.index), "--icon", icon]
  if (followsIconName(mode)) args.push("--rename", titleCase(icon))
  return args
}

function validModeName(name) {
  var text = String(name || "").trim()
  if (text === "") return false
  // BMAP stores names as 32 NUL-terminated UTF-8 bytes
  return unescape(encodeURIComponent(text)).length <= 31
}

function createModeArgs(icon) {
  return ["-j", "mode-create", titleCase(icon), "--icon", icon]
}

var SETTING_ARGS = {
  eq_bass: ["set", "eq", "bass"], eq_mid: ["set", "eq", "mid"], eq_treble: ["set", "eq", "treble"],
  eq_low_mid: ["set", "eq", "low_mid"], eq_high_mid: ["set", "eq", "high_mid"],
  multipoint: ["set", "multipoint"], voice_prompts: ["set", "voice-prompts"], sidetone: ["set", "sidetone"],
  auto_off_minutes: ["set", "auto-off"], shortcut: ["set", "shortcut"],
  mode_persistence: ["set", "mode-persistence"], cnc_persistence: ["set", "cnc-persistence"]
}

function settingArgs(key, value) {
  var base = SETTING_ARGS[key]
  if (!base) return null
  return ["-j"].concat(base, [flagValue(value)])
}

// Optimistic local update so controls don't snap back while the write is in flight.
function withModeValue(state, index, key, value) {
  if (!state) return state
  var copy = JSON.parse(JSON.stringify(state))
  var mode = findMode(copy, index)
  if (mode) {
    if (key === "icon" && followsIconName(mode)) mode.name = titleCase(value)
    mode[key] = value
  }
  return copy
}

function withActiveMode(state, index) {
  if (!state) return state
  var copy = JSON.parse(JSON.stringify(state))
  var list = modes(copy)
  for (var i = 0; i < list.length; i++) list[i].active = list[i].index === index
  return copy
}

function withSetting(state, key, value) {
  if (!state || !state.settings) return state
  var copy = JSON.parse(JSON.stringify(state))
  var s = copy.settings
  if (key.indexOf("eq_") === 0) {
    var band = key.slice(3)
    if (s.eq && s.eq[band]) s.eq[band].value = value
  } else if (key === "multipoint" && s.multipoint) s.multipoint.enabled = value
  else if (key === "voice_prompts" && s.voice_prompts) s.voice_prompts.enabled = value
  else if (key === "sidetone" && s.sidetone) s.sidetone.mode = value
  else if (key === "shortcut" && s.shortcut) s.shortcut.action = value
  else if (key === "auto_off_minutes") s.auto_off_minutes = Number(value)
  else s[key] = value
  return copy
}
