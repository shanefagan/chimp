import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

// Settings for the on-screen overlay that shows the controller while you use it.
// `vertical` stacks them for narrow places like the LPD8 page's left column.
GridLayout {
    id: settings
    property bool vertical: false

    columns: vertical ? 1 : 3
    columnSpacing: Kirigami.Units.largeSpacing
    rowSpacing: Kirigami.Units.smallSpacing

    QQC2.Switch {
        text: "Overlay"
        checked: desktop.overlay
        onToggled: desktop.setOverlay(checked)
        QQC2.ToolTip.visible: hovered
        QQC2.ToolTip.text: "Show a see-through picture of the controller (what each knob and pad does, live values) "
                           + "while you use it. Replaces Plasma's volume popups for knobs."
    }
    QQC2.ComboBox {
        Layout.fillWidth: settings.vertical
        Layout.leftMargin: settings.vertical ? Kirigami.Units.gridUnit * 2 : 0
        enabled: desktop.overlay
        model: [
            {value: "bottom", label: "Bottom"},
            {value: "top", label: "Top"},
            {value: "bottom-right", label: "Bottom right"},
            {value: "top-right", label: "Top right"}
        ]
        textRole: "label"
        valueRole: "value"
        currentIndex: Math.max(0, model.findIndex(m => m.value === desktop.overlayPosition))
        onActivated: desktop.setOverlayPosition(currentValue)
    }
    QQC2.CheckBox {
        Layout.leftMargin: settings.vertical ? Kirigami.Units.gridUnit * 2 : 0
        enabled: desktop.overlay
        text: "Over fullscreen games"
        checked: desktop.overlayOverGames
        onToggled: desktop.setOverlayOverGames(checked)
    }
}
