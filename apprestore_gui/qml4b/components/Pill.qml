import QtQuick
import "../theme"

// StatusPill (спека §2.2): h 36, L14 R16, точка 9 ok/off, зазор 9; при
// «не подключён» текст ink2. Смена цвета точки — 150 мс.
Rectangle {
    id: root
    property string text: ""
    property bool on: true
    property bool pressable: false
    signal clicked()
    height: Theme.pillHeight
    width: label.implicitWidth + 14 + Theme.pillDot + 9 + 16
    radius: height / 2
    color: Theme.card
    border.width: Theme.hair
    border.color: Theme.line
    Accessible.role: root.pressable ? Accessible.Button : Accessible.StaticText
    Accessible.name: root.pressable ? ("Выбрать устройство: " + root.text) : ("Статус: " + root.text)
    MouseArea {
        anchors.fill: parent
        enabled: root.pressable
        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: root.clicked()
    }
    Rectangle {
        id: dot
        x: 14
        anchors.verticalCenter: parent.verticalCenter
        width: Theme.pillDot; height: Theme.pillDot; radius: Theme.pillDot / 2
        color: root.on ? Theme.ok : Theme.off
        Behavior on color { ColorAnimation { duration: Theme.durFast } }
    }
    T {
        id: label
        Accessible.ignored: true
        x: dot.x + dot.width + 9
        anchors.verticalCenter: parent.verticalCenter
        token: "pill"
        text: root.text
        color: root.on ? Theme.ink : Theme.ink2
    }
}
