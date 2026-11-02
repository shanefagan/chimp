import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

// Left-column section: desktop options for the program on screen.
ColumnLayout {
    id: section
    readonly property int slot: backend.currentSlot
    readonly property bool hasActions: { desktop.revision; return desktop.hasActions(slot) }

    spacing: Kirigami.Units.smallSpacing

    Kirigami.Heading { level: 4; text: "Desktop control" }

    QQC2.Label {
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        font: Kirigami.Theme.smallFont
        opacity: 0.8
        text: section.hasActions
              ? "While Program " + section.slot + " is active on the LPD8, its pads and knobs run the actions "
                + "set at the bottom of each tile. The bars show the live desktop value."
              : "Program " + section.slot + " is plain MIDI. Pick an action at the bottom of any pad or knob "
                + "to make it control your desktop while this program is active."
    }

    QQC2.Switch {
        text: "On-screen display"
        checked: desktop.osd
        onToggled: desktop.setOsd(checked)
        QQC2.ToolTip.visible: hovered
        QQC2.ToolTip.text: "Show Plasma's volume/mute popup, and which program the LPD8 switched to"
    }
    QQC2.Switch {
        text: "Pad lights show state"
        checked: desktop.leds
        onToggled: desktop.setLeds(checked)
        QQC2.ToolTip.visible: hovered
        QQC2.ToolTip.text: "Muted mic/speakers and OBS recording light their pad in its ON colour"
    }
    QQC2.Button {
        Layout.fillWidth: true
        visible: section.hasActions
        text: "Colour pads by action"
        icon.name: "color-picker"
        onClicked: desktop.matchPadColours(section.slot)
        QQC2.ToolTip.visible: hovered
        QQC2.ToolTip.text: "Dim OFF colour, ON colour per action (red for mutes). Press Send to put it on the LPD8."
    }
    QQC2.Button {
        Layout.fillWidth: true
        visible: section.hasActions
        text: "Clear desktop actions"
        icon.name: "edit-clear-all"
        onClicked: desktop.clearActions(section.slot)
    }
}
