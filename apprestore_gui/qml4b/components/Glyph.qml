import QtQuick

Image {
    property string name: "info"
    property int size: 14
    source: Qt.resolvedUrl("../icons/" + name + ".svg")
    sourceSize.width: size * 2
    sourceSize.height: size * 2
    width: size
    height: size
    smooth: true
}
