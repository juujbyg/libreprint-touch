import QtQuick 2.7
import Lomiri.Components 1.3

// A row of options where one is picked, with a highlight that slides between them.
Rectangle {
    id: choice
    property var options: []
    property int current: 0
    signal picked(int index)

    width: parent ? parent.width : 0
    height: units.gu(4.5)
    radius: units.gu(1)
    color: root.ui.window

    Rectangle {
        y: units.gu(0.4)
        x: units.gu(0.4) + choice.current * (choice.width - units.gu(0.8)) / Math.max(choice.options.length, 1)
        width: (choice.width - units.gu(0.8)) / Math.max(choice.options.length, 1)
        height: parent.height - units.gu(0.8)
        radius: units.gu(0.7)
        color: root.ui.pill

        Behavior on x {
            NumberAnimation { duration: root.ui.quick; easing.type: Easing.OutCubic }
        }
    }

    Row {
        anchors.fill: parent
        anchors.margins: units.gu(0.4)

        Repeater {
            model: choice.options
            delegate: Item {
                width: parent.width / choice.options.length
                height: parent.height

                Label {
                    anchors.centerIn: parent
                    width: parent.width - units.gu(0.5)
                    horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideRight
                    textSize: Label.Small
                    text: modelData
                    color: index === choice.current ? root.ui.text : root.ui.dim
                }
                MouseArea {
                    anchors.fill: parent
                    onClicked: {
                        choice.current = index
                        choice.picked(index)
                    }
                }
            }
        }
    }
}
