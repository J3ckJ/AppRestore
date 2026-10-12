import QtQuick
import "../theme"

// «Журнал»: this install, then licenses Apple issued in this window.
// Plain language only. No Apple ID, password, token, storefront, or price.
Sheet {
    id: root
    objectName: "journalSheet"
    readonly property var j: ui.journal
    title: root.j.title || "Журнал"
    cancelText: root.j.close || "Закрыть"
    panelWidth: Theme.authSheetWidth
    onCancel: ui.closeJournal()

    Flickable {
        id: scroller
        width: parent.width
        height: Math.min(Math.max(body.implicitHeight, 24), 420)
        contentWidth: width
        contentHeight: body.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        Column {
            id: body
            width: scroller.width
            spacing: 0
            T {
                visible: (root.j.rows || []).length === 0
                width: parent.width
                wrapMode: Text.WordWrap
                token: "authSub"
                color: Theme.ink2
                text: root.j.empty || ""
            }
            Repeater {
                model: root.j.rows || []
                delegate: Column {
                    required property var modelData
                    required property int index
                    width: body.width
                    spacing: 0
                    Item { width: 1; height: index === 0 ? 0 : 14 }
                    T {
                        width: parent.width
                        token: "row"
                        elide: Text.ElideRight
                        text: modelData.name || ""
                    }
                    Item { width: 1; height: 4 }
                    T {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        token: "rowDev"
                        color: Theme.ink2
                        text: modelData.detail || ""
                    }
                }
            }
        }
    }
}
