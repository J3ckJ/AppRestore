import QtQuick
import QtQuick.Controls.Basic as Basic
import "../theme"

// «Восстановить из истории»: the Apple ID's purchase list and one restore button.
Item {
    id: root
    objectName: "historySheet"
    anchors.fill: parent
    readonly property var h: ui.history
    focus: true
    Keys.onEscapePressed: ui.closeHistory()

    Rectangle { anchors.fill: parent; color: Theme.backdrop }
    MouseArea { anchors.fill: parent; hoverEnabled: true; onWheel: function(w) { w.accepted = true } }

    Rectangle {
        id: sheet
        x: Theme.sheetInsetX
        y: Theme.sheetInsetY
        width: parent.width - 2 * Theme.sheetInsetX
        height: parent.height - 2 * Theme.sheetInsetY
        radius: Theme.radiusSheet
        color: Theme.sheet
        clip: true
        Accessible.role: Accessible.Dialog
        Accessible.name: root.h.title || ""

        Item {
            id: head
            width: parent.width
            height: Theme.sheetHead
            T { x: 32; anchors.verticalCenter: parent.verticalCenter; token: "sheetTitle"; text: root.h.title || "" }
            T {
                anchors.right: parent.right
                anchors.rightMargin: 32
                anchors.verticalCenter: parent.verticalCenter
                token: "sheetLink"
                color: Theme.ink2
                text: root.h.close || ""
                Accessible.role: Accessible.Button
                Accessible.name: text
                MouseArea { anchors.fill: parent; anchors.margins: -8; cursorShape: Qt.PointingHandCursor; onClicked: ui.closeHistory() }
            }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.line }
        }

        Rectangle {
            id: sbox
            x: Theme.listPadX
            y: head.height + 16
            width: parent.width - 2 * Theme.listPadX
            height: 44
            radius: Theme.radiusSearch
            readonly property bool typed: input.text.length > 0
            color: typed ? Theme.card : Theme.soft
            border.width: typed ? 2 : 0
            border.color: Theme.ink
            Glyph { x: 14; anchors.verticalCenter: parent.verticalCenter; name: sbox.typed ? "search-ink" : "search"; size: 16 }
            TextInput {
                id: input
                objectName: "historyInput"
                x: 14 + 16 + 10
                width: parent.width - x - 14
                anchors.verticalCenter: parent.verticalCenter
                font.family: Theme.fontFamily
                font.pixelSize: Theme.style("search").size
                color: Theme.ink
                selectByMouse: true
                clip: true
                focus: true
                onTextEdited: ui.setHistoryQuery(text)
                Keys.onEscapePressed: { if (text !== "") { text = ""; ui.setHistoryQuery("") } else ui.closeHistory() }
                Accessible.name: root.h.placeholder || ""
            }
            T {
                x: input.x
                anchors.verticalCenter: parent.verticalCenter
                visible: !sbox.typed
                token: "search"
                color: Theme.ink3Text
                text: root.h.placeholder || ""
            }
        }

        Item {
            id: byId
            visible: !!root.h.signedIn
            x: Theme.listPadX
            y: sbox.y + sbox.height + (visible ? 10 : 0)
            width: parent.width - 2 * Theme.listPadX
            height: visible ? 44 : 0

            Rectangle {
                id: idGo
                objectName: "historyStoreIdGo"
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                readonly property bool ready: idInput.text.trim().length > 0
                width: idGoText.implicitWidth + 2 * Theme.rowButtonPadX
                height: 44
                radius: Theme.radiusSearch
                color: !ready ? Theme.soft : idGoMouse.pressed ? Theme.accentPressed : idGoMouse.containsMouse ? Theme.accentHover : Theme.accent
                Accessible.role: Accessible.Button
                Accessible.name: root.h.idAction || ""
                T {
                    id: idGoText
                    anchors.centerIn: parent
                    token: "seg"
                    color: idGo.ready ? Theme.inkOnAccent : Theme.ink3Text
                    text: root.h.idAction || ""
                }
                MouseArea {
                    id: idGoMouse
                    anchors.fill: parent
                    hoverEnabled: idGo.ready
                    cursorShape: idGo.ready ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: {
                        if (idGo.ready)
                            ui.restoreByStoreId(idInput.text)
                    }
                }
            }

            Rectangle {
                anchors.left: parent.left
                anchors.right: idGo.left
                anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                height: 44
                radius: Theme.radiusSearch
                readonly property bool typed: idInput.text.length > 0
                color: typed ? Theme.card : Theme.soft
                border.width: typed ? 2 : 0
                border.color: Theme.ink
                TextInput {
                    id: idInput
                    objectName: "historyStoreId"
                    x: 14
                    width: parent.width - 28
                    anchors.verticalCenter: parent.verticalCenter
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.style("search").size
                    color: Theme.ink
                    selectByMouse: true
                    clip: true
                    inputMethodHints: Qt.ImhNoPredictiveText
                    onAccepted: {
                        if (text.trim() !== "")
                            ui.restoreByStoreId(text)
                    }
                    Accessible.name: root.h.idPlaceholder || ""
                }
                T {
                    x: 14
                    width: parent.width - 28
                    anchors.verticalCenter: parent.verticalCenter
                    visible: idInput.text.length === 0
                    token: "search"
                    color: Theme.ink3Text
                    elide: Text.ElideRight
                    text: root.h.idPlaceholder || ""
                }
            }
        }

        T {
            id: idNote
            objectName: "historyStoreIdNote"
            x: byId.x
            y: byId.y + byId.height + (visible ? 6 : 0)
            width: byId.width
            visible: byId.visible && (root.h.idNote || "") !== ""
            wrapMode: Text.WordWrap
            token: "status"
            color: Theme.ink2
            text: root.h.idNote || ""
        }

        T {
            id: lead
            x: sbox.x
            y: (idNote.visible ? idNote.y + idNote.implicitHeight : byId.y + byId.height) + 12
            width: sbox.width
            visible: (root.h.lead || "") !== ""
            wrapMode: Text.WordWrap
            token: "status"
            color: Theme.ink2
            text: root.h.lead || ""
        }

        ListView {
            id: list
            objectName: "historyList"
            x: Theme.listPadX
            y: (lead.visible ? lead.y + lead.implicitHeight : sbox.y + sbox.height) + 12
            width: parent.width - 2 * Theme.listPadX
            height: sheet.height - y - 16
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            model: root.h.rows || []
            delegate: Item {
                width: list.width
                height: Theme.rowHeight
                readonly property var r: modelData
                AppIcon {
                    id: icon
                    width: Theme.rowIcon; height: Theme.rowIcon
                    anchors.verticalCenter: parent.verticalCenter
                    storeId: r.storeId
                    bundleId: r.bundleId || ""
                }
                Column {
                    x: icon.width + 14
                    width: parent.width - x - btn.width - 16
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 1
                    T { width: parent.width; token: "row"; elide: Text.ElideRight; text: r.name }
                    T {
                        visible: (r.developer || "") !== ""
                        width: parent.width
                        token: "rowDev"
                        color: Theme.ink3Text
                        elide: Text.ElideRight
                        text: r.developer || ""
                    }
                }
                Rectangle {
                    id: btn
                    objectName: "historyRestore"
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    width: btnText.implicitWidth + 2 * Theme.rowButtonPadX
                    height: Theme.rowButtonHeight
                    radius: Theme.rowButtonRadius
                    color: btnMouse.pressed ? Theme.accentPressed : btnMouse.containsMouse ? Theme.accentHover : Theme.accent
                    Accessible.role: Accessible.Button
                    Accessible.name: (r.action || "") + " " + r.name
                    T { id: btnText; anchors.centerIn: parent; token: "seg"; color: Theme.inkOnAccent; text: r.action || "" }
                    MouseArea {
                        id: btnMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: ui.restoreFromHistory(r.storeId, r.name)
                    }
                }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.line }
            }
            footer: Column {
                width: list.width
                spacing: 12
                topPadding: 14
                Row {
                    objectName: "historySpinner"
                    visible: (root.h.spinner || "") !== ""
                    spacing: 10
                    Basic.BusyIndicator {
                        width: 16; height: 16
                        running: parent.visible
                        padding: 0
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    T { token: "status"; color: Theme.ink2; text: root.h.spinner || "" }
                }
                T {
                    objectName: "historyEmpty"
                    visible: (root.h.empty || "") !== ""
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    token: "authSub"
                    color: Theme.ink2
                    wrapMode: Text.WordWrap
                    text: root.h.empty || ""
                }
                T {
                    objectName: "historyEmptyLink"
                    visible: (root.h.emptyLink || "") !== ""
                    anchors.horizontalCenter: parent.horizontalCenter
                    token: "toolLink"
                    color: Theme.ink
                    font.underline: true
                    text: root.h.emptyLink || ""
                    Accessible.role: Accessible.Link
                    Accessible.name: text
                    MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: ui.openSignIn() }
                }
            }
        }
    }
}
