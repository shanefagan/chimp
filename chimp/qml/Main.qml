import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import QtQuick.Dialogs
import org.kde.kirigami as Kirigami

Kirigami.ApplicationWindow {
    id: root

    width: Kirigami.Units.gridUnit * 76
    height: Kirigami.Units.gridUnit * 52
    minimumWidth: Kirigami.Units.gridUnit * 52
    minimumHeight: Kirigami.Units.gridUnit * 34
    title: "Chimp"

    readonly property bool isLpd8: desktop.isLpd8

    property var settingsData: { backend.revision; return backend.settings() }
    readonly property bool dirty: { backend.revision; return backend.isDirty(backend.currentSlot) }

    Connections {
        target: backend
        function onNotify(message, kind) {
            root.showPassiveNotification(message, kind === "error" || kind === "positive" ? "long" : "short")
        }
    }

    // One shared colour dialog; whoever opens it says what to do with the result.
    ColorDialog {
        id: colorDialog
        property var apply: null
        onAccepted: if (apply) apply(selectedColor.toString())
    }
    function pickColor(title, current, apply) {
        colorDialog.title = title
        colorDialog.selectedColor = current
        colorDialog.apply = apply
        colorDialog.open()
    }

    FileDialog {
        id: openDialog
        title: "Load preset into program " + backend.currentSlot
        fileMode: FileDialog.OpenFile
        nameFilters: ["LPD8 mk2 presets (*.json *.syx)", "All files (*)"]
        currentFolder: backend.presetFolder()
        onAccepted: backend.loadPreset(selectedFile)
    }
    FileDialog {
        id: saveDialog
        title: "Save program " + backend.currentSlot
        fileMode: FileDialog.SaveFile
        defaultSuffix: "json"
        nameFilters: ["JSON preset (*.json)", "SysEx dump (*.syx)"]
        currentFolder: backend.presetFolder()
        onAccepted: backend.savePreset(selectedFile)
    }

    pageStack.initialPage: Kirigami.ScrollablePage {
        id: page
        title: root.isLpd8
               ? "LPD8 mk2 · Program " + backend.currentSlot + (root.dirty ? " — not sent" : "")
               : desktop.currentDevice + " · " + (desktop.profileNames[desktop.currentProfile] || "")

        actions: [
            Kirigami.Action {
                text: "Get"
                visible: root.isLpd8
                icon.name: "download"
                tooltip: "Read program " + backend.currentSlot + " from the LPD8 (replaces what's in the editor)"
                enabled: backend.connected && !backend.busy
                onTriggered: backend.getCurrent()
            },
            Kirigami.Action {
                text: "Send"
                visible: root.isLpd8
                icon.name: "document-send"
                tooltip: "Write program " + backend.currentSlot + " to the LPD8 and verify it"
                enabled: backend.connected && !backend.busy
                onTriggered: backend.sendCurrent()
            },
            Kirigami.Action {
                text: "Revert"
                visible: root.isLpd8
                icon.name: "edit-undo"
                tooltip: "Throw away edits and go back to what's on the LPD8"
                enabled: root.dirty
                onTriggered: backend.revert()
            },
            Kirigami.Action {
                text: "Preview LEDs"
                visible: root.isLpd8
                icon.name: "color-management"
                tooltip: "Show this program's OFF colours on the pads live while you edit"
                checkable: true
                checked: backend.previewLeds
                enabled: backend.connected
                onToggled: backend.previewLeds = checked
            },
            Kirigami.Action {
                text: desktop.serviceRunning ? "Desktop control: on" : "Desktop control: off"
                icon.name: "input-keyboard-virtual"
                displayHint: Kirigami.DisplayHint.KeepVisible
                checkable: true
                checked: desktop.serviceRunning
                tooltip: desktop.serviceRunning
                         ? "MIDI controls with an action are controlling your desktop. Click to turn off."
                         : "Turn on the background service so MIDI controls with an action control your desktop"
                onTriggered: desktop.setServiceEnabled(!desktop.serviceRunning)
            },
            Kirigami.Action {
                text: "Open…"
                visible: root.isLpd8
                icon.name: "document-open"
                onTriggered: openDialog.open()
            },
            Kirigami.Action {
                text: "Save…"
                visible: root.isLpd8
                icon.name: "document-save-as"
                onTriggered: saveDialog.open()
            },
            Kirigami.Action {
                text: "Get all programs"
                visible: root.isLpd8
                icon.name: "view-refresh"
                displayHint: Kirigami.DisplayHint.AlwaysHide
                enabled: backend.connected && !backend.busy
                onTriggered: backend.getAll()
            },
            Kirigami.Action {
                text: "Reset to factory defaults"
                visible: root.isLpd8
                icon.name: "edit-reset"
                displayHint: Kirigami.DisplayHint.AlwaysHide
                onTriggered: backend.resetToFactory()
            }
        ]

        ColumnLayout {
            spacing: Kirigami.Units.largeSpacing

            // --- device picker ------------------------------------------------------
            RowLayout {
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing
                QQC2.Label { text: "Device:"; opacity: 0.7 }
                Repeater {
                    model: desktop.devices
                    QQC2.ToolButton {
                        required property var modelData
                        checkable: true
                        checked: desktop.currentDevice === modelData.name
                        text: modelData.name + (modelData.connected ? "" : "  (not connected)")
                        icon.name: "audio-midi"
                        opacity: modelData.connected ? 1 : 0.6
                        onClicked: desktop.setCurrentDevice(modelData.name)
                    }
                }
                Item { Layout.fillWidth: true }
                QQC2.Label {
                    visible: desktop.devices.length === 1
                    text: "Plug in another MIDI controller and it appears here"
                    font: Kirigami.Theme.smallFont
                    opacity: 0.6
                }
            }
            Kirigami.Separator { Layout.fillWidth: true }

            GenericDevice {
                Layout.fillWidth: true
                visible: !root.isLpd8
            }

            // --- connection + program selector --------------------------------
            RowLayout {
                Layout.fillWidth: true
                visible: root.isLpd8
                spacing: Kirigami.Units.largeSpacing

                Rectangle {
                    width: Kirigami.Units.smallSpacing * 3
                    height: width
                    radius: width / 2
                    color: backend.connected ? Kirigami.Theme.positiveTextColor : Kirigami.Theme.disabledTextColor
                }
                QQC2.Label {
                    text: backend.connected ? "Connected · " + backend.portName : "Not connected"
                }
                QQC2.BusyIndicator {
                    running: backend.busy
                    visible: running
                    Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
                    Layout.preferredWidth: Layout.preferredHeight
                }
                Item { Layout.fillWidth: true }
                QQC2.Label {
                    visible: backend.connected && backend.activeProgram > 0
                    text: "Active on device: Program " + backend.activeProgram
                    opacity: 0.7
                }
                QQC2.ToolButton {
                    visible: backend.connected
                    icon.name: "view-refresh"
                    display: QQC2.AbstractButton.IconOnly
                    text: "Check which program is active"
                    QQC2.ToolTip.visible: hovered
                    QQC2.ToolTip.text: text
                    onClicked: backend.refreshActive()
                }
            }

            DesktopStatus { Layout.fillWidth: true }

            Kirigami.InlineMessage {
                Layout.fillWidth: true
                visible: root.isLpd8 && !backend.connected
                type: Kirigami.MessageType.Information
                text: "Plug in the LPD8 mk2 and it will be picked up automatically. You can still edit and save presets offline."
            }

            QQC2.TabBar {
                id: tabs
                Layout.fillWidth: true
                visible: root.isLpd8
                currentIndex: backend.currentSlot - 1
                onCurrentIndexChanged: backend.currentSlot = currentIndex + 1
                Repeater {
                    model: 4
                    QQC2.TabButton {
                        required property int index
                        readonly property bool slotDirty: { backend.revision; return backend.isDirty(index + 1) }
                        readonly property string job: { desktop.revision; return desktop.programJob(index + 1) }
                        text: "Program " + (index + 1) + (job !== "" ? " · " + job : "")
                              + (backend.activeProgram === index + 1 ? "  ●" : "")
                              + (slotDirty ? "  (edited)" : "")
                    }
                }
            }

            QQC2.Label {
                Layout.fillWidth: true
                visible: root.isLpd8
                wrapMode: Text.WordWrap
                font: Kirigami.Theme.smallFont
                opacity: 0.7
                text: "Each program is its own set of pads and knobs: hold PROGRAM and press pad 1–4 on the LPD8 to switch. "
                      + "A program whose pads or knobs have actions controls the desktop. The others are Chimp Loop's: "
                      + "Loop A plays sounds 1–8, Loop B sounds 9–16, and Loop FX is roll and the glitches. ● is the one the LPD8 is on."
            }

            // --- body -----------------------------------------------------------
            RowLayout {
                Layout.fillWidth: true
                visible: root.isLpd8
                spacing: Kirigami.Units.largeSpacing * 2

                // Program-wide settings + bulk tools
                ColumnLayout {
                    Layout.alignment: Qt.AlignTop
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 15
                    Layout.maximumWidth: Kirigami.Units.gridUnit * 15
                    spacing: Kirigami.Units.largeSpacing

                    Kirigami.Heading { level: 4; text: "Program settings" }

                    Kirigami.FormLayout {
                        Layout.fillWidth: true

                        QQC2.SpinBox {
                            Kirigami.FormData.label: "Global channel:"
                            from: 1; to: 16; editable: true
                            value: root.settingsData.globalChannel
                            onValueModified: backend.setSetting("globalChannel", value)
                        }
                        QQC2.ComboBox {
                            Kirigami.FormData.label: "Pad mode:"
                            model: ["Momentary", "Toggle"]
                            currentIndex: root.settingsData.toggle ? 1 : 0
                            onActivated: (index) => backend.setSetting("toggle", index === 1)
                        }
                        QQC2.ComboBox {
                            Kirigami.FormData.label: "Pressure:"
                            model: ["Off", "Channel", "Polyphonic"]
                            currentIndex: root.settingsData.pressure
                            onActivated: (index) => backend.setSetting("pressure", index)
                        }
                        QQC2.Switch {
                            Kirigami.FormData.label: "Full level:"
                            checked: root.settingsData.fullLevel
                            onToggled: backend.setSetting("fullLevel", checked)
                            QQC2.ToolTip.visible: hovered
                            QQC2.ToolTip.text: "Pads always send velocity 127"
                        }
                    }

                    Kirigami.Separator { Layout.fillWidth: true }
                    Kirigami.Heading { level: 4; text: "All pads" }

                    QQC2.Button {
                        Layout.fillWidth: true
                        text: "Set all OFF colours…"
                        icon.name: "color-picker"
                        onClicked: root.pickColor("OFF colour for all pads", backend.pad(0).off,
                                                  (c) => backend.setAllPadColors("off", c))
                    }
                    QQC2.Button {
                        Layout.fillWidth: true
                        text: "Set all ON colours…"
                        icon.name: "color-picker"
                        onClicked: root.pickColor("ON colour for all pads", backend.pad(0).on,
                                                  (c) => backend.setAllPadColors("on", c))
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        QQC2.Label { text: "Channel" }
                        ChannelCombo {
                            id: allChannel
                            Layout.fillWidth: true
                            channel: 10
                        }
                        QQC2.Button {
                            text: "Apply"
                            onClicked: backend.setAllPadChannels("", allChannel.currentIndex + 1)
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        QQC2.Label { text: "Notes from" }
                        QQC2.SpinBox {
                            id: startNote
                            Layout.fillWidth: true
                            from: 0; to: 120; editable: true
                            value: 36
                        }
                        QQC2.Button {
                            text: "Apply"
                            onClicked: backend.fillSequential(startNote.value)
                        }
                    }
                    Kirigami.Separator { Layout.fillWidth: true }
                    DesktopSettings { Layout.fillWidth: true }

                    QQC2.Label {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        font: Kirigami.Theme.smallFont
                        opacity: 0.7
                        text: "Hit pads and turn knobs on the LPD8 to see them light up here. "
                              + "After Send, switch to the program on the device (hold PROGRAM + pad 1–4) to load it."
                    }
                }

                // Hardware-style layout: knobs on top, pads below (5–8 over 1–4)
                ColumnLayout {
                    Layout.alignment: Qt.AlignTop
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.largeSpacing

                    Kirigami.Heading { level: 4; text: "Knobs" }
                    GridLayout {
                        columns: 4
                        columnSpacing: Kirigami.Units.largeSpacing
                        rowSpacing: Kirigami.Units.largeSpacing
                        Repeater {
                            model: 8
                            KnobTile {
                                required property int index
                                knobIndex: index
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                            }
                        }
                    }

                    Kirigami.Heading { level: 4; text: "Pads" }
                    GridLayout {
                        columns: 4
                        columnSpacing: Kirigami.Units.largeSpacing
                        rowSpacing: Kirigami.Units.largeSpacing
                        Repeater {
                            model: [4, 5, 6, 7, 0, 1, 2, 3]
                            PadTile {
                                required property int modelData
                                padIndex: modelData
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                onPickColor: (which, current) => root.pickColor(
                                    (which === "on" ? "ON" : "OFF") + " colour for pad " + (padIndex + 1),
                                    current,
                                    (c) => backend.setPad(padIndex, which, c))
                            }
                        }
                    }
                }
            }
        }
    }
}
