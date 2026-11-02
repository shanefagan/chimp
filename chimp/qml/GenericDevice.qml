import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

// Any MIDI controller other than the LPD8: profiles of learned controls.
ColumnLayout {
    id: view
    spacing: Kirigami.Units.largeSpacing

    property real nowTime: desktop.now()
    Timer { interval: 1000; running: view.visible; repeat: true; onTriggered: view.nowTime = desktop.now() }
    function ago(t) {
        const s = Math.max(0, Math.round(nowTime - t))
        return s < 2 ? "just now" : s < 60 ? s + "s ago" : Math.round(s / 60) + " min ago"
    }

    // --- status ---------------------------------------------------------------------------
    Kirigami.InlineMessage {
        Layout.fillWidth: true
        visible: true
        type: !desktop.deviceConnected ? Kirigami.MessageType.Information
              : (!desktop.serviceRunning || desktop.paused || !desktop.deviceEnabled) ? Kirigami.MessageType.Warning
              : Kirigami.MessageType.Positive
        text: {
            if (!desktop.deviceConnected)
                return desktop.currentDevice + " isn't connected. You can still edit its profiles."
            if (desktop.controlCount === 0)
                return "Click Learn, then move every knob and fader and press each button to teach the app this device."
            if (!desktop.serviceRunning)
                return "Desktop control is off, so this device's actions won't do anything yet."
            if (desktop.paused)
                return "Desktop control is paused. Resume it from the tray icon."
            if (!desktop.deviceEnabled)
                return desktop.currentDevice + " is switched off for desktop control."
            let line = "Desktop control is on. " + desktop.currentDevice + " is using profile \""
                       + (desktop.profileNames[Math.max(0, desktop.activeProfile)] || "") + "\"."
            if (desktop.lastAction !== "")
                line += "  Last: " + desktop.lastAction + " (" + view.ago(desktop.lastActionTime) + ")"
            return line
        }
        actions: [
            Kirigami.Action {
                text: "Turn on"
                icon.name: "media-playback-start"
                visible: !desktop.serviceRunning && desktop.controlCount > 0
                onTriggered: desktop.setServiceEnabled(true)
            }
        ]
    }

    // --- device + profile bar -------------------------------------------------------------
    RowLayout {
        Layout.fillWidth: true
        spacing: Kirigami.Units.largeSpacing

        QQC2.Switch {
            text: "Use for desktop control"
            checked: desktop.deviceEnabled
            onToggled: desktop.setDeviceEnabled(checked)
        }
        Kirigami.Separator { Layout.fillHeight: true }
        QQC2.Label { text: "Profile:" }
        QQC2.ComboBox {
            id: profileBox
            Layout.preferredWidth: Kirigami.Units.gridUnit * 10
            model: desktop.profileNames
            currentIndex: desktop.currentProfile
            onActivated: (index) => desktop.setCurrentProfile(index)
        }
        QQC2.Label {
            visible: desktop.deviceConnected && desktop.serviceRunning && desktop.activeProfile === desktop.currentProfile
            text: "● in use"
            color: Kirigami.Theme.positiveTextColor
        }
        QQC2.ToolButton {
            icon.name: "list-add"
            text: "New profile"
            onClicked: newMenu.open()
            QQC2.Menu {
                id: newMenu
                QQC2.MenuItem {
                    text: "Same controls, no actions"
                    onTriggered: { nameDialog.mode = "new"; nameDialog.open() }
                }
                QQC2.MenuItem {
                    text: "Duplicate this profile"
                    onTriggered: { nameDialog.mode = "copy"; nameDialog.open() }
                }
            }
        }
        QQC2.ToolButton {
            icon.name: "edit-rename"
            text: "Rename"
            onClicked: { nameDialog.mode = "rename"; nameDialog.open() }
        }
        QQC2.ToolButton {
            icon.name: "edit-delete"
            text: "Delete"
            enabled: desktop.profileNames.length > 1
            onClicked: desktop.deleteProfile()
        }
        Item { Layout.fillWidth: true }
        QQC2.Button {
            visible: !desktop.learning
            text: desktop.controlCount === 0 ? "Learn controls" : "Learn more controls"
            icon.name: "media-record"
            highlighted: desktop.controlCount === 0
            enabled: desktop.deviceConnected
            onClicked: desktop.startLearn()
        }
    }

    QQC2.Label {
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        font: Kirigami.Theme.smallFont
        opacity: 0.7
        visible: desktop.profileNames.length > 1 || desktop.controlCount > 0
        text: "Profiles are separate sets of actions for the same controls. Switch between them from the tray icon, "
              + "or give a button the \"Next profile\" action."
    }

    // --- learning ---------------------------------------------------------------------------
    Kirigami.InlineMessage {
        Layout.fillWidth: true
        visible: desktop.learning
        type: Kirigami.MessageType.Positive
        text: "Learning " + desktop.currentDevice + ": move each knob and fader through its whole range, "
              + "turn encoders both ways, and press each button twice. "
              + desktop.learnPreview.length + " control" + (desktop.learnPreview.length === 1 ? "" : "s") + " found so far."
        actions: [
            Kirigami.Action {
                text: "Done"
                icon.name: "dialog-ok-apply"
                onTriggered: desktop.finishLearn()
            },
            Kirigami.Action {
                text: "Cancel"
                icon.name: "dialog-cancel"
                onTriggered: desktop.cancelLearn()
            }
        ]
    }
    Flow {
        Layout.fillWidth: true
        visible: desktop.learning && desktop.learnPreview.length > 0
        spacing: Kirigami.Units.smallSpacing
        Repeater {
            model: desktop.learnPreview
            Kirigami.Chip {
                required property var modelData
                closable: false
                checkable: false
                text: modelData.kind + (modelData.kind === "button" || modelData.kind === "encoder" ? " (" + modelData.mode + ")" : "")
                      + " · " + modelData.where + (modelData.known ? " · already learned" : "")
                opacity: modelData.known ? 0.5 : 1
            }
        }
    }

    // --- controls ---------------------------------------------------------------------------
    Kirigami.PlaceholderMessage {
        Layout.fillWidth: true
        Layout.topMargin: Kirigami.Units.gridUnit * 2
        visible: desktop.controlCount === 0 && !desktop.learning
        icon.name: "audio-midi"
        text: "No controls learned yet"
        explanation: desktop.deviceConnected
                     ? "Click Learn controls, then move every knob and fader and press each button. "
                       + "The app works out what each one is: knob, fader, endless encoder or button."
                     : "Connect " + desktop.currentDevice + " to learn its controls."
    }

    GridLayout {
        Layout.fillWidth: true
        visible: desktop.controlCount > 0
        columns: Math.max(1, Math.floor(width / (Kirigami.Units.gridUnit * 14)))
        columnSpacing: Kirigami.Units.largeSpacing
        rowSpacing: Kirigami.Units.largeSpacing
        Repeater {
            model: desktop.controlCount
            ControlTile {
                required property int index
                controlIndex: index
                Layout.fillWidth: true
                Layout.fillHeight: true
            }
        }
    }

    QQC2.Button {
        Layout.alignment: Qt.AlignRight
        visible: !desktop.deviceConnected && desktop.controlCount > 0
        text: "Forget this device"
        icon.name: "edit-delete"
        onClicked: desktop.forgetDevice()
    }

    Kirigami.PromptDialog {
        id: nameDialog
        property string mode: "new"
        title: mode === "rename" ? "Rename profile" : "New profile"
        standardButtons: Kirigami.Dialog.Ok | Kirigami.Dialog.Cancel
        onOpened: {
            nameField.text = mode === "rename" ? (desktop.profileNames[desktop.currentProfile] || "") : ""
            nameField.forceActiveFocus()
        }
        onAccepted: {
            if (mode === "rename")
                desktop.renameProfile(nameField.text)
            else
                desktop.addProfile(nameField.text, mode === "copy")
        }
        QQC2.TextField {
            id: nameField
            placeholderText: "e.g. Streaming, Music, Gaming"
            onAccepted: nameDialog.accept()
        }
    }
}
