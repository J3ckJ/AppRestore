import QtQuick
import QtQuick.Shapes
import "../theme"

// One home-screen icon on the phone silhouette.
// kind: installed | cloud | slot | done | current | wait | na
Item {
    id: root
    property var tile: ({})
    readonly property string kind: tile.kind || "installed"
    width: Theme.tileIcon
    height: Theme.tileIcon + 7 + 15

    AppIcon {
        id: icon
        width: Theme.tileIcon; height: Theme.tileIcon
        radius: Theme.radiusTile
        storeId: root.tile.storeId || ""
        bundleId: root.tile.bundleId || ""
        visible: root.kind !== "slot"
        iconOpacity: root.kind === "current" ? 0.55
                   : root.kind === "wait" ? 0.35
                   : root.kind === "na" ? 0.3
                   : root.kind === "cloud" ? 0.6 : 1.0
    }
    Shape {
        width: Theme.tileIcon; height: Theme.tileIcon
        visible: root.kind === "slot"
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: Theme.slotStroke
            strokeWidth: 2
            strokeStyle: ShapePath.DashLine
            dashPattern: [2.2, 1.6]
            fillColor: "transparent"
            PathRectangle { x: 1; y: 1; width: Theme.tileIcon - 2; height: Theme.tileIcon - 2; radius: Theme.radiusTile - 1 }
        }
    }
    // installing: dark veil with a progress pie
    Rectangle {
        width: Theme.tileIcon; height: Theme.tileIcon
        radius: Theme.radiusTile
        color: Theme.veil
        visible: root.kind === "current"
        Shape {
            anchors.centerIn: parent
            width: 44; height: 44
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: "#ffffff"; strokeWidth: 2.5; fillColor: "transparent"
                PathAngleArc { centerX: 22; centerY: 22; radiusX: 19; radiusY: 19; startAngle: 0; sweepAngle: 360 }
            }
            ShapePath {
                strokeWidth: 0; strokeColor: "transparent"; fillColor: "#ffffff"
                startX: 22; startY: 22
                PathAngleArc { centerX: 22; centerY: 22; radiusX: 15; radiusY: 15; startAngle: -90; sweepAngle: 360 * Math.max(0.02, (root.tile.percent || 64) / 100) }
                PathLine { x: 22; y: 22 }
            }
        }
    }
    Row {
        anchors.horizontalCenter: parent.horizontalCenter
        y: Theme.tileIcon + 7
        spacing: 4
        Glyph { name: "cloud"; size: 11; visible: root.kind === "cloud"; anchors.verticalCenter: parent.verticalCenter }
        Rectangle { width: 6; height: 6; radius: 3; color: Theme.iosNew; visible: root.kind === "done"; anchors.verticalCenter: parent.verticalCenter }
        T {
            token: "tileLabel"
            text: root.tile.name || ""
            color: root.kind === "slot" ? Theme.ink2 : (root.kind === "na" ? Theme.ink3 : Theme.ink)
            elide: Text.ElideRight
            width: Math.min(implicitWidth, 86)
        }
    }
}
