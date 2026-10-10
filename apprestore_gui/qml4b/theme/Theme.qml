// Theme.qml — токены AppRestore 4b. Синглтон: в qmldir — "singleton Theme 1.0 Theme.qml".
// Источник: design/spec/tokens.json (из CSS концептов 4b). Значения — логические px.
//
// Правила (часть 1, §1.5):
//  * В компонентах НЕТ hex: только Theme.<семантическое имя>. Палитра — объект `palette`,
//    v1 всегда `light` (приложение принудительно светлое). Тёмная тема — после v1: добавить
//    `dark` с теми же ключами и переключать `palette`, компоненты не трогаем.
//  * Шрифты: таблица `typeMac` / `typeWin`, выбор по Qt.platform.os. Компоненты берут
//    Theme.type.<стиль> и применяют через Theme.applyFont(item.font, style) или напрямую поля.
pragma Singleton
import QtQuick

QtObject {
    id: theme
    readonly property bool isMac: Qt.platform.os === "osx" || Qt.platform.os === "macos"
    // ui4b: «--ui4b-as-windows» only for offscreen screenshots next to the -windows renders
    readonly property bool isWin: Qt.platform.os === "windows" || Qt.application.arguments.indexOf("--ui4b-as-windows") >= 0
    // Segoe UI Variable есть только в Windows 11; на Windows 10 — статический Segoe UI (веса 400/600/700).
    readonly property bool winVariable: isWin && Qt.fontFamilies().indexOf("Segoe UI Variable Text") >= 0

    // ── Палитра (семантические ключи; v1 = light)
    readonly property var light: ({
        bg: "#EBEAE6",
        card: "#FFFFFF",
        surfaceSoft: "#F3F2EE",
        ink: "#121212",
        ink2: "#61605C",
        ink3: "#97958F",
        ink3Text: "#77756F",
        line: "#D6D4CE",
        lineSoft: "#ECEBE6",
        track: "#D3D1CA",
        accent: "#B35128",
        accentHover: "#A34A24",
        accentPressed: "#934221",
        accentDisabled: "#D9D7D1",
        accentTint: "#F6EBE4",
        ok: "#2F9E55",
        okText: "#217A40",
        off: "#A3A29D",
        iosNew: "#0A84FF",
        wall: "#DDD8CC",
        screenOff: "#1F1F21",
        phoneFrame: "#1B1B1D",
        phoneFrameEdge: "#38383B",
        phoneBezel: "#0B0B0B",
        slotDash: Qt.rgba(0, 0, 0, 0.32),
        veil: Qt.rgba(0, 0, 0, 0.45),
        backdrop: Qt.rgba(20/255, 20/255, 18/255, 0.34),
        sheet: "#FFFFFF",
        rowOn: "#FBF6F2",
        mark: "#F6EBE4",
        capTrack: "#E2E0DA",
        checkBorder: "#B8B6AE",
        checkDisabledBorder: "#DCDAD3",
        stepTodoBorder: "#C3C1BA",
        stepOkBg: "#DCDAD3",
        stepLine: "#CFCDC6",
        iconPlaceholder: "#E2E0DA",
        errorText: "#A04824",
        iosAlert: "#F4F4F2",
        iosAlertBlue: "#0A6FE0",
    })
    property var palette: light          // v1: не переключается; системная тёмная тема игнорируется
    readonly property color bg: palette.bg
    readonly property color card: palette.card
    readonly property color surfaceSoft: palette.surfaceSoft
    readonly property color ink: palette.ink
    readonly property color ink2: palette.ink2
    readonly property color ink3: palette.ink3
    readonly property color ink3Text: palette.ink3Text
    readonly property color line: palette.line
    readonly property color lineSoft: palette.lineSoft
    readonly property color track: palette.track
    readonly property color accent: palette.accent
    readonly property color accentHover: palette.accentHover
    readonly property color accentPressed: palette.accentPressed
    readonly property color accentDisabled: palette.accentDisabled
    readonly property color accentTint: palette.accentTint
    readonly property color ok: palette.ok
    readonly property color okText: palette.okText
    readonly property color off: palette.off
    readonly property color iosNew: palette.iosNew
    readonly property color wall: palette.wall
    readonly property color screenOff: palette.screenOff
    readonly property color phoneFrame: palette.phoneFrame
    readonly property color phoneFrameEdge: palette.phoneFrameEdge
    readonly property color phoneBezel: palette.phoneBezel
    readonly property color slotDash: palette.slotDash
    readonly property color veil: palette.veil
    readonly property color backdrop: palette.backdrop
    readonly property color sheet: palette.sheet
    readonly property color rowOn: palette.rowOn
    readonly property color mark: palette.mark
    readonly property color capTrack: palette.capTrack
    readonly property color checkBorder: palette.checkBorder
    readonly property color checkDisabledBorder: palette.checkDisabledBorder
    readonly property color stepTodoBorder: palette.stepTodoBorder
    readonly property color stepOkBg: palette.stepOkBg
    readonly property color stepLine: palette.stepLine
    readonly property color iconPlaceholder: palette.iconPlaceholder
    readonly property color errorText: palette.errorText
    readonly property color iosAlert: palette.iosAlert
    readonly property color iosAlertBlue: palette.iosAlertBlue
    readonly property color focusRing: palette.ink

    // ── Шрифты
    readonly property string fontText: isMac ? ".AppleSystemUIFont" : (isWin ? (winVariable ? "Segoe UI Variable Text" : "Segoe UI") : "Inter")
    readonly property string fontDisplay: isMac ? ".AppleSystemUIFont" : (isWin ? (winVariable ? "Segoe UI Variable Display" : "Segoe UI") : "Inter")
    readonly property var tnum: ({ "tnum": 1 })  // font.features (Qt ≥ 6.6)

    // ── Типографика: size / weight / lh (px, фиксированный lineHeight) / ls (px) / tnum / display-семейство
    readonly property var typeMac: ({
        numeralXL: { size: 300, weight: 700, lh: 216, ls: -19.5, tnum: true, display: true },
        numeralL: { size: 250, weight: 700, lh: 180, ls: -16.25, tnum: true, display: true },
        numeralM: { size: 190, weight: 700, lh: 137, ls: -12.35, tnum: true, display: true },
        numeralCaption: { size: 38, weight: 650, lh: 40, ls: -0.95, tnum: false, display: true },
        numeralCaptionM: { size: 32, weight: 650, lh: 34, ls: -0.8, tnum: false, display: true },
        display: { size: 84, weight: 700, lh: 81, ls: -3.78, tnum: false, display: true },
        lead: { size: 22, weight: 400, lh: 31, ls: -0.11, tnum: false, display: false },
        step: { size: 21, weight: 400, lh: 28, ls: -0.21, tnum: false, display: false },
        button: { size: 21, weight: 650, lh: 26, ls: -0.21, tnum: false, display: false },
        brand: { size: 17, weight: 650, lh: 22, ls: -0.17, tnum: false, display: false },
        rowTitle: { size: 16.5, weight: 600, lh: 22, ls: 0.0, tnum: false, display: false },
        overline: { size: 15, weight: 700, lh: 20, ls: 0, tnum: false, display: false },
        link: { size: 15, weight: 500, lh: 20, ls: 0, tnum: false, display: false },
        fine: { size: 14.5, weight: 400, lh: 21.75, ls: 0.0, tnum: false, display: false },
        pill: { size: 14, weight: 550, lh: 18, ls: 0, tnum: false, display: false },
        rowMeta: { size: 13.5, weight: 450, lh: 18, ls: 0.0, tnum: false, display: false },
        stageLabel: { size: 12.5, weight: 600, lh: 16, ls: 0.0, tnum: false, display: false },
        phoneLabel: { size: 12.5, weight: 500, lh: 16, ls: 0.0, tnum: false, display: false },
        sheetTitle: { size: 30, weight: 700, lh: 36, ls: -0.9, tnum: false, display: true },
        sheetSub: { size: 15, weight: 500, lh: 20, ls: 0, tnum: false, display: false },
        sheetLink: { size: 15, weight: 550, lh: 20, ls: 0, tnum: false, display: false },
        rail: { size: 15, weight: 550, lh: 20, ls: 0, tnum: false, display: false },
        railCount: { size: 15, weight: 600, lh: 20, ls: 0, tnum: true, display: false },
        railSmall: { size: 12.5, weight: 500, lh: 16, ls: 0.0, tnum: false, display: false },
        railHelp: { size: 13, weight: 400, lh: 19.5, ls: 0, tnum: false, display: false },
        search: { size: 16, weight: 400, lh: 22, ls: 0, tnum: false, display: false },
        seg: { size: 13.5, weight: 550, lh: 18, ls: 0.0, tnum: false, display: false },
        toolLink: { size: 13.5, weight: 550, lh: 18, ls: 0.0, tnum: false, display: false },
        groupTitle: { size: 16, weight: 700, lh: 22, ls: 0, tnum: false, display: false },
        groupCount: { size: 14, weight: 500, lh: 18, ls: 0, tnum: false, display: false },
        groupAction: { size: 13.5, weight: 550, lh: 18, ls: 0.0, tnum: false, display: false },
        row: { size: 15, weight: 550, lh: 20, ls: 0, tnum: false, display: false },
        rowDev: { size: 13.5, weight: 400, lh: 18, ls: 0.0, tnum: false, display: false },
        rowSize: { size: 14, weight: 400, lh: 18, ls: 0, tnum: true, display: false },
        rowNote: { size: 13, weight: 400, lh: 17, ls: 0, tnum: false, display: false },
        total: { size: 17, weight: 700, lh: 22, ls: 0, tnum: true, display: false },
        warn: { size: 13.5, weight: 400, lh: 19, ls: 0.0, tnum: false, display: false },
        go: { size: 17, weight: 650, lh: 22, ls: 0, tnum: false, display: false },
        banner: { size: 14.5, weight: 400, lh: 20, ls: 0.0, tnum: false, display: false },
        stepper: { size: 14, weight: 550, lh: 18, ls: 0, tnum: false, display: false },
        stepNum: { size: 12, weight: 700, lh: 16, ls: 0, tnum: true, display: false },
        tableRow: { size: 18, weight: 400, lh: 24, ls: 0, tnum: false, display: false },
        tableValue: { size: 18, weight: 650, lh: 24, ls: 0, tnum: false, display: false },
        scanCaption: { size: 15, weight: 600, lh: 20, ls: 0, tnum: true, display: false },
        foundNum: { size: 22, weight: 700, lh: 28, ls: -0.44, tnum: true, display: false },
        found: { size: 16, weight: 400, lh: 22, ls: 0, tnum: false, display: false },
        authSub: { size: 16, weight: 400, lh: 24, ls: 0, tnum: false, display: false },
        fieldLabel: { size: 13.5, weight: 600, lh: 18, ls: 0.0, tnum: false, display: false },
        field: { size: 16, weight: 400, lh: 22, ls: 0, tnum: false, display: false },
        fieldError: { size: 14, weight: 550, lh: 20, ls: 0, tnum: false, display: false },
        codeDigit: { size: 32, weight: 650, lh: 40, ls: 0, tnum: true, display: true },
        note: { size: 14, weight: 400, lh: 20, ls: 0, tnum: false, display: false },
        status: { size: 14, weight: 400, lh: 20, ls: 0, tnum: false, display: false },
    })
    readonly property var typeWin: ({
        numeralXL: { size: 300, weight: 700, lh: 216, ls: -16.5, tnum: true, display: true },
        numeralL: { size: 250, weight: 700, lh: 180, ls: -13.75, tnum: true, display: true },
        numeralM: { size: 190, weight: 700, lh: 137, ls: -10.45, tnum: true, display: true },
        numeralCaption: { size: 38, weight: 650, lh: 40, ls: -0.76, tnum: false, display: true },
        numeralCaptionM: { size: 32, weight: 650, lh: 34, ls: -0.64, tnum: false, display: true },
        display: { size: 84, weight: 700, lh: 81, ls: -2.94, tnum: false, display: true },
        lead: { size: 22, weight: 400, lh: 31, ls: -0.06, tnum: false, display: false },
        step: { size: 21, weight: 400, lh: 28, ls: -0.1, tnum: false, display: false },
        button: { size: 21, weight: 650, lh: 26, ls: -0.1, tnum: false, display: false },
        brand: { size: 17, weight: 650, lh: 22, ls: -0.09, tnum: false, display: false },
        rowTitle: { size: 16.5, weight: 600, lh: 22, ls: 0.0, tnum: false, display: false },
        overline: { size: 15, weight: 700, lh: 20, ls: 0, tnum: false, display: false },
        link: { size: 15, weight: 500, lh: 20, ls: 0, tnum: false, display: false },
        fine: { size: 14.5, weight: 400, lh: 21.75, ls: 0.0, tnum: false, display: false },
        pill: { size: 14, weight: 550, lh: 18, ls: 0, tnum: false, display: false },
        rowMeta: { size: 13.5, weight: 450, lh: 18, ls: 0.0, tnum: false, display: false },
        stageLabel: { size: 13, weight: 600, lh: 16.6, ls: 0, tnum: false, display: false },
        phoneLabel: { size: 13, weight: 500, lh: 16.6, ls: 0, tnum: false, display: false },
        sheetTitle: { size: 30, weight: 700, lh: 36, ls: -0.6, tnum: false, display: true },
        sheetSub: { size: 15, weight: 500, lh: 20, ls: 0, tnum: false, display: false },
        sheetLink: { size: 15, weight: 550, lh: 20, ls: 0, tnum: false, display: false },
        rail: { size: 15, weight: 550, lh: 20, ls: 0, tnum: false, display: false },
        railCount: { size: 15, weight: 600, lh: 20, ls: 0, tnum: true, display: false },
        railSmall: { size: 13, weight: 500, lh: 16.6, ls: 0, tnum: false, display: false },
        railHelp: { size: 13, weight: 400, lh: 19.5, ls: 0, tnum: false, display: false },
        search: { size: 16, weight: 400, lh: 22, ls: 0, tnum: false, display: false },
        seg: { size: 13.5, weight: 550, lh: 18, ls: 0.0, tnum: false, display: false },
        toolLink: { size: 13.5, weight: 550, lh: 18, ls: 0.0, tnum: false, display: false },
        groupTitle: { size: 16, weight: 700, lh: 22, ls: 0, tnum: false, display: false },
        groupCount: { size: 14, weight: 500, lh: 18, ls: 0, tnum: false, display: false },
        groupAction: { size: 13.5, weight: 550, lh: 18, ls: 0.0, tnum: false, display: false },
        row: { size: 15, weight: 550, lh: 20, ls: 0, tnum: false, display: false },
        rowDev: { size: 13.5, weight: 400, lh: 18, ls: 0.0, tnum: false, display: false },
        rowSize: { size: 14, weight: 400, lh: 18, ls: 0, tnum: true, display: false },
        rowNote: { size: 13, weight: 400, lh: 17, ls: 0, tnum: false, display: false },
        total: { size: 17, weight: 700, lh: 22, ls: 0, tnum: true, display: false },
        warn: { size: 13.5, weight: 400, lh: 19, ls: 0.0, tnum: false, display: false },
        go: { size: 17, weight: 650, lh: 22, ls: 0, tnum: false, display: false },
        banner: { size: 14.5, weight: 400, lh: 20, ls: 0.0, tnum: false, display: false },
        stepper: { size: 14, weight: 550, lh: 18, ls: 0, tnum: false, display: false },
        stepNum: { size: 13, weight: 700, lh: 17.3, ls: 0, tnum: true, display: false },
        tableRow: { size: 18, weight: 400, lh: 24, ls: 0, tnum: false, display: false },
        tableValue: { size: 18, weight: 650, lh: 24, ls: 0, tnum: false, display: false },
        scanCaption: { size: 15, weight: 600, lh: 20, ls: 0, tnum: true, display: false },
        foundNum: { size: 22, weight: 700, lh: 28, ls: -0.44, tnum: true, display: false },
        found: { size: 16, weight: 400, lh: 22, ls: 0, tnum: false, display: false },
        authSub: { size: 16, weight: 400, lh: 24, ls: 0, tnum: false, display: false },
        fieldLabel: { size: 13.5, weight: 600, lh: 18, ls: 0.0, tnum: false, display: false },
        field: { size: 16, weight: 400, lh: 22, ls: 0, tnum: false, display: false },
        fieldError: { size: 14, weight: 550, lh: 20, ls: 0, tnum: false, display: false },
        codeDigit: { size: 32, weight: 650, lh: 40, ls: 0, tnum: true, display: true },
        note: { size: 14, weight: 400, lh: 20, ls: 0, tnum: false, display: false },
        status: { size: 14, weight: 400, lh: 20, ls: 0, tnum: false, display: false },
    })
    readonly property var type: isWin ? typeWin : typeMac

    // Вес с учётом Windows 10 (статический Segoe UI: 450→400, 550→600, 650→600).
    function weightFor(w) {
        if (!isWin || winVariable) return w
        return w === 450 ? 400 : (w === 550 || w === 650) ? 600 : w
    }
    // Применить стиль к font и вернуть lineHeight: Text { font: ...; lineHeight: Theme.applyFont(font, Theme.type.lead); lineHeightMode: Text.FixedHeight }
    function applyFont(f, s) {
        f.family = s.display ? fontDisplay : fontText
        f.pixelSize = s.size
        f.weight = weightFor(s.weight)
        f.letterSpacing = s.ls
        if (s.tnum) f.features = tnum
        return s.lh
    }

    // Совместимость с частью 1: прежние имена = стили текущей ОС.
    readonly property var numeralXL: type.numeralXL
    readonly property var numeralL: type.numeralL
    readonly property var numeralM: type.numeralM
    readonly property var numeralCaption: type.numeralCaption
    readonly property var numeralCaptionM: type.numeralCaptionM
    readonly property var display: type.display
    readonly property var lead: type.lead
    readonly property var step: type.step
    readonly property var button: type.button
    readonly property var brand: type.brand
    readonly property var rowTitle: type.rowTitle
    readonly property var overline: type.overline
    readonly property var link: type.link
    readonly property var fine: type.fine
    readonly property var pill: type.pill
    readonly property var rowMeta: type.rowMeta
    readonly property var stageLabel: type.stageLabel
    readonly property var phoneLabel: type.phoneLabel

    // ── Отступы: macOS / Windows (Windows — плотнее по вертикали в листах, см. части 2–4)
    readonly property int padX: 76
    readonly property int heroTop: 150
    readonly property int headerTop: isWin ? 40 : 52          // на Windows нет «светофора», заголовок окна свой
    readonly property int heroWidth: 620
    readonly property int ctaHeight: isWin ? 60 : 68
    readonly property int ctaPadX: 46
    readonly property int ctaRadius: isWin ? 8 : 18
    readonly property int pillHeight: 36
    readonly property int pillDot: 9
    readonly property int tileRadius: 17
    readonly property int phoneW: 430
    readonly property int phoneIcon: 74
    readonly property int phoneRightMargin: 156
    readonly property int phoneTop: 128
    readonly property real hair: 1
    readonly property real slotDashWidth: 2
    readonly property var slotDashPattern: [3, 3]   // в единицах толщины линии
    readonly property real ringWidth: 2.5
    readonly property int restoreAllMax: 8          // = ui4b.home.DIRECT_LIMIT (было 12 в черновике)

    // ── Листы (части 2 и 4)
    readonly property int sheetInsetX: 96
    readonly property int sheetInsetY: 34
    readonly property int sheetRadius: isWin ? 8 : 18
    readonly property int sheetHead: isWin ? 72 : 84
    readonly property int sheetFoot: isWin ? 72 : 84
    readonly property int sheetPadX: isWin ? 24 : 32
    readonly property int railWidth: 262
    readonly property int rowHeight: isWin ? 40 : 46
    readonly property int groupHeight: isWin ? 40 : 44
    readonly property int rowIcon: isWin ? 30 : 34
    readonly property int rowIconRadius: isWin ? 6 : 8
    readonly property int searchHeight: isWin ? 36 : 44
    readonly property int searchRadius: isWin ? 6 : 12
    readonly property int checkSize: 20
    readonly property int checkRadius: isWin ? 4 : 6
    readonly property int capWidth: 220
    readonly property int goHeight: isWin ? 44 : 52
    readonly property int goRadius: isWin ? 8 : 14
    readonly property int authSheetWidth: 600
    readonly property int fieldHeight: isWin ? 40 : 48
    readonly property int fieldRadius: isWin ? 6 : 12
    readonly property int codeCellW: isWin ? 52 : 62
    readonly property int codeCellH: isWin ? 60 : 70
    readonly property int stepperDot: 24
    // вкладка «Найти» (часть 2, §6b)
    readonly property int findRowHeight: isWin ? 56 : 64
    readonly property int rowButtonHeight: isWin ? 28 : 32
    readonly property int rowButtonRadius: isWin ? 4 : 8
    readonly property int rowButtonPadX: 14
    readonly property int tagRadius: 4
    // настройки (часть 2, §6b)
    readonly property int settingsRowHeight: isWin ? 56 : 64
    readonly property int toggleW: isWin ? 40 : 36
    readonly property int toggleH: 20

    // ── Движение
    readonly property int durFast: 150
    readonly property int durBase: 220
    readonly property int durSlow: 320
    readonly property int durSlotFill: 240
    readonly property int durCrossfade: 180
    readonly property int easeOut: Easing.OutCubic
    readonly property int easeInOut: Easing.InOutCubic

    // ═════════════════════════════════════════════════════════════════════════
    // ui4b: дополнения Димы. Выше — Theme.qml Ники (полная спека, части 1–4) без
    // изменений, кроме isWin для скриншотов. Ниже — только то, чего у Ники нет
    // (литералы, которые раньше жили в компонентах, и прежние имена).
    // ═════════════════════════════════════════════════════════════════════════
    readonly property string fontFamily: fontText
    readonly property string displayFamily: fontDisplay

    readonly property int brandIcon: 34
    readonly property int rowIconSize: 40      // иконка в очереди (tokens.json)
    readonly property int radiusQueueIcon: 9
    readonly property int stageBar: 5
    readonly property int barRadius: 3
    readonly property int newDot: 6
    readonly property int pieBox: 44
    readonly property int heroLeadWidth: 540
    readonly property int fineWidth: 470
    readonly property int queueWidth: 560
    readonly property int linksGap: 30
    readonly property int windowMinWidth: 1180
    readonly property int windowMinHeight: 760
    readonly property int windowWidth: 1392
    readonly property int windowHeight: 852

    readonly property color soft: surfaceSoft
    readonly property color rowLine: lineSoft
    readonly property color goDisabled: accentDisabled
    readonly property color slotStroke: slotDash
    readonly property color phoneOff: screenOff
    readonly property int listPadX: 28
    readonly property int radiusSheet: sheetRadius
    readonly property int radiusGo: goRadius
    readonly property int radiusSearch: searchRadius
    readonly property int radiusRail: 10
    readonly property int radiusSeg: 10
    readonly property int radiusSegItem: 8
    readonly property int radiusCheck: checkRadius
    readonly property int radiusRowIcon: rowIconRadius
    readonly property int radiusField: fieldRadius

    readonly property color inkOnAccent: "#FFFFFF"          // текст/галочка на тёмном и accent
    readonly property color focusRingInner: "#FFFFFF"       // tokens.json
    readonly property color secondaryPressed: "#E6E4DE"     // спека §1.1 [предл.]
    readonly property color iconHairline: Qt.rgba(0, 0, 0, 0.1)
    readonly property color sheetHairline: Qt.rgba(0, 0, 0, 0.08)
    readonly property color progressInk: "#FFFFFF"          // кольцо и сектор поверх вуали
    readonly property color island: "#000000"
    readonly property color batteryStroke: Qt.rgba(0, 0, 0, 0.75)
    readonly property color pageDotOn: Qt.rgba(0, 0, 0, 0.7)
    readonly property color pageDotOff: Qt.rgba(0, 0, 0, 0.22)
    readonly property color iosDim: Qt.rgba(0, 0, 0, 0.3)
    readonly property color iosAlertBg: iosAlert
    readonly property color iosAlertInk: "#111111"
    readonly property color iosAlertText: "#333333"
    readonly property color iosAlertLine: "#D3D3D0"
    readonly property color iosAlertButton: iosAlertBlue
    readonly property color phoneScreenOn: wall             // шаг 1 онбординга: экран не тёмный

    // Прежние имена главного экрана → токены Ники
    readonly property int topY: headerTop
    readonly property int buttonHeight: ctaHeight
    readonly property int radiusCta: ctaRadius
    readonly property int radiusTile: tileRadius
    readonly property int tileIcon: phoneIcon
    readonly property int phoneWidth: phoneW
    readonly property int phoneY: phoneTop
    readonly property int queueIcon: rowIconSize
    readonly property int leadWidth: heroLeadWidth
    readonly property int tileRowGap: 26

    // <T token>: стили Ники (Theme.type) + немного своих имён.
    readonly property var extraStyles: ({
        numeralCaptionCol: { size: 40, weight: 650, lh: 42, ls: -1.0, display: true },
        queueRight: { size: 14, weight: 400, lh: 18, ls: 0 }
    })
    readonly property var styleAlias: ({
        number: "numeralXL", numberSm: "numeralM", numberWord: "numeralCaption",
        numberWordSm: "numeralCaptionM", h1: "display", cta: "button", hint: "fine",
        over: "overline", queueName: "rowTitle", queueSmall: "rowMeta", stage: "stageLabel",
        tileLabel: "phoneLabel", stepNum12: "stepNum", help: "railHelp", group: "groupTitle",
        size: "rowSize", scanCap: "scanCaption", table: "tableRow"
    })
    function style(name) {
        var n = styleAlias[name] || name
        return type[n] || extraStyles[n] || type.lead
    }
    function isDisplay(name) {
        return !!style(name).display
    }
}
