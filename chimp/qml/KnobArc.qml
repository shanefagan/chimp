import QtQuick
import org.kde.kirigami as Kirigami

// Read-only knob indicator: 270° track, the configured min..max range, and the live value.
Canvas {
    id: arc

    property int value: -1
    property int minimum: 0
    property int maximum: 127
    property color trackColor: Qt.alpha(Kirigami.Theme.textColor, 0.15)
    property color rangeColor: Qt.alpha(Kirigami.Theme.highlightColor, 0.35)
    property color valueColor: Kirigami.Theme.highlightColor

    onValueChanged: requestPaint()
    onMinimumChanged: requestPaint()
    onMaximumChanged: requestPaint()
    onWidthChanged: requestPaint()

    function angle(v) {
        // 0..127 mapped onto 135° .. 405° (canvas angles, clockwise from 3 o'clock)
        return (135 + 270 * Math.max(0, Math.min(127, v)) / 127) * Math.PI / 180
    }

    onPaint: {
        const ctx = getContext("2d")
        ctx.reset()
        const cx = width / 2, cy = height / 2
        const r = Math.min(width, height) / 2 - 4
        ctx.lineCap = "round"

        ctx.lineWidth = 4
        ctx.strokeStyle = trackColor
        ctx.beginPath(); ctx.arc(cx, cy, r, angle(0), angle(127)); ctx.stroke()

        ctx.strokeStyle = rangeColor
        ctx.beginPath(); ctx.arc(cx, cy, r, angle(Math.min(minimum, maximum)), angle(Math.max(minimum, maximum))); ctx.stroke()

        if (value >= 0) {
            ctx.strokeStyle = valueColor
            ctx.beginPath(); ctx.arc(cx, cy, r, angle(0), angle(value)); ctx.stroke()
            const a = angle(value)
            ctx.lineWidth = 3
            ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(a) * (r - 6), cy + Math.sin(a) * (r - 6)); ctx.stroke()
        }
    }
}
