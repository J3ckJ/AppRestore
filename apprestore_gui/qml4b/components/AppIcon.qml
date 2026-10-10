import QtQuick
import "../theme"

// Real artwork from iconBook (by store id, then bundle id); a neutral
// rounded square when there is none. Never a letter.
Item {
    id: root
    property string storeId: ""
    property string bundleId: ""
    property real radius: Theme.radiusRowIcon
    property real iconOpacity: 1.0
    readonly property string source: {
        var rev = iconBook.revision
        var a = storeId ? iconBook.pathFor(storeId) : ""
        return a ? a : (bundleId ? iconBook.pathFor(bundleId) : "")
    }
    readonly property bool hasArt: img.status === Image.Ready

    Rectangle {
        anchors.fill: parent
        radius: root.radius
        color: Theme.iconPlaceholder
        visible: !root.hasArt
        opacity: root.iconOpacity
    }
    Image {
        id: img
        anchors.fill: parent
        source: root.source
        sourceSize.width: Math.ceil(root.width * 2)
        sourceSize.height: Math.ceil(root.height * 2)
        smooth: true
        mipmap: true
        asynchronous: false
        opacity: root.iconOpacity
        visible: status === Image.Ready
    }
    // hairline like box-shadow: 0 0 0 .5px rgba(0,0,0,.1)
    Rectangle {
        anchors.fill: parent
        radius: root.radius
        color: "transparent"
        border.width: 0.5
        border.color: Theme.iconHairline
        opacity: root.iconOpacity
    }
}
