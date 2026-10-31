import QtQuick 2.7

// An on and off switch in the app's accent colour.
Rectangle {
    id: toggle
    property bool checked: true

    width: units.gu(6)
    height: units.gu(3.25)
    radius: height / 2
    color: checked ? root.ui.accent : root.ui.pill

    Behavior on color {
        ColorAnimation { duration: root.ui.quick }
    }

    Rectangle {
        x: toggle.checked ? toggle.width - width - units.gu(0.4) : units.gu(0.4)
        anchors.verticalCenter: parent.verticalCenter
        width: parent.height - units.gu(0.8)
        height: width
        radius: width / 2
        color: root.ui.text

        Behavior on x {
            NumberAnimation { duration: root.ui.quick; easing.type: Easing.OutCubic }
        }
    }

    MouseArea {
        anchors.fill: parent
        anchors.margins: -units.gu(1)
        onClicked: toggle.checked = !toggle.checked
    }
}
