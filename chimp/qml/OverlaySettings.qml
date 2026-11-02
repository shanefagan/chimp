import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

// Settings for the on-screen overlay that shows the controller while you use it.
RowLayout {
    spacing: Kirigami.Units.largeSpacing

    QQC2.Switch {
        text: "Overlay"
        checked: desktop.overlay
        onToggled: desktop.setOverlay(checked)
        QQC2.ToolTip.visible: hovered
        QQC2.ToolTip.text: "Show a see-through picture of the controller (what each knob and pad does, live values) "
                           + "while you use it. Replaces Plasma's volume popups for knobs."
    }
    QQC2.ComboBox {
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
        enabled: desktop.overlay
        text: "Over fullscreen games"
        checked: desktop.overlayOverGames
        onToggled: desktop.setOverlayOverGames(checked)
    }
}
