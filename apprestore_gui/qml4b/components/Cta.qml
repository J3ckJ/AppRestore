import QtQuick
import "../theme"

// Big home button: accent, or white "secondary" with a hairline.
Rectangle {
    id: root
    property string text: ""
    property bool secondary: false
    property bool enabledLook: true
    signal clicked()
    height: Theme.buttonHeight
    width: label.implicitWidth + 2 * Theme.ctaPadX
    radius: Theme.radiusCta
    color: secondary ? Theme.card : (enabledLook ? Theme.accent : Theme.goDisabled)
    border.width: secondary ? 1 : 0
    border.color: Theme.line
    T {
        id: label
        anchors.centerIn: parent
        token: "cta"
        text: root.text
        color: root.secondary ? Theme.ink : "#ffffff"
    }
    MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: if (root.enabledLook) root.clicked()
    }
}
