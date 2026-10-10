import QtQuick
import "../theme"

// Text in one of the Theme type tokens.
Text {
    property string token: "lead"
    font.family: (token === "number" || token === "numberSm" || token === "h1") ? Theme.displayFamily : Theme.fontFamily
    font.pixelSize: Math.round(Theme.type[token].size)
    font.weight: Theme.type[token].weight
    font.letterSpacing: Theme.ls(token)
    color: Theme.ink
    textFormat: Text.PlainText
}
