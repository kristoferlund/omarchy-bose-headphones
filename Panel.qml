import QtQuick
import QtQuick.Controls
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root
  moduleName: "io.github.kristoferlund.bose"
  ipcTarget: "io.github.kristoferlund.bose"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null

  // Latest decoded headset state from `bosectl watch` (see Model.parseWatchLine).
  property var headsetState: null
  property bool connected: false
  property bool loading: true
  property string lastError: ""    // last failed command
  property string streamError: ""  // why the watch stream ended; cleared when it delivers again
  property int selectedIndex: -1
  property bool creating: false
  property bool menuOpen: false
  property bool editing: false
  property bool deleteConfirmOpen: false
  property var commandQueue: []

  readonly property var barIdentity: hostWidget || root
  readonly property string address: String(setting("address", "") || "").trim()
  readonly property bool announceModes: setting("announceModes", true) !== false
  readonly property string helperPath: decodeURIComponent(
    String(Qt.resolvedUrl("bosectl")).replace(/^file:\/\//, ""))
  readonly property color contentForeground: root.bar ? root.bar.foreground : Color.foreground
  readonly property string contentFontFamily: root.bar ? root.bar.fontFamily : Style.font.family
  readonly property color dimForeground: Qt.darker(contentForeground, 1.4)
  readonly property var settingsState: headsetState ? headsetState.settings : null
  readonly property var activeMode: Model.activeMode(headsetState)
  readonly property var selectedMode: Model.findMode(headsetState, selectedIndex) || activeMode

  function open() {
    root.controller.show()
  }

  function close() {
    root.creating = false
    root.controller.hide()
  }

  function toggle() {
    if (root.opened) root.close()
    else root.open()
  }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function persistSettings(values) {
    var entry = { id: root.moduleName }
    for (var existing in root.settings) if (existing !== "id") entry[existing] = root.settings[existing]
    for (var key in values) entry[key] = values[key]
    root.settings = entry
    if (root.hostWidget && "settings" in root.hostWidget) root.hostWidget.settings = entry
    if (root.bar && root.bar.shell && typeof root.bar.shell.updateEntryInline === "function")
      root.bar.shell.updateEntryInline(root.moduleName, entry)
  }

  // Restarting the watcher makes the daemon send a fresh state snapshot.
  function refresh() {
    root.lastError = ""
    root.streamError = ""
    if (watchProc.running) watchProc.running = false
    else watchProc.running = true
  }

  function baseArgs() {
    var args = ["python3", root.helperPath]
    if (root.address !== "") args.push("--address", root.address)
    return args
  }

  function applyWatchLine(line) {
    var parsed = Model.parseWatchLine(line)
    if (!parsed) return
    root.streamError = parsed.error
    root.loading = false
    root.connected = parsed.connected
    // Keep optimistic values while the user is dragging or writes are still queued.
    if (!root.editing && root.commandQueue.length === 0 && !commandProc.running)
      root.headsetState = parsed.state
    if (root.selectedIndex >= 0 && !Model.findMode(root.headsetState, root.selectedIndex))
      root.selectedIndex = -1
  }

  function runCommand(key, args) {
    var queue = []
    for (var i = 0; i < root.commandQueue.length; i++)
      if (root.commandQueue[i].key !== key) queue.push(root.commandQueue[i])
    queue.push({ key: key, args: args })
    root.commandQueue = queue
    root.startNextCommand()
  }

  function startNextCommand() {
    if (commandProc.running || root.commandQueue.length === 0) return
    var queue = root.commandQueue.slice(0)
    var next = queue.shift()
    root.commandQueue = queue
    root.lastError = ""
    commandProc.command = root.baseArgs().concat(next.args)
    commandProc.running = true
    commandTimeout.restart()
  }

  function activate(mode) {
    root.selectedIndex = mode.index
    root.creating = false
    if (mode.active) return
    root.headsetState = Model.withActiveMode(root.headsetState, mode.index)
    root.runCommand("mode", Model.switchModeArgs(mode.index, root.announceModes))
  }

  function stepMode(delta) {
    var index = Model.stepModeIndex(root.headsetState, delta)
    var mode = Model.findMode(root.headsetState, index)
    if (mode) root.activate(mode)
  }

  function setModeValue(mode, key, value) {
    if (!mode) return
    root.headsetState = Model.withModeValue(root.headsetState, mode.index, key, value)
    root.runCommand("mode:" + mode.index + ":" + key, Model.modeEditArgs(mode.index, key, value))
  }

  function setSetting(key, value) {
    var args = Model.settingArgs(key, value)
    if (!args) return
    root.headsetState = Model.withSetting(root.headsetState, key, value)
    root.runCommand("setting:" + key, args)
  }

  function createMode(icon) {
    root.creating = false
    root.runCommand("create", Model.createModeArgs(icon))
  }

  function deleteSelectedMode() {
    var mode = root.selectedMode
    root.deleteConfirmOpen = false
    if (!mode || !mode.custom) return
    root.selectedIndex = -1
    root.runCommand("delete", ["-j", "mode-delete", String(mode.index)])
  }

  function setModeIcon(mode, icon) {
    if (!mode) return
    root.headsetState = Model.withModeValue(root.headsetState, mode.index, "icon", icon)
    root.runCommand("mode:" + mode.index + ":icon", Model.modeIconArgs(mode, icon))
  }

  function renameMode(mode, name) {
    var text = String(name || "").trim()
    if (!mode || text === mode.name) return
    if (!Model.validModeName(text)) {
      root.lastError = "Mode names need 1 to 31 characters."
      return
    }
    root.setModeValue(mode, "name", text)
  }

  onAddressChanged: root.refresh()
  // A slider hidden mid-drag never emits released; don't let that freeze state updates.
  onOpenedChanged: if (!root.opened) root.editing = false
  onConnectedChanged: if (!root.connected) root.editing = false

  Process {
    id: watchProc
    command: root.baseArgs().concat(["watch"])
    running: true
    stdout: SplitParser {
      onRead: function(line) { root.applyWatchLine(line) }
    }
    stderr: StdioCollector { id: watchError; waitForEnd: true }
    onExited: function(exitCode) {
      root.connected = false
      var stderrText = String(watchError.text || "").trim().replace(/^bosectl:\s*/, "")
      if (exitCode !== 0)
        root.streamError = stderrText !== "" ? stderrText : "Could not run bosectl with python3."
      watchRestart.restart()
    }
  }

  Timer {
    id: watchRestart
    interval: 2000
    onTriggered: {
      root.loading = true
      watchProc.running = true
    }
  }

  Timer {
    id: commandTimeout
    interval: 70000  // bosectl gives up on the daemon after 60 s
    onTriggered: if (commandProc.running) commandProc.running = false
  }

  Process {
    id: commandProc
    stdout: StdioCollector { id: commandOutput; waitForEnd: true }
    stderr: StdioCollector { id: commandError; waitForEnd: true }
    onExited: function(exitCode) {
      commandTimeout.stop()
      Qt.callLater(function() {
        var message = Model.commandError(exitCode, commandOutput.text, commandError.text)
        if (message !== "") root.lastError = message
        root.startNextCommand()
      })
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    readonly property real bodySpacing: Style.space(10)
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(
      hero.implicitHeight + panel.bodySpacing + body.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: root.menuOpen || root.editing || root.deleteConfirmOpen
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      // ------------------------------------------------------------ hero
      Item {
        id: hero
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        implicitHeight: Math.max(heroIcon.implicitHeight, heroLabels.implicitHeight, refreshButton.implicitHeight)

        Text {
          id: heroIcon
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
          text: root.connected && root.activeMode ? Model.iconGlyph(root.activeMode.icon)
            : (root.connected ? Model.GLYPHS.headphones : Model.GLYPHS.headphonesOff)
          color: root.contentForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.display
          opacity: root.connected ? 1.0 : 0.42
          textFormat: Text.PlainText
        }

        Column {
          id: heroLabels
          anchors.left: heroIcon.right
          anchors.leftMargin: Style.space(12)
          anchors.right: refreshButton.left
          anchors.rightMargin: Style.space(8)
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(1)

          Text {
            width: parent.width
            text: Model.deviceName(root.headsetState)
            color: root.contentForeground
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.title
            font.bold: true
            elide: Text.ElideRight
            textFormat: Text.PlainText
          }

          Text {
            width: parent.width
            text: Model.subtitle(root.connected, root.loading, root.headsetState)
            color: root.dimForeground
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
            font.letterSpacing: 0.8
            elide: Text.ElideRight
            textFormat: Text.PlainText
          }
        }

        PanelActionButton {
          id: refreshButton
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          iconText: Model.GLYPHS.refresh
          tooltipText: "Reconnect and refresh"
          foreground: root.contentForeground
          fontFamily: root.contentFontFamily
          focusable: true
          onClicked: root.refresh()
        }
      }

      ScrollView {
        id: scroll
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: hero.bottom
        anchors.topMargin: panel.bodySpacing
        anchors.bottom: parent.bottom
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: body.implicitHeight > height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff

        Binding {
          target: scroll.contentItem
          property: "interactive"
          value: body.implicitHeight > scroll.height
        }

        Column {
          id: body
          width: Math.max(1, scroll.availableWidth - Style.space(14))
          spacing: Style.space(8)

          Text {
            visible: text !== ""
            width: parent.width
            text: root.lastError !== "" ? root.lastError : (root.connected ? root.streamError : "")
            color: root.bar ? root.bar.urgent : Color.urgent
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
          }

          Text {
            visible: !root.connected
            width: parent.width
            text: Model.disconnectedMessage(root.loading, root.streamError)
            color: root.contentForeground
            font.family: root.contentFontFamily
            font.pixelSize: Style.font.body
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
          }

          // ---------------------------------------------------------- modes
          Column {
            visible: root.connected
            width: parent.width
            spacing: Style.space(6)

            PanelSeparator { width: parent.width; foreground: root.contentForeground }

            Item {
              width: parent.width
              implicitHeight: Math.max(modesHeader.implicitHeight, addButton.implicitHeight)

              PanelSectionHeader {
                id: modesHeader
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                text: "MODES"
                foreground: root.contentForeground
                fontFamily: root.contentFontFamily
              }

              PanelActionButton {
                id: addButton
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                visible: Model.canCreateMode(root.headsetState)
                iconText: Model.GLYPHS.plus
                tooltipText: root.creating ? "Cancel" : "New mode"
                foreground: root.contentForeground
                fontFamily: root.contentFontFamily
                onClicked: root.creating = !root.creating
              }
            }

            Repeater {
              model: Model.modes(root.headsetState)
              ModeRow {
                required property var modelData
                width: parent.width
                mode: modelData
              }
            }

            Text {
              visible: !Model.canCreateMode(root.headsetState) && Model.customModeCount(root.headsetState) > 0
              width: parent.width
              text: "All custom mode slots are in use. Delete one to create a new mode."
              color: root.dimForeground
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
              textFormat: Text.PlainText
            }
          }

          // ---------------------------------------------------------- new mode
          Column {
            visible: root.connected && root.creating
            width: parent.width
            spacing: Style.space(6)

            PanelSeparator { width: parent.width; foreground: root.contentForeground }
            PanelSectionHeader { text: "NEW MODE"; foreground: root.contentForeground; fontFamily: root.contentFontFamily }

            Text {
              width: parent.width
              text: "Pick a mode type. It starts with medium noise cancelling; adjust it after creating."
              color: root.dimForeground
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
              textFormat: Text.PlainText
            }

            IconPicker {
              width: parent.width
              current: ""
              onPicked: function(icon) { root.createMode(icon) }
            }
          }

          // ---------------------------------------------------------- selected mode
          Column {
            id: modeEditor
            readonly property var mode: root.selectedMode
            visible: root.connected && !root.creating && mode !== null
            width: parent.width
            spacing: Style.space(7)

            PanelSeparator { width: parent.width; foreground: root.contentForeground }

            Item {
              width: parent.width
              implicitHeight: Math.max(editorHeader.implicitHeight, deleteButton.implicitHeight)

              PanelSectionHeader {
                id: editorHeader
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                text: modeEditor.mode ? String(modeEditor.mode.name).toUpperCase() + " SETTINGS" : ""
                foreground: root.contentForeground
                fontFamily: root.contentFontFamily
              }

              PanelActionButton {
                id: deleteButton
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                visible: modeEditor.mode !== null && modeEditor.mode.custom === true
                iconText: Model.GLYPHS.remove
                tooltipText: "Delete mode"
                foreground: root.contentForeground
                fontFamily: root.contentFontFamily
                onClicked: root.deleteConfirmOpen = true
              }
            }

            Text {
              visible: modeEditor.mode !== null && !Model.hasEditableSettings(modeEditor.mode)
              width: parent.width
              text: "This is a Bose preset; its settings are fixed."
              color: root.dimForeground
              font.family: root.contentFontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
              textFormat: Text.PlainText
            }

            // Noise cancelling
            Column {
              visible: modeEditor.mode !== null && modeEditor.mode.editable.noise_cancel
              width: parent.width
              spacing: Style.space(4)

              LabelRow {
                width: parent.width
                label: "Noise cancelling"
                value: modeEditor.mode
                  ? String(ncSlider.dragging ? Math.round(ncSlider.liveValue) : modeEditor.mode.noise_cancel)
                    + " / " + modeEditor.mode.noise_cancel_max
                  : ""
              }

              PanelSlider {
                id: ncSlider
                width: parent.width
                bar: root.bar
                minimum: 0
                maximum: modeEditor.mode ? modeEditor.mode.noise_cancel_max : 10
                step: 1
                integer: true
                tickCount: modeEditor.mode ? modeEditor.mode.noise_cancel_max + 1 : 0
                value: modeEditor.mode ? modeEditor.mode.noise_cancel : 0
                onMoved: root.editing = true
                onReleased: function(value) {
                  root.editing = false
                  root.setModeValue(modeEditor.mode, "noise_cancel", Math.round(value))
                }
              }
            }

            Toggle {
              visible: modeEditor.mode !== null && modeEditor.mode.editable.wind_block
              width: parent.width
              label: "Wind Block"
              description: "Reduces wind noise outdoors."
              checked: modeEditor.mode !== null && modeEditor.mode.wind_block === true
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              onClicked: root.setModeValue(modeEditor.mode, "wind_block", !checked)
            }

            Toggle {
              visible: modeEditor.mode !== null && modeEditor.mode.editable.auto_cnc
              width: parent.width
              label: "ActiveSense"
              description: "Automatically adjusts noise cancelling when things get loud."
              checked: modeEditor.mode !== null && modeEditor.mode.auto_cnc === true
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              onClicked: root.setModeValue(modeEditor.mode, "auto_cnc", !checked)
            }

            Toggle {
              visible: modeEditor.mode !== null && modeEditor.mode.editable.anc_toggle
              width: parent.width
              label: "Noise cancelling on"
              checked: modeEditor.mode !== null && modeEditor.mode.anc_toggle === true
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              onClicked: root.setModeValue(modeEditor.mode, "anc_toggle", !checked)
            }

            Column {
              visible: modeEditor.mode !== null && modeEditor.mode.editable.spatial
              width: parent.width
              spacing: Style.space(4)

              LabelRow { width: parent.width; label: "Immersive audio" }

              ButtonGroup {
                options: Model.spatialOptions()
                value: modeEditor.mode && modeEditor.mode.spatial ? modeEditor.mode.spatial : "off"
                foreground: root.contentForeground
                fontFamily: root.contentFontFamily
                onChanged: function(value) { root.setModeValue(modeEditor.mode, "spatial", value) }
              }
            }

            Column {
              visible: modeEditor.mode !== null && modeEditor.mode.custom === true
              width: parent.width
              spacing: Style.space(4)

              LabelRow { width: parent.width; label: "Name" }

              TextField {
                id: nameField
                width: parent.width
                text: modeEditor.mode ? modeEditor.mode.name : ""
                maximumLength: 31
                placeholderText: "Mode name"
                foreground: root.contentForeground
                font.family: root.contentFontFamily
                onActiveFocusChanged: {
                  root.editing = activeFocus
                  if (!activeFocus) root.renameMode(modeEditor.mode, text)
                }
                onAccepted: keyCatcher.forceActiveFocus()
              }

              LabelRow { width: parent.width; label: "Mode type" }

              IconPicker {
                width: parent.width
                current: modeEditor.mode ? modeEditor.mode.icon : ""
                onPicked: function(icon) { root.setModeIcon(modeEditor.mode, icon) }
              }
            }
          }

          // ---------------------------------------------------------- sound
          Column {
            visible: root.connected && Model.eqBands(root.settingsState).length > 0
            width: parent.width
            spacing: Style.space(6)

            PanelSeparator { width: parent.width; foreground: root.contentForeground }
            PanelSectionHeader { text: "EQUALIZER"; foreground: root.contentForeground; fontFamily: root.contentFontFamily }

            Repeater {
              model: Model.eqBands(root.settingsState)
              Column {
                required property var modelData
                width: parent.width
                spacing: Style.space(3)

                LabelRow {
                  width: parent.width
                  label: modelData.label
                  value: Model.signedLabel(eqSlider.dragging ? eqSlider.liveValue : modelData.value)
                  onValueDoubleClicked: root.setSetting("eq_" + modelData.key, 0)
                }

                PanelSlider {
                  id: eqSlider
                  width: parent.width
                  bar: root.bar
                  minimum: modelData.min
                  maximum: modelData.max
                  step: 1
                  integer: true
                  value: modelData.value
                  onMoved: root.editing = true
                  onReleased: function(value) {
                    root.editing = false
                    root.setSetting("eq_" + modelData.key, Math.round(value))
                  }
                }
              }
            }
          }

          // ---------------------------------------------------------- headset
          Column {
            visible: root.connected && root.settingsState !== null
            width: parent.width
            spacing: Style.space(7)

            PanelSeparator { width: parent.width; foreground: root.contentForeground }
            PanelSectionHeader { text: "HEADSET"; foreground: root.contentForeground; fontFamily: root.contentFontFamily }

            Toggle {
              visible: !!(root.settingsState && root.settingsState.multipoint && root.settingsState.multipoint.supported)
              width: parent.width
              label: "Multipoint"
              description: "Stay connected to two devices at once."
              checked: !!(root.settingsState && root.settingsState.multipoint && root.settingsState.multipoint.enabled)
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              onClicked: root.setSetting("multipoint", !checked)
            }

            Toggle {
              visible: !!(root.settingsState && root.settingsState.voice_prompts)
              width: parent.width
              label: "Voice prompts"
              checked: !!(root.settingsState && root.settingsState.voice_prompts && root.settingsState.voice_prompts.enabled)
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              onClicked: root.setSetting("voice_prompts", !checked)
            }

            SettingDropdown {
              visible: Model.sidetoneOptions(root.settingsState).length > 0
              label: "Self voice on calls"
              value: root.settingsState && root.settingsState.sidetone ? root.settingsState.sidetone.mode : ""
              options: Model.sidetoneOptions(root.settingsState)
              onPicked: function(value) { root.setSetting("sidetone", value) }
            }

            SettingDropdown {
              visible: !!(root.settingsState && root.settingsState.auto_off_minutes !== undefined)
              label: "Auto-off"
              value: root.settingsState ? String(root.settingsState.auto_off_minutes) : ""
              options: Model.autoOffOptions(root.settingsState ? root.settingsState.auto_off_minutes : 0)
              onPicked: function(value) { root.setSetting("auto_off_minutes", Number(value)) }
            }

            SettingDropdown {
              visible: Model.shortcutOptions(root.settingsState).length > 0
              label: "Shortcut button"
              value: root.settingsState && root.settingsState.shortcut ? root.settingsState.shortcut.action : ""
              options: Model.shortcutOptions(root.settingsState)
              onPicked: function(value) { root.setSetting("shortcut", value) }
            }

            Toggle {
              width: parent.width
              label: "Announce mode changes"
              description: "Play the headset voice prompt when switching modes from the bar."
              checked: root.announceModes
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              onClicked: root.persistSettings({ announceModes: !checked })
            }

            Toggle {
              visible: !!(root.settingsState && root.settingsState.mode_persistence !== undefined)
              width: parent.width
              label: "Remember last mode"
              description: "Start in the mode you used last instead of the default."
              checked: !!(root.settingsState && root.settingsState.mode_persistence)
              foreground: root.contentForeground
              fontFamily: root.contentFontFamily
              onClicked: root.setSetting("mode_persistence", !checked)
            }
          }

          Item { width: parent.width; height: Style.space(2) }
        }
      }

      ConfirmDialog {
        anchors.fill: parent
        z: 10
        opened: root.deleteConfirmOpen
        message: "Delete the " + (root.selectedMode ? root.selectedMode.name : "") + " mode from the headset?"
        confirmText: "Delete"
        foreground: root.contentForeground
        fontFamily: root.contentFontFamily
        onCanceled: root.deleteConfirmOpen = false
        onConfirmed: root.deleteSelectedMode()
      }
    }
  }

  // -------------------------------------------------------------- components

  component LabelRow: Item {
    id: labelRow
    property string label: ""
    property string value: ""
    signal valueDoubleClicked()
    implicitHeight: Math.max(labelText.implicitHeight, valueText.implicitHeight)

    Text {
      id: labelText
      anchors.left: parent.left
      anchors.right: valueText.left
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      text: labelRow.label
      color: root.contentForeground
      font.family: root.contentFontFamily
      font.pixelSize: Style.font.body
      elide: Text.ElideRight
      textFormat: Text.PlainText
    }

    Text {
      id: valueText
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      text: labelRow.value
      color: root.dimForeground
      font.family: root.contentFontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
      textFormat: Text.PlainText

      MouseArea {
        anchors.fill: parent
        onDoubleClicked: labelRow.valueDoubleClicked()
      }
    }
  }

  component SettingDropdown: Column {
    id: settingDropdown
    property string label: ""
    property string value: ""
    property var options: []
    signal picked(string value)
    width: parent ? parent.width : 0
    spacing: Style.space(4)

    LabelRow { width: parent.width; label: settingDropdown.label }

    Dropdown {
      width: parent.width
      showLabel: false
      value: settingDropdown.value
      options: settingDropdown.options
      foreground: root.contentForeground
      fontFamily: root.contentFontFamily
      onPopupOpenChanged: root.menuOpen = popupOpen
      onChanged: function(value) { settingDropdown.picked(value) }
    }
  }

  component IconPicker: Flow {
    id: picker
    property string current: ""
    signal picked(string icon)
    spacing: Style.space(4)

    Repeater {
      model: Model.iconOptions(root.headsetState, picker.current)
      Button {
        required property var modelData
        iconText: modelData.icon
        iconSize: Style.font.subtitle
        tooltipText: modelData.tooltip
        selected: modelData.value === picker.current
        bordered: true
        foreground: root.contentForeground
        fontFamily: root.contentFontFamily
        onClicked: if (modelData.value !== picker.current) picker.picked(modelData.value)
      }
    }
  }

  component ModeRow: Rectangle {
    id: row
    property var mode: null
    readonly property bool selected: root.selectedMode !== null && mode !== null && root.selectedMode.index === mode.index
    implicitHeight: rowContent.implicitHeight + Style.space(12)
    radius: Style.cornerRadius
    color: selected ? Style.selectedFillFor(root.contentForeground, Color.accent)
      : (rowMouse.containsMouse ? Style.hoverFillFor(root.contentForeground, Color.accent) : "transparent")

    Item {
      id: rowContent
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(8)
      anchors.rightMargin: Style.space(8)
      implicitHeight: Math.max(modeIcon.implicitHeight, modeLabels.implicitHeight)

      Text {
        id: modeIcon
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        width: Style.space(30)
        horizontalAlignment: Text.AlignHCenter
        text: row.mode ? Model.iconGlyph(row.mode.icon) : ""
        color: row.mode && row.mode.active ? Color.accent : root.contentForeground
        font.family: root.contentFontFamily
        font.pixelSize: Style.font.iconLarge
        textFormat: Text.PlainText
      }

      Column {
        id: modeLabels
        anchors.left: modeIcon.right
        anchors.leftMargin: Style.space(10)
        anchors.right: activeLabel.left
        anchors.rightMargin: Style.space(8)
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(1)

        Text {
          width: parent.width
          text: row.mode ? row.mode.name : ""
          color: root.contentForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.body
          font.bold: row.mode !== null && row.mode.active === true
          elide: Text.ElideRight
          textFormat: Text.PlainText
        }

        Text {
          width: parent.width
          text: Model.modeSummary(row.mode)
          color: root.dimForeground
          font.family: root.contentFontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
          textFormat: Text.PlainText
        }
      }

      Text {
        id: activeLabel
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        text: row.mode && row.mode.active ? "ACTIVE" : ""
        color: Color.accent
        font.family: root.contentFontFamily
        font.pixelSize: Style.font.caption
        font.bold: true
        font.letterSpacing: 0.8
        textFormat: Text.PlainText
      }
    }

    MouseArea {
      id: rowMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: if (row.mode) root.activate(row.mode)
    }
  }
}
