import QtQuick
import QtQuick.Controls

ApplicationWindow {
    width: 960
    height: 640
    visible: true
    title: "AppRestore engine bench (Qt Quick)"

    Column {
        anchors.centerIn: parent
        spacing: 12
        Label { text: "AppRestore"; font.pixelSize: 28 }
        Button { text: "Find apps" }
    }
}
