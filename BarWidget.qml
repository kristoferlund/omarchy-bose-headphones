import QtQuick
import Quickshell.Io
import qs.Ui
import "Model.js" as Model

BarWidget {
  id: root
  moduleName: "io.github.kristoferlund.bose"

  // Optional outer widget used for bar popout ownership.
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  readonly property var panel: panelLoader.item
  readonly property bool opened: panel ? panel.opened === true : false
  readonly property bool connected: panel ? panel.connected === true : false
  readonly property bool popoutSwitchClosing: panel ? panel.popoutSwitchClosing === true : false
  readonly property var activeMode: panel ? Model.activeMode(panel.headsetState) : null

  function open() { if (panel) panel.open() }
  function close() { if (panel) panel.close() }
  function toggle() { if (panel) panel.toggle() }
  function refresh() { if (panel) panel.refresh() }
  function closeForPopoutSwitch() { if (panel) panel.closeForPopoutSwitch() }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    target.bar = root.bar
    target.settings = root.settings
    target.anchorItem = button
    target.hostWidget = root.barIdentity
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  IpcHandler {
    target: "io.github.kristoferlund.bose"

    function refresh() { root.broadcast("refresh") }
    function open() { root.open() }
    function close() { root.close() }
    function show() { root.open() }
    function hide() { root.close() }
    function toggle() { root.toggle() }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.connected && root.activeMode ? Model.iconGlyph(root.activeMode.icon) : Model.GLYPHS.headphones
    opacity: root.connected ? 1.0 : 0.48
    tooltipText: root.panel ? Model.tooltip(root.connected, root.panel.headsetState) : "Bose headphones"

    onPressed: function(buttonCode) {
      if (buttonCode === Qt.MiddleButton) root.refresh()
      else root.toggle()
    }
    // Scroll to step through modes without opening the panel.
    onWheelMoved: function(delta) {
      if (root.panel && root.connected) root.panel.stepMode(delta < 0 ? 1 : -1)
    }
  }
}
