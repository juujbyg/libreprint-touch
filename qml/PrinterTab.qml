import QtQuick 2.7
import Lomiri.Components 1.3
import 'components'

Flickable {
    id: tab

    readonly property var info: root.info
    readonly property var rows: {
        var printerJobs = info ? info.jobs : []
        return root.queueRows().concat(printerJobs.map(function(job) {
            return { name: job.name, text: jobText(job), id: job.id }
        }))
    }

    function stateText(state) {
        if (state === 'idle') return i18n.tr('Ready')
        if (state === 'printing') return i18n.tr('Printing')
        if (state === 'stopped') return i18n.tr('Stopped')
        if (state === 'offline') return i18n.tr('Offline')
        return i18n.tr('Unknown')
    }

    function stateColor(state) {
        if (state === 'idle') return root.ui.good
        if (state === 'printing') return root.ui.warning
        return root.ui.dangerText
    }

    function jobText(job) {
        var state = job.state
        if (job.stopping) return i18n.tr('Cancelling…')
        if (state === 'pending') return i18n.tr('Waiting')
        if (state === 'held') return i18n.tr('On hold')
        if (state === 'processing') return i18n.tr('Printing')
        if (state === 'stopped') return i18n.tr('Stopped')
        return state
    }

    function printTestPage() {
        root.testing = true
        root.call('print_test_page', [root.printer, root.info], function(reply) {
            root.testing = false
            if (reply.ok) {
                root.toast(i18n.tr('Test page sent to the printer'))
            } else {
                root.showError(reply.error)
            }
            root.refresh()
        })
    }

    contentHeight: column.height + units.gu(5)
    boundsBehavior: Flickable.StopAtBounds
    clip: true

    Column {
        id: column
        x: units.gu(2)
        y: units.gu(1)
        width: parent.width - units.gu(4)
        spacing: units.gu(2.5)

        Column {
            width: parent.width
            spacing: units.gu(1.25)

            Icon {
                anchors.horizontalCenter: parent.horizontalCenter
                width: units.gu(12)
                height: width
                name: 'printer-symbolic'
                color: root.ui.dim
            }
            Label {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                textSize: Label.Large
                font.weight: Font.Bold
                color: root.ui.text
                text: tab.info ? tab.info.model : root.printer ? root.printer.name : i18n.tr('No printer yet')
            }
            Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter
                visible: tab.info !== null
                width: stateLabel.width + units.gu(2.5)
                height: units.gu(3.25)
                radius: height / 2
                color: Qt.rgba(stateLabel.color.r, stateLabel.color.g, stateLabel.color.b, 0.16)

                Label {
                    id: stateLabel
                    anchors.centerIn: parent
                    font.weight: Font.DemiBold
                    color: tab.stateColor(tab.info ? tab.info.state : '')
                    text: tab.stateText(tab.info ? tab.info.state : '')
                }
            }
            ActivityIndicator {
                anchors.horizontalCenter: parent.horizontalCenter
                running: root.printer !== null && root.info === null && root.failure === ''
                visible: running
            }
            Repeater {
                model: tab.info ? tab.info.reasons : []
                delegate: Label {
                    width: column.width
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                    color: root.ui.warning
                    text: modelData
                }
            }
            Label {
                width: parent.width
                visible: root.failure !== ''
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                color: root.ui.dangerText
                text: i18n.tr("Can't reach this printer. Check that it's on and on the same Wi-Fi network.")
            }
            Label {
                width: parent.width
                visible: root.failure !== ''
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                textSize: Label.Small
                color: root.ui.dim
                text: root.failure
            }
        }

        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: units.gu(1)
            visible: root.printer === null

            Pill {
                kind: 'accent'
                text: i18n.tr('Choose a printer')
                onClicked: root.stack.push(Qt.resolvedUrl('PrintersPage.qml'))
            }
        }

        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: units.gu(1)
            visible: root.printer !== null

            Pill {
                kind: 'accent'
                iconName: 'document-print'
                text: i18n.tr('Print test page')
                busy: root.testing
                enabled: tab.info !== null && !root.testing && !root.printing && root.failure === ''
                onClicked: tab.printTestPage()
            }
            Pill {
                iconName: 'external-link'
                text: i18n.tr('Web page')
                onClicked: Qt.openUrlExternally('http://' + root.printer.host + '/')
            }
        }

        Column {
            width: parent.width
            spacing: units.gu(1)
            visible: tab.info !== null

            Item {
                width: parent.width
                height: Math.max(queueTitle.height, queueCancel.height)

                Section {
                    id: queueTitle
                    anchors { left: parent.left; right: queueCancel.left; rightMargin: units.gu(1) }
                    title: i18n.tr('Print queue')
                    subtitle: tab.rows.length > 0
                              ? i18n.tr('%1 job in the queue', '%1 jobs in the queue', tab.rows.length).arg(tab.rows.length)
                              : i18n.tr('Nothing is waiting to print')
                }
                Pill {
                    id: queueCancel
                    anchors.right: parent.right
                    visible: tab.rows.length > 1
                    kind: 'danger'
                    text: i18n.tr('Cancel all')
                    onClicked: root.cancelAll()
                }
            }

            Card {
                visible: tab.rows.length > 0
                border.width: units.dp(1.5)
                border.color: root.ui.dangerText

                Repeater {
                    model: tab.rows
                    delegate: CardRow {
                        Item {
                            width: parent.width
                            height: units.gu(5)

                            Column {
                                anchors {
                                    left: parent.left
                                    right: cancelJob.left
                                    rightMargin: units.gu(1)
                                    verticalCenter: parent.verticalCenter
                                }
                                Label {
                                    width: parent.width
                                    elide: Text.ElideMiddle
                                    color: root.ui.text
                                    text: modelData.name
                                }
                                Label {
                                    textSize: Label.Small
                                    color: root.ui.dim
                                    text: modelData.text
                                }
                            }
                            Pill {
                                id: cancelJob
                                anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                                kind: 'danger'
                                text: i18n.tr('Cancel')
                                onClicked: {
                                    if (modelData.id === undefined) {
                                        root.cancelAll()
                                        return
                                    }
                                    root.call('cancel', [root.printer, modelData.id], function(reply) {
                                        if (!reply.ok) {
                                            root.showError(reply.error)
                                        }
                                        root.refresh()
                                    })
                                }
                            }
                        }
                    }
                }
            }
        }

        Column {
            id: inkColumn
            width: parent.width
            spacing: units.gu(1)
            visible: tab.info !== null && tab.info.ink.length > 0

            Section {
                width: parent.width
                title: i18n.tr('Ink')
                subtitle: i18n.tr('Levels reported by the printer')
            }
            Row {
                spacing: units.gu(1)
                width: parent.width

                Repeater {
                    model: tab.info ? tab.info.ink : []
                    delegate: Gauge {
                        width: (inkColumn.width - (tab.info.ink.length - 1) * units.gu(1)) / tab.info.ink.length
                        ink: modelData
                    }
                }
            }
        }

        Column {
            width: parent.width
            spacing: units.gu(1)
            visible: tab.info !== null

            Section {
                width: parent.width
                title: i18n.tr('Details')
            }
            Card {
                Repeater {
                    model: tab.info ? [
                        { title: i18n.tr('Connection'), value: tab.info.uri },
                        { title: i18n.tr('Paper loaded'), value: tab.info.paper },
                        { title: i18n.tr('Firmware'), value: tab.info.firmware }
                    ].filter(function(row) { return row.value !== '' }) : []
                    delegate: CardRow {
                        Column {
                            width: parent.width
                            Label {
                                textSize: Label.Small
                                color: root.ui.dim
                                text: modelData.title
                            }
                            Label {
                                width: parent.width
                                wrapMode: Text.WrapAnywhere
                                color: root.ui.text
                                text: modelData.value
                            }
                        }
                    }
                }
            }
        }
    }
}
