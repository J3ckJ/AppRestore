import QtQuick
import "../theme"

// .num: huge figure with a two-line caption on its right (or below, "col").
Item {
    id: root
    property int number: 0
    property string word: ""
    property string word2: ""
    property bool small: false
    property bool column: false
    readonly property string numToken: small ? "numberSm" : "number"
    readonly property real numSize: column ? 250 : Theme.type[numToken].size
    readonly property real wordSize: column ? 40 : Theme.type[small ? "numberWordSm" : "numberWord"].size
    readonly property real boxHeight: numSize * 0.72
    readonly property real wordLine: wordSize * 1.06
    width: column ? Math.max(fig.width, words.width) : fig.x + fig.implicitWidth + (small ? 20 : 26) + words.width
    height: column ? boxHeight + 24 + 2 * wordLine : boxHeight

    Item {
        id: box
        width: fig.implicitWidth
        height: root.boxHeight
        T {
            id: fig
            token: root.numToken
            font.pixelSize: Math.round(root.numSize)
            font.letterSpacing: -0.065 * root.numSize
            font.features: { "tnum": 1 }
            x: -0.06 * root.numSize
            anchors.baseline: parent.bottom
            anchors.baselineOffset: root.numSize * 0.004
            text: String(root.number)
        }
    }
    Column {
        id: words
        x: root.column ? 0 : fig.x + fig.implicitWidth + (root.small ? 20 : 26)
        // Qt puts the glyphs lower than CSS in a fixed line box (no negative
        // half-leading): lift by the difference.
        y: (root.column ? root.boxHeight + 24 : root.boxHeight - 2 - 2 * root.wordLine) - (root.column ? 0 : root.wordSize * 0.16)
        T {
            token: root.small ? "numberWordSm" : "numberWord"
            font.pixelSize: Math.round(root.wordSize)
            font.letterSpacing: -0.025 * root.wordSize
            lineHeightMode: Text.FixedHeight
            lineHeight: root.wordLine
            text: root.word
        }
        T {
            token: root.small ? "numberWordSm" : "numberWord"
            font.pixelSize: Math.round(root.wordSize)
            font.letterSpacing: -0.025 * root.wordSize
            lineHeightMode: Text.FixedHeight
            lineHeight: root.wordLine
            color: Theme.ink3
            text: root.word2
        }
    }
}
