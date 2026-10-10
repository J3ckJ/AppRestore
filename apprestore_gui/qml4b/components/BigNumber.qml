import QtQuick
import "../theme"

// HeroNumeral (спека §2.2): цифра + две строки подписи справа, по низу цифры.
// 1–2 знака → numeralXL (300); 3 знака → numeralL (250), подпись под цифрой;
// 4+ → 190, под цифрой; installing → numeralM + numeralCaptionM, зазор 20.
// Если цифра с подписью шире maxWidth — сразу колонкой.
Item {
    id: root
    property int number: 0
    property string word: ""
    property string word2: ""
    property bool small: false        // installing
    property real maxWidth: Theme.heroWidth
    property string accessibleText: ""
    readonly property int digits: String(number).length
    readonly property string numToken: small ? "numeralM"
        : digits <= 2 ? "numeralXL" : digits === 3 ? "numeralL" : "numeralM"
    readonly property var numSt: Theme.style(numToken)
    readonly property real gap: small ? 20 : 26
    readonly property bool column: !small && (digits >= 3 || rowWidth > maxWidth)
    readonly property string capToken: small ? "numeralCaptionM" : (column ? "numeralCaptionCol" : "numeralCaption")
    readonly property var capSt: Theme.style(capToken)
    readonly property real capLine: capSt.size * 1.06
    readonly property real boxHeight: numSt.lh
    readonly property real rowWidth: figMetrics.advanceWidth - 0.06 * numSt.size + gap
                                     + Math.max(w1Metrics.advanceWidth, w2Metrics.advanceWidth)

    Accessible.role: Accessible.StaticText
    Accessible.name: accessibleText

    TextMetrics { id: figMetrics; font: fig.font; text: fig.text }
    // ширина подписи справа — всегда шрифтом numeralCaption (не w1.font: иначе петля)
    TextMetrics {
        id: w1Metrics
        font.family: Theme.fontText; font.pixelSize: Theme.numeralCaption.size
        font.weight: Theme.numeralCaption.weight; font.letterSpacing: Theme.numeralCaption.ls
        text: root.word
    }
    TextMetrics {
        id: w2Metrics
        font.family: Theme.fontText; font.pixelSize: Theme.numeralCaption.size
        font.weight: Theme.numeralCaption.weight; font.letterSpacing: Theme.numeralCaption.ls
        text: root.word2
    }

    width: column ? Math.max(fig.x + fig.implicitWidth, words.width) : words.x + words.width
    height: column ? boxHeight + 24 + 2 * capLine : boxHeight

    Item {
        id: box
        width: fig.implicitWidth
        height: root.boxHeight
        T {
            id: fig
            Accessible.ignored: true
            token: root.numToken
            font.features: Theme.tnum
            // −0.06em: оптическое выравнивание вертикали цифры по краю колонки
            x: -0.06 * root.numSt.size
            anchors.baseline: parent.bottom
            anchors.baselineOffset: root.numSt.size * 0.004
            text: String(root.number)
        }
    }
    Column {
        id: words
        x: root.column ? 0 : fig.x + fig.implicitWidth + root.gap
        // Qt ставит глифы в фиксированной строке ниже, чем CSS (нет
        // отрицательного полу-интерлиньяжа): поднимаем на разницу.
        y: root.column ? root.boxHeight + 24
                       : root.boxHeight - 2 - 2 * root.capLine - root.capSt.size * 0.16
        T {
            id: w1
            Accessible.ignored: true
            token: root.capToken
            lineHeightMode: Text.FixedHeight
            lineHeight: root.capLine
            text: root.word
        }
        T {
            id: w2
            Accessible.ignored: true
            token: root.capToken
            lineHeightMode: Text.FixedHeight
            lineHeight: root.capLine
            color: Theme.ink3
            text: root.word2
        }
    }
}
