import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

// One learned control on a generic MIDI device.
Rectangle {
    id: tile

    required property int controlIndex
    readonly property var d: {
        desktop.revision; desktop.controlCount; desktop.currentProfile; desktop.currentDevice
        return desktop.control(controlIndex)
    }
    readonly property bool isButton: d.kind === "button"
    property real midiLevel: -1
    property string midiText: ""

    readonly property var kindChoices: [
        {value: "knob", label: "Knob"},
        {value: "fader", label: "Fader"},
        {value: "encoder:rel64", label: "Encoder (64 ± n)"},
        {value: "encoder:rel2c", label: "Encoder (1 / 127)"},
        {value: "button:momentary", label: "Button (momentary)"},
        {value: "button:toggle", label: "Button (toggle)"}
    ]
    readonly property string kindKey: d.kind === "button" || d.kind === "encoder" ? d.kind + ":" + d.mode : (d.kind || "")

    Kirigami.Theme.colorSet: Kirigami.Theme.View
    Kirigami.Theme.inherit: false
    color: Kirigami.Theme.backgroundColor
    radius: Kirigami.Units.cornerRadius
    border.width: 1
    border.color: midiText !== "" ? Kirigami.Theme.highlightColor : Qt.alpha(Kirigami.Theme.textColor, 0.15)
    implicitWidth: Kirigami.Units.gridUnit * 13
    implicitHeight: col.implicitHeight + Kirigami.Units.largeSpacing * 2

    Connections {
        target: desktop
        function onControlMoved(i, level, text) {
            if (i !== tile.controlIndex)
                return
            tile.midiLevel = level
            tile.midiText = text
            fade.restart()
        }
        function onDeviceChanged() { tile.midiLevel = -1; tile.midiText = "" }
    }
    Timer {
        id: fade
        interval: tile.isButton ? 400 : 2500
        onTriggered: { tile.midiText = ""; if (tile.isButton) tile.midiLevel = -1 }
    }

    ColumnLayout {
        id: col
        anchors.fill: parent
        anchors.margins: Kirigami.Units.largeSpacing
        spacing: Kirigami.Units.smallSpacing

        RowLayout {
            Layout.fillWidth: true
            spacing: Kirigami.Units.smallSpacing
            Rectangle {
                // pad-like light for buttons, small dot for knobs
                Layout.preferredWidth: Kirigami.Units.gridUnit
                Layout.preferredHeight: Kirigami.Units.gridUnit
                radius: tile.isButton ? Kirigami.Units.cornerRadius : width / 2
                color: tile.midiText !== "" ? Kirigami.Theme.highlightColor : Qt.alpha(Kirigami.Theme.textColor, 0.15)
                Behavior on color { ColorAnimation { duration: 80 } }
            }
            QQC2.TextField {
                Layout.fillWidth: true
                text: tile.d.name || ""
                font.bold: true
                onEditingFinished: desktop.setControlName(tile.controlIndex, text)
            }
            QQC2.ToolButton {
                icon.name: "edit-delete"
                display: QQC2.AbstractButton.IconOnly
                text: "Remove this control"
                QQC2.ToolTip.visible: hovered
                QQC2.ToolTip.text: text
                onClicked: desktop.removeControl(tile.controlIndex)
            }
        }

        RowLayout {
            Layout.fillWidth: true
            QQC2.ComboBox {
                Layout.fillWidth: true
                model: tile.kindChoices
                textRole: "label"
                valueRole: "value"
                currentIndex: tile.kindChoices.findIndex(k => k.value === tile.kindKey)
                onActivated: desktop.setControlKind(tile.controlIndex, currentValue)
                QQC2.ToolTip.visible: hovered
                QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
                QQC2.ToolTip.text: "What kind of control this is (detected while learning; fix it here if it guessed wrong)"
            }
        }
        QQC2.Label {
            text: tile.d.where || ""
            font: Kirigami.Theme.smallFont
            opacity: 0.6
        }

        // Live MIDI from the control: "move it to test"
        RowLayout {
            Layout.fillWidth: true
            spacing: Kirigami.Units.smallSpacing
            Rectangle {
                visible: !tile.isButton && tile.d.kind !== "encoder"
                Layout.fillWidth: true
                Layout.preferredHeight: Kirigami.Units.smallSpacing
                radius: height / 2
                color: Qt.alpha(Kirigami.Theme.textColor, 0.1)
                Rectangle {
                    width: parent.width * Math.max(0, tile.midiLevel)
                    height: parent.height
                    radius: parent.radius
                    color: Qt.alpha(Kirigami.Theme.textColor, 0.5)
                }
            }
            QQC2.Label {
                Layout.fillWidth: tile.isButton || tile.d.kind === "encoder"
                text: tile.midiText !== "" ? "MIDI " + tile.midiText : (tile.isButton ? "press to test" : "move to test")
                font: Kirigami.Theme.smallFont
                opacity: tile.midiText !== "" ? 0.9 : 0.5
            }
        }

        ActionPicker {
            Layout.fillWidth: true
            act: tile.d.action || ({type: "none", target: ""})
            types: tile.isButton ? desktop.buttonTypes() : desktop.knobTypes()
            liveData: { desktop.liveRevision; return desktop.controlLive(tile.controlIndex) }
            levelStyle: !tile.isButton
            syncKey: desktop.currentDevice + "/" + desktop.currentProfile + "/" + tile.controlIndex
            tooltipText: "What this control does on your desktop in this profile"
            onTypeChosen: (kind) => desktop.setControlType(tile.controlIndex, kind)
            onTargetEdited: (t) => desktop.setControlTarget(tile.controlIndex, t)
        }

        Item { Layout.fillHeight: true }
    }
}
