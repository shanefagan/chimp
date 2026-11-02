import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Rectangle {
    id: tile

    required property int padIndex
    property var d: { backend.revision; return backend.pad(padIndex) }
    property int hitVelocity: 0
    property color offColor: d.off
    property color onColor: d.on
    signal pickColor(string which, color current)

    function textOn(c) {
        return (0.299 * c.r + 0.587 * c.g + 0.114 * c.b) > 0.6 ? "#202020" : "#ffffff"
    }

    Kirigami.Theme.colorSet: Kirigami.Theme.View
    Kirigami.Theme.inherit: false
    color: Kirigami.Theme.backgroundColor
    radius: Kirigami.Units.cornerRadius
    border.width: 1
    border.color: Qt.alpha(Kirigami.Theme.textColor, 0.15)
    implicitWidth: Kirigami.Units.gridUnit * 13
    implicitHeight: col.implicitHeight + Kirigami.Units.largeSpacing * 2

    Connections {
        target: backend
        function onPadHit(i, velocity) {
            if (i === tile.padIndex)
                tile.hitVelocity = velocity
        }
        function onCurrentSlotChanged() { tile.hitVelocity = 0 }
    }

    ColumnLayout {
        id: col
        anchors.fill: parent
        anchors.margins: Kirigami.Units.largeSpacing
        spacing: Kirigami.Units.smallSpacing

        Rectangle {
            id: face
            readonly property bool lit: tile.hitVelocity > 0 || faceMouse.pressed
            Layout.fillWidth: true
            Layout.preferredHeight: Kirigami.Units.gridUnit * 3
            radius: Kirigami.Units.cornerRadius
            color: lit ? tile.onColor : tile.offColor
            border.width: 1
            border.color: Qt.alpha("#000000", 0.25)
            Behavior on color { ColorAnimation { duration: 60 } }

            QQC2.Label {
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.margins: Kirigami.Units.smallSpacing
                text: "PAD " + (tile.padIndex + 1)
                font.bold: true
                color: tile.textOn(face.color)
            }
            QQC2.Label {
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: Kirigami.Units.smallSpacing
                visible: tile.hitVelocity > 0
                text: tile.hitVelocity
                font: Kirigami.Theme.smallFont
                color: tile.textOn(face.color)
            }
            MouseArea {
                id: faceMouse
                anchors.fill: parent
                QQC2.ToolTip.visible: containsMouse && !pressed
                QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
                QQC2.ToolTip.text: "Hold to preview the ON colour"
                hoverEnabled: true
            }
        }

        GridLayout {
            columns: 2
            columnSpacing: Kirigami.Units.smallSpacing
            rowSpacing: Kirigami.Units.smallSpacing
            Layout.fillWidth: true

            QQC2.Label { text: "Note" }
            RowLayout {
                spacing: Kirigami.Units.smallSpacing
                QQC2.SpinBox {
                    from: 0; to: 127; editable: true
                    value: tile.d.note
                    Layout.fillWidth: true
                    onValueModified: backend.setPad(tile.padIndex, "note", value)
                }
                QQC2.Label {
                    text: tile.d.noteName
                    opacity: 0.7
                    Layout.minimumWidth: Kirigami.Units.gridUnit * 1.8
                }
            }

            QQC2.Label { text: "CC" }
            QQC2.SpinBox {
                from: 0; to: 127; editable: true
                value: tile.d.cc
                Layout.fillWidth: true
                onValueModified: backend.setPad(tile.padIndex, "cc", value)
            }

            QQC2.Label { text: "Prog chg" }
            QQC2.SpinBox {
                from: 0; to: 127; editable: true
                value: tile.d.pc
                Layout.fillWidth: true
                onValueModified: backend.setPad(tile.padIndex, "pc", value)
            }

            QQC2.Label { text: "Channel" }
            ChannelCombo {
                Layout.fillWidth: true
                channel: tile.d.channel
                onChannelPicked: (ch) => backend.setPad(tile.padIndex, "channel", ch)
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Kirigami.Units.smallSpacing
            ColorSwatch {
                Layout.fillWidth: true
                label: "Off"
                swatch: tile.offColor
                onClicked: tile.pickColor("off", tile.offColor)
            }
            ColorSwatch {
                Layout.fillWidth: true
                label: "On"
                swatch: tile.onColor
                onClicked: tile.pickColor("on", tile.onColor)
            }
        }

        ActionPicker {
            Layout.fillWidth: true
            act: { desktop.revision; backend.revision; return desktop.action(backend.currentSlot, true, tile.padIndex) }
            types: desktop.padTypes()
            liveData: { desktop.liveRevision; return desktop.live(true, tile.padIndex) }
            levelStyle: false
            syncKey: backend.currentSlot
            tooltipText: "What this pad does on your desktop while Program " + backend.currentSlot + " is active on the LPD8"
            onTypeChosen: (kind) => desktop.setType(backend.currentSlot, true, tile.padIndex, kind)
            onTargetEdited: (t) => desktop.setTarget(backend.currentSlot, true, tile.padIndex, t)
        }
    }
}
