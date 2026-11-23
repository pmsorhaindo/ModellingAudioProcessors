# Gyre (code name, #75 / #116): AU v2 + VST 3 wrapper of the C++ DK core (#22).
#
# Our own targets, directly against the two SDKs (docs/licensing-and-wrapper-plan.md section 6):
#   map_vst3      VST 3 bundle   (Steinberg VST 3 SDK, MIT since 3.8; no VSTGUI)
#   map_au        AU v2 bundle   (Apple AudioUnitSDK, Apache-2.0; C++23 for its std::expected)
#   map_engine    static library (the DK core + the shared realtime engine, our MIT code)
#   validator     Steinberg's VST 3 validator (built from the SDK, a test tool, not shipped)
#   map_engine_test, map_smoothing_test, map_null_vst3, map_null_au   our tests (tests/)
#
#   cmake -S plugin -B plugin/build -DCMAKE_BUILD_TYPE=Release
#   cmake --build plugin/build -j
#
# The SDKs are fetched at configure time (FetchContent) at the pinned tags below; their files keep their own
# licences (THIRD_PARTY_NOTICES.md at the repository root). Nothing is vendored into this repository.
cmake_minimum_required(VERSION 3.25)

set(CMAKE_OSX_DEPLOYMENT_TARGET "11.0" CACHE STRING "macOS deployment target (AudioUnitSDK: 11.0 or later)")
set(CMAKE_OSX_ARCHITECTURES "arm64;x86_64" CACHE STRING "macOS architectures (universal by default)")

project(map_valve_stage VERSION 0.1.0 LANGUAGES C CXX OBJC OBJCXX)

if(NOT APPLE)
    message(FATAL_ERROR "This wrapper builds AU and VST 3 bundles for macOS only (for now).")
endif()
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

set(MAP_REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/..")
set(MAP_CORE_DIR "${MAP_REPO_ROOT}/prototypes/fullstage/cpp")

# ---- identity: read the MAP_* defines from the single source of truth ------------------------------------------
file(STRINGS "${CMAKE_CURRENT_SOURCE_DIR}/src/map_identity.h" _map_defs REGEX "^#define MAP_[A-Z0-9_]+ ")
foreach(_line IN LISTS _map_defs)
    if(_line MATCHES "^#define (MAP_[A-Z0-9_]+) +\"([^\"]*)\"")
        set(${CMAKE_MATCH_1} "${CMAKE_MATCH_2}")
    elseif(_line MATCHES "^#define (MAP_[A-Z0-9_]+) +(.*)$")
        set(${CMAKE_MATCH_1} "${CMAKE_MATCH_2}")
    endif()
endforeach()
foreach(_v MAP_PRODUCT_NAME MAP_VENDOR_NAME MAP_VERSION_STRING MAP_AU_VERSION_INT MAP_VST3_BUNDLE_ID
           MAP_AU_BUNDLE_ID MAP_BUNDLE_NAME MAP_BUNDLE_FILE MAP_AU_TYPE MAP_AU_SUBTYPE MAP_AU_MANUFACTURER MAP_AU_FACTORY)
    if(NOT DEFINED ${_v})
        message(FATAL_ERROR "src/map_identity.h does not define ${_v}")
    endif()
endforeach()
message(STATUS "[MAP] ${MAP_PRODUCT_NAME} ${MAP_VERSION_STRING}: AU ${MAP_AU_TYPE} ${MAP_AU_SUBTYPE} ${MAP_AU_MANUFACTURER}")

# ---- SDKs (pinned) --------------------------------------------------------------------------------------------
include(FetchContent)
set(FETCHCONTENT_QUIET OFF)
# VST 3 SDK v3.8.1_build_84 (MIT, LICENSE.txt at that tag). Only the submodules we build: no vstgui4, no doc,
# no tutorials (VSTGUI and the mda files are BSD-like; neither is fetched or built).
FetchContent_Declare(vst3sdk
    GIT_REPOSITORY https://github.com/steinbergmedia/vst3sdk.git
    GIT_TAG v3.8.1_build_84
    GIT_SHALLOW TRUE
    GIT_SUBMODULES base cmake pluginterfaces public.sdk
    GIT_SUBMODULES_RECURSE FALSE
    GIT_PROGRESS FALSE
    SOURCE_SUBDIR do-not-add-automatically
)
# AudioUnitSDK-1.4.0 (Apache-2.0, LICENSE.txt at that tag; no NOTICE file at that tag)
FetchContent_Declare(audiounitsdk
    GIT_REPOSITORY https://github.com/apple/AudioUnitSDK.git
    GIT_TAG AudioUnitSDK-1.4.0
    GIT_SHALLOW TRUE
    GIT_PROGRESS FALSE
    SOURCE_SUBDIR do-not-add-automatically
)
# CHOC (ISC, Tracktion), pinned by commit (main, 23 Oct 2026). Header-only; we include choc/gui/choc_WebView.h and the
# few headers it pulls in (THIRD_PARTY_NOTICES.md lists them). Fetched, not vendored, like the SDKs. A full clone at
# a commit (FetchContent cannot shallow-fetch a SHA): about 15 MB.
FetchContent_Declare(choc
    GIT_REPOSITORY https://github.com/Tracktion/choc.git
    GIT_TAG 8af031c2ba4f5723f4b0a5ca14e0b4984af15b0e
    GIT_PROGRESS FALSE
    SOURCE_SUBDIR do-not-add-automatically
)
FetchContent_MakeAvailable(vst3sdk audiounitsdk choc)

# ---- VST 3 SDK: libraries, the validator and moduleinfotool; no examples, no VSTGUI ---------------------------
set(SMTG_ENABLE_VST3_PLUGIN_EXAMPLES OFF CACHE BOOL "" FORCE)
set(SMTG_ENABLE_VST3_HOSTING_EXAMPLES ON CACHE BOOL "" FORCE)    # builds the validator
set(SMTG_ENABLE_VSTGUI_SUPPORT OFF CACHE BOOL "" FORCE)
set(SMTG_RUN_VST_VALIDATOR OFF CACHE BOOL "" FORCE)              # run explicitly (README), not on every build
set(SMTG_CREATE_PLUGIN_LINK OFF CACHE BOOL "" FORCE)             # do not touch ~/Library/Audio/Plug-Ins
set(SMTG_CREATE_MODULE_INFO ON CACHE BOOL "" FORCE)
set(SMTG_DISABLE_CODE_SIGNING ON CACHE BOOL "" FORCE)
set(SMTG_BUILD_UNIVERSAL_BINARY OFF CACHE BOOL "" FORCE)         # Xcode-only switch; CMAKE_OSX_ARCHITECTURES rules
# The SDK's CMake reads the Xcode version from `xcodebuild -version` and only uses it as a gate (>= 9). With the
# Command Line Tools alone (no Xcode.app) that command fails; pass the CLT version through the SDK's own
# XCODE_VERSION override instead. Not needed when Xcode is installed (e.g. the GitHub macOS runners).
if(NOT XCODE AND NOT DEFINED ENV{XCODE_VERSION})
    execute_process(COMMAND xcodebuild -version RESULT_VARIABLE _map_xcb OUTPUT_QUIET ERROR_QUIET)
    if(NOT _map_xcb EQUAL 0)
        execute_process(COMMAND pkgutil --pkg-info=com.apple.pkg.CLTools_Executables
                        OUTPUT_VARIABLE _map_clt ERROR_QUIET)
        string(REGEX MATCH "version: ([0-9.]+)" _ "${_map_clt}")
        if(CMAKE_MATCH_1)
            set(ENV{XCODE_VERSION} "${CMAKE_MATCH_1}")
            set(XCODE_VERSION "${CMAKE_MATCH_1}")
            message(STATUS "[MAP] No Xcode.app: using the Command Line Tools version ${CMAKE_MATCH_1} for the SDK's check")
        endif()
    endif()
endif()
set(vst3sdk_SOURCE_DIR "${vst3sdk_SOURCE_DIR}")
list(APPEND CMAKE_MODULE_PATH "${vst3sdk_SOURCE_DIR}/cmake/modules")
add_subdirectory("${vst3sdk_SOURCE_DIR}" "${CMAKE_BINARY_DIR}/vst3sdk" EXCLUDE_FROM_ALL)
include(SMTG_VST3_SDK)
smtg_enable_vst3_sdk()

# ---- shared engine (our code) ---------------------------------------------------------------------------------
add_library(map_engine STATIC src/map_engine.cpp src/map_engine.h src/map_params.h src/map_identity.h)
target_include_directories(map_engine PUBLIC src "${MAP_CORE_DIR}")
target_compile_features(map_engine PUBLIC cxx_std_17)
set_target_properties(map_engine PROPERTIES POSITION_INDEPENDENT_CODE ON)
# dk_core.h needs IEEE semantics; it #errors under -ffast-math. Warnings on for our own code.
target_compile_options(map_engine PRIVATE -Wall -Wextra)
find_package(Threads REQUIRED)
target_link_libraries(map_engine PUBLIC Threads::Threads)

# ---- web editor (#105): choc WebView + the page files, shared by both formats ---------------------------------
# The page is served from memory (choc fetchResource), so the files under ui/ are compiled in: no file:// URLs and no
# bundle resources to find at run time.
set(MAP_UI_DIR "${CMAKE_CURRENT_SOURCE_DIR}/ui/spike")
set(MAP_UI_FILES index.html)
set(_ui_deps "")
foreach(_f IN LISTS MAP_UI_FILES)
    list(APPEND _ui_deps "${MAP_UI_DIR}/${_f}")
endforeach()
set(MAP_UI_RESOURCES_CPP "${CMAKE_CURRENT_BINARY_DIR}/generated/map_ui_resources.cpp")
add_custom_command(OUTPUT "${MAP_UI_RESOURCES_CPP}"
    COMMAND "${CMAKE_COMMAND}" -DROOT=${MAP_UI_DIR} -DOUT=${MAP_UI_RESOURCES_CPP} "-DFILES=${MAP_UI_FILES}"
            -P "${CMAKE_CURRENT_SOURCE_DIR}/cmake/embed_resources.cmake"
    DEPENDS ${_ui_deps} "${CMAKE_CURRENT_SOURCE_DIR}/cmake/embed_resources.cmake"
    COMMENT "Embedding the web editor page")
add_library(map_webui STATIC src/ui/map_webui.mm src/ui/map_webui.h "${MAP_UI_RESOURCES_CPP}")
target_include_directories(map_webui PUBLIC src/ui PRIVATE "${choc_SOURCE_DIR}")
target_compile_features(map_webui PUBLIC cxx_std_17)
# manual reference counting (the code releases what it retains); choc's ObjC helpers work either way
target_compile_options(map_webui PRIVATE $<$<COMPILE_LANGUAGE:OBJCXX>:-fno-objc-arc>)
set_target_properties(map_webui PROPERTIES POSITION_INDEPENDENT_CODE ON CXX_VISIBILITY_PRESET hidden
    OBJCXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN ON)
target_link_libraries(map_webui PUBLIC "-framework Cocoa" "-framework WebKit")

# Objective-C class names must be unique per build (map_au_view.mm): version + a token drawn at configure time.
string(REPLACE "." "_" _map_ver_ "${MAP_VERSION_STRING}")
string(RANDOM LENGTH 8 ALPHABET "abcdefghijklmnopqrstuvwxyz0123456789" _map_tok)
set(MAP_OBJC_SUFFIX "_${_map_ver_}_${_map_tok}")
message(STATUS "[MAP] Objective-C class suffix: ${MAP_OBJC_SUFFIX}")

# ---- VST 3 ----------------------------------------------------------------------------------------------------
smtg_add_vst3plugin(map_vst3 PACKAGE_NAME "${MAP_BUNDLE_FILE}" SOURCES_LIST src/vst3/map_vst3.cpp)
target_link_libraries(map_vst3 PRIVATE sdk map_engine map_webui)
target_compile_features(map_vst3 PRIVATE cxx_std_17)
smtg_target_set_bundle(map_vst3 BUNDLE_IDENTIFIER "${MAP_VST3_BUNDLE_ID}" COMPANY_NAME "${MAP_VENDOR_NAME}")
# The SDK helper only fills the plist under the Xcode generator; set ours (from map_identity.h) for every generator.
configure_file(src/vst3/Info.plist.in "${CMAKE_CURRENT_BINARY_DIR}/generated/vst3-Info.plist" @ONLY)
set_target_properties(map_vst3 PROPERTIES MACOSX_BUNDLE_INFO_PLIST "${CMAKE_CURRENT_BINARY_DIR}/generated/vst3-Info.plist")

# ---- AU v2 ----------------------------------------------------------------------------------------------------
file(GLOB MAP_AUSDK_SOURCES CONFIGURE_DEPENDS "${audiounitsdk_SOURCE_DIR}/src/AudioUnitSDK/*.cpp")
add_library(map_ausdk STATIC ${MAP_AUSDK_SOURCES})
target_include_directories(map_ausdk PUBLIC "${audiounitsdk_SOURCE_DIR}/include")
target_compile_features(map_ausdk PUBLIC cxx_std_23)
set_target_properties(map_ausdk PROPERTIES CXX_EXTENSIONS OFF POSITION_INDEPENDENT_CODE ON)

set(MAP_AU_EXECUTABLE "${MAP_BUNDLE_FILE}")
configure_file(src/au/Info.plist.in "${CMAKE_CURRENT_BINARY_DIR}/generated/au-Info.plist" @ONLY)
add_library(map_au MODULE src/au/map_au.cpp src/au/map_au_view.mm)
target_link_libraries(map_au PRIVATE map_ausdk map_engine map_webui
    "-framework AudioToolbox" "-framework CoreAudio" "-framework CoreFoundation" "-framework AudioUnit")
target_compile_features(map_au PRIVATE cxx_std_23)
target_compile_options(map_au PRIVATE $<$<COMPILE_LANGUAGE:OBJCXX>:-fno-objc-arc>)
# the Cocoa view factory's class name, also returned by kAudioUnitProperty_CocoaUI (map_au.cpp)
target_compile_definitions(map_au PRIVATE MAP_OBJC_SUFFIX=${MAP_OBJC_SUFFIX}
    MAP_AU_VIEW_FACTORY_NAME="GyreAUViewFactory${MAP_OBJC_SUFFIX}")
set_target_properties(map_au PROPERTIES
    BUNDLE TRUE
    BUNDLE_EXTENSION component
    OUTPUT_NAME "${MAP_AU_EXECUTABLE}"
    MACOSX_BUNDLE_INFO_PLIST "${CMAKE_CURRENT_BINARY_DIR}/generated/au-Info.plist"
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/AU")
# Only the factory function is exported.
target_link_options(map_au PRIVATE "LINKER:-exported_symbol,_${MAP_AU_FACTORY}")
set(MAP_AU_BUNDLE "${CMAKE_BINARY_DIR}/AU/${MAP_BUNDLE_FILE}.component")

# ---- tests (not shipped) --------------------------------------------------------------------------------------
add_executable(map_engine_test tests/engine_test.cpp)
target_link_libraries(map_engine_test PRIVATE map_engine)

add_executable(map_smoothing_test tests/smoothing_test.cpp)       # #73: Drive / Output / Bias smoothing
target_link_libraries(map_smoothing_test PRIVATE map_engine)

add_executable(map_null_au tests/null_au.cpp)
target_link_libraries(map_null_au PRIVATE map_engine
    "-framework AudioToolbox" "-framework CoreAudio" "-framework CoreFoundation")
target_compile_definitions(map_null_au PRIVATE
    MAP_TEST_AU_TYPE="${MAP_AU_TYPE}" MAP_TEST_AU_SUBTYPE="${MAP_AU_SUBTYPE}"
    MAP_TEST_AU_MANUFACTURER="${MAP_AU_MANUFACTURER}")

add_executable(map_null_vst3 tests/null_vst3.cpp
    "${vst3sdk_SOURCE_DIR}/public.sdk/source/vst/hosting/plugprovider.cpp"
    "${vst3sdk_SOURCE_DIR}/public.sdk/source/common/memorystream.cpp"
    "${vst3sdk_SOURCE_DIR}/public.sdk/source/vst/hosting/module_mac.mm")
set_source_files_properties("${vst3sdk_SOURCE_DIR}/public.sdk/source/vst/hosting/module_mac.mm"
    PROPERTIES COMPILE_FLAGS "-fobjc-arc")
target_link_libraries(map_null_vst3 PRIVATE sdk_hosting map_engine "-framework CoreFoundation" "-framework Foundation")
target_compile_features(map_null_vst3 PRIVATE cxx_std_17)
get_target_property(MAP_VST3_PACKAGE map_vst3 SMTG_PLUGIN_PACKAGE_PATH)
target_compile_definitions(map_null_vst3 PRIVATE MAP_TEST_VST3_PATH="${MAP_VST3_PACKAGE}")
add_dependencies(map_null_vst3 map_vst3)

# #105: the web editors of both built plugins in a test window (key focus, parameter bridge, resize, open / close)
add_executable(map_ui_test tests/ui_test.mm
    "${vst3sdk_SOURCE_DIR}/public.sdk/source/vst/hosting/plugprovider.cpp"
    "${vst3sdk_SOURCE_DIR}/public.sdk/source/vst/hosting/module_mac.mm")
set_source_files_properties(tests/ui_test.mm PROPERTIES COMPILE_FLAGS "-fno-objc-arc")
set_source_files_properties("${vst3sdk_SOURCE_DIR}/public.sdk/source/vst/hosting/module_mac.mm"
    PROPERTIES COMPILE_FLAGS "-fobjc-arc")
target_include_directories(map_ui_test PRIVATE src)
target_link_libraries(map_ui_test PRIVATE sdk_hosting "-framework Cocoa" "-framework WebKit" "-framework AudioToolbox"
    "-framework CoreFoundation" "-framework Foundation")
target_compile_features(map_ui_test PRIVATE cxx_std_17)
target_compile_definitions(map_ui_test PRIVATE MAP_TEST_VST3_PATH="${MAP_VST3_PACKAGE}")
add_dependencies(map_ui_test map_vst3 map_au)

# the validator is EXCLUDE_FROM_ALL with the SDK; build it with the default target
add_custom_target(map_all ALL DEPENDS map_vst3 map_au validator map_engine_test map_smoothing_test map_null_au
    map_null_vst3 map_ui_test)
message(STATUS "[MAP] VST 3 bundle: ${MAP_VST3_PACKAGE}")
message(STATUS "[MAP] AU bundle:    ${MAP_AU_BUNDLE}")
