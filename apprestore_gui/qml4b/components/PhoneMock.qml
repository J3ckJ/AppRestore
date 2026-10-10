import QtQuick
import "../theme"

// iPhone silhouette (frame, bezel, island, status bar) with a 4×4 home grid.
Item {
    id: root
    property var tiles: []
    property bool off: false
    // Illustration on the phone during onboarding: "" | "trust" | "code".
    property string alert: ""
    property int pageDots: 0
    property string accessibleText: ""
    // phoneW = clamp(360, 430, 0.309·W) — задаёт Main; всё ниже от w (спека §2.3)
    property real w: Theme.phoneW
    readonly property real k: w / Theme.phoneW
    Accessible.role: Accessible.Graphic
    Accessible.name: accessibleText
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
            color: Theme.island
        }
        Item {  // status bar
            visible: !root.off
            x: root.w * 0.1
            y: root.w * 0.05
            width: screen.width - 2 * (root.w * 0.1)
            height: Math.round(root.w * 0.05 * 1.21)
            Text {
                font.family: Theme.fontText
                font.pixelSize: Math.round(root.w * 0.05)
                font.weight: 600
                font.letterSpacing: -0.01 * root.w * 0.05
                color: Theme.ink
                text: "17:39"
                anchors.verticalCenter: parent.verticalCenter
            }
            Row {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: 4
                Text {
                    font.family: Theme.fontText
                    font.pixelSize: Math.round(root.w * 0.05)
                    font.weight: 600
                    color: Theme.ink
                    text: "5G"
                    anchors.verticalCenter: parent.verticalCenter
                }
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: root.w * 0.085; height: root.w * 0.046
                    radius: 3
                    color: "transparent"
                    border.width: 1
                    border.color: Theme.batteryStroke
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
            rowSpacing: Theme.tileRowGap * root.k
            columnSpacing: 0
            Repeater {
                model: root.tiles
                Item {
                    width: grid.width / 4
                    height: cell.height
                    PhoneTile { id: cell; tile: modelData; k: root.k; anchors.horizontalCenter: parent.horizontalCenter }
                }
            }
        }
        Row {  // page dots (many apps)
            visible: root.pageDots > 1 && !root.off
            anchors.horizontalCenter: parent.horizontalCenter
            y: grid.y + 4 * ((Theme.tileIcon + 7 + 15 + Theme.tileRowGap) * root.k) - Theme.tileRowGap * root.k + 18
            spacing: 7
            Repeater {
                model: root.pageDots
                Rectangle { width: 7; height: 7; radius: 3.5; color: index === 0 ? Theme.pageDotOn : Theme.pageDotOff }
            }
        }
            Rectangle {  // dim under a system alert
            visible: root.alert !== ""
            anchors.fill: parent
            color: Theme.iosDim
            z: 2
        }
        Rectangle {
            id: alertBox
            visible: root.alert !== ""
            z: 3
            x: parent.width * 0.12
            width: parent.width * 0.76
            y: parent.height * 0.30 + 12
            height: alertCol.height
            radius: 18
            color: Theme.iosAlertBg
            clip: true
            Column {
                id: alertCol
                width: parent.width
                Item { width: 1; height: 18 }
                Text {
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    font.family: Theme.fontFamily
                    font.pixelSize: 16
                    font.weight: Font.DemiBold
                    color: Theme.iosAlertInk
                    text: root.alert === "code" ? "Код проверки Apple ID" : "Доверять этому компьютеру?"
                }
                Item { width: 1; height: 4 }
                Text {
                    visible: root.alert === "code"
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    font.family: Theme.fontFamily
                    font.pixelSize: 34
                    font.weight: Font.DemiBold
                    font.letterSpacing: 34 * 0.06
                    color: Theme.iosAlertInk
                    topPadding: 6
                    bottomPadding: 4
                    text: "482 913"
                }
                Text {
                    width: parent.width
                    leftPadding: 18
                    rightPadding: 18
                    bottomPadding: 16
                    wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignHCenter
                    font.family: Theme.fontFamily
                    font.pixelSize: 13
                    lineHeightMode: Text.FixedHeight
                    lineHeight: 12.5 * 1.35
                    color: Theme.iosAlertText
                    text: root.alert === "code"
                          ? "Введите этот код на компьютере, чтобы завершить вход."
                          : "Ваши настройки и данные будут доступны с этого компьютера при проводном или беспроводном подключении."
                }
                Rectangle { width: parent.width; height: 1; color: Theme.iosAlertLine }
                Row {
                    width: parent.width
                    Repeater {
                        model: root.alert === "code" ? ["OK"] : ["Доверять", "Не доверять"]
                        Item {
                            width: alertCol.width / (root.alert === "code" ? 1 : 2)
                            height: 15 * 1.21 + 24
                            Rectangle { visible: index > 0; width: 1; height: parent.height; color: Theme.iosAlertLine }
                            Text {
                                anchors.centerIn: parent
                                font.family: Theme.fontFamily
                                font.pixelSize: 15
                                font.weight: index === 0 ? Font.DemiBold : Font.Normal
                                color: Theme.iosAlertButton
                                text: modelData
                            }
                        }
                    }
                }
            }
        }
    }
}
