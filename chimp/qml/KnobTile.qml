import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Rectangle {
    id: tile

    required property int knobIndex
    property var d: { backend.revision; return backend.knob(knobIndex) }
    property int live: -1

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
        function onKnobMoved(i, value) {
            if (i === tile.knobIndex)
                tile.live = value
        }
        function onCurrentSlotChanged() { tile.live = -1 }
    }

    ColumnLayout {
        id: col
        anchors.fill: parent
        anchors.margins: Kirigami.Units.largeSpacing
        spacing: Kirigami.Units.smallSpacing

        RowLayout {
            spacing: Kirigami.Units.largeSpacing
            KnobArc {
                Layout.preferredWidth: Kirigami.Units.gridUnit * 2.6
                Layout.preferredHeight: Kirigami.Units.gridUnit * 2.6
                value: tile.live
                minimum: tile.d.min
                maximum: tile.d.max
            }
            ColumnLayout {
                spacing: 0
                QQC2.Label {
                    text: "K" + (tile.knobIndex + 1)
                    font.bold: true
                }
                QQC2.Label {
                    text: tile.live < 0 ? "turn to test" : "value " + tile.live
                    opacity: 0.7
                    font: Kirigami.Theme.smallFont
                }
            }
        }

        GridLayout {
            columns: 2
            columnSpacing: Kirigami.Units.smallSpacing
            rowSpacing: Kirigami.Units.smallSpacing
            Layout.fillWidth: true

            QQC2.Label { text: "CC" }
            QQC2.SpinBox {
                from: 0; to: 127; editable: true
                value: tile.d.cc
                Layout.fillWidth: true
                onValueModified: backend.setKnob(tile.knobIndex, "cc", value)
            }

            QQC2.Label { text: "Channel" }
            ChannelCombo {
                Layout.fillWidth: true
                channel: tile.d.channel
                onChannelPicked: (ch) => backend.setKnob(tile.knobIndex, "channel", ch)
            }

            QQC2.Label { text: "Min" }
            QQC2.SpinBox {
                from: 0; to: 127; editable: true
                value: tile.d.min
                Layout.fillWidth: true
                onValueModified: backend.setKnob(tile.knobIndex, "min", value)
            }

            QQC2.Label { text: "Max" }
            QQC2.SpinBox {
                from: 0; to: 127; editable: true
                value: tile.d.max
                Layout.fillWidth: true
                onValueModified: backend.setKnob(tile.knobIndex, "max", value)
            }
        }

        ActionPicker {
            Layout.fillWidth: true
            act: { desktop.revision; backend.revision; return desktop.action(backend.currentSlot, false, tile.knobIndex) }
            types: desktop.knobTypes()
            liveData: { desktop.liveRevision; return desktop.live(false, tile.knobIndex) }
            levelStyle: true
            syncKey: backend.currentSlot
            tooltipText: "What this knob does on your desktop while Program " + backend.currentSlot + " is active on the LPD8"
            onTypeChosen: (kind) => desktop.setType(backend.currentSlot, false, tile.knobIndex, kind)
            onTargetEdited: (t) => desktop.setTarget(backend.currentSlot, false, tile.knobIndex, t)
        }
    }
}
