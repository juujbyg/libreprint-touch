cmake_minimum_required(VERSION 3.0.0)
project(libreprint C CXX)

# Automatically create moc files
set(CMAKE_AUTOMOC ON)

execute_process(
    COMMAND dpkg-architecture -qDEB_HOST_MULTIARCH
    OUTPUT_VARIABLE ARCH_TRIPLET
    OUTPUT_STRIP_TRAILING_WHITESPACE
)
set(QT_IMPORTS_DIR "lib/${ARCH_TRIPLET}")

set(PROJECT_NAME "libreprint")
set(FULL_PROJECT_NAME "libreprint.juujbyg")
set(DATA_DIR /)
set(DESKTOP_FILE_NAME ${PROJECT_NAME}.desktop)



configure_file(manifest.json.in ${CMAKE_CURRENT_BINARY_DIR}/manifest.json)
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/manifest.json DESTINATION ${CMAKE_INSTALL_PREFIX})
install(FILES ${PROJECT_NAME}.apparmor ${PROJECT_NAME}.content-hub DESTINATION ${DATA_DIR})

install(DIRECTORY assets DESTINATION ${DATA_DIR})
install(DIRECTORY src DESTINATION ${DATA_DIR} PATTERN "__pycache__" EXCLUDE)

install(DIRECTORY qml DESTINATION ${DATA_DIR})

find_package(Qt5 COMPONENTS Core Gui REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(POPPLER REQUIRED poppler-qt5)
add_executable(pdf2pwg native/pdf2pwg.cpp)
target_include_directories(pdf2pwg PRIVATE ${POPPLER_INCLUDE_DIRS})
target_link_libraries(pdf2pwg Qt5::Core Qt5::Gui ${POPPLER_LIBRARIES})
add_executable(office2pdf native/office2pdf.c)
target_link_libraries(office2pdf ${CMAKE_DL_LIBS})
add_executable(docscan native/docscan.cpp)
target_link_libraries(docscan Qt5::Core Qt5::Gui)
# Every pixel of a photo passes through it, so an unoptimised build takes minutes.
target_compile_options(docscan PRIVATE -O2)
install(TARGETS pdf2pwg office2pdf docscan RUNTIME DESTINATION lib/${ARCH_TRIPLET}/bin)

# Translations
file(GLOB_RECURSE I18N_SRC_FILES RELATIVE ${CMAKE_CURRENT_SOURCE_DIR}/po qml/*.qml qml/*.js)
list(APPEND I18N_SRC_FILES ${DESKTOP_FILE_NAME}.in.h)

find_program(INTLTOOL_MERGE intltool-merge)
if(NOT INTLTOOL_MERGE)
    message(FATAL_ERROR "Could not find intltool-merge, please install the intltool package")
endif()
find_program(INTLTOOL_EXTRACT intltool-extract)
if(NOT INTLTOOL_EXTRACT)
    message(FATAL_ERROR "Could not find intltool-extract, please install the intltool package")
endif()

add_custom_target(${DESKTOP_FILE_NAME} ALL
    COMMENT "Merging translations into ${DESKTOP_FILE_NAME}..."
    COMMAND LC_ALL=C ${INTLTOOL_MERGE} -d -u ${CMAKE_SOURCE_DIR}/po ${CMAKE_SOURCE_DIR}/${DESKTOP_FILE_NAME}.in ${DESKTOP_FILE_NAME}
    COMMAND sed -i 's/${PROJECT_NAME}-//g' ${CMAKE_CURRENT_BINARY_DIR}/${DESKTOP_FILE_NAME}
)

install(FILES ${CMAKE_CURRENT_BINARY_DIR}/${DESKTOP_FILE_NAME} DESTINATION ${DATA_DIR})

add_subdirectory(po)

# Make source files visible in qtcreator
file(GLOB_RECURSE PROJECT_SRC_FILES
    RELATIVE ${CMAKE_CURRENT_SOURCE_DIR}
    qml/*.qml
    qml/*.js
    src/*
    *.json
    *.json.in
    *.apparmor
    *.desktop.in
)

add_custom_target(${PROJECT_NAME}_FILES ALL SOURCES ${PROJECT_SRC_FILES})

