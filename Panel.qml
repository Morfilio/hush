pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui

Ui.Panel {
  id: root
  moduleName: "io.github.morfilio.hush"
  manageIpc: false
  implicitWidth: icon.implicitWidth
  implicitHeight: icon.implicitHeight

  readonly property string helper: Qt.resolvedUrl("hush").toString().replace(/^file:\/\//, "")
  readonly property Item panelContent: popup.loadedContent

  // Names and seconds-until-expiry, index-aligned. Names are only replaced when
  // the set changes, so rows are not rebuilt on every poll.
  property var names: []
  property var expiries: []
  property bool listLoaded: false
  property bool promptActive: false

  // Text typed into the name field; the field itself is the source of truth.
  property string draft: ""
  readonly property bool draftValid: validName(draft)
  readonly property bool draftExists: draftValid && names.indexOf(draft) >= 0

  property int cursorIndex: 0
  property bool cursorActive: false
  property bool helpOpen: false

  // Destructive actions need a second click or key press.
  property string armed: ""
  property string armedName: ""

  property string statusKind: ""
  property string statusKey: ""
  property string statusName: ""
  property string flashName: ""
  property string flashKind: ""
  property bool keepResult: false
  property string focusAfterRefresh: ""

  property string actionKind: ""
  property string actionName: ""
  property var pendingCommand: []
  property var queuedCommand: []

  readonly property bool actionBusy: action.inFlight || launch.running || queuedCommand.length > 0
  readonly property bool waiting: promptActive || (action.inFlight && actionKind === "set")
  readonly property bool busy: actionBusy || promptActive

  readonly property color foreground: Commons.Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property color urgent: Commons.Color.urgent

  readonly property string hintTone: {
    if (armed !== "" || statusKind === "error") return "urgent"
    if (statusKey !== "") return "ok"
    if (waiting) return "dim"
    if (draftExists) return "urgent"
    return "dim"
  }
  readonly property string hintText: {
    if (armed === "forget") return fill("confirm.forget", armedName)
    if (armed === "clear") return strings.t("confirm.clear")
    if (statusKey !== "") return fill(statusKey, statusName)
    if (waiting) return strings.t("hint.waiting")
    if (draftExists) return fill("hint.replace", draft)
    return strings.t("hint.default")
  }

  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }

  function validName(name) {
    return typeof name === "string" && /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/.test(name)
  }

  function fill(key, name) {
    var text = strings.t(key)
    return name !== "" ? text.arg(name) : text
  }

  function expiryText(seconds) {
    if (seconds < 0) return ""
    if (seconds >= 5400) {
      var hours = Math.round(seconds / 3600)
      return strings.tn("expires.hours", hours).arg(Commons.I18n.formatNumber(hours))
    }
    if (seconds >= 60) {
      var minutes = Math.round(seconds / 60)
      return strings.tn("expires.minutes", minutes).arg(Commons.I18n.formatNumber(minutes))
    }
    return strings.t("expires.soon")
  }

  // ---------------------------------------------------------------- helper runs

  function refresh() {
    if (action.inFlight || launch.running) return
    listing.begin(["python3", root.helper, "list", "--details"])
  }

  function applyListing(code, out, err) {
    if (code !== 0) {
      // A paste prompt holds the store lock; keep what is on screen.
      if (/Another secret prompt/.test(err)) { promptActive = true; return }
      names = []
      expiries = []
      showStatus("error", "status.listFailed", "")
      return
    }
    promptActive = false
    var rows = null
    try { rows = out.length <= 20000 ? JSON.parse(out) : null } catch (e) { rows = null }
    if (!Array.isArray(rows) || rows.length > 100) {
      names = []
      expiries = []
      showStatus("error", "status.listFailed", "")
      return
    }
    var nextNames = []
    var nextExpiries = []
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i]
      if (!row || typeof row !== "object" || !validName(row.name) || nextNames.indexOf(row.name) >= 0) continue
      var seconds = Number(row.expires_in)
      nextNames.push(row.name)
      nextExpiries.push(isFinite(seconds) ? Math.max(0, Math.min(86400, Math.floor(seconds))) : 0)
    }
    if (nextNames.join("\n") !== names.join("\n")) names = nextNames
    expiries = nextExpiries
    listLoaded = true
    if (statusKey === "status.listFailed") clearStatus()
    if (focusAfterRefresh !== "") {
      var at = names.indexOf(focusAfterRefresh)
      focusAfterRefresh = ""
      if (at >= 0 && opened) {
        cursorIndex = at
        cursorActive = true
        focusList()
        scrollCursorIntoView()
      }
    }
  }

  function act(kind, name) {
    if (busy) return
    disarm()
    clearStatus()
    actionKind = kind
    actionName = name || ""
    var argv = ["python3", root.helper, kind]
    if (name) argv.push(name)
    if (kind === "instructions") argv.push("--copy")
    if (kind === "set") {
      // The paste prompt needs keyboard focus, so the panel gets out of its way.
      pendingCommand = argv
      root.close()
      launch.restart()
    } else if (listing.inFlight) {
      queuedCommand = argv
    } else {
      action.begin(argv)
    }
  }

  function finishAction(code, err) {
    var kind = actionKind
    var name = actionName
    var lockBusy = /Another secret prompt/.test(err)
    actionKind = ""
    actionName = ""
    if (kind === "set") {
      if (code === 0) {
        if (panelContent) panelContent.nameField.text = ""
        focusAfterRefresh = name
        showStatus("ok", "status.saved", name)
        showResult()
      } else if (!/Canceled/.test(err)) {
        // Canceling keeps the typed name and says nothing; real failures explain.
        showStatus("error", lockBusy ? "hint.waiting" : "status.saveFailed", lockBusy ? "" : name)
        showResult()
      }
    } else if (code === 0) {
      if (kind === "reference") { showStatus("ok", "status.reference", name); flash(name, kind) }
      else if (kind === "copy") { showStatus("ok", "status.value", name); flash(name, kind) }
      else if (kind === "remove") showStatus("ok", "status.forgotten", name)
      else if (kind === "clear") showStatus("ok", "status.cleared", "")
      else if (kind === "instructions") showStatus("ok", "status.setup", "")
    } else {
      showStatus("error", lockBusy ? "hint.waiting" : "status.actionFailed", "")
    }
    refresh()
  }

  function showResult() {
    if (opened) return
    keepResult = true
    open()
  }

  // ---------------------------------------------------------------- feedback

  function showStatus(kind, key, name) {
    statusKind = kind
    statusKey = key
    statusName = name
    if (kind === "ok") statusTimer.restart()
    else statusTimer.stop()
  }

  function clearStatus() {
    statusKind = ""
    statusKey = ""
    statusName = ""
    statusTimer.stop()
  }

  function flash(name, kind) {
    flashName = name
    flashKind = kind
    flashTimer.restart()
  }

  function disarm() {
    armed = ""
    armedName = ""
    armTimer.stop()
  }

  function forget(name) {
    if (!name || busy) return
    if (armed === "forget" && armedName === name) { act("remove", name); return }
    clearStatus()
    armed = "forget"
    armedName = name
    armTimer.restart()
  }

  function forgetAll() {
    if (busy || names.length === 0) return
    if (armed === "clear") { act("clear", ""); return }
    clearStatus()
    armed = "clear"
    armedName = ""
    armTimer.restart()
  }

  function paste() {
    if (draftValid && !busy) act("set", draft)
  }

  // ---------------------------------------------------------------- keyboard

  function focusField() {
    cursorActive = false
    if (panelContent) panelContent.nameField.forceActiveFocus()
  }

  function focusList() {
    if (panelContent) panelContent.forceActiveFocus()
  }

  function selectedName() {
    return cursorActive && cursorIndex >= 0 && cursorIndex < names.length ? names[cursorIndex] : ""
  }

  function moveCursor(dy) {
    if (dy === 0) return
    if (names.length === 0) { focusField(); return }
    if (!cursorActive) {
      cursorActive = true
      cursorIndex = dy < 0 ? names.length - 1 : 0
    } else if (dy < 0 && cursorIndex === 0) {
      focusField()
      return
    } else {
      cursorIndex = Math.max(0, Math.min(names.length - 1, cursorIndex + dy))
    }
    scrollCursorIntoView()
  }

  function enterList() {
    if (names.length === 0) return false
    cursorActive = true
    cursorIndex = Math.max(0, Math.min(cursorIndex, names.length - 1))
    focusList()
    scrollCursorIntoView()
    return true
  }

  function hoverRow(index) {
    cursorActive = true
    cursorIndex = index
  }

  function clampCursor() {
    if (names.length === 0) {
      cursorIndex = 0
      if (cursorActive && opened) focusField()
      cursorActive = false
      return
    }
    cursorIndex = Math.max(0, Math.min(cursorIndex, names.length - 1))
  }

  function scrollCursorIntoView() {
    Qt.callLater(function() {
      var content = root.panelContent
      if (!content || !root.cursorActive) return
      var item = content.rows.itemAt(root.cursorIndex)
      if (!item) return
      var flick = content.flick
      var margin = Style.space(6)
      var top = item.mapToItem(flick.contentItem, 0, 0).y
      var bottom = top + item.height
      if (top < flick.contentY + margin) flick.contentY = Math.max(0, top - margin)
      else if (bottom > flick.contentY + flick.height - margin)
        flick.contentY = Math.min(Math.max(0, flick.contentHeight - flick.height), bottom + margin - flick.height)
    })
  }

  onNamesChanged: clampCursor()
  onOpenedChanged: {
    if (opened) {
      if (!keepResult) {
        clearStatus()
        cursorActive = false
        if (panelContent) panelContent.flick.contentY = 0
      }
      keepResult = false
      refresh()
      Qt.callLater(function() { if (root.opened && !root.cursorActive) root.focusField() })
    } else {
      disarm()
      cursorActive = false
    }
  }

  // ---------------------------------------------------------------- processes

  // One helper invocation: settles once it has exited and both streams closed.
  // Output is only kept where it is needed (the name listing); action output
  // such as a private file path never enters QML state.
  component HelperRun: Process {
    id: run
    signal done(int code, string out, string err)
    property bool captureOutput: false
    property bool inFlight: false
    property bool exitSeen: false
    property bool outSeen: false
    property bool errSeen: false
    property int exitValue: 0
    property string outText: ""
    property string errText: ""

    function begin(argv) {
      if (run.inFlight) return false
      run.exitSeen = false
      run.outSeen = false
      run.errSeen = false
      run.outText = ""
      run.errText = ""
      run.command = argv
      run.inFlight = true
      run.running = true
      return true
    }

    function settle(force) {
      if (!run.inFlight || !run.exitSeen || (!force && !(run.outSeen && run.errSeen))) return
      run.inFlight = false
      run.settleFallback.stop()
      run.done(run.exitValue, run.outText, run.errText)
    }

    property Timer settleFallback: Timer { interval: 400; onTriggered: run.settle(true) }

    stdout: StdioCollector {
      onStreamFinished: {
        if (run.captureOutput) run.outText = text
        run.outSeen = true
        run.settle(false)
      }
    }
    stderr: StdioCollector {
      onStreamFinished: {
        run.errText = text.slice(0, 2000)
        run.errSeen = true
        run.settle(false)
      }
    }
    onExited: function(exitCode) {
      run.exitValue = exitCode
      run.exitSeen = true
      run.settle(false)
      if (run.inFlight) run.settleFallback.restart()
    }
  }

  HelperRun {
    id: listing
    captureOutput: true
    onDone: function(code, out, err) {
      root.applyListing(code, out, err)
      if (root.queuedCommand.length > 0) {
        var next = root.queuedCommand
        root.queuedCommand = []
        action.begin(next)
      }
    }
  }

  HelperRun {
    id: action
    onDone: function(code, out, err) { root.finishAction(code, err) }
  }

  Timer {
    id: launch
    interval: 220
    onTriggered: {
      if (listing.inFlight) restart()
      else action.begin(root.pendingCommand)
    }
  }
  Timer { interval: 2000; running: root.opened; repeat: true; onTriggered: root.refresh() }
  Timer {
    id: statusTimer
    interval: 5000
    onTriggered: if (root.statusKind === "ok") root.clearStatus()
  }
  Timer { id: armTimer; interval: 4000; onTriggered: root.disarm() }
  Timer { id: flashTimer; interval: 1600; onTriggered: root.flashName = "" }

  // ---------------------------------------------------------------- UI

  Ui.BarIconButton {
    id: icon
    anchors.fill: parent
    bar: root.bar
    slotSize: Style.bar.statusSlot
    text: "󰌋"
    tooltipText: strings.t("title")
    onPressed: root.toggle()
  }

  Ui.KeyboardPanel {
    id: popup
    anchorItem: icon
    owner: root
    bar: root.bar
    open: root.opened
    contentWidth: popup.fittedContentWidth(Style.space(440))
    contentHeight: popup.fittedContentHeight(popup.loadedContentHeight, Style.space(620))
    focusTarget: popup.loadedContent ? popup.loadedContent.nameField : null

    content: Ui.PanelKeyCatcher {
      id: keys
      property alias nameField: nameInput
      property alias flick: flick
      property alias rows: rowRepeater
      anchors.fill: parent
      implicitHeight: column.implicitHeight
      blocked: nameInput.activeFocus
      onMoveRequested: function(dx, dy) { root.moveCursor(dy) }
      onActivateRequested: {
        var name = root.selectedName()
        if (name) root.act("reference", name)
        else root.enterList()
      }
      onDeleteRequested: root.forget(root.selectedName())
      onCloseRequested: root.armed !== "" ? root.disarm() : root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (t === "c" || t === "C") {
          var name = root.selectedName()
          if (name) root.act("copy", name)
        } else if (t === "/" || t === "n" || t === "a") {
          root.focusField()
        } else if (t === "?") {
          root.helpOpen = !root.helpOpen
        }
      }

      Flickable {
        id: flick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: flick.width
          spacing: Style.space(10)

          // Header: what this is, in one line, with help tucked behind an icon.
          RowLayout {
            width: parent.width
            spacing: Style.space(12)

            Text {
              textFormat: Text.PlainText
              text: "󰌋"
              color: root.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.display
              Layout.alignment: Qt.AlignVCenter
            }

            ColumnLayout {
              Layout.fillWidth: true
              spacing: Style.space(2)

              Text {
                textFormat: Text.PlainText
                Layout.fillWidth: true
                text: strings.t("title")
                color: root.foreground
                font.family: Style.font.family
                font.pixelSize: Style.font.title
                font.bold: true
                elide: Text.ElideRight
              }

              Text {
                textFormat: Text.PlainText
                Layout.fillWidth: true
                text: strings.t("meta")
                color: root.dim
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
              }
            }

            Ui.PanelActionButton {
              iconText: "󰘥"
              tooltipText: strings.t("help.toggle")
              foreground: root.helpOpen ? Commons.Color.accent : root.dim
              hoverColor: Commons.Color.accent
              hasCursor: root.helpOpen
              Layout.alignment: Qt.AlignVCenter
              onClicked: root.helpOpen = !root.helpOpen
            }
          }

          // Add: a name, then one action that opens the private paste prompt.
          RowLayout {
            width: parent.width
            spacing: Style.space(8)

            Ui.TextField {
              id: nameInput
              Layout.fillWidth: true
              placeholderText: strings.t("name.placeholder")
              maximumLength: 64
              validator: RegularExpressionValidator { regularExpression: /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/ }
              onTextChanged: root.draft = text
              onAccepted: root.paste()
              Keys.onPressed: function(event) {
                if ((event.key === Qt.Key_Down || event.key === Qt.Key_Tab) && root.enterList()) {
                  event.accepted = true
                } else if (event.key === Qt.Key_Escape) {
                  if (root.armed !== "") root.disarm()
                  else root.close()
                  event.accepted = true
                }
              }
            }

            Ui.Button {
              text: root.draftExists ? strings.t("replace") : strings.t("paste")
              tooltipText: strings.t("paste.tooltip")
              bordered: true
              foreground: root.draftExists ? root.urgent : Commons.Color.accent
              enabled: root.draftValid && !root.busy
              opacity: enabled ? 1 : 0.5
              Layout.preferredHeight: nameInput.implicitHeight
              Layout.alignment: Qt.AlignVCenter
              onClicked: root.paste()
            }
          }

          // One line for hints, confirmations and results, so nothing pops up
          // elsewhere and the layout does not jump.
          RowLayout {
            width: parent.width
            spacing: Style.space(6)

            Text {
              textFormat: Text.PlainText
              visible: root.hintTone !== "dim"
              text: root.hintTone === "ok" ? "󰄬" : "󰀦"
              color: root.hintTone === "ok" ? Commons.Color.accent : root.urgent
              font.family: Style.font.family
              font.pixelSize: Style.font.bodySmall
              Layout.alignment: Qt.AlignTop
            }

            Text {
              textFormat: Text.PlainText
              Layout.fillWidth: true
              text: root.hintText
              color: root.hintTone === "urgent" ? root.urgent : (root.hintTone === "ok" ? root.foreground : root.dim)
              wrapMode: Text.Wrap
              font.family: Style.font.family
              font.pixelSize: Style.font.bodySmall
            }
          }

          Column {
            visible: root.listLoaded && root.names.length === 0
            width: parent.width
            spacing: Style.space(4)
            topPadding: Style.space(10)
            bottomPadding: Style.space(6)

            Text {
              textFormat: Text.PlainText
              width: parent.width
              horizontalAlignment: Text.AlignHCenter
              text: strings.t("empty.title")
              color: root.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.body
            }

            Text {
              textFormat: Text.PlainText
              width: parent.width
              horizontalAlignment: Text.AlignHCenter
              text: strings.t("empty.body")
              color: root.dim
              wrapMode: Text.Wrap
              font.family: Style.font.family
              font.pixelSize: Style.font.bodySmall
            }
          }

          Column {
            visible: root.names.length > 0
            width: parent.width
            spacing: Style.space(8)

            Ui.PanelSeparator { foreground: root.foreground }

            RowLayout {
              width: parent.width
              spacing: Style.space(8)

              Ui.PanelSectionHeader {
                Layout.fillWidth: true
                text: strings.tn("section.count", root.names.length).arg(Commons.I18n.formatNumber(root.names.length))
                foreground: root.foreground
                elide: Text.ElideRight
              }

              Ui.Button {
                text: root.armed === "clear" ? strings.t("forgetAll.confirm") : strings.t("forgetAll")
                foreground: root.armed === "clear" ? root.urgent : root.dim
                bordered: root.armed === "clear"
                fontSize: Style.font.caption
                horizontalPadding: Style.space(6)
                verticalPadding: Style.space(2)
                enabled: !root.busy
                opacity: enabled ? 1 : 0.5
                Layout.alignment: Qt.AlignVCenter
                onClicked: root.forgetAll()
              }
            }

            Column {
              id: rowsColumn
              width: parent.width
              spacing: Style.space(4)

              Repeater {
                id: rowRepeater
                model: root.names
                delegate: SecretRow { width: rowsColumn.width }
              }
            }
          }

          // Help and setup text stay out of the everyday flow.
          Column {
            visible: root.helpOpen
            width: parent.width
            spacing: Style.space(8)

            Ui.PanelSeparator { foreground: root.foreground }

            Repeater {
              model: ["help.agents", "help.reference", "help.value", "help.storage"]
              Text {
                required property string modelData
                textFormat: Text.PlainText
                width: parent ? parent.width : 0
                text: strings.t(modelData)
                color: root.dim
                wrapMode: Text.Wrap
                font.family: Style.font.family
                font.pixelSize: Style.font.bodySmall
              }
            }

            Text {
              textFormat: Text.PlainText
              width: parent.width
              text: strings.t("help.keys")
              color: root.dim
              wrapMode: Text.Wrap
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }

            Ui.Button {
              text: strings.t("help.setup")
              iconText: "󰆏"
              bordered: true
              fontSize: Style.font.bodySmall
              iconSize: Style.font.bodySmall
              enabled: !root.busy
              opacity: enabled ? 1 : 0.5
              onClicked: root.act("instructions", "")
            }
          }
        }
      }
    }
  }

  // A secret: name and time left, the everyday action as a labeled button,
  // and the raw value and forget as quieter icon actions.
  component SecretRow: Ui.CursorSurface {
    id: row
    required property string modelData
    required property int index
    readonly property int expiresIn: root.expiries.length > index ? root.expiries[index] : -1
    readonly property bool armedHere: root.armed === "forget" && root.armedName === modelData
    readonly property bool flashed: root.flashName === modelData

    hasCursor: root.cursorActive && root.cursorIndex === index
    foreground: root.foreground
    implicitHeight: Math.max(info.implicitHeight, useButton.implicitHeight) + Style.spacing.lg * 2

    HoverHandler { onHoveredChanged: if (hovered) root.hoverRow(row.index) }

    RowLayout {
      anchors.fill: parent
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(6)
      spacing: Style.space(8)

      Text {
        textFormat: Text.PlainText
        text: "󰌋"
        color: row.hasCursor ? root.foreground : root.dim
        font.family: Style.font.family
        font.pixelSize: Style.font.icon
        Layout.alignment: Qt.AlignVCenter
      }

      ColumnLayout {
        id: info
        Layout.fillWidth: true
        Layout.minimumWidth: Style.space(96)
        Layout.alignment: Qt.AlignVCenter
        spacing: Style.space(1)

        Text {
          id: nameText
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: row.modelData
          color: root.foreground
          elide: Text.ElideRight
          font.family: Style.font.family
          font.pixelSize: Style.font.body

          HoverHandler { id: nameHover }
          Ui.PanelToolTip {
            visible: nameHover.hovered && nameText.truncated
            text: row.modelData
          }
        }

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          visible: row.expiresIn >= 0
          text: root.expiryText(row.expiresIn)
          color: row.expiresIn >= 0 && row.expiresIn < 900 ? root.urgent : root.dim
          elide: Text.ElideRight
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }

      Ui.Button {
        id: useButton
        text: strings.t("row.reference")
        iconText: row.flashed && root.flashKind === "reference" ? "󰄬" : "󰍡"
        tooltipText: strings.t("row.reference.tooltip").arg(row.modelData)
        bordered: true
        fontSize: Style.font.bodySmall
        iconSize: Style.font.bodySmall
        horizontalPadding: Style.space(8)
        verticalPadding: Style.space(3)
        enabled: !root.busy
        opacity: enabled ? 1 : 0.5
        Layout.minimumWidth: Style.space(72)
        Layout.preferredWidth: implicitWidth
        Layout.alignment: Qt.AlignVCenter
        onClicked: root.act("reference", row.modelData)
      }

      Ui.PanelActionButton {
        iconText: row.flashed && root.flashKind === "copy" ? "󰄬" : "󰆏"
        tooltipText: strings.t("row.value.tooltip")
        foreground: root.dim
        hoverColor: root.foreground
        enabled: !root.busy
        Layout.alignment: Qt.AlignVCenter
        onClicked: root.act("copy", row.modelData)
      }

      Ui.PanelActionButton {
        iconText: "󰆴"
        tooltipText: strings.t("row.forget.tooltip")
        foreground: row.armedHere ? root.urgent : root.dim
        hoverColor: root.urgent
        hasCursor: row.armedHere
        enabled: !root.busy
        Layout.alignment: Qt.AlignVCenter
        onClicked: root.forget(row.modelData)
      }
    }
  }
}
