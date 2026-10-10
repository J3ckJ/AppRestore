import QtQuick
import "../theme"

// QuietLinks (спека §1.1): ink2; hover — ink + подчёркивание 1px с отступом 2;
// фокус — кольцо 2px ink, радиус 6, отступ 2. Рука-курсор на обеих ОС.
Row {
    id: root
    property var items: []
    signal activated(string name)
    spacing: Theme.linksGap
    Repeater {
        model: root.items
        Item {
            id: link
            width: label.implicitWidth
            height: label.implicitHeight
            activeFocusOnTab: true
            Accessible.role: Accessible.Link
            Accessible.name: modelData
            Accessible.onPressAction: root.activated(modelData)
            Keys.onReturnPressed: root.activated(modelData)
            Keys.onSpacePressed: root.activated(modelData)
            T {
                id: label
                token: "link"
                color: mouse.containsMouse || mouse.pressed ? Theme.ink : Theme.ink2
                text: modelData
            }
            Rectangle {
                visible: mouse.containsMouse
                y: label.baselineOffset + 2
                width: label.implicitWidth; height: 1
                color: Theme.ink
            }
            Rectangle {
                visible: link.activeFocus
                anchors.fill: parent
                anchors.margins: -2
                radius: 6
                color: "transparent"
                border.width: 2
                border.color: Theme.focusRing
            }
            MouseArea {
                id: mouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.activated(modelData)
            }
        }
    }
}
