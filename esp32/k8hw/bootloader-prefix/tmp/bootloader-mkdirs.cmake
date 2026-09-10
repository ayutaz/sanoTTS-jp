# Distributed under the OSI-approved BSD 3-Clause License.  See accompanying
# file LICENSE.rst or https://cmake.org/licensing for details.

cmake_minimum_required(VERSION ${CMAKE_VERSION}) # this file comes with cmake

# If CMAKE_DISABLE_SOURCE_CHANGES is set to true and the source directory is an
# existing directory in our source tree, calling file(MAKE_DIRECTORY) on it
# would cause a fatal error, even though it would be a no-op.
if(NOT EXISTS "/Users/s19447/esp/esp-idf/components/bootloader/subproject")
  file(MAKE_DIRECTORY "/Users/s19447/esp/esp-idf/components/bootloader/subproject")
endif()
file(MAKE_DIRECTORY
  "/Users/s19447/Desktop/saanoTTS-jp/esp32/k8hw/bootloader"
  "/Users/s19447/Desktop/saanoTTS-jp/esp32/k8hw/bootloader-prefix"
  "/Users/s19447/Desktop/saanoTTS-jp/esp32/k8hw/bootloader-prefix/tmp"
  "/Users/s19447/Desktop/saanoTTS-jp/esp32/k8hw/bootloader-prefix/src/bootloader-stamp"
  "/Users/s19447/Desktop/saanoTTS-jp/esp32/k8hw/bootloader-prefix/src"
  "/Users/s19447/Desktop/saanoTTS-jp/esp32/k8hw/bootloader-prefix/src/bootloader-stamp"
)

set(configSubDirs )
foreach(subDir IN LISTS configSubDirs)
    file(MAKE_DIRECTORY "/Users/s19447/Desktop/saanoTTS-jp/esp32/k8hw/bootloader-prefix/src/bootloader-stamp/${subDir}")
endforeach()
if(cfgdir)
  file(MAKE_DIRECTORY "/Users/s19447/Desktop/saanoTTS-jp/esp32/k8hw/bootloader-prefix/src/bootloader-stamp${cfgdir}") # cfgdir has leading slash
endif()
