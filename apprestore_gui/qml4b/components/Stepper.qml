import QtQuick
import "../theme"

// Onboarding steps at the top centre: ok (check), on (filled), todo (ring).
Row {
    id: root
    property var steps: []
    spacing: 0
    height: Theme.pillHeight
    Repeater {
        model: root.steps
        Row {
            spacing: 0
            height: root.height
            Rectangle {
                visible: index > 0
                width: 26 + 28 + 26; height: 1.5
                color: "transparent"
                anchors.verticalCenter: parent.verticalCenter
                Rectangle { x: 26; width: 28; height: 1.5; color: Theme.stepLine }
            }
            Row {
                spacing: 8
                anchors.verticalCenter: parent.verticalCenter
                Rectangle {
                    width: 24; height: 24; radius: 12
                    color: modelData.state === "on" ? Theme.ink : (modelData.state === "ok" ? Theme.stepOkBg : "transparent")
                    border.width: modelData.state === "todo" ? 1.5 : 0
                    border.color: Theme.ink3
                    T {
                        anchors.centerIn: parent
                        visible: modelData.state !== "ok"
                        token: "stepNum12"
                        color: modelData.state === "on" ? Theme.inkOnAccent : Theme.ink
                        text: String(modelData.n)
                    }
                    Glyph { anchors.centerIn: parent; visible: modelData.state === "ok"; name: "check-ink"; size: 12 }
                }
                T {
                    anchors.verticalCenter: parent.verticalCenter
                    token: "stepper"
                    color: modelData.state === "on" ? Theme.ink : Theme.ink2
                    text: modelData.title
                }
            }
        }
    }
}
