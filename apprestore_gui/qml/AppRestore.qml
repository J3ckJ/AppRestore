import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: win
    width: 1040
    height: 720
    minimumWidth: 920
    minimumHeight: 640
    visible: true
    title: "AppRestore"
    color: "#E8E8ED"

    font.family: onest.name
    font.pixelSize: 15

    FontLoader {
        id: onest
        source: Qt.resolvedUrl("../resources/fonts/Onest-Variable.ttf")
    }

    property color ink: "#1C1C1E"
    property color muted: "#6C6C70"
    property color line: "#D8D8DC"
    property color accent: "#C45C2A"
    property color accentHover: "#A84C22"
    property bool motion: false
    property string deviceForm: "iphone-island"
    property string deviceNoun: "iPhone"
    property bool phoneOn: false
    property bool catalogReady: false
    property bool deviceMenu: false
    property string restoreNote: ""
    property string installNote: ""
    property bool installing: false
    property int installPercent: -1
    property string installPhase: ""
    property bool returning: false
    property var backIds: ({})
    property var homeApps: []
    property int homeExtra: 0
    property var missingApps: []
    property var pickIds: ({})
    property int faceSlots: 4
    property var restoreQueue: []
    property int restoreIndex: 0
    property int readyChecks: 0
    property bool checksStarted: false
    property var savedCopies: []
    property var phoneCopies: []
    property string filesNote: ""
    property bool filesBusy: false
    property bool phoneLoading: false
    property bool keychainOpen: false
    property string keychainFor: ""
    property bool loginOpen: false
    property string loginFor: ""
    property string seenPhase: ""
    property string liveNote: ""

    function markBack(storeId) {
        if (!storeId)
            return
        var next = {}
        for (var key in backIds)
            next[key] = backIds[key]
        next[storeId] = true
        backIds = next
    }

    function missingLeft() {
        var left = 0
        var apps = missingApps
        var backs = backIds
        for (var i = 0; i < apps.length; i++) {
            if (backs[apps[i].storeId] !== true)
                left++
        }
        return left
    }

    function stillMissing() {
        var apps = []
        for (var i = 0; i < missingApps.length; i++) {
            if (backIds[missingApps[i].storeId] !== true)
                apps.push(missingApps[i])
        }
        return apps
    }

    function chosenMissing() {
        var apps = []
        var all = stillMissing()
        for (var i = 0; i < all.length; i++) {
            if (pickIds[all[i].storeId] === true)
                apps.push(all[i])
        }
        return apps
    }

    function chosenCount() {
        var ids = pickIds
        var backs = backIds
        var apps = missingApps
        var count = 0
        for (var i = 0; i < apps.length; i++) {
            if (backs[apps[i].storeId] === true)
                continue
            if (ids[apps[i].storeId] === true)
                count++
        }
        return count
    }

    function appById(storeId) {
        for (var i = 0; i < missingApps.length; i++) {
            if (missingApps[i].storeId === storeId)
                return missingApps[i]
        }
        return null
    }

    function refreshHome() {
        if (returning && restoreQueue.length) {
            var start = Math.floor(Math.max(0, restoreIndex - 1) / faceSlots) * faceSlots
            var apps = []
            for (var i = start; i < restoreQueue.length && apps.length < faceSlots; i++) {
                var app = appById(restoreQueue[i])
                if (app)
                    apps.push(app)
            }
            homeApps = apps
            var pending = restoreQueue.length - restoreIndex
            var waitingHere = 0
            for (var j = 0; j < apps.length; j++) {
                if (backIds[apps[j].storeId] !== true)
                    waitingHere++
            }
            homeExtra = Math.max(0, pending - waitingHere)
            return
        }
        var chosen = chosenMissing()
        var pool = chosen.length ? chosen : stillMissing()
        var face = []
        for (var k = 0; k < pool.length && face.length < faceSlots; k++)
            face.push(pool[k])
        homeApps = face
        homeExtra = pool.length - face.length
    }

    function togglePick(storeId) {
        var next = {}
        for (var key in pickIds)
            next[key] = pickIds[key]
        if (next[storeId] === true)
            delete next[storeId]
        else
            next[storeId] = true
        pickIds = next
    }

    function selectListed(list) {
        var next = {}
        for (var key in pickIds)
            next[key] = pickIds[key]
        for (var i = 0; i < list.length; i++)
            next[list[i].storeId] = true
        pickIds = next
    }

    function clearListed(list) {
        var next = {}
        for (var key in pickIds)
            next[key] = pickIds[key]
        for (var j = 0; j < list.length; j++)
            delete next[list[j].storeId]
        pickIds = next
    }

    function markMany(ids) {
        if (!ids.length)
            return
        var next = {}
        for (var key in backIds)
            next[key] = backIds[key]
        for (var i = 0; i < ids.length; i++)
            next[ids[i]] = true
        backIds = next
    }

    function bringBack() {
        if (returning)
            return
        var queueSource = chosenMissing()
        if (!queueSource.length) {
            var all = stillMissing()
            if (all.length > faceSlots)
                return
            queueSource = all
        }
        var queue = []
        for (var i = 0; i < queueSource.length; i++)
            queue.push(queueSource[i].storeId)
        if (!queue.length)
            return
        restoreQueue = queue
        restoreIndex = 0
        restoreNote = ""
        returning = true
        session.restore(queue)
    }

    function placeSaved(index) {
        if (filesBusy)
            return
        if (!phoneOn) {
            filesNote = "Подключите " + deviceNoun + " кабелем."
            return
        }
        var row = savedCopies[index]
        if (!row || !row.path)
            return
        filesBusy = true
        filesNote = "Ставим копию на " + deviceNoun + "…"
        session.installSaved(row.path)
    }

    function go(index) {
        if (turn.running || index === stack.currentIndex)
            return
        turn.next = index
        turn.start()
    }

    function startChecks() {
        if (checksStarted)
            return
        checksStarted = true
        checkTimer.start()
    }

    Timer {
        id: boot
        interval: 40
        running: true
        onTriggered: win.motion = true
    }

    Timer {
        id: restoreTimer
        interval: 520
        repeat: true
        onTriggered: {
            var step = 1
            if (win.restoreQueue.length > 40)
                step = 10
            else if (win.restoreQueue.length > 12)
                step = 4
            var ids = []
            var end = Math.min(win.restoreQueue.length, win.restoreIndex + step)
            while (win.restoreIndex < end) {
                ids.push(win.restoreQueue[win.restoreIndex])
                win.restoreIndex++
            }
            win.markMany(ids)
            if (win.restoreIndex >= win.restoreQueue.length) {
                stop()
                win.returning = false
            }
        }
    }

    Timer {
        id: checkTimer
        interval: 420
        repeat: true
        onTriggered: {
            win.readyChecks++
            if (win.readyChecks >= 3)
                stop()
        }
    }

    onPickIdsChanged: refreshHome()
    onBackIdsChanged: refreshHome()
    onMissingAppsChanged: refreshHome()
    onDeviceFormChanged: refreshHome()
    onRestoreIndexChanged: {
        if (returning)
            refreshHome()
    }
    onReturningChanged: refreshHome()

    Connections {
        target: session
        function onChanged() {
            win.missingApps = session.apps
            win.catalogReady = !session.loading
            win.phoneOn = session.connected
            win.deviceForm = session.deviceForm
            win.deviceNoun = session.deviceNoun
            // A poll error replaces whatever is on screen. An empty poll
            // clears only that same error, so a restore result stays put.
            if (session.note)
                win.restoreNote = session.note
            else if (win.restoreNote === win.liveNote)
                win.restoreNote = ""
            win.liveNote = session.note
            if (session.authPhase === "in" && win.seenPhase !== "in") {
                win.keychainOpen = false
                win.loginOpen = false
            }
            if (session.authPhase === "need_code" && win.seenPhase !== "need_code") {
                win.loginOpen = false
                win.go(3)
            }
            win.seenPhase = session.authPhase
            win.refreshHome()
        }
        function onKeychainPrompt(email) {
            var next = email || ""
            if (!win.keychainOpen || win.keychainFor !== next)
                keychainField.text = ""
            win.keychainFor = next
            win.loginOpen = false
            win.keychainOpen = true
        }
        function onLoginPrompt(email) {
            win.loginFor = email || session.boundEmail || ""
            loginEmail.text = win.loginFor
            loginPassword.text = ""
            win.keychainOpen = false
            win.loginOpen = true
        }
        function onAppRestored(storeId) {
            win.markBack(storeId)
        }
        function onRestoreSettled(message) {
            win.returning = false
            win.restoreNote = message
            win.refreshHome()
        }
        function onInstallSettled(storeId, ok, message) {
            win.installing = false
            win.installPercent = -1
            win.installPhase = ""
            if (ok) {
                win.installNote = ""
                win.markBack(storeId)
            } else {
                win.installNote = message
            }
        }
        function onInstallProgress(percent, phase) {
            if (!win.installing)
                return
            win.installPercent = percent
            win.installPhase = phase
        }
        function onDeviceSwitched() {
            win.backIds = ({})
            win.pickIds = ({})
            win.restoreNote = ""
            win.missingApps = []
            win.phoneCopies = []
            win.filesNote = ""
            win.deviceMenu = false
            win.refreshHome()
        }
        function onFilesChanged() {
            win.phoneCopies = session.phoneApps
            win.savedCopies = session.libraryFiles
        }
        function onFilesNoteChanged(note, busy) {
            win.filesNote = note
            win.filesBusy = busy
        }
        function onPhoneLoadingChanged(loading) {
            win.phoneLoading = loading
        }
        function onCopySettled(key, ok, message) {
            win.filesBusy = false
            win.filesNote = message
        }
    }

    Component.onCompleted: {
        missingApps = []
        session.refresh()
    }

    SequentialAnimation {
        id: turn
        property int next: 0
        NumberAnimation { target: veil; property: "opacity"; to: 1; duration: 140; easing.type: Easing.InQuad }
        ScriptAction {
            script: {
                stack.currentIndex = turn.next
                if (turn.next === 2)
                    session.loadLibrary()
                if (turn.next === 3)
                    win.startChecks()
            }
        }
        NumberAnimation { target: veil; property: "opacity"; to: 0; duration: 240; easing.type: Easing.OutCubic }
    }

    header: Rectangle {
        height: 72
        color: "#E8E8ED"

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 32
            anchors.rightMargin: 32
            spacing: 12

            Image {
                Layout.preferredWidth: 34
                Layout.preferredHeight: 34
                source: Qt.resolvedUrl("../resources/icons/app-icon-256.png")
                fillMode: Image.PreserveAspectFit
                smooth: true
                mipmap: true
            }
            Text {
                text: "AppRestore"
                color: win.ink
                font.pixelSize: 20
                font.weight: 650
            }
            Item { Layout.fillWidth: true }
            Rectangle {
                id: statusChip
                visible: stack.currentIndex === 0
                radius: 12
                color: "white"
                implicitWidth: statusRow.implicitWidth + 22
                implicitHeight: 28
                Row {
                    id: statusRow
                    anchors.centerIn: parent
                    spacing: 8
                    Rectangle {
                        width: 8
                        height: 8
                        radius: 4
                        color: win.phoneOn ? "#1B7F3A" : "#C7C7CC"
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        text: {
                            if (!win.phoneOn)
                                return win.catalogReady ? ("Нет " + win.deviceNoun) : "Смотрим кабель"
                            var name = session.deviceName || win.deviceNoun
                            return session.devices.length > 1 ? name : (name + " подключён")
                        }
                        color: win.ink
                        font.pixelSize: 13
                        elide: Text.ElideRight
                        width: Math.min(implicitWidth, 220)
                    }
                }
                MouseArea {
                    anchors.fill: parent
                    enabled: session.devices.length > 1
                    cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: win.deviceMenu = !win.deviceMenu
                }
            }
        }
    }

    StackLayout {
        id: stack
        objectName: "pages"
        anchors.fill: parent

        HomePage {}
        FindPage {}
        FilesPage {}
        AccountPage {}
        ChoosePage {}
    }

    Rectangle {
        id: veil
        anchors.fill: parent
        color: win.color
        opacity: 0
        enabled: opacity > 0.01
    }

    MouseArea {
        anchors.fill: parent
        visible: win.deviceMenu
        z: 25
        onClicked: win.deviceMenu = false
    }

    Rectangle {
        visible: win.deviceMenu && session.devices.length > 1
        z: 30
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.topMargin: 60
        anchors.rightMargin: 28
        width: 320
        height: deviceCol.implicitHeight + 12
        radius: 16
        color: "white"
        border.color: win.line

        Column {
            id: deviceCol
            width: parent.width
            y: 6
            Repeater {
                model: session.devices
                Rectangle {
                    required property var modelData
                    width: deviceCol.width
                    height: 58
                    color: modelData.active ? "#F8E8DF" : "transparent"
                    Text {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: 16
                        anchors.rightMargin: 16
                        text: modelData.name + (modelData.account ? ("\n" + modelData.account) : "")
                        color: win.ink
                        font.pixelSize: 14
                        wrapMode: Text.WordWrap
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            session.selectDevice(modelData.udid)
                            win.deviceMenu = false
                        }
                    }
                }
            }
        }
    }

    component PillButton: Button {
        id: pill
        property color fill: win.accent
        property color labelColor: "white"
        flat: true
        implicitHeight: 48
        background: Rectangle {
            radius: height / 2
            color: !pill.enabled
                  ? pill.fill
                  : (pill.down ? Qt.darker(pill.fill, 1.08) : (pill.hovered ? win.accentHover : pill.fill))
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.leftMargin: 18
                anchors.rightMargin: 18
                anchors.topMargin: 1
                height: 1
                radius: 1
                color: "#FFFFFF"
                opacity: pill.labelColor == "white" ? 0.45 : 0
            }
        }
        contentItem: Text {
            text: pill.text
            color: pill.labelColor
            font.pixelSize: 16
            font.weight: 650
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    component AppMark: Item {
        id: glyph
        property string storeId: ""
        property string bundleId: ""
        property string mark: ""
        property color fill: "#1C1C1E"
        property color ink: "#FFFFFF"
        property int corner: 13
        property bool faded: false
        property string iconSource: {
            if (!iconBook)
                return ""
            var rev = iconBook.revision
            var fromStore = glyph.storeId ? iconBook.pathFor(glyph.storeId) : ""
            if (fromStore)
                return fromStore
            return glyph.bundleId ? iconBook.pathFor(glyph.bundleId) : ""
        }

        Rectangle {
            anchors.fill: parent
            radius: glyph.corner
            color: glyph.fill
            visible: art.status !== Image.Ready
            Text {
                anchors.centerIn: parent
                text: glyph.mark
                color: glyph.ink
                font.pixelSize: Math.max(12, parent.height * 0.42)
                font.weight: 700
                opacity: glyph.faded ? 0.45 : 1
            }
        }
        Image {
            id: art
            anchors.fill: parent
            source: glyph.iconSource
            fillMode: Image.PreserveAspectCrop
            smooth: true
            mipmap: true
            visible: status === Image.Ready
            opacity: glyph.faded ? 0.82 : 1
        }
    }

    component HomePage: Item {
        id: home
        property real arrive: 0
        property real breath: 0
        property real bloom: 0
        property real numScale: 1
        property int missing: {
            var ids = win.backIds
            var apps = win.homeApps
            return win.missingLeft()
        }

        Timer {
            interval: 80
            running: true
            onTriggered: home.arrive = 1
        }

        Behavior on arrive {
            NumberAnimation { duration: 680; easing.type: Easing.OutCubic }
        }

        SequentialAnimation on breath {
            loops: Animation.Infinite
            running: home.missing > 0 && !win.returning
            NumberAnimation { to: 1; duration: 1600; easing.type: Easing.InOutSine }
            NumberAnimation { to: 0; duration: 1600; easing.type: Easing.InOutSine }
        }

        onMissingChanged: {
            if (!win.motion)
                return
            home.numScale = 0.84
            numPop.restart()
        }

        NumberAnimation {
            id: numPop
            target: home
            property: "numScale"
            to: 1
            duration: 520
            easing.type: Easing.OutBack
            easing.overshoot: 2.4
        }

        states: State {
            name: "lit"
            when: win.motion && win.homeApps.length > 0 && (win.returning || home.missing === 0)
            PropertyChanges { target: home; bloom: 1 }
        }
        transitions: Transition {
            NumberAnimation { property: "bloom"; duration: 700; easing.type: Easing.OutCubic }
        }

        Connections {
            target: win
            function onReturningChanged() {
                if (win.returning)
                    sheenSweep.restart()
            }
        }

        Item {
            anchors.centerIn: parent
            width: win.deviceForm.indexOf("ipad") === 0 ? 880 : 760
            height: 540
            opacity: home.arrive
            anchors.verticalCenterOffset: (1 - home.arrive) * 16

            Column {
                width: 360
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                spacing: 0

                Text {
                    text: {
                        if (!win.catalogReady)
                            return "смотрим"
                        if (!win.phoneOn)
                            return "нет кабеля"
                        return home.missing === 0 ? ("на " + win.deviceNoun) : "не хватает"
                    }
                    color: win.muted
                    font.pixelSize: 15
                    font.weight: 650
                    font.letterSpacing: 1.4
                }
                Text {
                    text: {
                        if (!win.catalogReady)
                            return "…"
                        if (!win.phoneOn)
                            return "—"
                        return home.missing === 0 ? "всё" : String(home.missing)
                    }
                    color: win.ink
                    font.pixelSize: (!win.catalogReady || !win.phoneOn) ? 64 : ((home.missing === 0) ? 112 : (home.missing >= 100 ? 108 : 156))
                    font.weight: 700
                    font.letterSpacing: (!win.catalogReady || !win.phoneOn) ? 0 : -6
                    scale: home.numScale
                    transformOrigin: Item.Left
                }
                Text {
                    width: parent.width
                    topPadding: 6
                    visible: !(win.chosenCount() > 0 && !win.returning && home.missing > 0)
                    text: {
                        if (!win.catalogReady)
                            return "Смотрим, что сгружено"
                        if (!win.phoneOn)
                            return "Подключите кабелем и разблокируйте"
                        if (win.returning)
                            return win.deviceNoun + " докачивает сам"
                        if (home.missing === 0)
                            return "Можно отключать кабель"
                        if (home.missing > win.faceSlots)
                            return "Отметьте, какие вернуть"
                        return "Их нет на " + win.deviceNoun
                    }
                    color: win.muted
                    font.pixelSize: 16
                    wrapMode: Text.WordWrap
                }
                Row {
                    visible: win.chosenCount() > 0 && !win.returning && home.missing > 0
                    spacing: 12
                    topPadding: 6
                    Text {
                        text: "Сейчас вернём " + win.chosenCount()
                        color: win.muted
                        font.pixelSize: 16
                    }
                    Text {
                        text: "Изменить"
                        color: win.accent
                        font.pixelSize: 16
                        font.weight: 650
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.go(4)
                        }
                    }
                }
                Item { width: 1; height: 28 }
                PillButton {
                    width: 240
                    text: {
                        if (!win.catalogReady)
                            return "Смотрим"
                        if (!win.phoneOn)
                            return "Нет " + win.deviceNoun
                        var chosen = win.chosenCount()
                        if (home.missing === 0)
                            return "Готово"
                        if (win.returning)
                            return "Возвращаем"
                        if (chosen > 1)
                            return "Вернуть " + chosen
                        if (chosen === 1)
                            return "Вернуть"
                        if (home.missing > win.faceSlots)
                            return "Выбрать"
                        return "Вернуть"
                    }
                    enabled: win.catalogReady && win.phoneOn && home.missing > 0 && !win.returning
                    fill: (!win.catalogReady || !win.phoneOn || home.missing === 0) ? "#FFFFFF" : win.accent
                    labelColor: (!win.catalogReady || !win.phoneOn || home.missing === 0) ? win.ink : "white"
                    onClicked: {
                        if (home.missing > win.faceSlots && win.chosenCount() === 0)
                            win.go(4)
                        else
                            win.bringBack()
                    }
                }
                Item { width: 1; height: 22 }
                Text {
                    visible: win.restoreNote !== ""
                    width: parent.width
                    text: win.restoreNote
                    color: "#C0392B"
                    font.pixelSize: 14
                    wrapMode: Text.WordWrap
                }
                Row {
                    spacing: 4
                    Repeater {
                        model: [
                            { label: "Найти", page: 1 },
                            { label: "Файлы", page: 2 },
                            { label: "Apple ID", page: 3 }
                        ]
                        Rectangle {
                            radius: 14
                            color: linkArea.containsMouse ? "white" : "transparent"
                            implicitWidth: linkLabel.implicitWidth + 24
                            implicitHeight: 34
                            Text {
                                id: linkLabel
                                anchors.centerIn: parent
                                text: modelData.label
                                color: win.ink
                                font.pixelSize: 14
                            }
                            MouseArea {
                                id: linkArea
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: win.go(modelData.page)
                            }
                        }
                    }
                }
            }

            Item {
                width: win.deviceForm.indexOf("ipad") === 0 ? 500 : 320
                height: 540
                anchors.right: parent.right

                Repeater {
                    model: 6
                    Rectangle {
                        required property int index
                        anchors.centerIn: parent
                        width: (phone.pad ? 210 : 150) + index * (phone.pad ? 34 : 42)
                        height: (phone.pad ? 140 : 190) + index * (phone.pad ? 26 : 46)
                        radius: width / 2
                        color: "#C45C2A"
                        opacity: (0.055 - index * 0.008) * (0.35 + home.bloom * 0.9)
                    }
                }

                Rectangle {
                    id: phone
                    property bool pad: win.deviceForm.indexOf("ipad") === 0
                    property bool homeButton: win.deviceForm.indexOf("home") >= 0
                    property bool air: win.deviceForm === "iphone-air"
                    property bool notch: win.deviceForm === "iphone-notch"
                    property bool island: win.deviceForm === "iphone-island" || air
                    property bool sideCam: win.deviceForm === "ipad-side"
                    width: pad ? 472 : (air ? 232 : (homeButton ? 250 : 268))
                    height: pad ? 348 : (homeButton ? 468 : 500)
                    radius: pad ? 28 : (homeButton ? 36 : (air ? 40 : 46))
                    color: "#141416"
                    anchors.centerIn: parent
                    Behavior on width { NumberAnimation { duration: 420; easing.type: Easing.OutCubic } }
                    Behavior on height { NumberAnimation { duration: 420; easing.type: Easing.OutCubic } }

                    Rectangle {
                        anchors.fill: parent
                        radius: phone.radius
                        color: "transparent"
                        border.width: 1
                        border.color: "#3A3A3E"
                    }

                    Rectangle {
                        id: glass
                        anchors.fill: parent
                        anchors.leftMargin: phone.sideCam ? 20 : (phone.homeButton ? 16 : (phone.air ? 6 : (phone.pad ? 14 : 8)))
                        anchors.rightMargin: phone.homeButton ? 16 : (phone.air ? 6 : (phone.pad ? 14 : 8))
                        anchors.topMargin: phone.homeButton ? 18 : (phone.air ? 6 : (phone.pad ? 16 : 8))
                        anchors.bottomMargin: phone.homeButton ? (phone.pad ? 48 : 62) : (phone.air ? 6 : (phone.pad ? 14 : 8))
                        radius: Math.max(18, phone.radius - 8)
                        clip: true
                        gradient: Gradient {
                            GradientStop { position: 0.0; color: "#FFFFFF" }
                            GradientStop { position: 0.22; color: "#F7F7F8" }
                            GradientStop { position: 1.0; color: "#F3F3F5" }
                        }

                        Rectangle {
                            id: sheen
                            x: 28
                            width: parent.width - 56
                            height: 64
                            y: -100
                            opacity: 0
                            gradient: Gradient {
                                GradientStop { position: 0.0; color: "#00FFFFFF" }
                                GradientStop { position: 0.5; color: "#CCFFFFFF" }
                                GradientStop { position: 1.0; color: "#00FFFFFF" }
                            }
                        }

                        ParallelAnimation {
                            id: sheenSweep
                            NumberAnimation {
                                target: sheen
                                property: "y"
                                from: -70
                                to: 340
                                duration: 820
                                easing.type: Easing.InOutCubic
                            }
                            SequentialAnimation {
                                NumberAnimation { target: sheen; property: "opacity"; to: 0.8; duration: 70 }
                                PauseAnimation { duration: 460 }
                                NumberAnimation { target: sheen; property: "opacity"; to: 0; duration: 290 }
                            }
                        }

                        Rectangle {
                            visible: phone.island
                            width: phone.air ? 68 : 78
                            height: 22
                            radius: 11
                            color: "#141416"
                            anchors.horizontalCenter: parent.horizontalCenter
                            anchors.top: parent.top
                            anchors.topMargin: 12
                        }

                        Rectangle {
                            visible: phone.notch
                            anchors.horizontalCenter: parent.horizontalCenter
                            width: 132
                            height: 36
                            y: -16
                            radius: 16
                            color: "#141416"
                        }

                        Rectangle {
                            visible: !phone.homeButton
                            width: phone.pad ? 88 : 70
                            height: 4
                            radius: 2
                            color: "#1C1C1E"
                            opacity: 0.16
                            anchors.horizontalCenter: parent.horizontalCenter
                            anchors.bottom: parent.bottom
                            anchors.bottomMargin: 8
                        }

                        Text {
                            visible: win.homeExtra > 0
                            anchors.horizontalCenter: parent.horizontalCenter
                            anchors.bottom: parent.bottom
                            anchors.bottomMargin: phone.homeButton ? 8 : 22
                            text: "и ещё " + win.homeExtra
                            color: "#8E8E93"
                            font.pixelSize: 13
                        }

                        Grid {
                            anchors.centerIn: parent
                            columns: phone.pad ? 4 : 2
                            columnSpacing: phone.pad ? 16 : 34
                            rowSpacing: 20
                            Repeater {
                                    model: win.homeApps
                                    Column {
                                        required property var modelData
                                        spacing: 8
                                        width: phone.pad ? 68 : 78
                                        property bool awake: win.backIds[modelData.storeId] === true

                                        onAwakeChanged: {
                                            if (awake && win.motion)
                                                wake.restart()
                                        }

                                        Item {
                                            width: phone.pad ? 54 : 68
                                            height: phone.pad ? 54 : 68
                                            anchors.horizontalCenter: parent.horizontalCenter

                                            Rectangle {
                                                id: ring
                                                anchors.centerIn: parent
                                                width: phone.pad ? 54 : 68
                                                height: phone.pad ? 54 : 68
                                                radius: phone.pad ? 18 : 22
                                                color: "transparent"
                                                border.width: 2
                                                border.color: modelData.color
                                                opacity: 0
                                                scale: 0.8
                                            }

                                            Item {
                                                id: tile
                                                anchors.centerIn: parent
                                                width: phone.pad ? 46 : 58
                                                height: phone.pad ? 46 : 58
                                                scale: awake ? 1 : (0.94 + home.breath * 0.03)
                                                opacity: 1
                                                Behavior on scale {
                                                    enabled: win.motion
                                                    NumberAnimation { duration: 460; easing.type: Easing.OutBack; easing.overshoot: 2.1 }
                                                }
                                                AppMark {
                                                    anchors.fill: parent
                                                    storeId: modelData.storeId || ""
                                                    mark: modelData.mark
                                                    fill: modelData.color
                                                    ink: modelData.ink
                                                    corner: phone.pad ? 13 : 16
                                                    faded: false
                                                }
                                                Rectangle {
                                                    id: flash
                                                    anchors.fill: parent
                                                    radius: phone.pad ? 13 : 16
                                                    color: "white"
                                                    opacity: 0
                                                }
                                            }

                                            ParallelAnimation {
                                                id: wake
                                                NumberAnimation { target: ring; property: "opacity"; from: 0.85; to: 0; duration: 680; easing.type: Easing.OutCubic }
                                                NumberAnimation { target: ring; property: "scale"; from: 0.72; to: 1.45; duration: 680; easing.type: Easing.OutCubic }
                                                NumberAnimation { target: flash; property: "opacity"; from: 0.85; to: 0; duration: 280 }
                                            }
                                        }

                                        Text {
                                            width: parent.width
                                            text: modelData.name
                                            color: awake ? win.ink : "#8E8E93"
                                            font.pixelSize: 12
                                            font.weight: awake ? 650 : 400
                                            horizontalAlignment: Text.AlignHCenter
                                            elide: Text.ElideRight
                                        }
                                    }
                                }
                            }
                        }

                        Rectangle {
                            visible: phone.pad && !phone.sideCam
                            width: 9
                            height: 9
                            radius: 5
                            color: "#0A0A0C"
                            anchors.horizontalCenter: parent.horizontalCenter
                            anchors.top: parent.top
                            anchors.topMargin: 4
                        }
                        Rectangle {
                            visible: phone.sideCam
                            width: 9
                            height: 9
                            radius: 5
                            color: "#0A0A0C"
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.left: parent.left
                            anchors.leftMargin: 5
                        }
                        Rectangle {
                            visible: phone.homeButton
                            width: phone.pad ? 30 : 36
                            height: width
                            radius: width / 2
                            color: "#141416"
                            border.width: 2
                            border.color: "#A1A1A6"
                            anchors.horizontalCenter: parent.horizontalCenter
                            anchors.bottom: parent.bottom
                            anchors.bottomMargin: phone.pad ? 9 : 12
                        }
                    }
                }
            }
    }

    component FindPage: Item {
        id: find
        objectName: "findPage"
        property var shelf: []
        property var picked: null

        function pastedStoreId(text) {
            var link = text.match(/apps\.apple\.com\/.*\/id(\d{6,12})/i)
            if (!link)
                link = text.match(/\bid(\d{6,12})\b/i)
            if (link)
                return link[1]
            if (/^\d{6,12}$/.test(text))
                return text
            return ""
        }

        function refreshShelf() {
            var raw = query.text.trim()
            var q = raw.toLowerCase()
            var apps = []
            for (var i = 0; i < popularApps.length; i++) {
                var app = popularApps[i]
                var blob = (app.name + " " + app.detail + " " + app.storeId).toLowerCase()
                if (!q || blob.indexOf(q) !== -1)
                    apps.push(app)
            }
            var pasted = pastedStoreId(raw)
            if (pasted) {
                var known = false
                for (var k = 0; k < apps.length; k++) {
                    if (apps[k].storeId === pasted)
                        known = true
                }
                if (!known) {
                    apps.unshift({
                        storeId: pasted,
                        name: "Номер " + pasted,
                        detail: "из ссылки или номера",
                        mark: "№",
                        color: "#1C1C1E",
                        ink: "#FFFFFF"
                    })
                }
            }
            shelf = apps
            if (picked) {
                var still = false
                for (var j = 0; j < apps.length; j++) {
                    if (apps[j].storeId === picked.storeId)
                        still = true
                }
                if (!still)
                    picked = null
            }
        }

        Component.onCompleted: refreshShelf()

        Flickable {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: actionBar.top
            contentWidth: width
            contentHeight: column.implicitHeight + 24
            clip: true
            boundsBehavior: Flickable.StopAtBounds

            Column {
                id: column
                width: parent.width - 72
                x: 36
                y: 8
                spacing: 12

                Text {
                    text: "← Назад"
                    color: win.muted
                    font.pixelSize: 14
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: win.go(0)
                    }
                }
                Text {
                    text: "Найти"
                    color: win.ink
                    font.pixelSize: 40
                    font.weight: 700
                }
                Text {
                    width: parent.width
                    text: "Имя, ссылка с apps.apple.com или номер из App Store"
                    color: win.muted
                    font.pixelSize: 15
                    wrapMode: Text.WordWrap
                }

                TextField {
                    id: query
                    objectName: "findQuery"
                    width: parent.width
                    implicitHeight: 56
                    placeholderText: "Сбер, VK или номер"
                    placeholderTextColor: "#8E8E93"
                    color: win.ink
                    font.pixelSize: 17
                    leftPadding: 18
                    rightPadding: 18
                    selectByMouse: true
                    background: Rectangle {
                        radius: 16
                        color: "white"
                        border.color: query.activeFocus ? win.accent : win.line
                        border.width: query.activeFocus ? 2 : 1
                    }
                    onTextChanged: refreshShelf()
                }

                Text {
                    text: {
                        var raw = query.text.trim()
                        if (raw === "")
                            return "Часто возвращают"
                        if (pastedStoreId(raw))
                            return "По ссылке или номеру"
                        return "В частых нашлось"
                    }
                    color: win.ink
                    font.pixelSize: 18
                    font.weight: 650
                    topPadding: 8
                }
                Text {
                    width: parent.width
                    text: "Последний известный выпуск. Скачивается и ставится, даже если его ещё не было на вашем Apple ID."
                    color: win.muted
                    font.pixelSize: 14
                    wrapMode: Text.WordWrap
                }

                Text {
                    visible: shelf.length === 0
                    width: parent.width
                    text: "В частых такого нет. Вставьте ссылку apps.apple.com или номер — карточка появится здесь."
                    color: win.ink
                    font.pixelSize: 15
                    wrapMode: Text.WordWrap
                }

                Grid {
                    id: shelfGrid
                    width: parent.width
                    columns: 4
                    columnSpacing: 12
                    rowSpacing: 12

                    Repeater {
                        model: shelf
                        Item {
                            required property var modelData
                            property bool chosen: picked && picked.storeId === modelData.storeId
                            property bool back: win.backIds[modelData.storeId] === true
                            width: (shelfGrid.width - 36) / 4
                            height: 122

                            Rectangle {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.bottom: parent.bottom
                                height: 112
                                radius: 20
                                color: "#14000000"
                            }

                            Rectangle {
                                id: cardFace
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                height: 112
                                radius: 20
                                y: chosen ? -3 : (cardArea.containsMouse ? -1 : 0)
                                color: "white"
                                border.width: chosen ? 2 : 1
                                border.color: chosen ? win.accent : "#E4E4E8"
                                Behavior on y {
                                    enabled: win.motion
                                    NumberAnimation { duration: 180; easing.type: Easing.OutCubic }
                                }

                                Column {
                                    anchors.centerIn: parent
                                    spacing: 6
                                    AppMark {
                                        width: 44
                                        height: 44
                                        corner: 13
                                        storeId: modelData.storeId || ""
                                        mark: modelData.mark
                                        fill: modelData.color
                                        ink: modelData.ink
                                        anchors.horizontalCenter: parent.horizontalCenter
                                    }
                                    Text {
                                        text: modelData.name
                                        width: cardFace.width - 16
                                        color: win.ink
                                        font.pixelSize: 14
                                        font.weight: 650
                                        horizontalAlignment: Text.AlignHCenter
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        text: back ? ("на " + win.deviceNoun) : modelData.detail
                                        width: cardFace.width - 16
                                        color: back ? "#1B7F3A" : win.muted
                                        font.pixelSize: 12
                                        horizontalAlignment: Text.AlignHCenter
                                        elide: Text.ElideRight
                                    }
                                }
                                MouseArea {
                                    id: cardArea
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        picked = modelData
                                        win.installNote = ""
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        Rectangle {
            id: actionBar
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 100
            color: "#E8E8ED"

            Rectangle {
                anchors.fill: parent
                anchors.leftMargin: 36
                anchors.rightMargin: 36
                anchors.bottomMargin: 16
                anchors.topMargin: 8
                radius: 18
                color: "white"
                border.color: win.line

                Rectangle {
                    visible: win.installing
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.leftMargin: 18
                    anchors.rightMargin: 18
                    anchors.topMargin: 8
                    height: 4
                    radius: 2
                    color: "#E4E4E8"
                    property real slide: 0
                    SequentialAnimation on slide {
                        running: win.installing && win.installPercent < 0
                        loops: Animation.Infinite
                        NumberAnimation { from: 0; to: 1; duration: 850; easing.type: Easing.InOutQuad }
                        NumberAnimation { from: 1; to: 0; duration: 850; easing.type: Easing.InOutQuad }
                    }
                    Rectangle {
                        height: parent.height
                        radius: 2
                        color: win.accent
                        width: win.installPercent < 0
                               ? Math.max(28, parent.width * 0.34)
                               : Math.max(4, parent.width * Math.min(100, win.installPercent) / 100)
                        x: win.installPercent < 0 ? parent.slide * Math.max(0, parent.width - width) : 0
                    }
                }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 18
                    anchors.rightMargin: 18
                    anchors.topMargin: win.installing ? 14 : 0
                    spacing: 14
                    AppMark {
                        visible: picked !== null
                        width: 44
                        height: 44
                        corner: 13
                        storeId: picked ? (picked.storeId || "") : ""
                        mark: picked ? picked.mark : ""
                        fill: picked ? picked.color : "transparent"
                        ink: picked ? picked.ink : win.ink
                    }
                    ColumnLayout {
                        spacing: 2
                        Text {
                            text: picked ? picked.name : "Выберите приложение"
                            color: win.ink
                            font.pixelSize: 17
                            font.weight: 650
                        }
                        Text {
                            text: win.installing
                                  ? (win.installPhase !== "" ? win.installPhase : "Скачиваем и ставим")
                                  : (win.installNote !== ""
                                     ? win.installNote
                                     : (!picked
                                        ? "Нажмите карточку выше"
                                        : (!win.phoneOn
                                           ? ("Сначала подключите " + win.deviceNoun + " кабелем")
                                           : (win.backIds[picked.storeId] === true ? ("Уже на " + win.deviceNoun) : picked.detail))))
                            color: win.installNote !== "" && !win.installing ? "#C0392B" : win.muted
                            font.pixelSize: 13
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                        Text {
                            objectName: "freeLicenseHint"
                            visible: picked !== null && !win.installing && win.installNote === ""
                                     && win.backIds[picked.storeId] !== true
                            text: "Если приложения нет на вашем Apple ID, бесплатное будет добавлено на него (не больше 5 в сутки). Платные не добавляем."
                            color: win.muted
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }
                    Item { Layout.fillWidth: true }
                    PillButton {
                        text: win.installing
                              ? "Ставим"
                              : (picked && !win.phoneOn
                                 ? ("Нет " + win.deviceNoun)
                                 : (picked && win.backIds[picked.storeId] === true ? ("На " + win.deviceNoun) : "Поставить"))
                        implicitWidth: 160
                        enabled: picked !== null && !win.installing && win.backIds[picked.storeId] !== true && win.phoneOn
                        fill: enabled ? win.accent : "#E4E4E8"
                        labelColor: enabled ? "white" : "#8E8E93"
                        onClicked: {
                            if (!picked || win.installing)
                                return
                            win.installing = true
                            win.installPercent = -1
                            win.installPhase = "Проверяем лицензию"
                            win.installNote = ""
                            session.installStore(picked.storeId)
                        }
                    }
                }
            }
        }
    }

    component FilesPage: Item {
        id: files
        objectName: "filesPage"
        property bool sheet: false
        property var chosen: ({})
        property string needle: ""
        property var shown: []

        function refill() {
            var query = needle.trim().toLowerCase()
            var source = win.phoneCopies
            if (!query) {
                shown = source
                return
            }
            var rows = []
            for (var i = 0; i < source.length; i++) {
                var app = source[i]
                var blob = (app.name + " " + app.bundleId + " " + app.detail).toLowerCase()
                if (blob.indexOf(query) >= 0)
                    rows.push(app)
            }
            shown = rows
        }

        function toggle(bundleId) {
            var next = {}
            for (var key in chosen)
                next[key] = chosen[key]
            next[bundleId] = !next[bundleId]
            chosen = next
        }

        function chosenCount() {
            var count = 0
            for (var i = 0; i < win.phoneCopies.length; i++) {
                var app = win.phoneCopies[i]
                if (app.storeId && chosen[app.bundleId] === true && !alreadySaved(app.bundleId))
                    count++
            }
            return count
        }

        function alreadySaved(bundleId) {
            for (var i = 0; i < win.savedCopies.length; i++) {
                if (win.savedCopies[i].bundleId === bundleId)
                    return true
            }
            return false
        }

        function commitCopies() {
            var ids = []
            for (var i = 0; i < win.phoneCopies.length; i++) {
                var app = win.phoneCopies[i]
                if (app.storeId && chosen[app.bundleId] === true && !alreadySaved(app.bundleId))
                    ids.push(app.bundleId)
            }
            if (!ids.length)
                return
            chosen = ({})
            sheet = false
            win.filesBusy = true
            win.filesNote = "Сохраняем копию…"
            session.saveCopies(ids)
        }

        Connections {
            target: win
            function onPhoneCopiesChanged() { files.refill() }
            function onSavedCopiesChanged() { files.refill() }
        }

        Flickable {
            anchors.fill: parent
            contentWidth: width
            contentHeight: filesCol.implicitHeight + 28
            clip: true
            boundsBehavior: Flickable.StopAtBounds

            Column {
                id: filesCol
                width: parent.width - 72
                x: 36
                y: 8
                spacing: 14

            Text {
                text: "← Назад"
                color: win.muted
                font.pixelSize: 14
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: win.go(0) }
            }
            Text { text: "Файлы"; color: win.ink; font.pixelSize: 40; font.weight: 700 }
            Text {
                width: parent.width
                text: "Копия лежит на компьютере. С " + win.deviceNoun + " приложение не пропадает."
                color: win.muted
                font.pixelSize: 15
                wrapMode: Text.WordWrap
            }
            Text {
                width: parent.width
                visible: win.filesNote !== ""
                text: win.filesNote
                color: win.ink
                font.pixelSize: 15
                wrapMode: Text.WordWrap
            }

            Rectangle {
                width: parent.width
                height: 92
                radius: 18
                color: "white"
                border.color: win.line
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 18
                    ColumnLayout {
                        spacing: 2
                        Text {
                            text: "СКОПИРОВАТЬ С " + win.deviceNoun.toUpperCase()
                            color: win.muted
                            font.pixelSize: 12
                            font.letterSpacing: 1
                        }
                        Text { text: "Выбрать приложения"; color: win.ink; font.pixelSize: 16; font.weight: 650 }
                    }
                    Item { Layout.fillWidth: true }
                    PillButton {
                        text: win.phoneLoading ? "Смотрим" : (!win.phoneOn ? ("Нет " + win.deviceNoun) : "Выбрать")
                        implicitWidth: 150
                        enabled: win.phoneOn && !win.phoneLoading && !win.filesBusy
                        fill: enabled ? win.accent : "#E4E4E8"
                        labelColor: enabled ? "white" : "#8E8E93"
                        onClicked: {
                            files.sheet = true
                            fileQuery.text = ""
                            files.needle = ""
                            files.refill()
                            session.loadPhone()
                        }
                    }
                }
            }

            Rectangle {
                width: parent.width
                visible: win.savedCopies.length > 0
                radius: 18
                color: "white"
                border.color: win.line
                implicitHeight: localCol.implicitHeight + 16
                Column {
                    id: localCol
                    width: parent.width
                    y: 8
                    Repeater {
                        model: win.savedCopies
                        Rectangle {
                            required property var modelData
                            required property int index
                            width: localCol.width
                            height: index === 0 ? 86 : 64
                            Column {
                                visible: index === 0
                                x: 18
                                y: 8
                                Text {
                                    text: "УЖЕ НА КОМПЬЮТЕРЕ"
                                    color: win.muted
                                    font.pixelSize: 12
                                    font.letterSpacing: 1
                                }
                            }
                            RowLayout {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.bottom: parent.bottom
                                anchors.leftMargin: 18
                                anchors.rightMargin: 18
                                anchors.bottomMargin: 8
                                height: 48
                                AppMark {
                                    width: 40
                                    height: 40
                                    corner: 12
                                    storeId: modelData.storeId || ""
                                    bundleId: modelData.bundleId || ""
                                    mark: modelData.mark
                                    fill: modelData.color
                                    ink: modelData.ink
                                }
                                ColumnLayout {
                                    spacing: 0
                                    Text { text: modelData.name; color: win.ink; font.pixelSize: 16; font.weight: 650 }
                                    Text { text: modelData.detail; color: win.muted; font.pixelSize: 12 }
                                }
                                Item { Layout.fillWidth: true }
                                Text {
                                    text: modelData.placed
                                          ? ("На " + win.deviceNoun)
                                          : (win.phoneOn ? "Поставить" : ("Нет " + win.deviceNoun))
                                    color: modelData.placed ? "#1B7F3A" : (win.phoneOn ? win.accent : "#8E8E93")
                                    font.pixelSize: 15
                                    font.weight: 650
                                    MouseArea {
                                        anchors.fill: parent
                                        enabled: !modelData.placed && !win.filesBusy && win.phoneOn
                                        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                        onClicked: win.placeSaved(index)
                                    }
                                }
                            }
                        }
                    }
                }
            }
            }
        }

        Rectangle {
            anchors.fill: parent
            visible: files.sheet
            color: "#331C1C1E"
            MouseArea { anchors.fill: parent; onClicked: files.sheet = false }

            Rectangle {
                width: Math.min(520, parent.width - 80)
                height: sheetCol.implicitHeight + 36
                radius: 22
                color: "white"
                anchors.centerIn: parent
                MouseArea { anchors.fill: parent }

                Column {
                    id: sheetCol
                    width: parent.width - 36
                    x: 18
                    y: 18
                    spacing: 12
                    Text { text: "Что сохранить"; color: win.ink; font.pixelSize: 26; font.weight: 700 }
                    Text {
                        width: parent.width
                        text: "На " + win.deviceNoun + " всё останется. На компьютер ляжет копия."
                        color: win.muted
                        font.pixelSize: 14
                        wrapMode: Text.WordWrap
                    }
                    TextField {
                        id: fileQuery
                        width: parent.width
                        implicitHeight: 44
                        placeholderText: "Имя или пакет"
                        placeholderTextColor: "#8E8E93"
                        color: win.ink
                        font.pixelSize: 15
                        leftPadding: 14
                        rightPadding: 14
                        selectByMouse: true
                        background: Rectangle {
                            radius: 12
                            color: "#F7F7F8"
                            border.color: fileQuery.activeFocus ? win.accent : win.line
                            border.width: fileQuery.activeFocus ? 2 : 1
                        }
                        onTextChanged: {
                            files.needle = text
                            files.refill()
                        }
                    }
                    Text {
                        width: parent.width
                        visible: files.shown.length === 0
                        text: {
                            if (win.phoneLoading)
                                return "Смотрим приложения…"
                            if (files.needle.trim() !== "")
                                return "Ничего не нашлось"
                            return "На " + win.deviceNoun + " нет приложений, которые можно скопировать"
                        }
                        color: win.muted
                        font.pixelSize: 14
                        wrapMode: Text.WordWrap
                    }
                    Flickable {
                        id: fileScroll
                        width: parent.width
                        implicitHeight: Math.min(320, fileList.implicitHeight)
                        height: implicitHeight
                        contentWidth: width
                        contentHeight: fileList.implicitHeight
                        clip: true
                        boundsBehavior: Flickable.StopAtBounds
                        Column {
                            id: fileList
                            width: fileScroll.width
                            spacing: 8
                            Repeater {
                                model: files.shown
                                Rectangle {
                                    required property var modelData
                                    width: fileList.width
                                    height: 58
                                    radius: 14
                                    color: "#F7F7F8"
                                    property bool fromStore: modelData.storeId !== ""
                                    property bool saved: files.alreadySaved(modelData.bundleId)
                                    property bool on: files.chosen[modelData.bundleId] === true
                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 12
                                        anchors.rightMargin: 12
                                        AppMark {
                                            width: 36
                                            height: 36
                                            corner: 10
                                            storeId: modelData.storeId || ""
                                            bundleId: modelData.bundleId || ""
                                            mark: modelData.mark
                                            fill: modelData.color
                                            ink: modelData.ink
                                        }
                                        ColumnLayout {
                                            spacing: 0
                                            Layout.fillWidth: true
                                            Text {
                                                text: modelData.name
                                                color: win.ink
                                                font.pixelSize: 16
                                                font.weight: 650
                                                elide: Text.ElideRight
                                                Layout.fillWidth: true
                                            }
                                            Text {
                                                text: modelData.detail
                                                color: win.muted
                                                font.pixelSize: 12
                                                elide: Text.ElideRight
                                                Layout.fillWidth: true
                                            }
                                        }
                                        Text {
                                            visible: saved || !fromStore
                                            text: saved ? "Уже сохранено" : "Не из App Store"
                                            color: win.muted
                                            font.pixelSize: 13
                                        }
                                        Rectangle {
                                            visible: fromStore && !saved
                                            width: 24
                                            height: 24
                                            radius: 7
                                            color: on ? win.accent : "white"
                                            border.width: on ? 0 : 1
                                            border.color: win.line
                                            Text {
                                                anchors.centerIn: parent
                                                visible: on
                                                text: "✓"
                                                color: "white"
                                                font.pixelSize: 13
                                                font.weight: 700
                                            }
                                        }
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        enabled: fromStore && !saved
                                        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                        onClicked: files.toggle(modelData.bundleId)
                                    }
                                }
                            }
                        }
                    }
                    RowLayout {
                        width: parent.width
                        Text {
                            text: "Отмена"
                            color: win.muted
                            font.pixelSize: 15
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: files.sheet = false }
                        }
                        Item { Layout.fillWidth: true }
                        PillButton {
                            text: files.chosenCount() > 0 ? ("Сохранить " + files.chosenCount()) : "Сохранить"
                            implicitWidth: 180
                            enabled: files.chosenCount() > 0 && !win.filesBusy
                            fill: enabled ? win.accent : "#E4E4E8"
                            labelColor: enabled ? "white" : "#8E8E93"
                            onClicked: files.commitCopies()
                        }
                    }
                }
            }
        }
    }

    component AccountPage: Item {
        id: account

        Flickable {
            anchors.fill: parent
            contentWidth: width
            contentHeight: accountCol.implicitHeight + 32
            clip: true
            boundsBehavior: Flickable.StopAtBounds

            Column {
                id: accountCol
                width: Math.min(640, parent.width - 72)
                x: 36
                y: 8
                spacing: 16

                Text {
                    text: "← Назад"
                    color: win.muted
                    font.pixelSize: 14
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: win.go(0) }
                }
                Text { text: "Apple ID"; color: win.ink; font.pixelSize: 40; font.weight: 700 }
                Text {
                    width: parent.width
                    text: "У каждого устройства свой Apple ID. Сессия хранится отдельно и открывается, когда устройство выбрано. Пароль Apple ID программа не сохраняет."
                    color: win.muted
                    font.pixelSize: 15
                    wrapMode: Text.WordWrap
                }

                Rectangle {
                    visible: session.accounts.length > 0
                    width: parent.width
                    radius: 18
                    color: "white"
                    border.color: win.line
                    implicitHeight: accountList.implicitHeight + 16

                    Column {
                        id: accountList
                        width: parent.width
                        y: 8
                        spacing: 0

                        Repeater {
                            model: session.accounts
                            delegate: Rectangle {
                                required property var modelData
                                width: accountList.width
                                height: modelData.devices ? 58 : 46
                                color: modelData.active ? "#F8E8DF" : "transparent"

                                Column {
                                    anchors.left: parent.left
                                    anchors.right: openLabel.left
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.leftMargin: 16
                                    anchors.rightMargin: 12
                                    spacing: 2
                                    Text {
                                        width: parent.width
                                        text: modelData.email
                                        color: win.ink
                                        font.pixelSize: 15
                                        font.weight: 650
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        visible: modelData.devices !== ""
                                        width: parent.width
                                        text: modelData.devices
                                        color: win.muted
                                        font.pixelSize: 13
                                        elide: Text.ElideRight
                                    }
                                }
                                Text {
                                    id: openLabel
                                    anchors.right: parent.right
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.rightMargin: 16
                                    text: modelData.active ? "Открыта" : "Открыть"
                                    color: modelData.active ? "#1B7F3A" : win.accent
                                    font.pixelSize: 14
                                    font.weight: 650
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    enabled: !modelData.active && session.authPhase !== "running"
                                    cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                    onClicked: session.useAccount(modelData.email)
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    width: parent.width
                    radius: 18
                    color: "white"
                    border.color: win.line
                    implicitHeight: idCol.implicitHeight + 32
                    Column {
                        id: idCol
                        width: parent.width - 36
                        x: 18
                        y: 16
                        spacing: 10

                        Row {
                            spacing: 8
                            Rectangle {
                                width: 10
                                height: 10
                                radius: 5
                                color: session.signedIn ? "#1B7F3A" : "#C7C7CC"
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            Text {
                                text: session.signedIn
                                      ? (session.accountEmail || "Вход выполнен")
                                      : (session.authPhase === "locked" ? "Сессия сохранена" : "Вход не выполнен")
                                color: win.ink
                                font.pixelSize: 17
                                font.weight: 650
                            }
                        }
                        Text {
                            width: parent.width
                            text: {
                                if (session.boundEmail && session.accountEmail && session.boundEmail !== session.accountEmail)
                                    return "Сейчас открыт " + session.accountEmail + ". Для этого " + win.deviceNoun + " запомнен " + session.boundEmail + "."
                                if (session.boundEmail)
                                    return "Этот " + win.deviceNoun + " привязан к " + session.boundEmail + "."
                                if (session.signedIn)
                                    return "Сессия открыта. " + win.deviceNoun + " может докачать сгруженное и без неё."
                                return "Пока сессии нет, " + win.deviceNoun + " может докачать сгруженное сам."
                            }
                            color: win.muted
                            font.pixelSize: 14
                            wrapMode: Text.WordWrap
                        }

                        TextField {
                            id: emailField
                            visible: !session.signedIn && session.authPhase !== "locked" && session.authPhase !== "need_passphrase" && session.authPhase !== "need_code" && session.authPhase !== "checking"
                            width: parent.width
                            implicitHeight: 48
                            enabled: session.authPhase !== "running"
                            placeholderText: session.boundEmail || "you@icloud.com"
                            placeholderTextColor: "#8E8E93"
                            color: win.ink
                            font.pixelSize: 16
                            leftPadding: 16
                            selectByMouse: true
                            background: Rectangle {
                                radius: 14
                                color: "white"
                                border.color: emailField.activeFocus ? win.accent : win.line
                                border.width: emailField.activeFocus ? 2 : 1
                            }
                        }
                        TextField {
                            id: passwordField
                            visible: emailField.visible
                            width: parent.width
                            implicitHeight: 48
                            enabled: session.authPhase !== "running"
                            echoMode: TextInput.Password
                            placeholderText: "пароль Apple ID"
                            placeholderTextColor: "#8E8E93"
                            color: win.ink
                            font.pixelSize: 16
                            leftPadding: 16
                            background: Rectangle {
                                radius: 14
                                color: "white"
                                border.color: passwordField.activeFocus ? win.accent : win.line
                                border.width: passwordField.activeFocus ? 2 : 1
                            }
                            onAccepted: account.submit()
                        }
                        TextField {
                            id: codeField
                            visible: session.authPhase === "need_code"
                            width: parent.width
                            implicitHeight: 48
                            placeholderText: "код из сообщения Apple"
                            placeholderTextColor: "#8E8E93"
                            color: win.ink
                            font.pixelSize: 16
                            leftPadding: 16
                            background: Rectangle {
                                radius: 14
                                color: "white"
                                border.color: codeField.activeFocus ? win.accent : win.line
                                border.width: codeField.activeFocus ? 2 : 1
                            }
                            onAccepted: account.submit()
                        }
                        TextField {
                            id: passField
                            visible: session.authPhase === "locked" || session.authPhase === "need_passphrase"
                            width: parent.width
                            implicitHeight: 48
                            echoMode: TextInput.Password
                            placeholderText: "пароль связки ключей"
                            placeholderTextColor: "#8E8E93"
                            color: win.ink
                            font.pixelSize: 16
                            leftPadding: 16
                            background: Rectangle {
                                radius: 14
                                color: "white"
                                border.color: passField.activeFocus ? win.accent : win.line
                                border.width: passField.activeFocus ? 2 : 1
                            }
                            onAccepted: account.submit()
                        }
                        Text {
                            visible: session.authStatus !== ""
                            width: parent.width
                            text: session.authStatus
                            color: session.authPhase === "in" ? "#1B7F3A" : win.muted
                            font.pixelSize: 14
                            wrapMode: Text.WordWrap
                        }
                        Row {
                            spacing: 10
                            Rectangle {
                                visible: session.signedIn
                                width: 96
                                height: 40
                                radius: 12
                                color: "#F2F2F4"
                                Text { anchors.centerIn: parent; text: "Выйти"; color: win.ink; font.pixelSize: 14 }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: session.signOut()
                                }
                            }
                            PillButton {
                                visible: !session.signedIn
                                implicitWidth: 180
                                enabled: session.authPhase !== "running" && session.authPhase !== "checking"
                                text: {
                                    if (session.authPhase === "checking")
                                        return "Смотрим"
                                    if (session.authPhase === "running")
                                        return "Входим…"
                                    if (session.authPhase === "need_code")
                                        return "Отправить код"
                                    if (session.authPhase === "locked" || session.authPhase === "need_passphrase")
                                        return "Открыть"
                                    return "Войти"
                                }
                                onClicked: account.submit()
                            }
                        }

                        // Session check and purchase list. Neutral placeholder until
                        // Ника's layouts are approved; all logic is in Python.
                        Text {
                            visible: session.signedIn && session.sessionNote !== ""
                            width: parent.width
                            text: session.sessionNote
                            color: session.sessionState === "expired" ? "#B3261E" : win.muted
                            font.pixelSize: 14
                            wrapMode: Text.WordWrap
                        }
                        Row {
                            visible: session.signedIn && session.sessionRelogin
                            spacing: 10
                            PillButton {
                                implicitWidth: 180
                                text: "Войти заново"
                                onClicked: session.loginPrompt(session.accountEmail)
                            }
                        }
                        Column {
                            id: purchasesBox
                            visible: session.signedIn
                            width: parent.width
                            spacing: 8
                            onVisibleChanged: if (visible) session.loadPurchases()

                            Row {
                                width: parent.width
                                spacing: 10
                                Text {
                                    text: "Покупки"
                                    color: win.ink
                                    font.pixelSize: 17
                                    font.weight: 600
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                                Text {
                                    text: session.purchasesProgress
                                    color: win.muted
                                    font.pixelSize: 14
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                                Rectangle {
                                    width: 96
                                    height: 32
                                    radius: 10
                                    color: "#F2F2F4"
                                    Text {
                                        anchors.centerIn: parent
                                        text: session.purchasesBusy ? "Отмена" : "Обновить"
                                        color: win.ink
                                        font.pixelSize: 14
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: session.purchasesBusy ? session.cancelPurchases() : session.loadPurchases()
                                    }
                                }
                            }
                            Text {
                                visible: session.purchasesNote !== ""
                                width: parent.width
                                text: session.purchasesNote
                                color: win.muted
                                font.pixelSize: 14
                                wrapMode: Text.WordWrap
                            }
                            ListView {
                                width: parent.width
                                height: Math.min(contentHeight, 280)
                                clip: true
                                model: session.purchases
                                delegate: Item {
                                    required property var modelData
                                    width: ListView.view.width
                                    height: 40
                                    Column {
                                        anchors.verticalCenter: parent.verticalCenter
                                        Text { text: modelData.name; color: win.ink; font.pixelSize: 14; elide: Text.ElideRight }
                                        Text {
                                            text: modelData.bundleId + (modelData.date ? " · " + modelData.date : "")
                                            color: win.muted
                                            font.pixelSize: 12
                                            elide: Text.ElideRight
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        function submit() {
            if (session.authPhase === "need_code")
                session.submitCode(codeField.text)
            else if (session.authPhase === "locked" || session.authPhase === "need_passphrase")
                session.unlock(passField.text)
            else
                session.login(emailField.text || session.boundEmail, passwordField.text)
        }
    }

    component ChoosePage: Item {
        id: choose
        objectName: "choosePage"
        property string query: ""
        property var rows: []

        function refresh() {
            var q = query.trim().toLowerCase()
            var apps = []
            var backs = win.backIds
            var source = win.missingApps
            for (var i = 0; i < source.length; i++) {
                var app = source[i]
                if (backs[app.storeId] === true)
                    continue
                var blob = (app.name + " " + app.detail).toLowerCase()
                if (q && blob.indexOf(q) === -1)
                    continue
                apps.push(app)
            }
            rows = apps
        }

        onQueryChanged: refresh()
        Component.onCompleted: refresh()
        Connections {
            target: win
            function onBackIdsChanged() { choose.refresh() }
            function onMissingAppsChanged() { choose.refresh() }
        }

        Column {
            id: chooseHead
            width: parent.width - 72
            x: 36
            y: 8
            spacing: 12

            Text {
                text: "← Назад"
                color: win.muted
                font.pixelSize: 14
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: win.go(0)
                }
            }
            Text {
                text: "Что вернуть"
                color: win.ink
                font.pixelSize: 40
                font.weight: 700
            }
            Text {
                width: parent.width
                text: "Отметьте приложения. На " + win.deviceNoun + " вернутся только они."
                color: win.muted
                font.pixelSize: 15
                wrapMode: Text.WordWrap
            }
            TextField {
                id: chooseQuery
                objectName: "chooseQuery"
                width: parent.width
                implicitHeight: 56
                placeholderText: "Название"
                placeholderTextColor: "#8E8E93"
                color: win.ink
                font.pixelSize: 17
                leftPadding: 18
                rightPadding: 18
                selectByMouse: true
                background: Rectangle {
                    radius: 16
                    color: "white"
                    border.color: chooseQuery.activeFocus ? win.accent : win.line
                    border.width: chooseQuery.activeFocus ? 2 : 1
                }
                onTextChanged: choose.query = text
            }
            Row {
                spacing: 18
                Text {
                    text: choose.query.trim() === "" ? "Выбрать все" : "Все найденные"
                    color: win.ink
                    font.pixelSize: 15
                    font.weight: 650
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: win.selectListed(choose.rows)
                    }
                }
                Text {
                    text: "Снять"
                    color: win.muted
                    font.pixelSize: 15
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: win.clearListed(choose.rows)
                    }
                }
            }
        }

        ListView {
            id: chooseList
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: chooseHead.bottom
            anchors.bottom: chooseBar.top
            anchors.leftMargin: 36
            anchors.rightMargin: 36
            anchors.topMargin: 8
            clip: true
            spacing: 8
            boundsBehavior: Flickable.StopAtBounds
            model: choose.rows

            delegate: Rectangle {
                required property var modelData
                width: chooseList.width
                height: 68
                radius: 16
                property bool on: win.pickIds[modelData.storeId] === true
                color: "white"
                border.width: on ? 2 : 1
                border.color: on ? win.accent : win.line

                Row {
                    anchors.fill: parent
                    anchors.leftMargin: 14
                    anchors.rightMargin: 16
                    spacing: 12
                    AppMark {
                        width: 40
                        height: 40
                        corner: 12
                        storeId: modelData.storeId || ""
                        mark: modelData.mark
                        fill: modelData.color
                        ink: modelData.ink
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        width: parent.width - 40 - 28 - 24
                        spacing: 2
                        Text {
                            width: parent.width
                            text: modelData.name
                            color: win.ink
                            font.pixelSize: 16
                            font.weight: 650
                            elide: Text.ElideRight
                        }
                        Text {
                            width: parent.width
                            text: modelData.detail
                            color: win.muted
                            font.pixelSize: 13
                            elide: Text.ElideRight
                        }
                    }
                    Rectangle {
                        width: 24
                        height: 24
                        radius: 12
                        anchors.verticalCenter: parent.verticalCenter
                        color: on ? win.accent : "transparent"
                        border.width: on ? 0 : 1.5
                        border.color: "#C7C7CC"
                        Text {
                            anchors.centerIn: parent
                            visible: on
                            text: "✓"
                            color: "white"
                            font.pixelSize: 13
                            font.weight: 700
                        }
                    }
                }
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: win.togglePick(modelData.storeId)
                }
            }
        }

        Text {
            visible: choose.rows.length === 0
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: chooseHead.bottom
            anchors.topMargin: 28
            text: "Среди сгруженных такого нет"
            color: win.ink
            font.pixelSize: 16
        }

        Rectangle {
            id: chooseBar
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 92
            color: "#E8E8ED"

            Rectangle {
                anchors.fill: parent
                anchors.leftMargin: 36
                anchors.rightMargin: 36
                anchors.bottomMargin: 16
                anchors.topMargin: 8
                radius: 18
                color: "white"
                border.color: win.line

                Item {
                    anchors.fill: parent
                    anchors.leftMargin: 18
                    anchors.rightMargin: 18
                    Text {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Выбрано " + win.chosenCount()
                        color: win.ink
                        font.pixelSize: 17
                        font.weight: 650
                    }
                    PillButton {
                        width: 160
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Готово"
                        onClicked: win.go(0)
                    }
                }
            }
        }
    }

    Item {
        id: keychainAsk
        visible: win.keychainOpen
        anchors.fill: parent
        z: 50

        Rectangle {
            anchors.fill: parent
            color: "#66000000"
        }
        MouseArea {
            anchors.fill: parent
            onClicked: win.keychainOpen = false
        }
        Rectangle {
            anchors.centerIn: parent
            width: 460
            radius: 18
            color: "white"
            implicitHeight: keychainCol.implicitHeight + 36

            MouseArea { anchors.fill: parent }

            Column {
                id: keychainCol
                width: parent.width - 36
                x: 18
                y: 18
                spacing: 12

                Text {
                    width: parent.width
                    text: win.keychainFor ? win.keychainFor : "Откройте связку один раз"
                    color: win.ink
                    font.pixelSize: 18
                    font.weight: 700
                    wrapMode: Text.WordWrap
                }
                Text {
                    width: parent.width
                    text: "Это не пароль Apple ID. ipatool спрашивает его у каждого своего запуска. Введите его один раз — дальше AppRestore подставит его сам."
                    color: win.muted
                    font.pixelSize: 14
                    wrapMode: Text.WordWrap
                }
                TextField {
                    id: keychainField
                    width: parent.width
                    implicitHeight: 48
                    echoMode: TextInput.Password
                    placeholderText: "пароль связки ключей"
                    placeholderTextColor: "#8E8E93"
                    color: win.ink
                    font.pixelSize: 16
                    leftPadding: 16
                    background: Rectangle {
                        radius: 14
                        color: "white"
                        border.color: keychainField.activeFocus ? win.accent : win.line
                        border.width: keychainField.activeFocus ? 2 : 1
                    }
                    onAccepted: session.unlock(keychainField.text)
                }
                Text {
                    visible: session.authStatus !== "" && session.authPhase !== "checking"
                    width: parent.width
                    text: session.authStatus
                    color: win.muted
                    font.pixelSize: 13
                    wrapMode: Text.WordWrap
                }
                Row {
                    width: parent.width
                    spacing: 10
                    Rectangle {
                        width: 96
                        height: 40
                        radius: 12
                        color: "#F2F2F4"
                        Text { anchors.centerIn: parent; text: "Позже"; color: win.ink; font.pixelSize: 14 }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.keychainOpen = false
                        }
                    }
                    Item { width: parent.width - 96 - 160 - 10; height: 1 }
                    PillButton {
                        implicitWidth: 160
                        implicitHeight: 40
                        enabled: session.authPhase !== "running"
                        text: session.authPhase === "running" ? "Открываем…" : "Открыть"
                        onClicked: session.unlock(keychainField.text)
                    }
                }
            }
        }
        onVisibleChanged: {
            if (visible)
                keychainField.forceActiveFocus()
        }
    }

    Item {
        id: loginAsk
        visible: win.loginOpen
        anchors.fill: parent
        z: 51

        Rectangle {
            anchors.fill: parent
            color: "#66000000"
        }
        MouseArea {
            anchors.fill: parent
            onClicked: {
                session.keepAccount()
                win.loginOpen = false
            }
        }
        Rectangle {
            anchors.centerIn: parent
            width: 460
            radius: 18
            color: "white"
            implicitHeight: loginCol.implicitHeight + 36

            MouseArea { anchors.fill: parent }

            Column {
                id: loginCol
                width: parent.width - 36
                x: 18
                y: 18
                spacing: 12

                Text {
                    width: parent.width
                    text: "Войти в Apple ID"
                    color: win.ink
                    font.pixelSize: 18
                    font.weight: 700
                }
                Text {
                    width: parent.width
                    text: win.loginFor
                          ? ("Для этого " + win.deviceNoun + " нужен " + win.loginFor + ". Пароль программа не сохраняет.")
                          : "Пароль программа не сохраняет."
                    color: win.muted
                    font.pixelSize: 14
                    wrapMode: Text.WordWrap
                }
                TextField {
                    id: loginEmail
                    width: parent.width
                    implicitHeight: 48
                    placeholderText: "you@icloud.com"
                    placeholderTextColor: "#8E8E93"
                    color: win.ink
                    font.pixelSize: 16
                    leftPadding: 16
                    enabled: session.authPhase !== "running"
                    background: Rectangle {
                        radius: 14
                        color: "white"
                        border.color: loginEmail.activeFocus ? win.accent : win.line
                        border.width: loginEmail.activeFocus ? 2 : 1
                    }
                }
                TextField {
                    id: loginPassword
                    width: parent.width
                    implicitHeight: 48
                    echoMode: TextInput.Password
                    placeholderText: "пароль Apple ID"
                    placeholderTextColor: "#8E8E93"
                    color: win.ink
                    font.pixelSize: 16
                    leftPadding: 16
                    enabled: session.authPhase !== "running"
                    background: Rectangle {
                        radius: 14
                        color: "white"
                        border.color: loginPassword.activeFocus ? win.accent : win.line
                        border.width: loginPassword.activeFocus ? 2 : 1
                    }
                    onAccepted: session.login(loginEmail.text, loginPassword.text)
                }
                Text {
                    visible: session.authStatus !== "" && session.authPhase !== "checking"
                    width: parent.width
                    text: session.authStatus
                    color: win.muted
                    font.pixelSize: 13
                    wrapMode: Text.WordWrap
                }
                Row {
                    width: parent.width
                    spacing: 10
                    Rectangle {
                        width: 148
                        height: 40
                        radius: 12
                        color: "#F2F2F4"
                        Text {
                            anchors.centerIn: parent
                            text: session.signedIn ? "Оставить текущий" : "Позже"
                            color: win.ink
                            font.pixelSize: 14
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                session.keepAccount()
                                win.loginOpen = false
                            }
                        }
                    }
                    Item { width: parent.width - 148 - 140 - 10; height: 1 }
                    PillButton {
                        implicitWidth: 140
                        implicitHeight: 40
                        enabled: session.authPhase !== "running"
                        text: session.authPhase === "running" ? "Входим…" : "Войти"
                        onClicked: session.login(loginEmail.text, loginPassword.text)
                    }
                }
            }
        }
    }
}
