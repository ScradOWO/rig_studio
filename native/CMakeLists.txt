cmake_minimum_required(VERSION 3.20)
project(rig_studio_spinnaker LANGUAGES CXX)

set(SPINNAKER_ROOT "C:/Program Files/Teledyne/Spinnaker" CACHE PATH "Spinnaker SDK root")
set(
    RIG_STUDIO_NATIVE_OUTPUT
    "${CMAKE_CURRENT_SOURCE_DIR}/../rig_studio/native/bin"
    CACHE PATH
    "Directory for the native backend DLL"
)

add_library(rig_studio_spinnaker SHARED src/bridge.cpp)
target_compile_features(rig_studio_spinnaker PRIVATE cxx_std_17)
target_compile_definitions(rig_studio_spinnaker PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
target_include_directories(rig_studio_spinnaker PRIVATE
    "${SPINNAKER_ROOT}/include"
)
target_link_directories(rig_studio_spinnaker PRIVATE
    "${SPINNAKER_ROOT}/lib64/vs2015"
)
target_link_libraries(rig_studio_spinnaker PRIVATE Spinnaker_v140)

set_target_properties(rig_studio_spinnaker PROPERTIES
    OUTPUT_NAME "rig_studio_spinnaker"
    RUNTIME_OUTPUT_DIRECTORY "${RIG_STUDIO_NATIVE_OUTPUT}"
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${RIG_STUDIO_NATIVE_OUTPUT}"
    RUNTIME_OUTPUT_DIRECTORY_RELWITHDEBINFO "${RIG_STUDIO_NATIVE_OUTPUT}"
)
