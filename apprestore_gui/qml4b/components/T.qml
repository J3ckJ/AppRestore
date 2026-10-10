import QtQuick
import QtQuick.Window
import "../theme"

// Text in a Theme style (Ника, часть 1: size / weight / lh / ls in px).
// Fractional sizes (16.5, 14.5, 12.5) go through pointSize: pixelSize is int.
Text {
    property string token: "lead"
    readonly property var st: Theme.style(token)
    font.family: Theme.isDisplay(token) ? Theme.fontDisplay : Theme.fontText
    font.pointSize: st.size * 72 / (Screen.logicalPixelDensity * 25.4)
    font.weight: st.weight
    font.letterSpacing: st.ls
    color: Theme.ink
    textFormat: Text.PlainText
}
