import QtQuick
import "../theme"

Row {
    id: root
    property var items: []
    signal activated(string name)
    spacing: Theme.linksGap
    Repeater {
        model: root.items
        T {
            token: "link"
            color: Theme.ink2
            text: modelData
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.activated(modelData)
            }
        }
    }
}
