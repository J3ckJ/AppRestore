import QtQuick
import "../theme"

// InstallQueue (спека §3): иконка 40/9, имя rowTitle, под ним rowMeta.
// Текущее: 3 этапа, дорожки 5 px / радиус 3 / зазор 4. «Скачивание N %»
// заполняется по проценту; «Установка» без числа — полоса accent 30 %
// ширины бегает туда-сюда за 1200 мс; «Готово» — по installSettled(ok).
Column {
    id: root
    property var rows: []
    width: Theme.queueWidth
    Rectangle { width: parent.width; height: Theme.hair; color: Theme.line }
    Repeater {
        model: root.rows
        Item {
            id: row
            required property var modelData
            readonly property bool cur: modelData.state === "current"
            readonly property bool wait: modelData.state === "wait"
            readonly property int stage: modelData.stage || 0
            width: root.width
            height: (cur ? 14 + 16 : 22) + body.height + 1
            Accessible.role: cur ? Accessible.ProgressBar : Accessible.StaticText
            Accessible.name: modelData.name + ", " + (cur ? modelData.detail : (modelData.right || modelData.detail))
            AppIcon {
                anchors.verticalCenter: body.verticalCenter
                width: Theme.rowIconSize; height: Theme.rowIconSize
                radius: Theme.radiusQueueIcon
                storeId: row.modelData.storeId
                bundleId: row.modelData.bundleId
                iconOpacity: row.wait ? 0.45 : 1
            }
            Column {
                id: body
                x: Theme.rowIconSize + 14
                y: row.cur ? 14 : 11
                width: root.width - x - right.width - 14
                T { Accessible.ignored: true; token: "rowTitle"; text: row.modelData.name; color: row.wait ? Theme.ink2 : Theme.ink }
                Item { width: 1; height: 2 }
                T {
                    Accessible.ignored: true
                    token: "rowMeta"
                    font.features: Theme.tnum
                    text: row.modelData.detail
                    color: row.modelData.state === "failed" ? Theme.accent : Theme.ink3Text
                    elide: Text.ElideRight
                    width: parent.width
                }
                Item { width: 1; height: row.cur ? 10 : 0 }
                Row {
                    visible: row.cur
                    spacing: 4
                    Repeater {
                        model: row.modelData.stages || []
                        Column {
                            id: stageCol
                            required property int index
                            required property string modelData
                            width: (body.width - 8) / 3
                            spacing: 7
                            Rectangle {
                                width: parent.width; height: Theme.stageBar; radius: Theme.barRadius
                                color: stageCol.index < row.stage ? Theme.ink : Theme.track
                                clip: true
                                // скачивание: по проценту
                                Rectangle {
                                    visible: stageCol.index === row.stage && row.stage === 0
                                    height: parent.height; radius: Theme.barRadius
                                    width: parent.width * Math.max(0, row.modelData.percent) / 100
                                    color: Theme.accent
                                    Behavior on width { NumberAnimation { duration: 200 } }
                                }
                                // установка: неопределённо
                                Rectangle {
                                    id: runner
                                    visible: stageCol.index === row.stage && row.stage === 1
                                    height: parent.height; radius: Theme.barRadius
                                    width: parent.width * 0.3
                                    color: Theme.accent
                                    SequentialAnimation on x {
                                        running: runner.visible
                                        loops: Animation.Infinite
                                        NumberAnimation { from: 0; to: runner.parent.width * 0.7; duration: 600; easing.type: Easing.InOutSine }
                                        NumberAnimation { from: runner.parent.width * 0.7; to: 0; duration: 600; easing.type: Easing.InOutSine }
                                    }
                                }
                            }
                            T {
                                Accessible.ignored: true
                                token: "stageLabel"
                                font.features: Theme.tnum
                                text: stageCol.modelData
                                color: stageCol.index < row.stage ? Theme.ink2 : (stageCol.index === row.stage ? Theme.ink : Theme.ink3Text)
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
                Glyph { Accessible.ignored: true; name: "check-ok"; size: 14; visible: row.modelData.state === "done"; anchors.verticalCenter: parent.verticalCenter }
                T {
                    Accessible.ignored: true
                    token: "queueRight"
                    text: row.modelData.right
                    color: row.modelData.state === "done" ? Theme.okText : Theme.ink2
                    font.weight: row.modelData.state === "done" ? 600 : 400
                }
            }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: Theme.hair; color: Theme.line }
        }
    }
}
