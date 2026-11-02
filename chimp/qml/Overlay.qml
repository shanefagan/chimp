import QtQuick
import QtQuick.Layouts
import QtQuick.Window
import org.kde.layershell as LayerShell

// Chimp's device overlay: a translucent picture of the controller you're touching,
// on Plasma's overlay layer (above fullscreen apps), click-through, no focus.
Window {
    id: win

    readonly property string pos: overlay.position
    readonly property bool atTop: pos === "top" || pos === "top-right"
    readonly property bool atRight: pos === "bottom-right" || pos === "top-right"

    readonly property int edgeGap: atTop ? 48 : 72  // from the screen edge
    readonly property int sideGap: atRight ? 32 : 0
    width: card.implicitWidth + sideGap + 2
    height: card.implicitHeight + edgeGap + 2
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
    visible: overlay.shown || fadeOut.running

    LayerShell.Window.scope: "chimp-overlay"
    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone
    LayerShell.Window.exclusionZone: -1
    LayerShell.Window.anchors: (atTop ? LayerShell.Window.AnchorTop : LayerShell.Window.AnchorBottom)
                               | (atRight ? LayerShell.Window.AnchorRight : 0)

    // Follow the active screen where layer-shell-qt can (6.4+; Debian 13 has 6.3).
    // Set before the first show, which is when the layer surface is made.
    Component.onCompleted: {
        const lw = LayerShell.Window
        if ("wantsToBeOnActiveScreen" in lw)
            lw.wantsToBeOnActiveScreen = true
    }

    readonly property color bg: "#d9121318"
    readonly property color fg: "#f2f3f5"
    readonly property color dim: "#9aa0aa"
    readonly property color accent: "#3d8bff"

    // Text on a pad is whichever of near-black or white contrasts more with the pad's
    // colour (WCAG relative luminance), so any pad colour, lit or not, stays readable.
    function luminance(c) {
        const ch = v => v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)
        return 0.2126 * ch(c.r) + 0.7152 * ch(c.g) + 0.0722 * ch(c.b)
    }
    function inkFor(c) { return luminance(c) > 0.179 ? "#111216" : "#ffffff" }

    Rectangle {
        id: card
        x: 1
        y: win.atTop ? win.edgeGap : 1
        radius: 18
        color: win.bg
        border.color: "#33ffffff"
        border.width: 1
        opacity: overlay.shown ? 1 : 0
        Behavior on opacity { NumberAnimation { id: fadeOut; duration: overlay.shown ? 90 : 350 } }

        implicitWidth: body.implicitWidth + 40
        implicitHeight: body.implicitHeight + 32

        ColumnLayout {
            id: body
            anchors.centerIn: parent
            spacing: 12

            RowLayout {
                spacing: 10
                Text { text: overlay.title; color: win.fg; font.pixelSize: 17; font.bold: true }
                Text { text: overlay.subtitle; color: win.dim; font.pixelSize: 15 }
            }

            // LPD8: knobs (2 x 4) over pads (5-8 on top of 1-4), like the hardware
            ColumnLayout {
                visible: overlay.layout === "lpd8"
                spacing: 10
                GridLayout {
                    columns: 4
                    columnSpacing: 10
                    rowSpacing: 8
                    Repeater {
                        model: overlay.items.filter(it => it.kind === "knob")
                        delegate: KnobCell { required property var modelData; d: modelData }
                    }
                }
                GridLayout {
                    columns: 4
                    columnSpacing: 10
                    rowSpacing: 10
                    Repeater {
                        model: {
                            const pads = overlay.items.filter(it => it.kind === "pad")
                            return pads.length === 8 ? [pads[4], pads[5], pads[6], pads[7], pads[0], pads[1], pads[2], pads[3]] : pads
                        }
                        delegate: PadCell { required property var modelData; d: modelData }
                    }
                }
            }

            // Anything else: the current group's controls
            GridLayout {
                visible: overlay.layout === "grid"
                columns: overlay.columns > 0 ? overlay.columns
                                             : Math.min(6, Math.max(3, Math.ceil(Math.sqrt(overlay.items.length))))
                columnSpacing: 10
                rowSpacing: 10
                Repeater {
                    model: overlay.layout === "grid" ? overlay.items : []
                    delegate: Loader {
                        required property var modelData
                        sourceComponent: modelData.kind === "pad" ? padComp : knobComp
                        Component { id: padComp; PadCell { d: modelData } }
                        Component { id: knobComp; KnobCell { d: modelData } }
                    }
                }
            }
        }
    }

    component KnobCell: Rectangle {
        property var d
        implicitWidth: 132
        implicitHeight: 64
        radius: 12
        color: d.pressed ? "#33ffffff" : "#14ffffff"
        opacity: d.active ? 1 : 0.45
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 8
            spacing: 3
            RowLayout {
                Text { text: d.name; color: win.dim; font.pixelSize: 11; font.bold: true }
                Item { Layout.fillWidth: true }
                Text { text: d.value; color: win.fg; font.pixelSize: 13; font.bold: true }
            }
            Text {
                Layout.fillWidth: true
                text: d.label || "—"
                color: win.fg
                font.pixelSize: 14
                elide: Text.ElideRight
            }
            Rectangle {
                Layout.fillWidth: true
                height: 4
                radius: 2
                color: "#26ffffff"
                visible: d.level >= 0
                Rectangle {
                    width: parent.width * Math.max(0, Math.min(1, d.level))
                    height: parent.height
                    radius: 2
                    color: win.accent
                    Behavior on width { NumberAnimation { duration: 90 } }
                }
            }
        }
    }

    component PadCell: Rectangle {
        property var d
        implicitWidth: 132
        implicitHeight: 64
        radius: 12
        readonly property bool lit: d.pressed || d.on
        readonly property color fill: lit ? d.onColor : Qt.darker(d.offColor, 1.6)
        readonly property color ink: win.inkFor(fill)
        // A black or near-black pad would vanish into the dark card: give it a visible edge.
        readonly property bool nearBlack: win.luminance(fill) < 0.03
        color: fill
        border.color: nearBlack ? "#59ffffff" : Qt.rgba(ink.r, ink.g, ink.b, 0.18)
        border.width: 1
        opacity: d.active ? 1 : 0.45
        Behavior on color { ColorAnimation { duration: 80 } }
        // Pressed: a white ring just outside the tile. The card behind is always dark, so
        // it shows whatever the pad's colour (an outline on the tile itself can't).
        Rectangle {
            anchors.fill: parent
            anchors.margins: -4
            radius: parent.radius + 4
            color: "transparent"
            border.color: "#ffffff"
            border.width: 2
            visible: d.pressed
        }
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 8
            spacing: 2
            Text { text: d.name; color: Qt.rgba(ink.r, ink.g, ink.b, 0.75); font.pixelSize: 11; font.bold: true }
            Text {
                Layout.fillWidth: true
                text: d.label || "—"
                color: ink
                font.pixelSize: 14
                font.bold: true
                elide: Text.ElideRight
            }
        }
    }
}
