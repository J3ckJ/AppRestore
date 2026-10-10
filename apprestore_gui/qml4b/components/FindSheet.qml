import QtQuick
import QtQuick.Controls.Basic as Basic
import "../theme"

// «Найти» (02-picker §6b): search field, groups «В App Store» · «Ваши покупки»
// · «Удалено из App Store»; the archive spinner under them; Лена's footnote.
// Rows: name / developer + tag / one small line (archive date, bank line,
// «Найдено по запросу „…“»). Never hit.brand.
Item {
    id: root
    objectName: "findSheet"
    anchors.fill: parent
    readonly property var f: ui.find
    readonly property var v: f.view
    focus: true
    Keys.onEscapePressed: f.close()

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
            T { x: 32; anchors.verticalCenter: parent.verticalCenter; token: "sheetTitle"; text: root.v.title || "" }
            T {
                anchors.right: parent.right
                anchors.rightMargin: 32
                anchors.verticalCenter: parent.verticalCenter
                token: "sheetLink"
                color: Theme.ink2
                text: root.v.close || ""
                Accessible.role: Accessible.Button
                Accessible.name: text
                MouseArea { anchors.fill: parent; anchors.margins: -8; cursorShape: Qt.PointingHandCursor; onClicked: root.f.close() }
            }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.line }
        }

        // -- search field ------------------------------------------------------------
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
                objectName: "findInput"
                x: 14 + 16 + 10
                width: parent.width - x - 14
                anchors.verticalCenter: parent.verticalCenter
                font.family: Theme.fontFamily
                font.pixelSize: Theme.style("search").size
                color: Theme.ink
                selectByMouse: true
                clip: true
                focus: true
                text: root.f.query
                onTextEdited: debounce.restart()
                Keys.onEscapePressed: { if (text !== "") { text = ""; root.f.search("") } else root.f.close() }
                Accessible.name: root.v.placeholder || ""
            }
            // empty field = placeholder only (no suggestions)
            T {
                x: input.x
                anchors.verticalCenter: parent.verticalCenter
                visible: !sbox.typed
                token: "search"
                color: Theme.ink3Text
                text: root.v.placeholder || ""
            }
            Timer { id: debounce; interval: 250; onTriggered: root.f.search(input.text) }
        }

        // -- banner: offline / archive down --------------------------------------------
        Rectangle {
            id: banner
            objectName: "findBanner"
            x: sbox.x
            y: sbox.y + sbox.height + 12
            width: sbox.width
            visible: (root.v.banner || "") !== ""
            height: visible ? bannerText.implicitHeight + 22 : 0
            radius: 10
            color: Theme.surfaceSoft
            T {
                id: bannerText
                x: 14; y: 11
                width: parent.width - 28
                token: "status"
                color: Theme.ink2
                wrapMode: Text.WordWrap
                text: root.v.banner || ""
            }
        }

        ListView {
            id: list
            objectName: "findList"
            x: Theme.listPadX
            y: (banner.visible ? banner.y + banner.height : sbox.y + sbox.height) + 12
            width: parent.width - 2 * Theme.listPadX
            height: sheet.height - y - 16
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            model: root.v.rows || []
            delegate: Loader {
                width: list.width
                readonly property var r: modelData
                sourceComponent: r.kind === "header" ? headerRow : appRow
            }
            footer: Column {
                width: list.width
                spacing: 12
                topPadding: 14
                // archive: spinner under the groups, nothing is blocked
                Row {
                    objectName: "findSpinner"
                    visible: (root.v.spinner || "") !== ""
                    spacing: 10
                    Basic.BusyIndicator {
                        width: 16; height: 16
                        running: parent.visible
                        padding: 0
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    T { token: "status"; color: Theme.ink2; text: root.v.spinner || "" }
                }
                Column {
                    visible: (root.v.empty || "") !== ""
                    width: parent.width
                    spacing: 8
                    T {
                        objectName: "findEmpty"
                        width: parent.width
                        horizontalAlignment: Text.AlignHCenter
                        token: "authSub"
                        color: Theme.ink2
                        wrapMode: Text.WordWrap
                        text: root.v.empty || ""
                    }
                    T {
                        visible: (root.v.emptyLink || "") !== ""
                        anchors.horizontalCenter: parent.horizontalCenter
                        token: "toolLink"
                        color: Theme.ink2
                        font.underline: true
                        text: root.v.emptyLink || ""
                        MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: root.f.openSettings() }
                    }
                }
                T {
                    objectName: "findFootnote"
                    visible: (root.v.footnote || "") !== ""
                    width: parent.width
                    token: "fine"
                    color: Theme.ink2
                    wrapMode: Text.WordWrap
                    text: root.v.footnote || ""
                }
            }
        }

        Component {
            id: headerRow
            Column {
                width: list.width
                Item {
                    width: parent.width
                    height: 6 + Theme.groupHeight
                    T {
                        id: gTitle
                        y: 6 + (Theme.groupHeight - height) / 2
                        token: "group"
                        text: r.title
                    }
                    T {
                        anchors.left: gTitle.right
                        anchors.leftMargin: 8
                        anchors.baseline: gTitle.baseline
                        token: "groupCount"
                        color: Theme.ink3Text
                        font.features: { "tnum": 1 }
                        text: String(r.count)
                    }
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.ink }
                }
                T {
                    visible: (r.note || "") !== ""
                    topPadding: 8
                    bottomPadding: 6
                    width: parent.width
                    token: "rowNote"
                    color: Theme.ink2
                    wrapMode: Text.WordWrap
                    text: r.note || ""
                }
            }
        }

        Component {
            id: appRow
            Item {
                width: list.width
                height: (r.line3 || "") !== "" ? Theme.findRowHeight : Theme.rowHeight
                AppIcon {
                    id: icon
                    width: Theme.rowIcon; height: Theme.rowIcon
                    anchors.verticalCenter: parent.verticalCenter
                    storeId: r.storeId
                    iconOpacity: r.enabled ? 1.0 : 0.5
                }
                Column {
                    x: icon.width + 14
                    width: parent.width - x - right.width - 16
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 1
                    T { width: parent.width; token: "row"; elide: Text.ElideRight; text: r.name; color: r.enabled ? Theme.ink : Theme.ink2 }
                    Row {
                        spacing: 8
                        width: parent.width
                        T {
                            id: dev
                            token: "rowDev"; color: Theme.ink3Text
                            elide: Text.ElideRight
                            width: Math.min(implicitWidth, parent.width - (tag.visible ? tag.width + 8 : 0))
                            text: r.developer
                        }
                        Rectangle {
                            id: tag
                            visible: (r.tag || "") !== ""
                            width: tagText.implicitWidth + 12
                            height: tagText.implicitHeight + 2
                            radius: Theme.tagRadius
                            color: Theme.surfaceSoft
                            anchors.verticalCenter: dev.verticalCenter
                            T { id: tagText; anchors.centerIn: parent; token: "rowNote"; color: Theme.ink2; text: r.tag || "" }
                        }
                    }
                    T {
                        visible: (r.line3 || "") !== ""
                        width: parent.width
                        token: "rowNote"; color: Theme.ink2
                        elide: Text.ElideRight
                        maximumLineCount: 1
                        text: r.line3 || ""
                    }
                }
                Item {
                    id: right
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.max(btn.visible ? btn.width : 0, note.visible ? note.implicitWidth : 0)
                    height: Theme.rowButtonHeight
                    Rectangle {
                        id: btn
                        objectName: "findInstall"
                        visible: (r.action || "") !== ""
                        anchors.right: parent.right
                        width: btnText.implicitWidth + 2 * Theme.rowButtonPadX
                        height: Theme.rowButtonHeight
                        radius: Theme.rowButtonRadius
                        color: !r.enabled ? Theme.accentDisabled : btnMouse.pressed ? Theme.accentPressed
                               : btnMouse.containsMouse ? Theme.accentHover : Theme.accent
                        Accessible.role: Accessible.Button
                        Accessible.name: (r.action || "") + " " + r.name
                        T { id: btnText; anchors.centerIn: parent; token: "seg"; color: Theme.inkOnAccent; text: r.action || "" }
                        MouseArea {
                            id: btnMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            enabled: r.enabled
                            cursorShape: Qt.PointingHandCursor
                            onClicked: root.f.install(r.storeId, r.name)
                        }
                    }
                    T {
                        id: note
                        visible: !btn.visible && (r.actionNote || "") !== ""
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        token: "rowNote"
                        color: Theme.ink2
                        textFormat: Text.StyledText
                        linkColor: Theme.ink
                        text: (r.actionNote || "") + ((r.actionLink || "") !== "" ? " · <a href='" + r.actionLink + "'><b>" + r.actionLink + "</b></a>" : "")
                        onLinkActivated: function(link) { ui.link(link) }
                    }
                }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.rowLine }
            }
        }
        Rectangle { anchors.fill: parent; radius: parent.radius; color: "transparent"; border.width: 1; border.color: Theme.sheetHairline }
    }
}
