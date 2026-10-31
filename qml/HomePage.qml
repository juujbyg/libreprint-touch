import QtQuick 2.7
import QtQml.Models 2.2
import Lomiri.Components 1.3
import 'components'

Page {
    id: home

    readonly property var tabs: [
        { title: i18n.tr('Printer'), icon: 'printer-symbolic' },
        { title: i18n.tr('Print'), icon: 'document-print' },
        { title: i18n.tr('Scan'), icon: 'camera-symbolic' },
        { title: i18n.tr('Diagnostics'), icon: 'settings' }
    ]
    readonly property int jobCount: root.info ? root.info.jobs.length : 0

    function jobsStopping() {
        return root.info !== null && home.jobCount > 0 && root.info.jobs.every(function(job) {
            return job.stopping
        })
    }

    function bannerText() {
        var progress = root.progress
        if (root.cancelling || home.jobsStopping()) {
            return i18n.tr('Cancelling…')
        }
        if (root.failure !== '') {
            return i18n.tr("Can't reach the printer")
        }
        if (progress === null) {
            return i18n.tr('Printing — %1 job in the queue', 'Printing — %1 jobs in the queue', home.jobCount).arg(home.jobCount)
        }
        var place = progress.total > 1 ? ' (%1/%2)'.arg(progress.index + 1).arg(progress.total) : ''
        if (progress.stage === 'waiting') {
            return i18n.tr('Waiting for the printer to finish') + place
        }
        if (progress.pages > 0 && progress.page < progress.pages) {
            return i18n.tr('Preparing page %1 of %2').arg(progress.page).arg(progress.pages) + place
        }
        return i18n.tr('Sending to the printer') + place
    }

    header: PageHeader {
        id: pageHeader
        StyleHints {
            backgroundColor: root.ui.window
            dividerColor: 'transparent'
            foregroundColor: root.ui.text
        }
        contents: Item {
            anchors.fill: parent

            Row {
                anchors.verticalCenter: parent.verticalCenter
                spacing: units.gu(1)
                width: parent.width

                Label {
                    id: printerName
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.min(implicitWidth, parent.width - units.gu(3))
                    elide: Text.ElideRight
                    textSize: Label.Large
                    color: root.ui.text
                    text: root.printer ? root.printer.name : i18n.tr('LibrePrint')
                }
                Icon {
                    anchors.verticalCenter: parent.verticalCenter
                    width: units.gu(1.75)
                    height: width
                    name: 'down'
                    color: root.ui.dim
                }
            }
            MouseArea {
                anchors.fill: parent
                onClicked: root.stack.push(Qt.resolvedUrl('PrintersPage.qml'))
            }
        }
        trailingActionBar.actions: [
            Action {
                iconName: 'reload'
                text: i18n.tr('Refresh')
                onTriggered: root.refresh(true)
            }
        ]
    }

    Column {
        id: top
        anchors {
            top: home.header.bottom
            left: parent.left
            right: parent.right
        }

        Item {
            width: parent.width
            height: units.gu(6)

            Rectangle {
                y: units.gu(0.5)
                x: units.gu(0.5) + (pager.width > 0 ? pager.contentX / pager.width : 0) * tabRow.tabWidth
                width: tabRow.tabWidth
                height: units.gu(5)
                radius: units.gu(1)
                color: root.ui.card
            }

            Row {
                id: tabRow
                property real tabWidth: (parent.width - units.gu(1)) / home.tabs.length
                x: units.gu(0.5)

                Repeater {
                    model: home.tabs
                    delegate: Item {
                        width: tabRow.tabWidth
                        height: units.gu(6)

                        Column {
                            anchors.centerIn: parent
                            spacing: units.gu(0.1)

                            Icon {
                                anchors.horizontalCenter: parent.horizontalCenter
                                width: units.gu(2.25)
                                height: width
                                name: modelData.icon
                                color: tabLabel.color
                            }
                            Label {
                                id: tabLabel
                                anchors.horizontalCenter: parent.horizontalCenter
                                textSize: Label.XSmall
                                font.weight: index === root.tab ? Font.Bold : Font.Normal
                                color: index === root.tab ? root.ui.text : root.ui.dim
                                text: modelData.title
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: root.tab = index
                        }
                    }
                }
            }
        }

        Rectangle {
            id: banner
            property bool shown: root.printing || home.jobCount > 0

            width: parent.width
            height: shown ? units.gu(5.5) : 0
            clip: true
            color: root.ui.banner

            Behavior on height {
                NumberAnimation { duration: root.ui.quick; easing.type: Easing.OutCubic }
            }

            Label {
                anchors {
                    left: parent.left
                    leftMargin: units.gu(2)
                    right: bannerCancel.left
                    rightMargin: units.gu(1)
                    verticalCenter: parent.verticalCenter
                }
                elide: Text.ElideRight
                font.weight: Font.DemiBold
                color: root.ui.text
                text: home.bannerText()
            }

            Rectangle {
                id: bannerCancel
                anchors {
                    right: parent.right
                    rightMargin: units.gu(1)
                    verticalCenter: parent.verticalCenter
                }
                width: bannerCancelLabel.width + units.gu(2.5)
                height: units.gu(3.75)
                radius: units.gu(1)
                color: root.ui.bannerPill
                visible: !root.cancelling && !home.jobsStopping()

                Label {
                    id: bannerCancelLabel
                    anchors.centerIn: parent
                    font.weight: Font.DemiBold
                    color: root.ui.text
                    text: i18n.tr('Cancel all')
                }
                MouseArea {
                    anchors.fill: parent
                    onClicked: root.cancelAll()
                }
            }

            Rectangle {
                anchors.bottom: parent.bottom
                height: units.dp(3)
                color: root.ui.text
                opacity: 0.8
                visible: root.progress !== null && root.progress.pages > 0
                width: root.progress && root.progress.pages > 0
                       ? parent.width * root.progress.page / root.progress.pages : 0

                Behavior on width {
                    NumberAnimation { duration: root.ui.quick }
                }
            }
        }
    }

    ListView {
        id: pager
        anchors {
            top: top.bottom
            left: parent.left
            right: parent.right
            bottom: parent.bottom
        }
        orientation: ListView.Horizontal
        snapMode: ListView.SnapOneItem
        highlightRangeMode: ListView.StrictlyEnforceRange
        highlightMoveDuration: root.ui.quick * 2
        boundsBehavior: Flickable.StopAtBounds
        cacheBuffer: width * home.tabs.length
        clip: true
        currentIndex: 0
        onCurrentIndexChanged: root.tab = currentIndex

        Connections {
            target: root
            onTabChanged: pager.currentIndex = root.tab
        }

        model: ObjectModel {
            PrinterTab {
                width: pager.width
                height: pager.height
            }
            PrintTab {
                width: pager.width
                height: pager.height
            }
            ScanTab {
                width: pager.width
                height: pager.height
            }
            DiagnosticsTab {
                width: pager.width
                height: pager.height
                shown: root.tab === 3
            }
        }
    }
}
