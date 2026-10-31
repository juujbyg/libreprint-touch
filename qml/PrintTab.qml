import QtQuick 2.7
import Lomiri.Components 1.3
import Lomiri.Content 1.3
import 'components'

Item {
    id: tab

    property int copies: 1
    property var transfer: null
    readonly property var info: root.info
    readonly property var sidesOffered: {
        var offered = [{ title: i18n.tr('Off'), value: '' }]
        if (info !== null && info.sides.indexOf('two-sided-long-edge') !== -1) {
            offered.push({ title: i18n.tr('Long edge'), value: 'two-sided-long-edge' })
        }
        if (info !== null && info.sides.indexOf('two-sided-short-edge') !== -1) {
            offered.push({ title: i18n.tr('Short edge'), value: 'two-sided-short-edge' })
        }
        return offered
    }
    readonly property var qualitiesOffered: {
        var names = { 3: i18n.tr('Draft'), 4: i18n.tr('Normal'), 5: i18n.tr('Best') }
        return [3, 4, 5].filter(function(value) {
            return info !== null && info.qualities.indexOf(value) !== -1
        }).map(function(value) {
            return { title: names[value], value: value }
        })
    }

    function detail(row) {
        if (row.state === 'preparing') {
            return root.info === null ? i18n.tr('Waiting for the printer…')
                                      : i18n.tr('Preparing for printing, this can take a moment…')
        }
        if (row.state === 'failed') {
            return row.message
        }
        if (row.action === 'office-missing') {
            return i18n.tr('Needs LibreOffice. Install it in Diagnostics')
        }
        if (row.action === 'refuse') {
            return i18n.tr("This printer can't read this file type")
        }
        return row.pages > 0 ? i18n.tr('%1 page', '%1 pages', row.pages).arg(row.pages) : i18n.tr('Ready')
    }

    function pick(type) {
        tab.transfer = null
        picker.contentType = type
        picker.visible = true
    }

    function printAll() {
        var quality = qualitiesOffered.length > 0 ? qualitiesOffered[Math.max(qualityChoice.current, 0)].value : 0
        // Normal is what the printer does unasked. Saying so isn't needed, and it's
        // one of two changes behind the HP's dropouts until proven otherwise.
        quality = quality === 4 ? 0 : quality
        root.printDocuments({
            copies: tab.copies,
            sides: sidesOffered[sidesChoice.current].value,
            color: colorToggle.checked ? '' : 'monochrome',
            quality: quality,
            pages: pagesField.text
        })
    }

    Flickable {
        anchors {
            top: parent.top
            left: parent.left
            right: parent.right
            bottom: printBar.top
        }
        contentHeight: column.height + units.gu(3)
        boundsBehavior: Flickable.StopAtBounds
        clip: true

        Column {
            id: column
            x: units.gu(2)
            y: units.gu(1)
            width: parent.width - units.gu(4)
            spacing: units.gu(1.5)

            Section {
                width: parent.width
                title: i18n.tr('Documents')
                subtitle: i18n.tr('PDFs, photos, text and Office files')
            }

            Row {
                spacing: units.gu(1)

                Pill {
                    iconName: 'add'
                    text: i18n.tr('Document')
                    onClicked: tab.pick(ContentType.Documents)
                }
                Pill {
                    iconName: 'add'
                    text: i18n.tr('Photo')
                    onClicked: tab.pick(ContentType.Pictures)
                }
            }

            Card {
                visible: root.documents.count > 0

                Repeater {
                    model: root.documents
                    delegate: CardRow {
                        padding: units.gu(1)

                        Item {
                            width: parent.width
                            height: units.gu(6.5)

                            Rectangle {
                                id: thumbnailFrame
                                anchors.verticalCenter: parent.verticalCenter
                                width: units.gu(4.5)
                                height: units.gu(6)
                                radius: units.gu(0.4)
                                color: thumbnailImage.status === Image.Ready ? 'white' : root.ui.pill

                                Image {
                                    id: thumbnailImage
                                    anchors.fill: parent
                                    anchors.margins: units.dp(1)
                                    fillMode: Image.PreserveAspectFit
                                    asynchronous: true
                                    cache: false
                                    sourceSize.height: units.gu(12)
                                    source: model.thumbnail !== '' ? 'file://' + model.thumbnail : ''
                                }
                                Icon {
                                    anchors.centerIn: parent
                                    width: units.gu(2.5)
                                    height: width
                                    name: 'note'
                                    color: root.ui.dim
                                    visible: thumbnailImage.status !== Image.Ready && model.state !== 'preparing'
                                }
                                ActivityIndicator {
                                    anchors.centerIn: parent
                                    running: model.state === 'preparing'
                                    visible: running
                                }
                            }

                            Column {
                                anchors {
                                    left: thumbnailFrame.right
                                    leftMargin: units.gu(1.25)
                                    right: rowActions.left
                                    rightMargin: units.gu(0.5)
                                    verticalCenter: parent.verticalCenter
                                }
                                Label {
                                    width: parent.width
                                    elide: Text.ElideMiddle
                                    color: root.ui.text
                                    text: model.name
                                }
                                Label {
                                    width: parent.width
                                    wrapMode: Text.WordWrap
                                    maximumLineCount: 2
                                    elide: Text.ElideRight
                                    textSize: Label.Small
                                    color: model.state === 'failed' || model.state === 'blocked' ? root.ui.dangerText : root.ui.dim
                                    text: tab.detail(model)
                                }
                            }

                            Row {
                                id: rowActions
                                anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                                spacing: units.gu(0.5)

                                Pill {
                                    width: units.gu(5)
                                    iconName: 'view-on'
                                    visible: model.state === 'ready' && model.thumbnail !== ''
                                    onClicked: root.stack.push(Qt.resolvedUrl('PreviewPage.qml'), {
                                        name: model.name, path: model.path, image: model.mime === 'application/pdf' ? '' : model.path, pages: model.pages
                                    })
                                }
                                Pill {
                                    width: units.gu(5)
                                    iconName: 'close'
                                    onClicked: root.documents.remove(index)
                                }
                            }
                        }
                    }
                }
            }

            Section {
                width: parent.width
                title: i18n.tr('Options')
                subtitle: i18n.tr('Used for every document')
            }

            Card {
                CardRow {
                    Item {
                        width: parent.width
                        height: units.gu(4)

                        Label {
                            anchors.verticalCenter: parent.verticalCenter
                            color: root.ui.text
                            text: i18n.tr('Copies')
                        }
                        Row {
                            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                            spacing: units.gu(1.25)

                            Label {
                                anchors.verticalCenter: parent.verticalCenter
                                color: root.ui.text
                                text: tab.copies
                            }
                            Pill {
                                width: units.gu(4)
                                height: width
                                iconName: 'remove'
                                enabled: tab.copies > 1
                                onClicked: tab.copies--
                            }
                            Pill {
                                width: units.gu(4)
                                height: width
                                iconName: 'add'
                                enabled: tab.copies < 99
                                onClicked: tab.copies++
                            }
                        }
                    }
                }

                CardRow {
                    visible: tab.sidesOffered.length > 1
                    Column {
                        width: parent.width
                        spacing: units.gu(0.75)

                        Label {
                            color: root.ui.text
                            text: i18n.tr('Two-sided')
                        }
                        Choice {
                            id: sidesChoice
                            options: tab.sidesOffered.map(function(side) { return side.title })
                        }
                    }
                }

                CardRow {
                    visible: tab.info !== null && tab.info.colorModes.indexOf('monochrome') !== -1
                    Item {
                        width: parent.width
                        height: units.gu(4.5)

                        Column {
                            anchors.verticalCenter: parent.verticalCenter
                            Label {
                                color: root.ui.text
                                text: i18n.tr('Colour')
                            }
                            Label {
                                textSize: Label.Small
                                color: root.ui.dim
                                text: i18n.tr('Off prints in greyscale')
                            }
                        }
                        Toggle {
                            id: colorToggle
                            anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                        }
                    }
                }

                CardRow {
                    visible: tab.qualitiesOffered.length > 1
                    Column {
                        width: parent.width
                        spacing: units.gu(0.75)

                        Label {
                            color: root.ui.text
                            text: i18n.tr('Quality')
                        }
                        Choice {
                            id: qualityChoice
                            current: Math.min(1, tab.qualitiesOffered.length - 1)
                            options: tab.qualitiesOffered.map(function(quality) { return quality.title })
                        }
                    }
                }

                CardRow {
                    TextField {
                        id: pagesField
                        width: parent.width
                        placeholderText: i18n.tr('Pages (e.g. 1-3,5 — empty for all)')
                        inputMethodHints: Qt.ImhNoPredictiveText
                    }
                }
            }
        }
    }

    Rectangle {
        id: printBar
        anchors {
            left: parent.left
            right: parent.right
            bottom: parent.bottom
        }
        height: units.gu(8)
        color: root.ui.window

        Pill {
            anchors.centerIn: parent
            kind: 'accent'
            iconName: 'document-print'
            busy: root.printing
            enabled: !root.printing && root.preparing === 0 && root.readyDocuments > 0 && root.info !== null && root.failure === ''
            text: root.readyDocuments > 0
                  ? i18n.tr('Print %1 document', 'Print %1 documents', root.readyDocuments).arg(root.readyDocuments)
                  : i18n.tr('Print')
            onClicked: tab.printAll()
        }
    }

    Connections {
        target: tab.transfer
        onStateChanged: {
            if (tab.transfer && tab.transfer.state === ContentTransfer.Charged) {
                picker.visible = false
                root.takeItems(tab.transfer)
                tab.transfer = null
            }
        }
    }

    ContentPeerPicker {
        id: picker
        parent: root
        anchors.fill: parent
        visible: false
        z: 50
        contentType: ContentType.Documents
        handler: ContentHandler.Source
        onPeerSelected: {
            peer.selectionType = ContentTransfer.Multiple
            tab.transfer = peer.request()
        }
        onCancelPressed: visible = false
    }
}
