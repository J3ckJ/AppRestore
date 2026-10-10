import QtQuick
import "../theme"

Rectangle {
    id: root
    property string text: ""
    property bool on: true
    height: Theme.pillHeight
    width: label.implicitWidth + 14 + 9 + 9 + 16
    radius: height / 2
    color: Theme.card
    border.width: 1
    border.color: Theme.line
    Rectangle {
        id: dot
        x: 14
        anchors.verticalCenter: parent.verticalCenter
        width: 9; height: 9; radius: 4.5
        color: root.on ? Theme.ok : Theme.off
    }
    T {
        id: label
        x: dot.x + dot.width + 9
        anchors.verticalCenter: parent.verticalCenter
        token: "pill"
        text: root.text
        color: root.on ? Theme.ink : Theme.ink2
    }
}
