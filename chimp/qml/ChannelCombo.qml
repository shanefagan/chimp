import QtQuick
import QtQuick.Controls as QQC2

// MIDI channel picker: 1-16 plus "Global" (stored as 17)
QQC2.ComboBox {
    id: combo

    property int channel: 1
    signal channelPicked(int channel)

    model: ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15", "16", "Global"]
    currentIndex: channel - 1
    onActivated: (index) => combo.channelPicked(index + 1)
}
