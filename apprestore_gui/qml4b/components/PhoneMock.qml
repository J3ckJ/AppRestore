import QtQuick
import "../theme"

// iPhone silhouette (frame, bezel, island, status bar) with a 4×4 home grid.
Item {
    id: root
    property var tiles: []
    property bool off: false
    property int pageDots: 0
    readonly property real w: Theme.phoneWidth
    width: w
    height: w * 2.05

    // side buttons
    Repeater {
        model: [ { l: true, t: 0.16, h: 0.05 }, { l: true, t: 0.25, h: 0.09 }, { l: true, t: 0.36, h: 0.09 }, { l: false, t: 0.28, h: 0.14 } ]
        Rectangle {
            x: modelData.l ? -root.w * 0.01 : root.width - root.w * 0.012 + root.w * 0.01
            y: root.height * modelData.t
            width: root.w * 0.012
            height: root.height * modelData.h
            radius: 2
            color: Theme.phoneFrame
        }
    }
    Rectangle {
        anchors.fill: parent
        radius: root.w * 0.185
        color: Theme.phoneFrame
        border.width: 1
        border.color: Theme.phoneFrameEdge
    }
    Rectangle {
        anchors.fill: parent
        anchors.margins: root.w * 0.014
        radius: root.w * 0.172
        color: Theme.phoneBezel
    }
    Rectangle {
        id: screen
        anchors.fill: parent
        anchors.margins: root.w * 0.047
        radius: root.w * 0.135
        color: root.off ? Theme.phoneOff : Theme.wall
        clip: true

        Rectangle {  // dynamic island
            anchors.horizontalCenter: parent.horizontalCenter
            y: root.w * 0.035
            width: parent.width * 0.32
            height: root.w * 0.092
            radius: height / 2
            color: "#000000"
        }
        Item {  // status bar
            visible: !root.off
            x: root.w * 0.1
            y: root.w * 0.05
            width: screen.width - 2 * (root.w * 0.1)
            height: Math.round(root.w * 0.05 * 1.21)
            T {
                token: "brand"
                font.pixelSize: Math.round(root.w * 0.05)
                font.weight: 600
                font.letterSpacing: -0.01 * root.w * 0.05
                text: "17:39"
                anchors.verticalCenter: parent.verticalCenter
            }
            Row {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: 4
                T {
                    token: "brand"
                    font.pixelSize: Math.round(root.w * 0.05)
                    font.weight: 600
                    text: "5G"
                    anchors.verticalCenter: parent.verticalCenter
                }
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: root.w * 0.085; height: root.w * 0.046
                    radius: 3
                    color: "transparent"
                    border.width: 1
                    border.color: Qt.rgba(0, 0, 0, 0.75)
                    Rectangle { x: 2; y: 2; width: (parent.width - 4) * 0.62; height: parent.height - 4; radius: 1; color: Theme.ink }
                }
            }
        }
        Grid {
            id: grid
            visible: !root.off
            x: root.w * 0.075
            y: root.w * 0.2
            width: screen.width - 2 * root.w * 0.075
            columns: 4
            rowSpacing: Theme.tileRowGap
            columnSpacing: 0
            Repeater {
                model: root.tiles
                Item {
                    width: grid.width / 4
                    height: cell.height
                    PhoneTile { id: cell; tile: modelData; anchors.horizontalCenter: parent.horizontalCenter }
                }
            }
        }
        Row {  // page dots (many apps)
            visible: root.pageDots > 1 && !root.off
            anchors.horizontalCenter: parent.horizontalCenter
            y: grid.y + 4 * (Theme.tileIcon + 7 + 15 + Theme.tileRowGap) - Theme.tileRowGap + 18
            spacing: 7
            Repeater {
                model: root.pageDots
                Rectangle { width: 7; height: 7; radius: 3.5; color: index === 0 ? Qt.rgba(0, 0, 0, 0.7) : Qt.rgba(0, 0, 0, 0.22) }
            }
        }
    }
}
