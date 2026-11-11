import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import QtQuick.Dialogs
import org.kde.kirigami as Kirigami

// Chimp Loop: a drum machine for any controller. Live looping and step entry on one
// pattern, a sound per pad, a master bus over everything.
Kirigami.ApplicationWindow {
    id: root

    width: Kirigami.Units.gridUnit * 86
    height: Kirigami.Units.gridUnit * 54
    minimumWidth: Kirigami.Units.gridUnit * 64
    minimumHeight: Kirigami.Units.gridUnit * 44
    title: "Chimp Loop"

    // Keys laid out like the pads on screen: Z X C V along the bottom row, then A S D F,
    // Q W E R and 1 2 3 4 for as many rows as there are (the first four pads of each).
    readonly property var keyRows: ["ZXCV", "ASDF", "QWER", "1234"]
    function cellKey(i) {
        const fromBottom = loop.padRows - 1 - Math.floor(i / loop.padColumns), col = i % loop.padColumns
        return fromBottom < 4 && col < 4 ? keyRows[fromBottom][col] : ""
    }
    function cellForKey(k) {
        if (k === "") return null
        const cells = loop.padCells
        for (let i = 0; i < cells.length; i++)
            if (cellKey(i) === k && (cells[i].sound >= 0 || cells[i].fx !== "")) return cells[i]
        return null
    }
    property var keysDown: ({})  // key -> the pad it pressed (the page may change before it's let go)
    function soundColor(i) { return Qt.hsla(i / 16, 0.62, 0.55, 1) }
    function stateText(s) {
        return { empty: "Ready", recording: "Recording", playing: "Playing", overdub: "Overdubbing", stopped: "Stopped" }[s] || s
    }
    readonly property bool typing: saveName.activeFocus || exportName.activeFocus || soundName.activeFocus
                                   || newKitName.activeFocus || padNameField.activeFocus
    property string view: "steps"  // "steps" or "pads"
    readonly property var holdKeys: ({ "G": "roll", "5": "g.stutter4", "6": "g.stutter8", "7": "g.stutter16",
                                       "8": "g.stutter32", "9": "g.tapestop", "0": "g.reverse", "-": "g.half" })
    function how(mods) {
        return (mods & Qt.ShiftModifier) ? "select" : (mods & Qt.ControlModifier) ? "mute"
             : (mods & Qt.AltModifier) ? "solo" : ""
    }
    function keyName(event) {
        if (event.key === Qt.Key_Minus) return "-"
        return event.text ? event.text.toUpperCase() : ""
    }

    Connections {
        target: loop
        function onNotify(message) { root.showPassiveNotification(message, "short") }
        function onPadHit(sound, vel) { pads.flash(sound, vel) }
    }

    // --- keyboard -------------------------------------------------------------------
    Shortcut { sequences: ["Space"]; autoRepeat: false; enabled: !root.typing; onActivated: loop.runAction("record") }
    Shortcut { sequences: ["Return", "Enter"]; autoRepeat: false; enabled: !root.typing; onActivated: loop.runAction("play") }
    Shortcut { sequences: ["Backspace"]; autoRepeat: false; enabled: !root.typing; onActivated: loop.runAction("undo") }
    Shortcut { sequences: ["Ctrl+Backspace", "Delete"]; autoRepeat: false; enabled: !root.typing; onActivated: loop.runAction("clear") }
    Shortcut { sequences: ["Escape"]; enabled: loop.assigning !== ""; onActivated: loop.assign("") }
    Shortcut { sequences: ["T"]; autoRepeat: false; enabled: !root.typing; onActivated: loop.tapTempo() }
    Shortcut { sequences: ["K"]; autoRepeat: false; enabled: !root.typing; onActivated: loop.runAction("page") }
    Shortcut { sequences: ["]"]; autoRepeat: false; enabled: !root.typing; onActivated: loop.runAction("sound_next") }
    Shortcut { sequences: ["["]; autoRepeat: false; enabled: !root.typing; onActivated: loop.runAction("sound_prev") }

    // --- a parameter dial: label, dial, value, LPD8-knob badge, assign button ---------
    component ParamDial: ColumnLayout {
        id: pd
        property var p: ({ id: "", label: "", value: 0, text: "", assigned: "", knob: 0 })
        spacing: 0
        Layout.preferredWidth: Kirigami.Units.gridUnit * 4.2
        QQC2.Label {
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            text: pd.p.label
            font.pointSize: Kirigami.Theme.smallFont.pointSize
            font.bold: true
            elide: Text.ElideRight
        }
        Item {
            Layout.alignment: Qt.AlignHCenter
            implicitWidth: Kirigami.Units.gridUnit * 2.4
            implicitHeight: implicitWidth
            QQC2.Dial {
                id: dial
                anchors.fill: parent
                focusPolicy: Qt.NoFocus
                inputMode: QQC2.Dial.Vertical
                from: 0; to: 1
                onMoved: loop.setParam(pd.p.id, value)
                // follows the hardware knob, except while you drag it here
                Binding on value { value: pd.p.value; when: !dial.pressed }
                TapHandler { onDoubleTapped: loop.resetParam(pd.p.id) }
                QQC2.ToolTip.text: "Drag up/down · double-click resets" + (pd.p.assigned ? " · on " + pd.p.assigned : "")
                QQC2.ToolTip.visible: hovered && !pressed
                QQC2.ToolTip.delay: 600
            }
            Rectangle {  // which LPD8 knob turns it on this page
                visible: pd.p.knob > 0
                anchors.top: parent.top
                anchors.right: parent.right
                anchors.margins: -2
                width: Kirigami.Units.gridUnit * 0.9; height: width; radius: width / 2
                color: Kirigami.Theme.highlightColor
                QQC2.Label {
                    anchors.centerIn: parent
                    text: pd.p.knob
                    color: Kirigami.Theme.highlightedTextColor
                    font.pointSize: Kirigami.Theme.smallFont.pointSize * 0.85
                    font.bold: true
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 0
            QQC2.Label {
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                text: pd.p.text
                font.pointSize: Kirigami.Theme.smallFont.pointSize
                opacity: 0.8
                elide: Text.ElideRight
            }
            QQC2.ToolButton {
                focusPolicy: Qt.NoFocus
                icon.name: "input-gaming"
                icon.width: Kirigami.Units.iconSizes.small * 0.8
                icon.height: icon.width
                implicitWidth: Kirigami.Units.gridUnit * 1.1
                implicitHeight: implicitWidth
                padding: 0
                checkable: true
                checked: loop.assigning === "param:" + pd.p.id
                opacity: checked || pd.p.assigned ? 1 : 0.45
                onClicked: loop.assign(checked ? "param:" + pd.p.id : "")
                QQC2.ToolTip.text: (pd.p.assigned ? "On " + pd.p.assigned + ". " : "") + "Click, then move a knob or fader for it"
                QQC2.ToolTip.visible: hovered
            }
        }
    }

    component ChoiceBox: RowLayout {
        id: cb
        property var c: ({ id: "", label: "", options: [], index: 0 })
        QQC2.Label { text: cb.c.label }
        QQC2.ComboBox {
            focusPolicy: Qt.NoFocus
            model: cb.c.options
            currentIndex: cb.c.index
            onActivated: (i) => loop.setChoice(cb.c.id, i)
        }
    }

    pageStack.initialPage: Kirigami.Page {
        title: "Chimp Loop"
        padding: Kirigami.Units.largeSpacing

        actions: [
            Kirigami.Action {
                displayComponent: QQC2.Label {
                    text: loop.lpd8Note !== "" ? loop.lpd8Note
                          : loop.devices.length ? "Listening to " + loop.devices.join(", ") : "No MIDI controllers"
                    color: loop.lpd8Note !== "" ? Kirigami.Theme.neutralTextColor : Kirigami.Theme.textColor
                    opacity: loop.lpd8Note !== "" ? 1 : 0.7
                }
            }
        ]

        // Pads (ZXCV ASDF QWER 1234; Shift selects, Ctrl mutes, Alt solos) and the held
        // keys (G roll, 5-0 and - glitches) need key releases, so they're handled here.
        Item {
            id: keyCatcher
            focus: true
            Component.onCompleted: forceActiveFocus()
            Keys.onPressed: (event) => {
                if (event.isAutoRepeat) { event.accepted = true; return }
                const k = root.keyName(event)
                const cell = (event.modifiers & Qt.MetaModifier) ? null : root.cellForKey(k)
                if (cell) {
                    root.keysDown[k] = cell
                    if (cell.fx !== "") loop.holdDown(cell.fx)
                    else loop.padDown(cell.sound, root.how(event.modifiers))
                    event.accepted = true
                } else if (root.holdKeys[k] !== undefined) {
                    loop.holdDown(root.holdKeys[k]); event.accepted = true
                }
            }
            Keys.onReleased: (event) => {
                if (event.isAutoRepeat) { event.accepted = true; return }
                const k = root.keyName(event)
                const cell = root.keysDown[k]
                if (cell) {
                    delete root.keysDown[k]
                    if (cell.fx !== "") loop.holdUp(cell.fx)
                    else loop.padUp(cell.sound)
                    event.accepted = true
                } else if (root.holdKeys[k] !== undefined) { loop.holdUp(root.holdKeys[k]); event.accepted = true }
            }
        }

        RowLayout {
            anchors.fill: parent
            spacing: Kirigami.Units.largeSpacing * 2

            // ===== left: transport, steps, pads, pad sound =====
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: Kirigami.Units.largeSpacing

                RowLayout {
                    spacing: Kirigami.Units.smallSpacing
                    QQC2.Button {
                        focusPolicy: Qt.NoFocus
                        icon.name: "media-record"
                        text: ({ empty: "Record", recording: "Close loop", playing: "Overdub", overdub: "Stop overdub", stopped: "Overdub" })[loop.state]
                        highlighted: loop.state === "recording" || loop.state === "overdub"
                        onClicked: loop.runAction("record")
                        QQC2.ToolTip.text: "Space"
                        QQC2.ToolTip.visible: hovered
                    }
                    QQC2.Button {
                        focusPolicy: Qt.NoFocus
                        icon.name: loop.state === "playing" || loop.state === "overdub" ? "media-playback-stop" : "media-playback-start"
                        text: loop.state === "playing" || loop.state === "overdub" ? "Stop" : "Play"
                        enabled: loop.state !== "empty"
                        onClicked: loop.runAction("play")
                        QQC2.ToolTip.text: "Enter"
                        QQC2.ToolTip.visible: hovered
                    }
                    QQC2.Button {
                        focusPolicy: Qt.NoFocus
                        icon.name: "edit-undo"
                        text: "Undo"
                        enabled: loop.layerCount > 0 || loop.state === "recording"
                        onClicked: loop.runAction("undo")
                        QQC2.ToolTip.text: "Backspace: the last layer or step entered"
                        QQC2.ToolTip.visible: hovered
                    }
                    QQC2.Button {
                        focusPolicy: Qt.NoFocus
                        icon.name: "edit-clear-all"
                        text: "Clear"
                        enabled: loop.state !== "empty"
                        onClicked: loop.runAction("clear")
                        QQC2.ToolTip.text: "Delete"
                        QQC2.ToolTip.visible: hovered
                    }
                    Item { Layout.fillWidth: true }
                    Kirigami.Heading {
                        level: 3
                        text: root.stateText(loop.state)
                        color: loop.state === "recording" || loop.state === "overdub" ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.textColor
                    }
                    QQC2.Label {
                        text: loop.timeText + (loop.layerCount ? " · " + loop.layerCount + (loop.layerCount === 1 ? " layer" : " layers") : "")
                        opacity: 0.7
                    }
                }

                Flow {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.largeSpacing
                    RowLayout {
                        QQC2.Label { text: "Tempo" }
                        QQC2.SpinBox {
                            focusPolicy: Qt.ClickFocus
                            from: 40; to: 300
                            value: Math.round(loop.bpm)
                            enabled: loop.state === "empty"
                            onValueModified: loop.setBpm(value)
                        }
                        QQC2.Button {
                            focusPolicy: Qt.NoFocus
                            text: "Tap"
                            enabled: loop.state === "empty"
                            onClicked: loop.tapTempo()
                            QQC2.ToolTip.text: "Tap three or more times (or press T)"
                            QQC2.ToolTip.visible: hovered
                        }
                    }
                    RowLayout {
                        QQC2.Label { text: "Length" }
                        QQC2.ComboBox {
                            focusPolicy: Qt.NoFocus
                            enabled: loop.state === "empty"
                            model: [{ text: "Free", bars: 0 }, { text: "1 bar", bars: 1 }, { text: "2 bars", bars: 2 },
                                    { text: "4 bars", bars: 4 }, { text: "8 bars", bars: 8 }]
                            textRole: "text"
                            currentIndex: Math.max(0, model.findIndex(m => m.bars === loop.bars))
                            onActivated: loop.setBars(model[currentIndex].bars)
                            QQC2.ToolTip.text: "Free: as long as your first take. Bars: recording stops on its own, and step entry starts that long."
                            QQC2.ToolTip.visible: hovered
                        }
                    }
                    RowLayout {
                        QQC2.Label { text: "Quantise" }
                        QQC2.ComboBox {
                            focusPolicy: Qt.NoFocus
                            model: [{ text: "Off", q: 0 }, { text: "1/4", q: 4 }, { text: "1/8", q: 8 }, { text: "1/16", q: 16 }, { text: "1/32", q: 32 }]
                            textRole: "text"
                            currentIndex: Math.max(0, model.findIndex(m => m.q === loop.quantise))
                            onActivated: loop.setQuantise(model[currentIndex].q)
                            QQC2.ToolTip.text: "Applied on playback, so you can change it after recording"
                            QQC2.ToolTip.visible: hovered
                        }
                    }
                    QQC2.Switch {
                        focusPolicy: Qt.NoFocus
                        text: "Metronome"
                        checked: loop.metronome
                        onToggled: loop.setMetronome(checked)
                    }
                    QQC2.Switch {
                        focusPolicy: Qt.NoFocus
                        text: "Fixed velocity"
                        checked: loop.fixedVelocity
                        onToggled: loop.setFixedVelocity(checked)
                        QQC2.ToolTip.text: "Every hit at full strength, however hard you hit the pad"
                        QQC2.ToolTip.visible: hovered
                    }
                    RowLayout {
                        QQC2.Label { text: "Kit" }
                        QQC2.ComboBox {
                            focusPolicy: Qt.NoFocus
                            model: loop.kitNames
                            currentIndex: loop.kitIndex
                            onActivated: loop.setKit(currentIndex)
                        }
                        QQC2.ToolButton {
                            focusPolicy: Qt.NoFocus
                            icon.name: "document-import"
                            text: "Import…"
                            onClicked: kitFolderDialog.open()
                            QQC2.ToolTip.text: "Import a kit: a folder of WAVs (or drop a folder, .chimpkit or .zip on the window)"
                            QQC2.ToolTip.visible: hovered
                        }
                        QQC2.ToolButton {
                            id: kitMenuButton
                            focusPolicy: Qt.NoFocus
                            icon.name: "application-menu"
                            onClicked: kitMenu.popup(kitMenuButton, 0, kitMenuButton.height)
                            QQC2.ToolTip.text: "More for kits"
                            QQC2.ToolTip.visible: hovered && !kitMenu.visible
                            QQC2.Menu {
                                id: kitMenu
                                QQC2.MenuItem {
                                    icon.name: "list-add"
                                    text: "New empty kit…"
                                    onTriggered: { newKitName.text = ""; newKitDialog.open() }
                                }
                                QQC2.MenuItem {
                                    icon.name: "document-import"
                                    text: "Import a kit file (.chimpkit, .zip)…"
                                    onTriggered: kitFileDialog.open()
                                }
                                QQC2.MenuItem {
                                    icon.name: "document-export"
                                    text: "Export " + loop.kitName + " as .chimpkit…"
                                    onTriggered: { kitExportDialog.selectedFile = loop.kitExportUrl; kitExportDialog.open() }
                                }
                                QQC2.MenuSeparator {}
                                QQC2.MenuItem {
                                    icon.name: "folder-open"
                                    text: "Open kits folder"
                                    onTriggered: loop.openKitsFolder()
                                }
                            }
                        }
                    }
                }

                // performance: view, roll, glitches (all held: press and hold)
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing
                    QQC2.Button {
                        focusPolicy: Qt.NoFocus
                        text: "Steps"
                        checkable: true
                        checked: root.view === "steps"
                        onClicked: root.view = "steps"
                    }
                    QQC2.Button {
                        focusPolicy: Qt.NoFocus
                        text: "Pads"
                        checkable: true
                        checked: root.view === "pads"
                        onClicked: root.view = "pads"
                    }
                    Kirigami.Separator { Layout.fillHeight: true; Layout.leftMargin: 6; Layout.rightMargin: 6 }
                    QQC2.Button {
                        id: rollButton
                        focusPolicy: Qt.NoFocus
                        text: "Roll"
                        highlighted: loop.held.indexOf("roll") >= 0
                        onPressed: loop.holdDown("roll")
                        onReleased: loop.holdUp("roll")
                        onCanceled: loop.holdUp("roll")
                        QQC2.ToolTip.text: "Hold (or hold G) and hit pads: they repeat on the beat, and record"
                        QQC2.ToolTip.visible: hovered
                    }
                    QQC2.ComboBox {
                        focusPolicy: Qt.NoFocus
                        model: loop.rollRates
                        currentIndex: loop.rollRateIndex
                        onActivated: (i) => loop.setRollRate(i)
                        implicitWidth: Kirigami.Units.gridUnit * 5
                    }
                    Kirigami.Separator { Layout.fillHeight: true; Layout.leftMargin: 6; Layout.rightMargin: 6 }
                    Repeater {
                        model: loop.glitches
                        delegate: QQC2.Button {
                            required property var modelData
                            focusPolicy: Qt.NoFocus
                            text: modelData.label.replace("Stutter ", "")
                            icon.name: modelData.id.startsWith("stutter") ? "media-playlist-repeat"
                                     : modelData.id === "tapestop" ? "media-playback-pause"
                                     : modelData.id === "reverse" ? "media-seek-backward" : "media-skip-backward"
                            highlighted: loop.glitchMode === modelData.id
                            onPressed: loop.holdDown("g." + modelData.id)
                            onReleased: loop.holdUp("g." + modelData.id)
                            onCanceled: loop.holdUp("g." + modelData.id)
                            QQC2.ToolTip.text: modelData.label + " — hold (key " + modelData.key + ")"
                            QQC2.ToolTip.visible: hovered
                        }
                    }
                    Item { Layout.fillWidth: true }
                }

                // pages: the pads on screen are the controller's, a page of the kit at a time
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing
                    QQC2.Label { text: "Pads"; opacity: 0.7; visible: loop.pageTabs.length > 0 }
                    Repeater {
                        model: loop.pageTabs
                        delegate: QQC2.Button {
                            required property var modelData
                            focusPolicy: Qt.NoFocus
                            checkable: true
                            checked: loop.viewPage === modelData.id
                            flat: !checked
                            font.bold: checked
                            text: modelData.label + " · " + modelData.detail + (modelData.here ? "  ●" : "")
                            onClicked: { loop.setViewPage(modelData.id); keyCatcher.forceActiveFocus() }
                            QQC2.ToolTip.text: modelData.here ? "Your controller is on this page" : "Look at this page (your controller stays on its own)"
                            QQC2.ToolTip.visible: hovered
                        }
                    }
                    QQC2.Label {
                        Layout.leftMargin: Kirigami.Units.largeSpacing
                        visible: loop.handNote !== ""
                        text: loop.handNote
                        font.bold: true
                    }
                    Repeater {  // what each LPD8 program does here; the one it's on stands out
                        model: loop.lpd8Programs
                        delegate: Rectangle {
                            required property var modelData
                            readonly property bool desktop: modelData.text === "desktop"
                            implicitWidth: chip.implicitWidth + Kirigami.Units.smallSpacing * 3
                            implicitHeight: chip.implicitHeight + Kirigami.Units.smallSpacing
                            radius: height / 2
                            color: modelData.here ? (desktop ? Kirigami.Theme.neutralBackgroundColor : Kirigami.Theme.highlightColor)
                                                  : "transparent"
                            border.width: 1
                            border.color: modelData.here ? "transparent" : Qt.alpha(Kirigami.Theme.textColor, 0.25)
                            opacity: modelData.here ? 1 : 0.7
                            QQC2.Label {
                                id: chip
                                anchors.centerIn: parent
                                font: Kirigami.Theme.smallFont
                                color: modelData.here && !parent.desktop ? Kirigami.Theme.highlightedTextColor : Kirigami.Theme.textColor
                                text: "<b>" + modelData.program + "</b> " + modelData.text
                                textFormat: Text.StyledText
                            }
                            QQC2.ToolTip.text: "LPD8 Program " + modelData.program + ": " + (desktop ? "desktop control (not the looper's)" : modelData.text)
                                               + (modelData.here ? ". The LPD8 is on this one." : ". Hold PROGRAM + pad " + modelData.program + " to switch to it.")
                            QQC2.ToolTip.visible: chipHover.hovered
                            HoverHandler { id: chipHover }
                        }
                    }
                    Item { Layout.fillWidth: true }
                    QQC2.Label { text: "Show"; opacity: 0.7 }
                    QQC2.ComboBox {
                        focusPolicy: Qt.NoFocus
                        implicitWidth: Kirigami.Units.gridUnit * 9
                        readonly property var sizes: [0, 8, 12, 16]
                        model: ["Controller's pads", "8 pads", "12 pads", "16 pads"]
                        currentIndex: Math.max(0, sizes.indexOf(loop.padsShown))
                        onActivated: (i) => loop.setPadsShown(sizes[i])
                        QQC2.ToolTip.text: "How many pads to show at once: as many as the controller you're playing has, or a number you pick"
                        QQC2.ToolTip.visible: hovered
                    }
                }

                Kirigami.InlineMessage {
                    Layout.fillWidth: true
                    visible: loop.assigning !== ""
                    type: Kirigami.MessageType.Information
                    text: (loop.assigning.startsWith("param:") ? "Move the knob or fader on your controller for "
                                                               : "Press the pad or button on your controller for ")
                          + loop.assigningLabel + " (Esc cancels)"
                    actions: [ Kirigami.Action { text: "Cancel"; onTriggered: loop.assign("") } ]
                }

                // --- step sequencer: the page's parts x 16 steps (a bar per page) --------
                Rectangle {
                    visible: root.view === "steps"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: Kirigami.Units.gridUnit * 14
                    radius: Kirigami.Units.cornerRadius
                    color: Kirigami.Theme.alternateBackgroundColor
                    border.color: Qt.alpha(Kirigami.Theme.textColor, 0.15)

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: Kirigami.Units.smallSpacing * 2
                        spacing: 2

                        RowLayout {
                            Layout.fillWidth: true
                            Kirigami.Heading { level: 4; text: "Steps" }
                            QQC2.Label {
                                text: "click: on/off · right-click: soft / normal / accent"
                                opacity: 0.55
                                font: Kirigami.Theme.smallFont
                            }
                            Item { Layout.fillWidth: true }
                            QQC2.Label {
                                visible: loop.recordProgress >= 0
                                text: "Recording " + Math.round(loop.recordProgress * 100) + "%"
                                color: Kirigami.Theme.negativeTextColor
                            }
                            Repeater {
                                model: loop.gridPages
                                delegate: QQC2.Button {
                                    required property int index
                                    focusPolicy: Qt.NoFocus
                                    text: "Bar " + (index + 1)
                                    checkable: true
                                    checked: loop.gridPage === index
                                    onClicked: loop.setGridPage(index)
                                    flat: !checked
                                }
                            }
                            QQC2.ToolButton {
                                focusPolicy: Qt.NoFocus
                                icon.name: "go-jump"
                                checkable: true
                                checked: loop.followPlayhead
                                onToggled: loop.setFollow(checked)
                                visible: loop.gridPages > 1
                                QQC2.ToolTip.text: "Follow the playing bar"
                                QQC2.ToolTip.visible: hovered
                            }
                        }

                        Repeater {
                            model: loop.stepSounds
                            delegate: RowLayout {
                                id: row
                                required property int modelData
                                readonly property int sound: modelData
                                readonly property color tint: root.soundColor(sound)
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                spacing: 2

                                readonly property bool isMuted: loop.muted.indexOf(sound) >= 0
                                readonly property bool isSoloed: loop.soloed.indexOf(sound) >= 0
                                readonly property bool silent: isMuted || (loop.soloed.length > 0 && !isSoloed)
                                opacity: silent ? 0.45 : 1
                                Rectangle {  // part name: click selects it for editing
                                    Layout.preferredWidth: Kirigami.Units.gridUnit * 6.5
                                    Layout.minimumWidth: Kirigami.Units.gridUnit * 6.5
                                    Layout.maximumWidth: Kirigami.Units.gridUnit * 6.5
                                    Layout.fillHeight: true
                                    radius: 3
                                    color: loop.selectedSound === row.sound ? Qt.alpha(row.tint, 0.45) : Qt.alpha(row.tint, 0.12)
                                    border.color: loop.selectedSound === row.sound ? row.tint : "transparent"
                                    QQC2.Label {
                                        anchors.fill: parent
                                        anchors.leftMargin: 6
                                        verticalAlignment: Text.AlignVCenter
                                        text: (row.sound + 1) + "  " + (loop.padLabels[row.sound] || "—")
                                        font.pointSize: Kirigami.Theme.smallFont.pointSize
                                        font.bold: loop.selectedSound === row.sound
                                        elide: Text.ElideRight
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        onClicked: { loop.selectPad(row.sound); keyCatcher.forceActiveFocus() }
                                        onDoubleClicked: loop.hitPad(row.sound)
                                    }
                                }
                                QQC2.ToolButton {
                                    focusPolicy: Qt.NoFocus
                                    Layout.fillHeight: true
                                    implicitWidth: Kirigami.Units.gridUnit * 1.3
                                    padding: 0
                                    text: "M"
                                    font.pointSize: Kirigami.Theme.smallFont.pointSize
                                    checkable: true
                                    checked: row.isMuted
                                    onClicked: loop.toggleMute(row.sound)
                                    QQC2.ToolTip.text: "Mute (Ctrl + its key)"
                                    QQC2.ToolTip.visible: hovered
                                }
                                QQC2.ToolButton {
                                    focusPolicy: Qt.NoFocus
                                    Layout.fillHeight: true
                                    implicitWidth: Kirigami.Units.gridUnit * 1.3
                                    padding: 0
                                    text: "S"
                                    font.pointSize: Kirigami.Theme.smallFont.pointSize
                                    checkable: true
                                    checked: row.isSoloed
                                    onClicked: loop.toggleSolo(row.sound)
                                    QQC2.ToolTip.text: "Solo (Alt + its key)"
                                    QQC2.ToolTip.visible: hovered
                                }
                                Repeater {
                                    model: 16
                                    delegate: Rectangle {
                                        id: cell
                                        required property int index
                                        readonly property real vel: (loop.gridCells[row.sound] || [])[index] || 0
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        Layout.preferredWidth: 10
                                        radius: 3
                                        color: vel > 0 ? Qt.alpha(row.tint, 0.35 + 0.65 * vel)
                                             : Math.floor(index / 4) % 2 === 0 ? Qt.alpha(Kirigami.Theme.textColor, 0.11)
                                             : Qt.alpha(Kirigami.Theme.textColor, 0.05)
                                        border.width: loop.stepNow === index ? 2 : (vel >= 0.95 ? 1 : 0)
                                        border.color: loop.stepNow === index ? Kirigami.Theme.highlightColor : Kirigami.Theme.textColor
                                        MouseArea {
                                            anchors.fill: parent
                                            acceptedButtons: Qt.LeftButton | Qt.RightButton
                                            onPressed: keyCatcher.forceActiveFocus()
                                            onClicked: (mouse) => mouse.button === Qt.RightButton || (mouse.modifiers & Qt.ShiftModifier)
                                                ? loop.cycleStepVelocity(row.sound, cell.index)
                                                : loop.toggleStep(row.sound, cell.index)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // --- big pads (Pads view) ---------------------------------------------------
                GridLayout {
                    visible: root.view === "pads"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: Kirigami.Units.gridUnit * 16
                    columns: loop.padColumns
                    rowSpacing: Kirigami.Units.smallSpacing * 2
                    columnSpacing: Kirigami.Units.smallSpacing * 2
                    Repeater {
                        id: bigPads
                        model: loop.padCells
                        delegate: Rectangle {
                            id: bp
                            required property int index
                            required property var modelData
                            readonly property int sound: modelData.sound
                            readonly property string fx: modelData.fx  // an FX pad: roll or a glitch, held
                            readonly property bool gap: sound < 0 && fx === ""
                            readonly property bool fxHeld: fx !== "" && loop.held.indexOf(fx) >= 0
                            readonly property color base: fx !== "" ? Kirigami.Theme.highlightColor : root.soundColor(Math.max(0, sound))
                            readonly property bool isMuted: loop.muted.indexOf(sound) >= 0
                            readonly property bool isSoloed: loop.soloed.indexOf(sound) >= 0
                            property real glow: 0
                            function flash(vel) { glow = 0.35 + 0.65 * vel; bpFade.restart() }
                            NumberAnimation { id: bpFade; target: bp; property: "glow"; to: 0; duration: 260; easing.type: Easing.OutQuad }
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.preferredWidth: 10
                            Layout.preferredHeight: 10
                            radius: Kirigami.Units.cornerRadius * 2
                            enabled: !gap
                            opacity: gap ? 0 : fx === "" && (isMuted || (loop.soloed.length > 0 && !isSoloed)) ? 0.45 : 1
                            color: Qt.tint(Qt.alpha(base, 0.22), Qt.alpha(base, fxHeld ? 0.8 : glow))
                            border.width: fx === "" && loop.selectedSound === sound ? 3 : 1
                            border.color: fx === "" && loop.selectedSound === sound ? Qt.lighter(base, 1.6) : Qt.alpha(base, 0.7)
                            MouseArea {
                                anchors.fill: parent
                                acceptedButtons: Qt.LeftButton | Qt.RightButton
                                onPressed: (mouse) => {
                                    keyCatcher.forceActiveFocus()
                                    if (bp.fx !== "") loop.holdDown(bp.fx)
                                    else if (mouse.button === Qt.RightButton) loop.padDown(bp.sound, "select")
                                    else loop.padDown(bp.sound, root.how(mouse.modifiers))
                                }
                                onReleased: bp.fx !== "" ? loop.holdUp(bp.fx) : loop.padUp(bp.sound)
                                onCanceled: if (bp.fx !== "") loop.holdUp(bp.fx)
                            }
                            DropArea {
                                id: bpDrop
                                enabled: bp.fx === ""
                                anchors.fill: parent
                                onDropped: (drop) => { loop.droppedOnPad(bp.sound, drop.urls); drop.accept() }
                            }
                            Rectangle {
                                anchors.fill: parent
                                radius: parent.radius
                                visible: bpDrop.containsDrag
                                color: "transparent"
                                border.width: 3
                                border.color: Kirigami.Theme.highlightColor
                            }
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: Kirigami.Units.smallSpacing * 2
                                RowLayout {
                                    QQC2.Label { text: bp.fx !== "" ? "FX" : bp.sound + 1; font.bold: true; opacity: 0.7 }
                                    Item { Layout.fillWidth: true }
                                    QQC2.Label { text: root.cellKey(bp.index); opacity: 0.5; font.family: "monospace" }
                                }
                                Item { Layout.fillHeight: true }
                                RowLayout {
                                    Kirigami.Heading {
                                        Layout.fillWidth: true
                                        level: 2
                                        text: bp.fx !== "" ? bp.modelData.label : (loop.padLabels[bp.sound] || "—")
                                        elide: Text.ElideRight
                                    }
                                    QQC2.ToolButton {
                                        visible: bp.fx === ""
                                        focusPolicy: Qt.NoFocus
                                        text: "M"; checkable: true; checked: bp.isMuted
                                        onClicked: loop.toggleMute(bp.sound)
                                    }
                                    QQC2.ToolButton {
                                        visible: bp.fx === ""
                                        focusPolicy: Qt.NoFocus
                                        text: "S"; checkable: true; checked: bp.isSoloed
                                        onClicked: loop.toggleSolo(bp.sound)
                                    }
                                }
                            }
                        }
                    }
                }

                // --- pads + the selected pad's sound --------------------------------------
                RowLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: false
                    Layout.preferredHeight: Kirigami.Units.gridUnit * 15.5
                    Layout.minimumHeight: Kirigami.Units.gridUnit * 15.5
                    Layout.maximumHeight: Kirigami.Units.gridUnit * 15.5
                    spacing: Kirigami.Units.largeSpacing

                    GridLayout {
                        id: pads
                        visible: root.view === "steps"
                        columns: loop.padColumns
                        rowSpacing: 4
                        columnSpacing: 4
                        Layout.fillHeight: true
                        Layout.preferredWidth: Kirigami.Units.gridUnit * 18
                        Layout.maximumWidth: Kirigami.Units.gridUnit * 18
                        property bool assignMode: assignSwitch.checked

                        function flash(sound, vel) {
                            for (const rep of [padRepeater, bigPads]) {
                                for (let i = 0; i < rep.count; i++) {
                                    const it = rep.itemAt(i)
                                    if (it && it.sound === sound) it.flash(vel)
                                }
                            }
                        }

                        Repeater {
                            id: padRepeater
                            model: loop.padCells
                            delegate: Rectangle {
                                id: pad
                                required property int index
                                required property var modelData
                                readonly property int sound: modelData.sound
                                readonly property string fx: modelData.fx
                                readonly property bool gap: sound < 0 && fx === ""
                                readonly property bool fxHeld: fx !== "" && loop.held.indexOf(fx) >= 0
                                enabled: !gap
                                opacity: gap ? 0 : 1
                                property real glow: 0
                                function flash(vel) { glow = 0.35 + 0.65 * vel; fade.restart() }
                                NumberAnimation { id: fade; target: pad; property: "glow"; to: 0; duration: 260; easing.type: Easing.OutQuad }

                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                Layout.preferredWidth: 10
                                Layout.preferredHeight: 10
                                radius: Kirigami.Units.cornerRadius * 1.5
                                readonly property color base: fx !== "" ? Kirigami.Theme.highlightColor : root.soundColor(Math.max(0, sound))
                                readonly property bool chosen: fx === "" && loop.selectedSound === sound
                                color: Qt.tint(Qt.alpha(base, 0.22), Qt.alpha(base, fxHeld ? 0.8 : glow))
                                border.width: loop.assigning === "sound:" + sound ? 3 : chosen ? 2 : 1
                                border.color: loop.assigning === "sound:" + sound ? Kirigami.Theme.highlightColor
                                            : chosen ? Qt.lighter(base, 1.5) : Qt.alpha(base, 0.7)

                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 5
                                    spacing: 0
                                    RowLayout {
                                        QQC2.Label { text: pad.fx !== "" ? "FX" : (pad.sound + 1); font.bold: true; opacity: 0.7; font.pointSize: Kirigami.Theme.smallFont.pointSize }
                                        Item { Layout.fillWidth: true }
                                        QQC2.Label { text: root.cellKey(pad.index); opacity: 0.5; font.family: "monospace"; font.pointSize: Kirigami.Theme.smallFont.pointSize }
                                    }
                                    Item { Layout.fillHeight: true }
                                    QQC2.Label {
                                        Layout.fillWidth: true
                                        text: pad.fx !== "" ? pad.modelData.label : (loop.padLabels[pad.sound] || "—")
                                        font.bold: true
                                        elide: Text.ElideRight
                                    }
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                                    onPressed: (mouse) => {
                                        keyCatcher.forceActiveFocus()
                                        if (pad.fx !== "") loop.holdDown(pad.fx)
                                        else if (pads.assignMode) loop.assign("sound:" + pad.sound)
                                        else if (mouse.button === Qt.RightButton) loop.padDown(pad.sound, "select")
                                        else loop.padDown(pad.sound, root.how(mouse.modifiers))
                                    }
                                    onReleased: pad.fx !== "" ? loop.holdUp(pad.fx) : loop.padUp(pad.sound)
                                    onCanceled: if (pad.fx !== "") loop.holdUp(pad.fx)
                                }
                                DropArea {
                                    id: padDrop
                                    enabled: pad.fx === ""
                                    anchors.fill: parent
                                    onDropped: (drop) => { loop.droppedOnPad(pad.sound, drop.urls); drop.accept() }
                                }
                                Rectangle {
                                    anchors.fill: parent
                                    radius: parent.radius
                                    visible: padDrop.containsDrag
                                    color: "transparent"
                                    border.width: 3
                                    border.color: Kirigami.Theme.highlightColor
                                }
                            }
                        }
                    }

                    // the selected pad's sound
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        radius: Kirigami.Units.cornerRadius
                        color: Kirigami.Theme.alternateBackgroundColor
                        border.color: Qt.alpha(root.soundColor(loop.selectedSound), 0.8)

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: Kirigami.Units.smallSpacing * 2
                            spacing: Kirigami.Units.smallSpacing

                            RowLayout {
                                Layout.fillWidth: true
                                Rectangle { width: 12; height: 12; radius: 6; color: root.soundColor(loop.selectedSound) }
                                Kirigami.Heading {
                                    level: 4
                                    text: "Pad " + (loop.selectedSound + 1) + " · " + (loop.padLabels[loop.selectedSound] || "")
                                }
                                QQC2.Button {
                                    focusPolicy: Qt.NoFocus
                                    icon.name: "document-open"
                                    text: "Sample…"
                                    onClicked: { sampleDialog.pad = loop.selectedSound; sampleDialog.open() }
                                    QQC2.ToolTip.text: loop.kitIsBuiltIn
                                        ? "Load a WAV on this pad. The built-in kit stays as it is: you get an editable copy of it"
                                        : "Load a WAV on this pad (or drop one on it). The old sample is kept in the kit's replaced folder"
                                    QQC2.ToolTip.visible: hovered
                                }
                                QQC2.ToolButton {
                                    focusPolicy: Qt.NoFocus
                                    icon.name: "edit-rename"
                                    enabled: (loop.padLabels[loop.selectedSound] || "") !== ""
                                    onClicked: { padNameField.text = loop.padLabels[loop.selectedSound]; renamePadDialog.open() }
                                    QQC2.ToolTip.text: "Rename this pad"
                                    QQC2.ToolTip.visible: hovered
                                }
                                QQC2.ToolButton {
                                    focusPolicy: Qt.NoFocus
                                    icon.name: "edit-clear"
                                    enabled: (loop.padLabels[loop.selectedSound] || "") !== ""
                                    onClicked: loop.clearPad(loop.selectedSound)
                                    QQC2.ToolTip.text: "Empty this pad (its sample goes to the kit's replaced folder)"
                                    QQC2.ToolTip.visible: hovered
                                }
                                Item { Layout.fillWidth: true }
                                QQC2.Label {
                                    text: "Select a part: click its name, right-click a pad, Shift + its key, or hold Select + pad"
                                    opacity: 0.5
                                    font: Kirigami.Theme.smallFont
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Repeater {
                                    model: loop.padChoices
                                    delegate: ChoiceBox { required property var modelData; c: modelData }
                                }
                                QQC2.Switch {
                                    focusPolicy: Qt.NoFocus
                                    text: "Reverse"
                                    checked: loop.padReverse
                                    onToggled: loop.setSwitch("p.reverse", checked)
                                }
                                Item { Layout.fillWidth: true }
                                QQC2.ToolButton {
                                    focusPolicy: Qt.NoFocus
                                    icon.name: "edit-reset"
                                    onClicked: loop.resetPad()
                                    QQC2.ToolTip.text: "Reset this pad's sound"
                                    QQC2.ToolTip.visible: hovered
                                }
                            }
                            GridLayout {
                                Layout.fillWidth: true
                                columns: 6
                                rowSpacing: Kirigami.Units.smallSpacing
                                columnSpacing: 0
                                Repeater {
                                    model: 12
                                    delegate: ParamDial {
                                        required property int index
                                        Layout.fillWidth: true
                                        p: loop.padParams[index] || p
                                    }
                                }
                            }
                        }
                    }
                }

                RowLayout {
                    QQC2.Switch {
                        id: assignSwitch
                        focusPolicy: Qt.NoFocus
                        text: "Assign pads"
                        onToggled: if (!checked) loop.assign("")
                        QQC2.ToolTip.text: "Click a pad here, then hit the pad on your controller that should play it. Unassigned notes 36–51 play sounds 1–16."
                        QQC2.ToolTip.visible: hovered
                    }
                    Item { Layout.fillWidth: true }
                    QQC2.Label { text: loop.latencyText; opacity: 0.6 }
                }
            }

            // ===== right: master, knobs, controller, loops, export =====
            QQC2.ScrollView {
                Layout.preferredWidth: Kirigami.Units.gridUnit * 22
                Layout.fillHeight: true
                contentWidth: availableWidth

                ColumnLayout {
                    width: parent.width
                    spacing: Kirigami.Units.largeSpacing

                    RowLayout {
                        Layout.fillWidth: true
                        Kirigami.Heading { level: 3; text: "Sound" }
                        QQC2.Label {
                            Layout.fillWidth: true
                            text: loop.soundName
                            elide: Text.ElideRight
                            opacity: 0.7
                        }
                        QQC2.ToolButton {
                            focusPolicy: Qt.NoFocus
                            icon.name: "go-previous"
                            onClicked: loop.runAction("sound_prev")
                            QQC2.ToolTip.text: "Previous saved sound ([)"
                            QQC2.ToolTip.visible: hovered
                        }
                        QQC2.ToolButton {
                            focusPolicy: Qt.NoFocus
                            icon.name: "go-next"
                            onClicked: loop.runAction("sound_next")
                            QQC2.ToolTip.text: "Next saved sound (])"
                            QQC2.ToolTip.visible: hovered
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        QQC2.TextField {
                            id: soundName
                            Layout.fillWidth: true
                            placeholderText: "Name this sound (kit, pads, master)"
                            onAccepted: if (loop.saveSound(text)) keyCatcher.forceActiveFocus()
                        }
                        QQC2.Button {
                            focusPolicy: Qt.NoFocus
                            icon.name: "document-save"
                            text: "Save"
                            enabled: soundName.text.trim() !== ""
                            onClicked: { loop.saveSound(soundName.text); keyCatcher.forceActiveFocus() }
                        }
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 4
                        Repeater {
                            model: loop.soundNames
                            delegate: QQC2.Button {
                                required property string modelData
                                focusPolicy: Qt.NoFocus
                                text: modelData
                                checkable: true
                                checked: loop.soundName === modelData
                                onClicked: { loop.loadSound(modelData); soundName.text = modelData }
                                QQC2.ToolTip.text: "Switch to this sound (right-click deletes)"
                                QQC2.ToolTip.visible: hovered
                                TapHandler { acceptedButtons: Qt.RightButton; onTapped: loop.deleteSound(modelData) }
                            }
                        }
                    }
                    Kirigami.Separator { Layout.fillWidth: true }

                    RowLayout {
                        Layout.fillWidth: true
                        Kirigami.Heading { level: 3; text: "Master" }
                        Item { Layout.fillWidth: true }
                        QQC2.ComboBox {
                            focusPolicy: Qt.NoFocus
                            model: loop.presets
                            displayText: "Preset…"
                            onActivated: (i) => loop.applyPreset(loop.presets[i])
                        }
                        QQC2.ToolButton {
                            focusPolicy: Qt.NoFocus
                            icon.name: "edit-reset"
                            onClicked: loop.resetMaster()
                            QQC2.ToolTip.text: "Reset the master section"
                            QQC2.ToolTip.visible: hovered
                        }
                    }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        rowSpacing: 2
                        Repeater {
                            model: loop.masterChoices
                            delegate: RowLayout {
                                required property var modelData
                                Layout.columnSpan: 2
                                QQC2.Label { text: modelData.label; Layout.preferredWidth: Kirigami.Units.gridUnit * 6 }
                                QQC2.ComboBox {
                                    Layout.fillWidth: true
                                    focusPolicy: Qt.NoFocus
                                    model: modelData.options
                                    currentIndex: modelData.index
                                    onActivated: (i) => loop.setChoice(modelData.id, i)
                                }
                            }
                        }
                        QQC2.Switch {
                            Layout.columnSpan: 2
                            focusPolicy: Qt.NoFocus
                            text: "Ping-pong delay"
                            checked: loop.pingPong
                            onToggled: loop.setSwitch("m.pingpong", checked)
                        }
                    }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: 4
                        rowSpacing: Kirigami.Units.smallSpacing
                        columnSpacing: 0
                        Repeater {
                            model: 13
                            delegate: ParamDial {
                                required property int index
                                Layout.fillWidth: true
                                p: loop.masterParams[index] || p
                            }
                        }
                    }
                    QQC2.Label {
                        Layout.fillWidth: true
                        opacity: 0.65
                        font: Kirigami.Theme.smallFont
                        text: "Compression + limiting: −" + Math.max(0, loop.compReduction).toFixed(1) + " dB"
                    }

                    Kirigami.Separator { Layout.fillWidth: true }
                    QQC2.Label { text: "Knobs 1–" + loop.knobCount + " turn:" }
                    RowLayout {
                        Layout.fillWidth: true
                        Repeater {
                            model: loop.knobPages
                            delegate: QQC2.Button {
                                required property var modelData
                                focusPolicy: Qt.NoFocus
                                Layout.fillWidth: true
                                text: modelData.label
                                checkable: true
                                checked: loop.knobPage === modelData.id
                                flat: !checked
                                font.bold: checked
                                onClicked: loop.setKnobPage(modelData.id)
                            }
                        }
                    }
                    QQC2.Label {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        opacity: 0.6
                        font: Kirigami.Theme.smallFont
                        text: "Your controller's knobs turn the numbered dials: a page of them at a time, so every dial can be reached. K, or a button assigned to “Knobs: next page”, moves on. Pad pages act on the selected part, which only changes when you choose it (playing pads doesn't move it)."
                    }

                    Kirigami.Separator { Layout.fillWidth: true }
                    QQC2.Button {
                        Layout.fillWidth: true
                        focusPolicy: Qt.NoFocus
                        icon.name: "input-gaming"
                        text: "Controller buttons…"
                        onClicked: actionsSheet.open()
                    }

                    Kirigami.Heading { level: 4; text: "Loops" }
                    RowLayout {
                        Layout.fillWidth: true
                        QQC2.TextField {
                            id: saveName
                            Layout.fillWidth: true
                            placeholderText: "Name"
                            onAccepted: if (loop.saveLoop(text)) keyCatcher.forceActiveFocus()
                        }
                        QQC2.Button {
                            focusPolicy: Qt.NoFocus
                            icon.name: "document-save"
                            text: "Save"
                            enabled: loop.state !== "empty" && loop.state !== "recording" && saveName.text.trim() !== ""
                            onClicked: loop.saveLoop(saveName.text)
                        }
                    }
                    Repeater {
                        model: loop.savedLoops
                        delegate: RowLayout {
                            required property string modelData
                            Layout.fillWidth: true
                            QQC2.Label { Layout.fillWidth: true; text: modelData; elide: Text.ElideRight }
                            QQC2.ToolButton {
                                focusPolicy: Qt.NoFocus
                                icon.name: "document-open"
                                onClicked: { loop.loadLoop(modelData); saveName.text = modelData }
                            }
                            QQC2.ToolButton {
                                focusPolicy: Qt.NoFocus
                                icon.name: "edit-delete"
                                onClicked: loop.deleteLoop(modelData)
                            }
                        }
                    }

                    Kirigami.Heading { level: 4; text: "Export" }
                    QQC2.TextField {
                        id: exportName
                        Layout.fillWidth: true
                        placeholderText: "File name"
                        text: saveName.text
                    }
                    RowLayout {
                        QQC2.Button {
                            focusPolicy: Qt.NoFocus
                            icon.name: "audio-x-wav"
                            text: "WAV"
                            enabled: loop.state !== "empty" && loop.state !== "recording" && exportName.text.trim() !== ""
                            onClicked: loop.exportWav(exportName.text, repeatsBox.value)
                        }
                        QQC2.SpinBox {
                            id: repeatsBox
                            focusPolicy: Qt.ClickFocus
                            from: 1; to: 16; value: 4
                            textFromValue: (v) => "× " + v
                        }
                        QQC2.Button {
                            focusPolicy: Qt.NoFocus
                            icon.name: "audio-midi"
                            text: "MIDI"
                            enabled: loop.state !== "empty" && loop.state !== "recording" && exportName.text.trim() !== ""
                            onClicked: loop.exportMidi(exportName.text)
                        }
                    }
                    QQC2.Label {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        opacity: 0.6
                        font: Kirigami.Theme.smallFont
                        text: "To " + loop.exportDir + ". WAV has every sound setting and effect, and loops seamlessly; MIDI is the hits (General MIDI drums, channel 10)."
                    }
                    Item { Layout.fillHeight: true }
                }
            }
        }
    }

    // --- kits: import a folder, swap a pad's sample, or drop either on the window ---------
    FolderDialog {
        id: kitFolderDialog
        title: "Import a kit: a folder of WAVs"
        onAccepted: loop.importKit(selectedFolder)
    }
    FileDialog {
        id: kitFileDialog
        title: "Import a kit file"
        nameFilters: ["Chimp kits and zips (*.chimpkit *.zip)", "All files (*)"]
        onAccepted: loop.importKit(selectedFile)
    }
    FileDialog {
        id: kitExportDialog
        title: "Export " + loop.kitName
        fileMode: FileDialog.SaveFile
        defaultSuffix: "chimpkit"
        nameFilters: ["Chimp kits (*.chimpkit)"]
        onAccepted: loop.exportKit(selectedFile)
    }
    Kirigami.PromptDialog {
        id: newKitDialog
        title: "New empty kit"
        subtitle: "Then put sounds on it pad by pad: Sample… on a pad, or drop WAVs on the pads."
        standardButtons: Kirigami.Dialog.Ok | Kirigami.Dialog.Cancel
        onOpened: newKitName.forceActiveFocus()
        onAccepted: { loop.newKit(newKitName.text); keyCatcher.forceActiveFocus() }
        onRejected: keyCatcher.forceActiveFocus()
        QQC2.TextField {
            id: newKitName
            placeholderText: "Kit name"
            onAccepted: newKitDialog.accept()
        }
    }
    Kirigami.PromptDialog {
        id: renamePadDialog
        title: "Rename pad " + (loop.selectedSound + 1)
        standardButtons: Kirigami.Dialog.Ok | Kirigami.Dialog.Cancel
        onOpened: { padNameField.forceActiveFocus(); padNameField.selectAll() }
        onAccepted: { loop.renamePad(loop.selectedSound, padNameField.text); keyCatcher.forceActiveFocus() }
        onRejected: keyCatcher.forceActiveFocus()
        QQC2.TextField {
            id: padNameField
            maximumLength: 40
            onAccepted: renamePadDialog.accept()
        }
    }
    FileDialog {
        id: sampleDialog
        property int pad: 0
        title: "A WAV for pad " + (pad + 1)
        nameFilters: ["WAV files (*.wav *.WAV)", "All files (*)"]
        onAccepted: loop.replacePadSample(pad, selectedFile)
    }
    DropArea {
        id: windowDrop
        parent: root.contentItem
        anchors.fill: parent
        z: -1
        onDropped: (drop) => { loop.dropped(drop.urls); drop.accept() }
    }
    Rectangle {
        parent: root.contentItem
        anchors.fill: parent
        z: 100
        visible: windowDrop.containsDrag
        color: Qt.alpha(Kirigami.Theme.highlightColor, 0.12)
        border.width: 3
        border.color: Kirigami.Theme.highlightColor
        QQC2.Label {
            anchors.centerIn: parent
            text: "Drop a kit (folder of WAVs, .chimpkit, .zip) to import it, or a WAV for pad " + (loop.selectedSound + 1)
            font.pointSize: Kirigami.Theme.defaultFont.pointSize * 1.4
        }
    }

    Kirigami.Dialog {
        id: actionsSheet
        title: "Controller buttons"
        preferredWidth: Kirigami.Units.gridUnit * 28
        standardButtons: Kirigami.Dialog.Close
        ColumnLayout {
            spacing: Kirigami.Units.smallSpacing
            QQC2.Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                opacity: 0.7
                text: "Give the looper's controls to buttons on your controller (a KO II key, a pad on a spare LPD8 program): press the controller icon, then the button."
            }
            Repeater {
                model: loop.actionList
                delegate: RowLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        QQC2.Label { text: modelData.label }
                        QQC2.Label {
                            Layout.fillWidth: true
                            text: modelData.assigned || "not assigned"
                            opacity: 0.6
                            font: Kirigami.Theme.smallFont
                            elide: Text.ElideRight
                        }
                    }
                    QQC2.Button {
                        focusPolicy: Qt.NoFocus
                        icon.name: "input-gaming"
                        checkable: true
                        checked: loop.assigning === "action:" + modelData.id
                        onClicked: loop.assign(checked ? "action:" + modelData.id : "")
                    }
                    QQC2.ToolButton {
                        focusPolicy: Qt.NoFocus
                        icon.name: "edit-clear"
                        visible: modelData.assigned !== ""
                        onClicked: loop.unassign("action:" + modelData.id)
                    }
                }
            }
        }
    }
}
