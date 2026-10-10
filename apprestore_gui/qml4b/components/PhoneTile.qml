import QtQuick
import QtQuick.Shapes
import "../theme"

// HomeTile (спека §2.4). kind: app | slot | offloaded | waiting |
// downloading | installing | new | unavailable. progress: 0…1 при
// скачивании, −1 = неопределённо (установка: сектор 90°, оборот 1200 мс).
Item {
    id: root
    property var tile: ({})
    property real k: 1.0                       // phoneW / 430
    readonly property string kind: tile.kind || "app"
    readonly property real icon: Theme.phoneIcon * k
    readonly property real r: Theme.tileRadius * k
    readonly property real progress: tile.progress === undefined ? -1 : tile.progress
    readonly property bool busy: kind === "downloading" || kind === "installing"
    // THE one rule for the dashed outline (Ника): dashes = a truly empty place.
    // Any tile that stands for a real app (store id or bundle id) is a normal icon
    // tile (artwork, else Theme.iconPlaceholder); a missing app's «slot» is the
    // plain Theme.iconPlaceholder tile of the same size and radius; no dashes, no letters.
    readonly property bool emptySlot: kind === "slot" && !(tile.storeId || tile.bundleId)
    width: icon
    height: icon + 7 + 15

    Accessible.role: Accessible.StaticText
    Accessible.name: {
        var n = root.tile.name || ""
        switch (root.kind) {
        case "slot": return "Пустое место: " + n + ", не хватает"
        case "waiting": return (root.tile.app || n) + ", ожидает"
        case "downloading": return (root.tile.app || n) + ", скачивается, " + Math.round(Math.max(0, root.progress) * 100) + " процента"
        case "installing": return (root.tile.app || n) + ", ставится"
        case "new": return n + ", вернулось"
        case "unavailable": return (root.tile.app || n) + ", недоступна в регионе"
        case "offloaded": return n + ", сгружено"
        default: return n
        }
    }

    AppIcon {
        id: art
        width: root.icon; height: root.icon
        radius: root.r
        // a missing app's place: the neutral placeholder tile (not its artwork —
        // it is not on the phone), same size and radius as a real icon
        storeId: root.kind === "slot" ? "" : (root.tile.storeId || "")
        bundleId: root.kind === "slot" ? "" : (root.tile.bundleId || "")
        visible: !root.emptySlot
        // opacity у иконки, не у плитки: подпись не гаснет (спека §6)
        iconOpacity: root.tile.pending ? 0.6
                   : root.busy ? 0.55
                   : root.kind === "waiting" ? 0.35
                   : root.kind === "unavailable" ? 0.3
                   : root.kind === "offloaded" ? 0.6 : 1.0
        Behavior on iconOpacity { NumberAnimation { duration: Theme.durFast; easing.type: Theme.easeOut } }
    }
    // пустое место: пунктир (у Rectangle.border пунктира нет)
    Shape {
        width: root.icon; height: root.icon
        objectName: "slotDash"
        visible: root.emptySlot && !root.tile.pending
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: Theme.slotDash
            strokeWidth: Theme.slotDashWidth
            strokeStyle: ShapePath.DashLine
            dashPattern: Theme.slotDashPattern
            fillColor: "transparent"
            PathRectangle {
                x: Theme.slotDashWidth / 2; y: Theme.slotDashWidth / 2
                width: root.icon - Theme.slotDashWidth; height: root.icon - Theme.slotDashWidth
                radius: root.r - Theme.slotDashWidth / 2
            }
        }
    }
    // скачивание / установка: вуаль + кольцо r19/2.5 + сектор r15
    Rectangle {
        width: root.icon; height: root.icon
        radius: root.r
        color: Theme.veil
        visible: root.busy
        Item {
            id: pie
            anchors.centerIn: parent
            width: Theme.pieBox; height: Theme.pieBox
            readonly property bool indeterminate: root.kind === "installing" || root.progress < 0
            property real shown: Math.max(0.02, Math.min(1, root.progress))
            Behavior on shown { NumberAnimation { duration: 200; easing.type: Easing.Linear } }
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeColor: Theme.progressInk; strokeWidth: Theme.ringWidth; fillColor: "transparent"
                    PathAngleArc { centerX: 22; centerY: 22; radiusX: 19; radiusY: 19; startAngle: 0; sweepAngle: 360 }
                }
            }
            Shape {
                id: sector
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeWidth: 0; strokeColor: "transparent"; fillColor: Theme.progressInk
                    startX: 22; startY: 22
                    PathAngleArc {
                        centerX: 22; centerY: 22; radiusX: 15; radiusY: 15
                        startAngle: -90
                        sweepAngle: pie.indeterminate ? 90 : 360 * pie.shown
                        moveToStart: false
                    }
                    PathLine { x: 22; y: 22 }
                }
                RotationAnimator on rotation {
                    running: pie.indeterminate && root.visible
                    from: 0; to: 360
                    duration: 1200
                    loops: Animation.Infinite
                }
            }
        }
    }
    Row {
        anchors.horizontalCenter: parent.horizontalCenter
        y: root.icon + 7
        spacing: 4
        Glyph { Accessible.ignored: true; name: "cloud"; size: 11; visible: root.kind === "offloaded"; anchors.verticalCenter: parent.verticalCenter }
        Rectangle {
            width: Theme.newDot; height: Theme.newDot; radius: Theme.newDot / 2
            color: Theme.iosNew
            visible: root.kind === "new"
            anchors.verticalCenter: parent.verticalCenter
        }
        T {
            Accessible.ignored: true
            token: "phoneLabel"
            text: root.tile.name || ""
            opacity: root.kind === "slot" && root.tile.pending ? 0 : 1
            color: root.kind === "slot" ? Theme.ink2 : (root.kind === "unavailable" ? Theme.ink3 : Theme.ink)
            elide: Text.ElideRight
            width: Math.min(implicitWidth, 86 * root.k)
        }
    }
}
