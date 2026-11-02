import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

QQC2.Button {
    id: btn

    property string label
    property color swatch

    QQC2.ToolTip.visible: hovered
    QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
    QQC2.ToolTip.text: label + " colour " + swatch.toString().toUpperCase()

    contentItem: RowLayout {
        spacing: Kirigami.Units.smallSpacing
        Rectangle {
            Layout.preferredWidth: Kirigami.Units.iconSizes.small
            Layout.preferredHeight: Kirigami.Units.iconSizes.small
            radius: 3
            color: btn.swatch
            border.width: 1
            border.color: Qt.alpha(Kirigami.Theme.textColor, 0.35)
        }
        QQC2.Label {
            text: btn.label
            Layout.fillWidth: true
            elide: Text.ElideRight
        }
    }
}
