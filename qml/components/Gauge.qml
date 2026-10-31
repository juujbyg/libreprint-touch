import QtQuick 2.7
import Lomiri.Components 1.3

// One ink cartridge. Tri-colour cartridges fill with a band for each colour.
Rectangle {
    id: gauge
    property var ink

    readonly property var fills: ink.colors.length > 0 ? ink.colors : ['8a8a8a']
    readonly property string levelText: ink.level < 0 ? '?' : ink.level + '%'

    height: content.height + units.gu(3)
    radius: units.gu(1.5)
    color: root.ui.card

    Column {
        id: content
        y: units.gu(1.5)
        width: parent.width
        spacing: units.gu(0.75)

        Rectangle {
            id: tank
            anchors.horizontalCenter: parent.horizontalCenter
            width: units.gu(6)
            height: units.gu(11)
            radius: units.gu(0.75)
            color: root.ui.gaugeEmpty
            border.width: units.dp(1.5)
            border.color: root.ui.gaugeEdge
            clip: true

            Item {
                anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: units.dp(2) }
                height: (tank.height - units.dp(4)) * Math.max(gauge.ink.level, 0) / 100
                clip: true

                Behavior on height {
                    NumberAnimation { duration: root.ui.slow; easing.type: Easing.OutCubic }
                }

                Row {
                    anchors.fill: parent
                    Repeater {
                        model: gauge.fills
                        delegate: Rectangle {
                            width: parent.width / gauge.fills.length
                            height: parent.height
                            // Black ink would vanish into the empty gauge.
                            color: modelData.toLowerCase() === '000000' ? '#151515' : '#' + modelData
                        }
                    }
                }
            }
        }

        Label {
            anchors.horizontalCenter: parent.horizontalCenter
            font.weight: Font.Bold
            textSize: Label.Large
            text: gauge.levelText
            color: root.ui.text
        }
        Label {
            width: parent.width - units.gu(1)
            anchors.horizontalCenter: parent.horizontalCenter
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            textSize: Label.Small
            text: gauge.ink.name
            color: root.ui.text
        }
        Label {
            anchors.horizontalCenter: parent.horizontalCenter
            visible: gauge.ink.low
            textSize: Label.Small
            font.weight: Font.DemiBold
            text: i18n.tr('Low — order soon')
            color: root.ui.warning
        }
    }
}
