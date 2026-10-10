import QtQuick
import QtQuick.Controls.Basic as Basic
import "../theme"

// «Apple ID» when signed in (04-auth §2a): who, the lock line, a quiet «Выйти»
// bottom left (not red; inactive during installation), then a small
// confirmation «Выйти из Apple ID?» with «Отмена» (default) and «Выйти».
Sheet {
    id: root
    objectName: "accountSheet"
    readonly property var a: ui.account
    title: root.a.title || ""
    cancelText: root.a.close || "Закрыть"
    panelWidth: Theme.authSheetWidth
    cancellable: !root.a.confirm
    onCancel: ui.closeAccount()

    Column {
        width: parent.width
        spacing: 0
        T { token: "fieldLabel"; color: Theme.ink2; text: root.a.label || "" }
        Item { width: 1; height: 6 }
        T {
            id: emailText
            objectName: "accountEmail"
            width: parent.width
            token: "row"
            color: Theme.ink
            elide: Text.ElideMiddle
            text: root.a.email || ""
            Accessible.name: root.a.email || ""
            Basic.ToolTip.visible: emailHover.containsMouse && emailText.truncated
            Basic.ToolTip.text: root.a.email || ""
            MouseArea { id: emailHover; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton }
        }
        Item { width: 1; height: 14 }
        InfoLine { maxWidth: parent.width; wrap: true; glyph: "lock"; text: root.a.fine || "" }
        Item { width: 1; height: 24 }
        Rectangle { width: parent.width; height: 1; color: Theme.line }
        Item { width: 1; height: 16 }
        Row {
            spacing: 12
            T {
                id: signOut
                objectName: "accountSignOut"
                token: "sheetLink"
                readonly property bool live: !!root.a.signOutEnabled
                color: !live ? Theme.ink3Text : (outHover.containsMouse ? Theme.ink : Theme.ink2)
                font.underline: live && outHover.containsMouse
                text: root.a.signOut || ""
                Accessible.role: Accessible.Button
                Accessible.name: text
                MouseArea {
                    id: outHover
                    anchors.fill: parent
                    anchors.margins: -6
                    hoverEnabled: true
                    cursorShape: signOut.live ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: if (signOut.live) ui.askSignOut()
                }
            }
            T {
                visible: (root.a.signOutHint || "") !== ""
                token: "status"
                color: Theme.ink2
                text: root.a.signOutHint || ""
            }
        }
    }

    // confirmation over the sheet
    Item {
        parent: root
        anchors.fill: parent
        visible: !!root.a.confirm
        focus: visible
        Keys.onEscapePressed: ui.cancelSignOut()
        Rectangle { anchors.fill: parent; color: Theme.backdrop }
        MouseArea { anchors.fill: parent }
        Rectangle {
            objectName: "signOutConfirm"
            width: Math.min(480, parent.width - 2 * Theme.sheetInsetX)
            height: confirmCol.height + 2 * Theme.sheetPadX
            anchors.centerIn: parent
            radius: Theme.radiusSheet
            color: Theme.sheet
            Accessible.role: Accessible.Dialog
            Accessible.name: root.a.confirmTitle || ""
            Column {
                id: confirmCol
                x: Theme.sheetPadX
                y: Theme.sheetPadX
                width: parent.width - 2 * Theme.sheetPadX
                spacing: 0
                T { token: "sheetTitle"; text: root.a.confirmTitle || "" }
                Item { width: 1; height: 10 }
                T { width: parent.width; token: "authSub"; color: Theme.ink2; wrapMode: Text.WordWrap; text: root.a.confirmText || "" }
                Item { width: 1; height: 24 }
                Row {
                    anchors.right: parent.right
                    spacing: 10
                    Cta {
                        objectName: "signOutCancel"
                        secondary: true
                        focus: true
                        text: root.a.confirmCancel || ""
                        onClicked: ui.cancelSignOut()
                    }
                    Cta {
                        objectName: "signOutGo"
                        text: root.a.confirmGo || ""
                        onClicked: ui.confirmSignOut()
                    }
                }
            }
        }
    }
}
