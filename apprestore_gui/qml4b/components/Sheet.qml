import QtQuick
import "../theme"

// A sheet over the main window, built like «Что вернуть»: backdrop, panel
// from the same top inset, header row (title · subtitle · «Отмена») with a
// hairline under it. Content goes into the body; the panel grows with it.
// Esc and «Отмена» call cancel(). Used by sign-in/2FA and license consent.
Item {
    id: root
    anchors.fill: parent
    property string title: ""
    property string subtitle: ""
    property bool cancellable: true
    property int panelWidth: 600
    default property alias body: content.data
    signal cancel()

    focus: true
    Keys.onEscapePressed: if (root.cancellable) root.cancel()

    Rectangle { anchors.fill: parent; color: Theme.backdrop }
    MouseArea { anchors.fill: parent; hoverEnabled: true; onWheel: function(w) { w.accepted = true } }

    Rectangle {
        id: panel
        objectName: "sheetPanel"
        width: Math.min(root.panelWidth, root.width - 2 * Theme.sheetInsetX)
        height: Math.min(head.height + content.childrenRect.height + 2 * 28, root.height - 2 * Theme.sheetInsetY)
        anchors.horizontalCenter: parent.horizontalCenter
        y: Theme.sheetInsetY
        radius: Theme.radiusSheet
        color: Theme.sheet
        clip: true
        Accessible.role: Accessible.Dialog
        Accessible.name: root.title

        Item {
            id: head
            width: parent.width
            height: Theme.sheetHead
            T {
                id: titleText
                x: 32
                anchors.verticalCenter: parent.verticalCenter
                token: "sheetTitle"
                text: root.title
            }
            T {
                anchors.left: titleText.right
                anchors.leftMargin: 14
                anchors.baseline: titleText.baseline
                token: "sheetSub"
                color: Theme.ink3Text
                text: root.subtitle
            }
            T {
                visible: root.cancellable
                anchors.right: parent.right
                anchors.rightMargin: 32
                anchors.verticalCenter: parent.verticalCenter
                token: "sheetLink"
                color: Theme.ink2
                text: "Отмена"
                Accessible.role: Accessible.Button
                Accessible.name: "Отмена"
                MouseArea { anchors.fill: parent; anchors.margins: -8; cursorShape: Qt.PointingHandCursor; onClicked: root.cancel() }
            }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.line }
        }

        Item {
            id: content
            x: 32
            y: head.height + 28
            width: panel.width - 64
            height: childrenRect.height
        }
    }
}
