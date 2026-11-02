import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

// Desktop action for one control, plus a live readout of the thing it controls
// (read from the desktop, so it proves the action worked). Device-agnostic: the
// owner supplies the action, the choices and the live reading, and handles changes.
ColumnLayout {
    id: picker

    required property var act        // {type, target}
    required property var types      // [{id, label, targetLabel, hint}]
    property var liveData: ({})      // {value 0-1 or -1, text, muted, on}
    property bool levelStyle: true   // knob-style level bar (true) or on/off state (false)
    property string tooltipText: ""
    property var syncKey             // changes when the tile shows a different control/program

    signal typeChosen(string kind)
    signal targetEdited(string target)

    readonly property var typeInfo: types.find(t => t.id === act.type) || types[0]
    readonly property bool hasAction: act.type !== "none"
    readonly property bool hasTarget: typeInfo.targetLabel !== ""

    spacing: Kirigami.Units.smallSpacing

    Kirigami.Separator { Layout.fillWidth: true; Layout.topMargin: Kirigami.Units.smallSpacing }

    RowLayout {
        Layout.fillWidth: true
        spacing: Kirigami.Units.smallSpacing
        Kirigami.Icon {
            source: "input-keyboard-virtual"
            implicitWidth: Kirigami.Units.iconSizes.small
            implicitHeight: implicitWidth
            opacity: picker.hasAction ? 1 : 0.4
        }
        QQC2.ComboBox {
            Layout.fillWidth: true
            model: picker.types
            textRole: "label"
            valueRole: "id"
            currentIndex: picker.types.findIndex(t => t.id === picker.act.type)
            onActivated: picker.typeChosen(currentValue)
            QQC2.ToolTip.visible: hovered && picker.tooltipText !== ""
            QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
            QQC2.ToolTip.text: picker.tooltipText
        }
    }

    QQC2.ComboBox {
        id: target
        visible: picker.hasTarget
        Layout.fillWidth: true
        editable: true
        textRole: "label"
        valueRole: "value"
        model: picker.hasTarget ? desktop.suggestions(picker.act.type) : []

        function sync() { if (!activeFocus) editText = picker.act.target || "" }
        Component.onCompleted: sync()
        Connections {
            target: picker
            function onActChanged() { target.sync() }
            function onSyncKeyChanged() { target.sync() }
        }
        onActivated: {
            editText = currentValue
            picker.targetEdited(currentValue)
        }
        onEditTextChanged: if (activeFocus) picker.targetEdited(editText)

        QQC2.ToolTip.visible: hovered
        QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
        QQC2.ToolTip.text: picker.typeInfo.targetLabel + ": " + picker.typeInfo.hint
    }

    // Live desktop level: moves when the control (or anything else) changes it
    RowLayout {
        visible: picker.levelStyle && picker.hasAction && (picker.liveData.text || "") !== ""
        Layout.fillWidth: true
        spacing: Kirigami.Units.smallSpacing
        Rectangle {
            id: track
            readonly property real raw: picker.liveData.value === undefined ? -1 : picker.liveData.value
            visible: raw >= 0
            Layout.fillWidth: true
            Layout.preferredHeight: Kirigami.Units.smallSpacing * 2
            radius: height / 2
            color: Qt.alpha(Kirigami.Theme.textColor, 0.15)
            Rectangle {
                width: parent.width * Math.max(0, Math.min(1, track.raw))
                height: parent.height
                radius: parent.radius
                color: picker.liveData.muted ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.highlightColor
                Behavior on width { NumberAnimation { duration: 120 } }
            }
        }
        QQC2.Label {
            text: picker.liveData.text || ""
            font.bold: true
            color: picker.liveData.muted ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.textColor
            Layout.minimumWidth: Kirigami.Units.gridUnit * 2.5
            horizontalAlignment: Text.AlignRight
        }
    }

    // Live on/off state (mutes)
    QQC2.Label {
        visible: !picker.levelStyle && (picker.liveData.text || "") !== ""
        text: "Now: " + (picker.liveData.text || "")
        font.bold: true
        color: picker.liveData.on ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.positiveTextColor
    }
}
