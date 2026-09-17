import QtQuick 2.15
import QtTest 1.3
import "../Model.js" as Model

TestCase {
  name: "BoseHeadphonesModel"

  function mode(index, name, icon, custom, active, extra) {
    var m = {
      index: index, name: name, icon: icon, custom: custom, active: active, default: false,
      noise_cancel: 10, noise_cancel_max: 10, cnc_level: 0, auto_cnc: false, spatial: "off",
      wind_block: false, anc_toggle: null,
      editable: { noise_cancel: custom, auto_cnc: false, spatial: false, wind_block: custom, anc_toggle: false }
    }
    for (var k in extra) m[k] = extra[k]
    return m
  }

  function sampleState() {
    return {
      device: { name: "Test QC Headphones", product_id: "0x4075", firmware: "1.0.0" },
      battery: [{ percent: 90, minutes: null, component: 0 }],
      capabilities: { bose_modes: 2, user_modes: 2 },
      icon_choices: ["commute", "focus", "home", "music", "outdoor", "relax", "run", "walk", "work", "workout"],
      modes: [
        mode(0, "Quiet", "quiet", false, false, {}),
        mode(1, "Aware", "aware", false, true, { noise_cancel: 0, cnc_level: 10 }),
        mode(2, "Work", "work", true, false, { noise_cancel: 3, cnc_level: 7 }),
        mode(3, "Outdoor", "outdoor", true, false, { wind_block: true })
      ],
      settings: {
        eq: { bass: { value: 0, min: -10, max: 10 }, mid: { value: 2, min: -10, max: 10 },
              treble: { value: -1, min: -10, max: 10 } },
        multipoint: { enabled: true, supported: true, can_disable: true },
        sidetone: { mode: "medium", persistent: true, modes: ["off", "high", "medium", "low"] },
        shortcut: { button: "shortcut", event: 9, action: "battery_level",
                    actions: ["battery_level", "disabled", "spotify_tap"] },
        auto_off_minutes: 0,
        voice_prompts: { enabled: true, language: "en-US", languages: ["en-US"] }
      }
    }
  }

  function test_parseWatchLine() {
    var parsed = Model.parseWatchLine(JSON.stringify({ event: "state", connected: true, state: sampleState() }))
    verify(parsed.connected)
    compare(parsed.state.modes.length, 4)
    compare(Model.parseWatchLine('{"event":"state","connected":false,"state":null}').connected, false)
    compare(Model.parseWatchLine('{"event":"mode","block":31}'), null)
    compare(Model.parseWatchLine("not json"), null)
    compare(Model.parseWatchLine('{"event":"state","connected":false,"state":null,"error":"bluetoothctl not found"}').error,
            "bluetoothctl not found")
    compare(Model.parseWatchLine('{"event":"state","connected":false,"state":null}').error, "")
    compare(Model.parseWatchLine(""), null)
  }

  function test_commandError() {
    compare(Model.commandError(0, "", ""), "")
    compare(Model.commandError(1, '{"error": "no free mode slot"}', "bosectl: no free mode slot"), "no free mode slot")
    compare(Model.commandError(1, "", "bosectl: headset not connected"), "headset not connected")
    verify(Model.commandError(1, "", "") !== "")
  }

  function test_labels() {
    var s = sampleState()
    compare(Model.activeMode(s).name, "Aware")
    compare(Model.tooltip(true, s), "Test QC Headphones · Aware · 90%")
    compare(Model.tooltip(false, null), "Bose headphones not connected")
    compare(Model.subtitle(true, false, s), "BATTERY 90% · AWARE")
    compare(Model.subtitle(false, true, null), "CONNECTING")
    compare(Model.modeSummary(Model.findMode(s, 3)), "Noise cancelling 10/10 · Wind block")
    compare(Model.autoOffLabel(0), "Never")
    compare(Model.autoOffLabel(60), "1 hour")
    compare(Model.autoOffLabel(180), "3 hours")
    compare(Model.signedLabel(3), "+3")
    compare(Model.signedLabel(-2), "-2")
  }

  function test_disconnectedMessage() {
    compare(Model.disconnectedMessage(true, ""), "Looking for your headphones…")
    verify(Model.disconnectedMessage(false, "").indexOf("Turn on your Bose headphones") === 0)
    verify(Model.disconnectedMessage(false, "bluetoothctl not found; install BlueZ").indexOf("install BlueZ") > 0)
    verify(Model.disconnectedMessage(false, "Could not run bosectl with python3.").indexOf("pacman") > 0)
  }

  function test_icons() {
    verify(Model.iconGlyph("work") !== Model.iconGlyph("home"))
    compare(Model.iconGlyph("unknown-icon"), Model.GLYPHS.headphones)
    for (var name in Model.ICONS) compare(Model.ICONS[name].length, 2)  // one astral-plane code point
  }

  function test_modeCapacity() {
    var s = sampleState()
    compare(Model.customModeCount(s), 2)
    verify(!Model.canCreateMode(s))
    s.modes.pop()
    verify(Model.canCreateMode(s))
    verify(Model.hasEditableSettings(Model.findMode(s, 2)))
    verify(!Model.hasEditableSettings(Model.findMode(s, 0)))
  }

  function test_stepModeIndex() {
    var s = sampleState()
    compare(Model.stepModeIndex(s, 1), 2)
    compare(Model.stepModeIndex(s, -1), 0)
    s = Model.withActiveMode(s, 3)
    compare(Model.stepModeIndex(s, 1), 0)
    compare(Model.stepModeIndex({ modes: [] }, 1), -1)
  }

  function test_options() {
    var s = sampleState()
    var icons = Model.iconOptions(s, "")
    compare(icons.length, 10)
    compare(icons[0].label, "Commute")
    compare(Model.iconOptions(s, "quiet")[0].value, "quiet")
    compare(Model.sidetoneOptions(s.settings)[2].label, "Medium")
    compare(Model.autoOffOptions(0)[0].label, "Never")
    compare(Model.autoOffOptions(15).length, 7)
    compare(Model.shortcutOptions(s.settings)[0].label, "Battery level")
    var bands = Model.eqBands(s.settings)
    compare(bands.map(function(b) { return b.key }).join(","), "bass,mid,treble")
  }

  function test_args() {
    compare(Model.modeEditArgs(3, "noise_cancel", 8).join(" "), "-j mode-edit 3 --noise-cancel 8")
    compare(Model.modeEditArgs(3, "wind_block", false).join(" "), "-j mode-edit 3 --wind-block off")
    compare(Model.createModeArgs("workout").join(" "), "-j mode-create Workout --icon workout")
    compare(Model.switchModeArgs(2, true).join(" "), "-j mode 2 --prompt")
    compare(Model.switchModeArgs(2, false).join(" "), "-j mode 2")
    compare(Model.settingArgs("eq_bass", -3).join(" "), "-j set eq bass -3")
    compare(Model.settingArgs("multipoint", true).join(" "), "-j set multipoint on")
    compare(Model.settingArgs("nope", 1), null)
  }

  function test_modeNaming() {
    var s = sampleState()
    var work = Model.findMode(s, 2)
    verify(Model.followsIconName(work))
    compare(Model.modeIconArgs(work, "focus").join(" "), "-j mode-edit 2 --icon focus --rename Focus")
    work.name = "Deep Work"
    verify(!Model.followsIconName(work))
    compare(Model.modeIconArgs(work, "focus").join(" "), "-j mode-edit 2 --icon focus")
    verify(!Model.followsIconName(Model.findMode(s, 1)))  // presets never rename
    compare(Model.modeEditArgs(2, "name", "Deep Work").join(" "), "-j mode-edit 2 --rename Deep Work")
    verify(Model.validModeName("Deep Work"))
    verify(!Model.validModeName("   "))
    verify(Model.validModeName("1234567890123456789012345678901"))
    verify(!Model.validModeName("12345678901234567890123456789012"))
    verify(!Model.validModeName("ÅÅÅÅÅÅÅÅÅÅÅÅÅÅÅÅ"))  // 32 UTF-8 bytes
  }

  function test_optimisticUpdates() {
    var s = sampleState()
    var edited = Model.withModeValue(s, 2, "noise_cancel", 6)
    compare(Model.findMode(edited, 2).noise_cancel, 6)
    compare(Model.findMode(s, 2).noise_cancel, 3)  // original untouched
    compare(Model.findMode(Model.withModeValue(s, 2, "icon", "focus"), 2).name, "Focus")
    var renamed = Model.withModeValue(s, 2, "name", "Deep Work")
    compare(Model.findMode(Model.withModeValue(renamed, 2, "icon", "focus"), 2).name, "Deep Work")
    compare(Model.activeMode(Model.withActiveMode(s, 2)).name, "Work")
    compare(Model.withSetting(s, "eq_mid", 5).settings.eq.mid.value, 5)
    compare(Model.withSetting(s, "multipoint", false).settings.multipoint.enabled, false)
    compare(Model.withSetting(s, "auto_off_minutes", "20").settings.auto_off_minutes, 20)
  }
}
