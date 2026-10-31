import QtQuick 2.7
import Lomiri.Components 1.3

// A rounded button. kind is 'accent', 'plain' or 'danger'.
Rectangle {
    id: pill
    property string text: ''
    property string iconName: ''
    property string kind: 'plain'
    property bool busy: false
    signal clicked()

    width: Math.max(units.gu(6), row.width + units.gu(3))
    height: units.gu(5)
    radius: height / 2
    opacity: enabled ? (area.pressed ? 0.75 : 1) : 0.45
    color: kind === 'accent' ? root.ui.accent : kind === 'danger' ? root.ui.dangerPill : root.ui.pill

    Behavior on opacity {
        NumberAnimation { duration: root.ui.quick }
    }

    Row {
        id: row
        anchors.centerIn: parent
        spacing: units.gu(0.75)

        ActivityIndicator {
            anchors.verticalCenter: parent.verticalCenter
            width: units.gu(2)
            height: width
            running: pill.busy
            visible: running
        }
        Icon {
            anchors.verticalCenter: parent.verticalCenter
            width: units.gu(2)
            height: width
            name: pill.iconName
            visible: pill.iconName !== '' && !pill.busy
            color: label.color
        }
        Label {
            id: label
            anchors.verticalCenter: parent.verticalCenter
            text: pill.text
            visible: text !== ''
            font.weight: Font.DemiBold
            color: pill.kind === 'danger' ? root.ui.dangerText : root.ui.text
        }
    }

    MouseArea {
        id: area
        anchors.fill: parent
        onClicked: pill.clicked()
    }
}
