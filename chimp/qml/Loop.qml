import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

// Chimp Loop: play a kit from any controller and loop it.
Kirigami.ApplicationWindow {
    id: root

    width: Kirigami.Units.gridUnit * 64
    height: Kirigami.Units.gridUnit * 47
    minimumWidth: Kirigami.Units.gridUnit * 46
    minimumHeight: Kirigami.Units.gridUnit * 40
    title: "Chimp Loop"

    // Pads on screen like an 8- or 16-pad controller: sound 1 bottom-left, 16 top-right.
    readonly property var padKeys: ["Z", "X", "C", "V", "A", "S", "D", "F", "Q", "W", "E", "R", "1", "2", "3", "4"]
    function soundColor(i) { return Qt.hsla(i / 16, 0.62, 0.55, 1) }
    function stateText(s) {
        return { empty: "Ready", recording: "Recording", playing: "Playing", overdub: "Overdubbing", stopped: "Stopped" }[s] || s
    }

    Connections {
        target: loop
        function onNotify(message) { root.showPassiveNotification(message, "short") }
        function onPadHit(sound, vel) { pads.flash(sound, vel) }
    }

    // --- keyboard -------------------------------------------------------------------
    Shortcut { sequences: ["Space"]; autoRepeat: false; onActivated: loop.runAction("record") }
    Shortcut { sequences: ["Return", "Enter"]; autoRepeat: false; onActivated: loop.runAction("play") }
    Shortcut { sequences: ["Backspace"]; autoRepeat: false; onActivated: loop.runAction("undo") }
    Shortcut { sequences: ["Ctrl+Backspace", "Delete"]; autoRepeat: false; onActivated: loop.runAction("clear") }
    Shortcut { sequences: ["Escape"]; enabled: loop.assigning !== ""; onActivated: loop.assign("") }
    Shortcut { sequences: ["T"]; autoRepeat: false; onActivated: loop.tapTempo() }
    Repeater {
        model: 16
        delegate: Item {
            required property int index
            Shortcut {
                sequences: [root.padKeys[index]]
                autoRepeat: false
                enabled: !saveName.activeFocus && !exportName.activeFocus
                onActivated: loop.hitPad(index)
            }
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

            // --- left: transport, timeline, pads -------------------------------------
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: Kirigami.Units.largeSpacing

                RowLayout {
                    spacing: Kirigami.Units.smallSpacing
                    QQC2.Button {
                        id: recButton
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
                        QQC2.ToolTip.text: "Backspace: remove the last layer"
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

                // tempo, length, quantise, metronome, kit, volume
                Flow {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.largeSpacing
                    RowLayout {
                        QQC2.Label { text: "Tempo" }
                        QQC2.SpinBox {
                            id: bpmBox
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
                            QQC2.ToolTip.text: "Free: the loop is as long as your first take. Bars: recording stops on its own."
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
                        QQC2.ToolTip.text: "Clicks while recording and overdubbing"
                        QQC2.ToolTip.visible: hovered
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
                    RowLayout {
                        Kirigami.Icon { source: "audio-volume-medium"; implicitWidth: Kirigami.Units.iconSizes.small; implicitHeight: implicitWidth }
                        QQC2.Slider {
                            focusPolicy: Qt.NoFocus
                            from: 0; to: 1
                            value: loop.volume
                            implicitWidth: Kirigami.Units.gridUnit * 7
                            onMoved: loop.setVolume(value)
                        }
                    }
                }

                // timeline: beats, hits (as they'll play), playhead
                Rectangle {
                    id: timeline
                    Layout.fillWidth: true
                    Layout.preferredHeight: Kirigami.Units.gridUnit * 5
                    radius: Kirigami.Units.cornerRadius
                    color: Kirigami.Theme.alternateBackgroundColor
                    border.color: Qt.alpha(Kirigami.Theme.textColor, 0.15)
                    clip: true

                    readonly property int beats: loop.beats
                    Repeater {
                        model: timeline.beats
                        delegate: Rectangle {
                            required property int index
                            x: timeline.width * index / timeline.beats
                            width: 1
                            height: timeline.height
                            color: Kirigami.Theme.textColor
                            opacity: index % 4 === 0 ? 0.35 : 0.12
                        }
                    }
                    Repeater {
                        model: loop.hitMarks
                        delegate: Rectangle {
                            required property var modelData
                            readonly property real size: 3 + 5 * modelData.vel
                            x: timeline.width * modelData.pos - size / 2
                            y: 6 + (timeline.height - 12) * (modelData.sound / 15) - size / 2
                            width: size; height: size; radius: size / 2
                            color: root.soundColor(modelData.sound)
                            opacity: 0.45 + 0.55 * modelData.vel
                        }
                    }
                    Rectangle {
                        visible: loop.position >= 0 && loop.state !== "empty" && loop.state !== "stopped"
                        x: timeline.width * Math.max(0, Math.min(1, loop.position))
                        width: 2
                        height: timeline.height
                        color: loop.state === "recording" || loop.state === "overdub" ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.highlightColor
                    }
                    QQC2.Label {
                        anchors.centerIn: parent
                        visible: loop.state === "empty" || (loop.state === "recording" && loop.position < 0)
                        opacity: 0.6
                        text: loop.state === "recording" ? "Recording… press Record again to close the loop"
                                                          : "Press Record (Space), play, press Record again to close the loop"
                    }
                }

                // effects: 8 dials, each on a knob of yours (LPD8 knobs 1-8 out of the box)
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: fxRow.implicitHeight + Kirigami.Units.smallSpacing * 2
                    radius: Kirigami.Units.cornerRadius
                    color: Kirigami.Theme.alternateBackgroundColor
                    border.color: Qt.alpha(Kirigami.Theme.textColor, 0.15)

                    RowLayout {
                        id: fxRow
                        anchors.fill: parent
                        anchors.margins: Kirigami.Units.smallSpacing
                        spacing: 0
                        Repeater {
                            model: 8
                            delegate: ColumnLayout {
                                id: cell
                                required property int index
                                readonly property var p: loop.params[index] || ({ id: "", label: "", value: 0, text: "", assigned: "" })
                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                                spacing: 0
                                QQC2.Label {
                                    Layout.fillWidth: true
                                    horizontalAlignment: Text.AlignHCenter
                                    text: cell.p.label
                                    font.bold: true
                                    elide: Text.ElideRight
                                }
                                QQC2.Dial {
                                    id: dial
                                    Layout.alignment: Qt.AlignHCenter
                                    implicitWidth: Kirigami.Units.gridUnit * 3
                                    implicitHeight: implicitWidth
                                    focusPolicy: Qt.NoFocus
                                    inputMode: QQC2.Dial.Vertical
                                    from: 0; to: 1
                                    onMoved: loop.setParam(cell.p.id, value)
                                    // follows the hardware knob, except while you drag it here
                                    Binding on value { value: cell.p.value; when: !dial.pressed }
                                    TapHandler { onDoubleTapped: loop.resetParam(cell.p.id) }
                                    QQC2.ToolTip.text: "Drag up/down; double-click to reset"
                                    QQC2.ToolTip.visible: hovered && !pressed
                                }
                                QQC2.Label {
                                    Layout.fillWidth: true
                                    horizontalAlignment: Text.AlignHCenter
                                    text: cell.p.text
                                    opacity: 0.8
                                    elide: Text.ElideRight
                                }
                                QQC2.ToolButton {
                                    Layout.alignment: Qt.AlignHCenter
                                    focusPolicy: Qt.NoFocus
                                    icon.name: "input-gaming"
                                    display: QQC2.AbstractButton.IconOnly
                                    checkable: true
                                    checked: loop.assigning === "param:" + cell.p.id
                                    onClicked: loop.assign(checked ? "param:" + cell.p.id : "")
                                    QQC2.ToolTip.text: (cell.p.assigned ? "On " + cell.p.assigned + ". " : "")
                                                       + "Click, then move a knob or fader to use that instead"
                                    QQC2.ToolTip.visible: hovered
                                }
                            }
                        }
                    }
                }

                // assign banner
                Kirigami.InlineMessage {
                    Layout.fillWidth: true
                    visible: loop.assigning !== ""
                    type: Kirigami.MessageType.Information
                    text: loop.assigning.startsWith("sound:")
                          ? "Press the pad or button on your controller for " + loop.padLabels[parseInt(loop.assigning.slice(6))] + " (Esc cancels)"
                          : loop.assigning.startsWith("param:")
                          ? "Move the knob or fader on your controller for " + (loop.params.find(p => "param:" + p.id === loop.assigning) || {label: ""}).label + " (Esc cancels)"
                          : "Press the button on your controller for " + (loop.actionList.find(a => "action:" + a.id === loop.assigning) || {label: ""}).label + " (Esc cancels)"
                    actions: [ Kirigami.Action { text: "Cancel"; onTriggered: loop.assign("") } ]
                }

                // pads
                GridLayout {
                    id: pads
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    columns: 4
                    rowSpacing: Kirigami.Units.smallSpacing * 2
                    columnSpacing: Kirigami.Units.smallSpacing * 2
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
                            Layout.minimumHeight: Kirigami.Units.gridUnit * 3
                            radius: Kirigami.Units.cornerRadius * 2
                            readonly property color base: root.soundColor(sound)
                            color: Qt.tint(Qt.alpha(base, 0.22), Qt.alpha(base, glow))
                            readonly property bool chosen: loop.selectedSound === sound
                            border.width: loop.assigning === "sound:" + sound ? 3 : chosen ? 2 : 1
                            border.color: loop.assigning === "sound:" + sound ? Kirigami.Theme.highlightColor
                                        : chosen ? Qt.lighter(base, 1.5) : Qt.alpha(base, 0.7)

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: Kirigami.Units.smallSpacing * 2
                                spacing: 0
                                RowLayout {
                                    QQC2.Label { text: (pad.sound + 1); font.bold: true; opacity: 0.7 }
                                    Item { Layout.fillWidth: true }
                                    QQC2.Label { text: root.padKeys[pad.sound]; opacity: 0.5; font.family: "monospace" }
                                }
                                Item { Layout.fillHeight: true }
                                QQC2.Label {
                                    Layout.fillWidth: true
                                    text: loop.padLabels[pad.sound] || "—"
                                    font.pixelSize: Kirigami.Theme.defaultFont.pixelSize * 1.25
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

            // --- right: controller buttons, loops, export --------------------------------
            QQC2.ScrollView {
                Layout.preferredWidth: Kirigami.Units.gridUnit * 19
                Layout.fillHeight: true
                contentWidth: availableWidth

                ColumnLayout {
                    width: parent.width
                    spacing: Kirigami.Units.largeSpacing

                    Kirigami.Heading { level: 4; text: "Controller buttons" }
                    QQC2.Label {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        opacity: 0.7
                        text: "Give the looper's controls to buttons on your controller (a KO II key, a pad on a spare LPD8 program)."
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
                                QQC2.ToolTip.text: "Assign: press this, then the button on your controller"
                                QQC2.ToolTip.visible: hovered
                            }
                            QQC2.ToolButton {
                                focusPolicy: Qt.NoFocus
                                icon.name: "edit-clear"
                                visible: modelData.assigned !== ""
                                onClicked: loop.unassign("action:" + modelData.id)
                            }
                        }
                    }

                    Kirigami.Separator { Layout.fillWidth: true }
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
                                QQC2.ToolTip.text: "Open"
                                QQC2.ToolTip.visible: hovered
                            }
                            QQC2.ToolButton {
                                focusPolicy: Qt.NoFocus
                                icon.name: "edit-delete"
                                onClicked: loop.deleteLoop(modelData)
                                QQC2.ToolTip.text: "Delete"
                                QQC2.ToolTip.visible: hovered
                            }
                        }
                    }

                    Kirigami.Separator { Layout.fillWidth: true }
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
                        text: "To " + loop.exportDir + ". WAV loops seamlessly (sounds ringing past the end carry into the start); MIDI uses General MIDI drum notes on channel 10."
                    }
                    Item { Layout.fillHeight: true }
                }
            }
        }
    }
}
