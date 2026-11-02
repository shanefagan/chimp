import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

// One line telling you whether the LPD8 is controlling the desktop right now, and why not.
Kirigami.InlineMessage {
    id: msg

    readonly property int slot: backend.currentSlot
    readonly property bool tabHasActions: { desktop.revision; return desktop.hasActions(slot) }
    readonly property bool activeHasActions: { desktop.revision; return backend.activeProgram > 0 && desktop.hasActions(backend.activeProgram) }
    property real nowTime: desktop.now()

    Timer { interval: 1000; running: msg.visible; repeat: true; onTriggered: msg.nowTime = desktop.now() }

    function ago(t) {
        const s = Math.max(0, Math.round(nowTime - t))
        return s < 2 ? "just now" : s < 60 ? s + "s ago" : Math.round(s / 60) + " min ago"
    }

    visible: desktop.isLpd8 && (tabHasActions || activeHasActions)
    showCloseButton: false
    type: (!desktop.serviceRunning || desktop.paused) ? Kirigami.MessageType.Warning
          : (backend.activeProgram > 0 && !activeHasActions) ? Kirigami.MessageType.Information
          : Kirigami.MessageType.Positive

    text: {
        if (!desktop.serviceRunning)
            return "Desktop control is off, so pads and knobs with an action won't do anything yet."
        if (desktop.paused)
            return "Desktop control is paused. Resume it from the LPD8 icon in the system tray."
        let line
        if (backend.activeProgram > 0 && !activeHasActions)
            line = "The LPD8 is on Program " + backend.activeProgram + ", which has no desktop actions. "
                   + "Hold PROGRAM and press pad " + slot + " on the LPD8 to use Program " + slot + "."
        else
            line = "Desktop control is on: Program " + (backend.activeProgram || slot)
                   + "'s pads and knobs are controlling your desktop."
        if (desktop.lastAction !== "")
            line += "  Last: " + desktop.lastAction + " (" + ago(desktop.lastActionTime) + ")"
        return line
    }

    actions: [
        Kirigami.Action {
            text: "Turn on"
            icon.name: "media-playback-start"
            visible: !desktop.serviceRunning
            onTriggered: desktop.setServiceEnabled(true)
        }
    ]
}
