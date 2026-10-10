import QtQuick
import "../theme"

// .queue: apps being returned, the current one with three stage bars.
Column {
    id: root
    property var rows: []
    width: Theme.queueWidth
    Rectangle { width: parent.width; height: 1; color: Theme.line }
    Repeater {
        model: root.rows
        Item {
            id: row
            readonly property bool cur: modelData.state === "current"
            readonly property bool wait: modelData.state === "wait"
            width: root.width
            height: (cur ? 14 + 16 : 22) + body.height + 1
            AppIcon {
                id: icon
                x: 0
                anchors.verticalCenter: body.verticalCenter
                width: Theme.queueIcon; height: Theme.queueIcon
                radius: Theme.radiusQueueIcon
                storeId: modelData.storeId
                bundleId: modelData.bundleId
                iconOpacity: row.wait ? 0.45 : 1
            }
            Column {
                id: body
                x: Theme.queueIcon + 14
                y: row.cur ? 14 : 11
                width: root.width - x - right.width - 14
                T { token: "queueName"; text: modelData.name; color: row.wait ? Theme.ink2 : Theme.ink }
                Item { width: 1; height: 2 }
                T { token: "queueSmall"; text: modelData.detail; color: modelData.state === "failed" ? Theme.accent : Theme.ink3; elide: Text.ElideRight; width: parent.width }
                Item { width: 1; height: row.cur ? 10 : 0 }
                Row {
                    visible: row.cur
                    spacing: 4
                    Repeater {
                        model: ["Скачано", "Установка", "Готово"]
                        Column {
                            width: (body.width - 8) / 3
                            spacing: 7
                            Rectangle {
                                width: parent.width; height: 5; radius: 3
                                color: index < stageOf(row) ? Theme.ink : Theme.track
                                clip: true
                                Rectangle {
                                    visible: index === stageOf(row)
                                    height: parent.height; radius: 3
                                    width: parent.width * Math.max(0, (rowData(row).percent || 0)) / 100
                                    color: Theme.accent
                                }
                            }
                            T {
                                token: "stage"
                                text: modelData
                                color: index < stageOf(row) ? Theme.ink2 : (index === stageOf(row) ? Theme.ink : Theme.ink3)
                            }
                        }
                    }
                }
            }
            Row {
                id: right
                anchors.right: parent.right
                anchors.verticalCenter: body.verticalCenter
                spacing: 6
                Glyph { name: "check-ok"; size: 14; visible: modelData.state === "done"; anchors.verticalCenter: parent.verticalCenter }
                T {
                    token: "queueRight"
                    text: modelData.right
                    color: modelData.state === "done" ? Theme.ok : Theme.ink2
                    font.weight: modelData.state === "done" ? 600 : 400
                }
            }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.line }
            property var data_: modelData
        }
    }
    function rowData(r) { return r.data_ }
    function stageOf(r) { return r.data_ ? r.data_.stage : 0 }
}
