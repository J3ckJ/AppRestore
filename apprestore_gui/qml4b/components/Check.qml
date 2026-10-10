import QtQuick
import "../theme"

// Check box: "on" | "off" | "mixed" | "disabled".
Rectangle {
    id: root
    property string state_: "off"
    signal clicked()
    width: 20; height: 20
    radius: Theme.radiusCheck
    color: state_ === "on" || state_ === "mixed" ? Theme.ink : (state_ === "disabled" ? Theme.soft : Theme.inkOnAccent)
    border.width: state_ === "on" || state_ === "mixed" ? 0 : 1.5
    border.color: state_ === "disabled" ? Theme.checkDisabledBorder : Theme.checkBorder
    Glyph { anchors.centerIn: parent; name: "check-white"; size: 12; visible: root.state_ === "on" }
    Rectangle { anchors.centerIn: parent; width: 9; height: 2; radius: 1; color: Theme.inkOnAccent; visible: root.state_ === "mixed" }
    MouseArea {
        anchors.fill: parent
        anchors.margins: -6
        enabled: root.state_ !== "disabled"
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked()
    }
}
