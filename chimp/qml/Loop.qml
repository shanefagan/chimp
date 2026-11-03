import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
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

    // Keys laid out like the pads: sound 1 bottom-left, 16 top-right.
    readonly property var padKeys: ["Z", "X", "C", "V", "A", "S", "D", "F", "Q", "W", "E", "R", "1", "2", "3", "4"]
    function soundColor(i) { return Qt.hsla(i / 16, 0.62, 0.55, 1) }
    function stateText(s) {
        return { empty: "Ready", recording: "Recording", playing: "Playing", overdub: "Overdubbing", stopped: "Stopped" }[s] || s
    }
    readonly property bool typing: saveName.activeFocus || exportName.activeFocus

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
    Repeater {
        model: 16
        delegate: Item {
            required property int index
            Shortcut {
                sequences: [root.padKeys[index]]
                autoRepeat: false
                enabled: !root.typing
                onActivated: loop.hitPad(index)
            }
        }
    }

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

                // --- step sequencer: 16 parts x 16 steps (a bar per page) ---------------
                Rectangle {
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
                            model: 16
                            delegate: RowLayout {
                                id: row
                                required property int index
                                readonly property int sound: index
                                readonly property color tint: root.soundColor(sound)
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                spacing: 2

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
                                        onClicked: loop.selectPad(row.sound)
                                        onDoubleClicked: loop.hitPad(row.sound)
                                    }
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

                // --- pads + the selected pad's sound --------------------------------------
                RowLayout {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Kirigami.Units.gridUnit * 15.5
                    Layout.minimumHeight: Kirigami.Units.gridUnit * 15.5
                    spacing: Kirigami.Units.largeSpacing

                    GridLayout {
                        id: pads
                        columns: 4
                        rowSpacing: 4
                        columnSpacing: 4
                        Layout.fillHeight: true
                        Layout.preferredWidth: Kirigami.Units.gridUnit * 18
                        Layout.maximumWidth: Kirigami.Units.gridUnit * 18
                        property bool assignMode: assignSwitch.checked

                        function flash(sound, vel) {
                            for (let i = 0; i < padRepeater.count; i++) {
                                const it = padRepeater.itemAt(i)
                                if (it && it.sound === sound) it.flash(vel)
                            }
                        }

                        Repeater {
                            id: padRepeater
                            model: 16
                            delegate: Rectangle {
                                id: pad
                                required property int index
                                readonly property int sound: (3 - Math.floor(index / 4)) * 4 + index % 4
                                property real glow: 0
                                function flash(vel) { glow = 0.35 + 0.65 * vel; fade.restart() }
                                NumberAnimation { id: fade; target: pad; property: "glow"; to: 0; duration: 260; easing.type: Easing.OutQuad }

                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                Layout.preferredWidth: 10
                                Layout.preferredHeight: 10
                                radius: Kirigami.Units.cornerRadius * 1.5
                                readonly property color base: root.soundColor(sound)
                                readonly property bool chosen: loop.selectedSound === sound
                                color: Qt.tint(Qt.alpha(base, 0.22), Qt.alpha(base, glow))
                                border.width: loop.assigning === "sound:" + sound ? 3 : chosen ? 2 : 1
                                border.color: loop.assigning === "sound:" + sound ? Kirigami.Theme.highlightColor
                                            : chosen ? Qt.lighter(base, 1.5) : Qt.alpha(base, 0.7)

                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 5
                                    spacing: 0
                                    RowLayout {
                                        QQC2.Label { text: (pad.sound + 1); font.bold: true; opacity: 0.7; font.pointSize: Kirigami.Theme.smallFont.pointSize }
                                        Item { Layout.fillWidth: true }
                                        QQC2.Label { text: root.padKeys[pad.sound]; opacity: 0.5; font.family: "monospace"; font.pointSize: Kirigami.Theme.smallFont.pointSize }
                                    }
                                    Item { Layout.fillHeight: true }
                                    QQC2.Label {
                                        Layout.fillWidth: true
                                        text: loop.padLabels[pad.sound] || "—"
                                        font.bold: true
                                        elide: Text.ElideRight
                                    }
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    onPressed: pads.assignMode ? loop.assign("sound:" + pad.sound) : loop.hitPad(pad.sound)
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
                                Item { Layout.fillWidth: true }
                                QQC2.Label { text: "Sound of the selected pad"; opacity: 0.5; font: Kirigami.Theme.smallFont }
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
                    RowLayout {
                        Layout.fillWidth: true
                        QQC2.Label { text: "LPD8 knobs 1–8:"; Layout.fillWidth: true }
                        QQC2.Button {
                            focusPolicy: Qt.NoFocus
                            text: "Pad"
                            checkable: true
                            checked: loop.knobPage === "pad"
                            onClicked: loop.setKnobPage("pad")
                        }
                        QQC2.Button {
                            focusPolicy: Qt.NoFocus
                            text: "Master"
                            checkable: true
                            checked: loop.knobPage === "master"
                            onClicked: loop.setKnobPage("master")
                        }
                    }
                    QQC2.Label {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        opacity: 0.6
                        font: Kirigami.Theme.smallFont
                        text: "The numbered dials are on the knobs (CC 70–77). K, or a button assigned to “Knobs: pad / master”, switches page. Pad knobs act on the selected pad: the last one you hit or clicked."
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
                            onAccepted: if (loop.saveLoop(text)) focus = false
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
