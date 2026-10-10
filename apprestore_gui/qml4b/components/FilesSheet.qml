import QtQuick
import "../theme"

// «Файлы IPA» (like 0.3.2's library), anatomy of «Что вернуть»: title, list
// (icon, name, version · size), bottom bar «Выгрузить с устройства» (secondary)
// and «Выбрать на ПК» (primary). Export mode: the phone's apps with checks,
// «Назад» / «Сохранить N». Theme tokens only; icons from iconBook or the placeholder.
Item {
    id: root
    objectName: "filesSheet"
    anchors.fill: parent
    readonly property var v: ui.files
    readonly property bool exporting: v.mode === "export"
    focus: true
    Keys.onEscapePressed: root.exporting ? ui.filesBack() : ui.closeFiles()

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
        Accessible.name: root.v.title || ""

        Item {
            id: head
            width: parent.width
            height: Theme.sheetHead
            Column {
                x: 32
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width - 2 * 32 - 120
                spacing: 2
                T { token: "sheetTitle"; text: root.v.title || "" }
                // where the files were found — built from the folders the scan really uses
                T {
                    objectName: "filesWhere"
                    width: parent.width
                    visible: text !== ""
                    token: "status"
                    color: Theme.ink2
                    elide: Text.ElideRight
                    text: root.v.where || ""
                }
            }
            T {
                anchors.right: parent.right
                anchors.rightMargin: 32
                anchors.verticalCenter: parent.verticalCenter
                token: "sheetLink"
                color: Theme.ink2
                text: root.v.close || ""
                Accessible.role: Accessible.Button
                Accessible.name: text
                MouseArea { anchors.fill: parent; anchors.margins: -8; cursorShape: Qt.PointingHandCursor; onClicked: ui.closeFiles() }
            }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.line }
        }

        ListView {
            id: list
            objectName: "filesList"
            x: Theme.listPadX
            y: head.height + 8
            width: parent.width - 2 * Theme.listPadX
            height: foot.y - y
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            model: root.v.rows || []
            delegate: Item {
                width: list.width
                height: Theme.rowHeight
                readonly property var r: modelData
                Check {
                    id: check
                    visible: root.exporting
                    anchors.verticalCenter: parent.verticalCenter
                    state_: r.checked ? "on" : "off"
                    onClicked: ui.filesToggle(r.key)
                }
                AppIcon {
                    id: icon
                    x: root.exporting ? check.width + 14 : 0
                    width: Theme.rowIcon; height: Theme.rowIcon
                    anchors.verticalCenter: parent.verticalCenter
                    storeId: r.storeId || ""
                    bundleId: r.bundleId || ""
                }
                Column {
                    x: icon.x + icon.width + 14
                    width: parent.width - x - (btn.visible ? btn.width + 16 : 0)
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 1
                    T { width: parent.width; token: "row"; elide: Text.ElideRight; text: r.name || "" }
                    T { width: parent.width; token: "rowDev"; color: Theme.ink3Text; elide: Text.ElideRight; text: r.meta || ""; visible: text !== "" }
                }
                Rectangle {
                    id: btn
                    objectName: "filesInstall"
                    visible: !root.exporting
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    width: btnText.implicitWidth + 2 * Theme.rowButtonPadX
                    height: Theme.rowButtonHeight
                    radius: Theme.rowButtonRadius
                    // like «Найти» rows but light: the only filled button is «Выбрать на ПК»
                    color: btnMouse.pressed ? Theme.secondaryPressed : btnMouse.containsMouse ? Theme.secondaryPressed
                           : Theme.surfaceSoft
                    opacity: root.v.busy ? 0.5 : 1
                    Accessible.role: Accessible.Button
                    Accessible.name: (root.v.install || "") + " " + (r.name || "")
                    T { id: btnText; anchors.centerIn: parent; token: "seg"; color: Theme.accent; text: root.v.install || "" }
                    MouseArea {
                        id: btnMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        enabled: !root.v.busy
                        cursorShape: Qt.PointingHandCursor
                        onClicked: ui.filesInstall(r.path)
                    }
                }
                MouseArea {
                    anchors.fill: parent
                    visible: root.exporting
                    cursorShape: Qt.PointingHandCursor
                    onClicked: ui.filesToggle(r.key)
                }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.line }
            }
            footer: T {
                objectName: "filesEmpty"
                visible: (root.v.empty || "") !== ""
                width: list.width
                topPadding: 28
                horizontalAlignment: Text.AlignHCenter
                token: "authSub"
                color: Theme.ink2
                wrapMode: Text.WordWrap
                text: root.v.empty || ""
            }
        }

        Item {
            id: foot
            y: parent.height - height
            width: parent.width
            height: Theme.sheetFoot
            Rectangle { width: parent.width; height: 1; color: Theme.line }
            T {
                x: 32
                anchors.verticalCenter: parent.verticalCenter
                width: buttons.x - x - 22
                token: "status"
                color: Theme.ink2
                wrapMode: Text.WordWrap
                maximumLineCount: 2
                elide: Text.ElideRight
                text: root.v.note || ""
            }
            Row {
                id: buttons
                anchors.right: parent.right
                anchors.rightMargin: 32
                anchors.verticalCenter: parent.verticalCenter
                spacing: 12
                Cta {
                    objectName: "filesSecondary"
                    compact: true
                    secondary: true
                    text: root.v.secondary || ""
                    enabledLook: !!root.v.secondaryEnabled
                    onClicked: root.exporting ? ui.filesBack() : ui.filesExport()
                }
                Cta {
                    objectName: "filesPrimary"
                    compact: true
                    text: root.v.primary || ""
                    enabledLook: !!root.v.primaryEnabled
                    onClicked: root.exporting ? ui.filesSave() : ui.filesPick()
                }
            }
        }
        Rectangle { anchors.fill: parent; radius: parent.radius; color: "transparent"; border.width: 1; border.color: Theme.sheetHairline }
    }
}
