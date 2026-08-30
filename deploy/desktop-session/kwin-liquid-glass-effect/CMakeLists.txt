cmake_minimum_required(VERSION 3.24)

project(echo-kwin-liquid-glass VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)

find_package(ECM 6.3 REQUIRED NO_MODULE)
list(APPEND CMAKE_MODULE_PATH ${ECM_MODULE_PATH})

include(KDEInstallDirs6)

find_package(Qt6 6.6 REQUIRED COMPONENTS Core DBus Gui)
find_package(KF6 6.3 REQUIRED COMPONENTS CoreAddons)
find_package(KWin 6.3 REQUIRED)

kcoreaddons_add_plugin(org.echoos.liquidglass
    SOURCES
        src/echo_liquid_glass_effect.cpp
        src/echo_liquid_glass_effect.h
        src/main.cpp
        src/echo_liquid_glass.qrc
    INSTALL_NAMESPACE "kwin/effects/plugins"
)

target_link_libraries(org.echoos.liquidglass PRIVATE
    KWin::kwin
    KF6::CoreAddons
    Qt6::Core
    Qt6::DBus
    Qt6::Gui
)

target_compile_definitions(org.echoos.liquidglass PRIVATE
    QT_NO_CAST_FROM_ASCII
    QT_NO_CAST_TO_ASCII
)
