import QtQuick
import "../theme"

// «Что вернуть»: rail of groups, search + sort, grouped list, space footer.
Item {
    id: root
    anchors.fill: parent

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

        // -- header ---------------------------------------------------------
        Item {
            id: head
            width: parent.width
            height: Theme.sheetHead
            T {
                id: title
                x: 32
                anchors.verticalCenter: parent.verticalCenter
                token: "sheetTitle"
                text: "Что вернуть"
            }
            T {
                anchors.left: title.right
                anchors.leftMargin: 14
                anchors.baseline: title.baseline
                token: "sheetSub"
                color: Theme.ink3
                text: ui.pickerSubtitle
            }
            T {
                anchors.right: parent.right
                anchors.rightMargin: 32
                anchors.verticalCenter: parent.verticalCenter
                token: "sheetLink"
                color: Theme.ink2
                text: "Отмена"
                MouseArea { anchors.fill: parent; anchors.margins: -8; cursorShape: Qt.PointingHandCursor; onClicked: ui.closePicker() }
            }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.line }
        }

        // -- rail -----------------------------------------------------------
        Rectangle {
            id: rail
            y: head.height
            width: Theme.railWidth
            height: parent.height - head.height - foot.height
            color: Theme.soft
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.line }
            Column {
                x: 14; y: 18
                width: parent.width - 28 - 1
                spacing: 2
                Repeater {
                    model: ui.rail
                    Rectangle {
                        width: parent.width
                        height: 10 + railTitle.height + 2 + railSub.height + 10
                        radius: Theme.radiusRail
                        color: modelData.on ? Theme.card : "transparent"
                        border.width: modelData.on ? 1 : 0
                        border.color: Theme.line
                        T {
                            id: railTitle
                            x: 14; y: 10
                            token: "rail"
                            text: modelData.title
                            color: modelData.zero ? Theme.ink3 : Theme.ink
                        }
                        T {
                            anchors.right: parent.right
                            anchors.rightMargin: 14
                            anchors.baseline: railTitle.baseline
                            token: "rail"
                            font.weight: 600
                            font.features: { "tnum": 1 }
                            text: String(modelData.count)
                            color: modelData.zero ? Theme.ink3 : Theme.ink2
                        }
                        T {
                            id: railSub
                            x: 14
                            y: railTitle.y + railTitle.height + 2
                            token: "railSmall"
                            color: Theme.ink3
                            text: modelData.sub
                        }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: ui.setRail(modelData.key) }
                    }
                }
            }
            T {
                x: 14 + 14
                width: parent.width - 56
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 18
                token: "help"
                color: Theme.ink2
                wrapMode: Text.WordWrap
                textFormat: Text.StyledText
                lineHeightMode: Text.FixedHeight
                lineHeight: 13 * 1.5
                text: "<font color='" + Theme.ink + "'><b>Сгруженные</b></font> " + "iPhone докачает без Apple ID. "
                      + "<font color='" + Theme.ink + "'><b>Удалённые</b></font> скачиваются через ваш Apple ID."
            }
        }

        // -- tools ------------------------------------------------------------
        Item {
            id: tools
            x: rail.width
            y: head.height
            width: parent.width - rail.width
            height: 16 + 44 + 12
            Rectangle {
                id: sbox
                x: Theme.listPadX
                y: 16
                width: seg.x - 14 - x
                height: 44
                radius: Theme.radiusSearch
                readonly property bool typed: input.text.length > 0
                color: typed ? Theme.card : Theme.soft
                border.width: typed ? 2 : 0
                border.color: Theme.ink
                Glyph { x: 14; anchors.verticalCenter: parent.verticalCenter; name: sbox.typed ? "search-ink" : "search"; size: 16 }
                TextInput {
                    id: input
                    x: 14 + 16 + 10
                    width: parent.width - x - (sbox.typed ? escHint.width + 28 : 14)
                    anchors.verticalCenter: parent.verticalCenter
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.style("search").size
                    color: Theme.ink
                    selectByMouse: true
                    clip: true
                    text: ui.query
                    onTextEdited: ui.setQuery(text)
                    Keys.onEscapePressed: { text = ""; ui.setQuery("") }
                }
                T {
                    x: input.x
                    anchors.verticalCenter: parent.verticalCenter
                    visible: !sbox.typed
                    token: "search"
                    color: Theme.ink3
                    text: "Название, разработчик или номер из App Store"
                }
                T {
                    id: escHint
                    visible: sbox.typed
                    anchors.right: parent.right
                    anchors.rightMargin: 14
                    anchors.verticalCenter: parent.verticalCenter
                    token: "note"
                    color: Theme.ink3
                    text: "Esc — очистить"
                }
            }
            Rectangle {
                id: seg
                anchors.right: selAll.left
                anchors.rightMargin: 14
                anchors.verticalCenter: sbox.verticalCenter
                width: segRow.width + 6
                height: segRow.height + 6
                radius: Theme.radiusSeg
                color: Theme.soft
                Row {
                    id: segRow
                    x: 3; y: 3
                    Repeater {
                        model: [ { key: "size", title: "По размеру" }, { key: "name", title: "По имени" } ]
                        Rectangle {
                            readonly property bool on: ui.sort === modelData.key
                            width: segLabel.implicitWidth + 24
                            height: segLabel.implicitHeight + 14
                            radius: Theme.radiusSegItem
                            color: on ? Theme.card : "transparent"
                            border.width: on ? 1 : 0
                            border.color: Theme.line
                            T { id: segLabel; anchors.centerIn: parent; token: "seg"; text: modelData.title; color: parent.on ? Theme.ink : Theme.ink2 }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: ui.setSort(modelData.key) }
                        }
                    }
                }
            }
            T {
                id: selAll
                anchors.right: clearAll.left
                anchors.rightMargin: 14
                anchors.verticalCenter: sbox.verticalCenter
                token: "seg"; color: Theme.ink2
                text: "Выбрать всё"
                MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: ui.selectAll() }
            }
            T {
                id: clearAll
                anchors.right: parent.right
                anchors.rightMargin: Theme.listPadX
                anchors.verticalCenter: sbox.verticalCenter
                token: "seg"; color: Theme.ink2
                text: "Снять всё"
                MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: ui.clearAll() }
            }
        }

        // -- list -------------------------------------------------------------
        ListView {
            id: list
            objectName: "pickerList"
            x: rail.width + Theme.listPadX
            y: tools.y + tools.height
            width: parent.width - rail.width - 2 * Theme.listPadX
            height: foot.y - y
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            model: ui.pickerModel
            delegate: Loader {
                id: cell
                required property string kind
                required property string key
                required property string group
                required property string title
                required property string countText
                required property string check
                required property string action
                required property string nameHtml
                required property string developer
                required property string storeId
                required property string bundleId
                required property string sizeText
                required property string note
                required property bool hasIpaHint
                required property bool selected
                required property bool selectable
                readonly property var row: cell
                width: list.width
                sourceComponent: kind === "header" ? headerRow : appRow
            }
            footer: Column {
                width: list.width
                visible: ui.query.length > 0
                height: visible ? implicitHeight + 22 : 0
                topPadding: 22
                spacing: 12
                T {
                    width: parent.width
                    token: "link"
                    font.weight: 400
                    color: Theme.ink2
                    wrapMode: Text.WordWrap
                    textFormat: Text.StyledText
                    lineHeightMode: Text.FixedHeight
                    lineHeight: 15 * 1.6
                    text: (ui.searchNote ? ui.searchNote + "<br>" : "")
                          + "<font color='" + Theme.ink + "'><b>Нет того, что искали?</b></font> Поищем в App Store и в архиве по названию, ссылке apps.apple.com или номеру."
                }
                Rectangle {
                    width: storeLabel.implicitWidth + 36
                    height: 40
                    radius: 12
                    color: "transparent"
                    border.width: 1
                    border.color: Theme.ink
                    T { id: storeLabel; anchors.centerIn: parent; token: "link"; font.weight: 600; text: "Искать «" + ui.query + "» в App Store и архиве" }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: ui.searchStore(ui.query) }
                }
            }
        }

        // Sticky group header while the list is scrolled (512 offloaded apps).
        Item {
            id: sticky
            objectName: "stickyHeader"
            readonly property var hdr: list.contentY > 6 ? ui.headerAt(list.indexAt(8, list.contentY + 2), ui.revision) : null
            visible: !!hdr && !!hdr.group
            x: list.x
            y: list.y
            width: list.width
            height: Theme.groupHeight
            Rectangle { anchors.fill: parent; color: Theme.sheet }
            Check {
                anchors.verticalCenter: parent.verticalCenter
                state_: sticky.hdr ? sticky.hdr.check : "off"
                onClicked: ui.toggleGroup(sticky.hdr.group)
            }
            T {
                id: sTitle
                x: 28 + 12
                anchors.verticalCenter: parent.verticalCenter
                token: "group"
                text: sticky.hdr ? sticky.hdr.title : ""
            }
            T {
                anchors.left: sTitle.right
                anchors.leftMargin: 8
                anchors.baseline: sTitle.baseline
                token: "groupCount"
                color: Theme.ink3
                text: sticky.hdr ? sticky.hdr.countText : ""
            }
            T {
                anchors.right: parent.right
                anchors.baseline: sTitle.baseline
                token: "seg"
                color: Theme.ink2
                text: sticky.hdr ? sticky.hdr.action : ""
                MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: ui.toggleGroup(sticky.hdr.group) }
            }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.ink }
        }

        Component {
            id: headerRow
            Item {
                height: 6 + Theme.groupHeight
                Check {
                    x: 0
                    y: 6 + (Theme.groupHeight - height) / 2
                    state_: row.check
                    onClicked: ui.toggleGroup(row.group)
                }
                T {
                    id: gTitle
                    x: 28 + 12
                    y: 6 + (Theme.groupHeight - height) / 2
                    token: "group"
                    text: row.title
                }
                T {
                    anchors.left: gTitle.right
                    anchors.leftMargin: 8
                    anchors.baseline: gTitle.baseline
                    token: "groupCount"
                    color: Theme.ink3
                    text: row.countText
                }
                T {
                    anchors.right: parent.right
                    anchors.baseline: gTitle.baseline
                    token: "seg"
                    color: Theme.ink2
                    text: row.action
                    MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: ui.toggleGroup(row.group) }
                }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.ink }
            }
        }

        Component {
            id: appRow
            Rectangle {
                height: Theme.rowHeight
                color: row.selected ? Theme.rowOn : "transparent"
                Check {
                    x: 0
                    anchors.verticalCenter: parent.verticalCenter
                    state_: row.check
                    onClicked: ui.toggle(row.key)
                }
                AppIcon {
                    id: rowIcon
                    x: 28 + 12
                    anchors.verticalCenter: parent.verticalCenter
                    width: Theme.rowIcon; height: Theme.rowIcon
                    radius: Theme.radiusRowIcon
                    storeId: row.storeId
                    bundleId: row.bundleId
                    iconOpacity: row.selectable ? 1 : 0.5
                }
                Text {
                    id: rowName
                    x: rowIcon.x + 36 + 12
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.min(implicitWidth, noteRow.x - 12 - x)
                    elide: Text.ElideRight
                    textFormat: Text.RichText
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.style("row").size
                    font.weight: Theme.style("row").weight
                    color: row.selectable ? Theme.ink : Theme.ink2
                    text: row.nameHtml
                }
                T {
                    anchors.left: rowName.right
                    anchors.leftMargin: 8
                    anchors.baseline: rowName.baseline
                    width: Math.max(0, noteRow.x - 12 - x)
                    elide: Text.ElideRight
                    token: "rowDev"
                    color: Theme.ink3
                    text: row.developer
                }
                Row {
                    id: noteRow
                    anchors.right: sizeText.left
                    anchors.rightMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 10
                    T { token: "note"; color: Theme.ink3; text: row.note }
                    T {
                        visible: row.hasIpaHint
                        token: "note"; font.weight: 550; color: Theme.ink2
                        font.underline: true
                        text: "Есть файл IPA"
                    }
                }
                T {
                    id: sizeText
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    width: 86
                    horizontalAlignment: Text.AlignRight
                    token: "size"
                    color: Theme.ink2
                    font.features: { "tnum": 1 }
                    text: row.sizeText
                }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.rowLine }
                MouseArea {
                    anchors.fill: parent
                    z: -1
                    enabled: row.selectable
                    cursorShape: Qt.PointingHandCursor
                    onClicked: ui.toggle(row.key)
                }
            }
        }

        // -- footer -----------------------------------------------------------
        Item {
            id: foot
            y: parent.height - height
            width: parent.width
            height: Theme.sheetFoot
            Rectangle { width: parent.width; height: 1; color: Theme.line }
            Row {
                id: footRow
                x: 32
                anchors.verticalCenter: parent.verticalCenter
                spacing: 22
                Row {
                    id: totals
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 5
                    T { id: totalText; token: "total"; text: ui.footer.total }
                    T { anchors.baseline: totalText.baseline; token: "total"; font.weight: 450; color: Theme.ink2; text: ui.footer.free }
                }
                Rectangle {
                    id: cap
                    anchors.verticalCenter: parent.verticalCenter
                    visible: ui.footer.known
                    width: Theme.capWidth; height: 8; radius: 4
                    color: Theme.capTrack
                    clip: true
                    Rectangle {
                        height: parent.height; radius: 4
                        width: parent.width * ui.footer.ratio
                        color: ui.footer.over ? Theme.accent : Theme.ink
                    }
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    visible: (ui.footer.warnBold + ui.footer.warnText) !== ""
                    // .warn max-width 420, but never under the button
                    width: Math.min(420, goButton.x - 22 - footRow.x - totals.width - 22 - (cap.visible ? cap.width + 22 : 0))
                    wrapMode: Text.WordWrap
                    textFormat: Text.StyledText
                    font.family: Theme.fontFamily
                    font.pixelSize: Math.round(Theme.style("warn").size)
                    lineHeightMode: Text.FixedHeight
                    lineHeight: 13.5 * 1.4
                    color: ui.footer.warnBold !== "" ? Theme.ink : Theme.ink3
                    text: (ui.footer.warnBold ? "<b>" + ui.footer.warnBold + "</b> " : "") + ui.footer.warnText
                }
            }
            Rectangle {
                id: goButton
                anchors.right: parent.right
                anchors.rightMargin: 32
                anchors.verticalCenter: parent.verticalCenter
                height: 52
                width: goLabel.implicitWidth + 68
                radius: Theme.radiusGo
                color: ui.footer.goEnabled ? Theme.accent : Theme.goDisabled
                T { id: goLabel; anchors.centerIn: parent; token: "go"; color: Theme.inkOnAccent; text: ui.footer.go }
                MouseArea { anchors.fill: parent; enabled: ui.footer.goEnabled; cursorShape: Qt.PointingHandCursor; onClicked: ui.restoreSelected() }
            }
        }
        // hairline around the sheet
        Rectangle { anchors.fill: parent; radius: parent.radius; color: "transparent"; border.width: 1; border.color: Theme.sheetHairline }
    }
}
