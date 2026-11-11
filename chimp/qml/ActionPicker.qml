import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import QtQuick.Dialogs
import org.kde.kirigami as Kirigami

// Desktop action for one control, plus a live readout of the thing it controls
// (read from the desktop, so it proves the action worked). Device-agnostic: the
// owner supplies the action, the choices and the live reading, and handles changes.
ColumnLayout {
    id: picker

    required property var act        // {type, target, options}
    required property var types      // [{id, label, targetLabel, hint, file, sound}]
    property var liveData: ({})      // {value 0-1 or -1, text, muted, on}
    property bool levelStyle: true   // knob-style level bar (true) or on/off state (false)
    property string tooltipText: ""
    property var syncKey             // changes when the tile shows a different control/program

    signal typeChosen(string kind)
    signal targetEdited(string target)
    signal optionEdited(string key, var value)

    readonly property var typeInfo: types.find(t => t.id === act.type) || types[0]
    readonly property bool hasAction: act.type !== "none"
    readonly property bool hasTarget: typeInfo.targetLabel !== ""
    readonly property var options: act.options || ({})

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

    RowLayout {
        visible: picker.hasTarget
        Layout.fillWidth: true
        spacing: Kirigami.Units.smallSpacing

        QQC2.ComboBox {
            id: target
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
        QQC2.ToolButton {
            visible: picker.typeInfo.file === true
            icon.name: "document-open"
            onClicked: fileDialog.open()
            QQC2.ToolTip.visible: hovered
            QQC2.ToolTip.text: "Choose a sound file"
        }
    }

    FileDialog {
        id: fileDialog
        title: "Choose a sound"
        nameFilters: ["Sound files (*.wav *.mp3 *.ogg *.oga *.opus *.flac *.m4a *.aac *.aiff *.aif)", "All files (*)"]
        currentFolder: "file://" + desktop.soundFolder()
        onAccepted: {
            const path = decodeURIComponent(selectedFile.toString().replace(/^file:\/\//, ""))
            target.editText = path
            picker.targetEdited(path)
        }
    }

    // The soundboard's settings for this pad: how a press plays it, where, how loud
    GridLayout {
        visible: picker.typeInfo.sound === true && picker.hasAction
        Layout.fillWidth: true
        columns: 2
        columnSpacing: Kirigami.Units.smallSpacing
        rowSpacing: Kirigami.Units.smallSpacing

        QQC2.ComboBox {
            Layout.columnSpan: 2
            Layout.fillWidth: true
            model: picker.typeInfo.sound === true ? desktop.soundModes() : []
            textRole: "label"
            valueRole: "id"
            currentIndex: Math.max(0, (model || []).findIndex(m => m.id === (picker.options.mode || "oneshot")))
            onActivated: picker.optionEdited("mode", currentValue)
            QQC2.ToolTip.visible: hovered
            QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
            QQC2.ToolTip.text: "How a press plays it. \"Hold\" needs a momentary pad (its Toggle setting off)."
        }
        QQC2.ComboBox {
            id: outputBox
            Layout.columnSpan: 2
            Layout.fillWidth: true
            property var outs: []
            model: outs
            textRole: "label"
            valueRole: "id"
            function refresh() {
                const chosen = picker.options.output || ""
                const list = desktop.soundOutputs()
                if (chosen !== "" && !list.some(o => o.id === chosen))
                    list.push({id: chosen, label: chosen + " (not connected)"})
                outs = list
                currentIndex = Math.max(0, outs.findIndex(o => o.id === chosen))
            }
            Component.onCompleted: if (picker.typeInfo.sound === true) refresh()
            Connections {
                target: picker
                function onActChanged() { if (picker.typeInfo.sound === true) outputBox.refresh() }
            }
            onPressedChanged: if (pressed) refresh()  // devices come and go: list what's there now
            onActivated: picker.optionEdited("output", currentValue)
            QQC2.ToolTip.visible: hovered
            QQC2.ToolTip.delay: Kirigami.Units.toolTipDelay
            QQC2.ToolTip.text: "Where this clip plays: your speakers, headphones, or a virtual device a stream or call picks up"
        }
        QQC2.Slider {
            id: volume
            Layout.fillWidth: true
            from: 0; to: 100; stepSize: 1
            value: picker.options.volume === undefined ? 100 : picker.options.volume
            onMoved: picker.optionEdited("volume", Math.round(value))
            QQC2.ToolTip.visible: hovered || pressed
            QQC2.ToolTip.text: "Volume " + Math.round(value) + "%"
        }
        RowLayout {
            spacing: 0
            QQC2.ToolButton {
                icon.name: "media-playback-start"
                enabled: picker.act.target !== ""
                onClicked: desktop.previewSound(picker.act.target, picker.options)
                QQC2.ToolTip.visible: hovered
                QQC2.ToolTip.text: "Play it here, with this volume and output"
            }
            QQC2.ToolButton {
                icon.name: "media-playback-stop"
                onClicked: desktop.stopPreview()
                QQC2.ToolTip.visible: hovered
                QQC2.ToolTip.text: "Stop"
            }
        }
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
